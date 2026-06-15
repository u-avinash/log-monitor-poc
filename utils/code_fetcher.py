"""Utility to fetch and analyze code from GitHub repositories."""
import logging
import re
from typing import Optional, Dict, Any, List
from integrations.github_client import GitHubClient

logger = logging.getLogger(__name__)


class CodeFetcher:
    """Fetch and analyze code from GitHub for error analysis and fixes."""

    def __init__(self, project_id: Optional[str] = None):
        try:
            self.github_client = GitHubClient(project_id=project_id)
        except ValueError as exc:
            logger.warning("GitHub not configured for project %s — code fetching disabled: %s", project_id, exc)
            self.github_client = None  # type: ignore[assignment]

    def _normalize_repo_name(self, repo_full_name: str) -> str:
        """Ensure repo_full_name includes org/owner prefix.

        Only prepends the configured org when:
        - the value has no '/' (bare repo name), AND
        - the bare value does NOT equal the org itself (org-only strings are not valid repo names)
        """
        if not repo_full_name:
            return repo_full_name
        if '/' not in repo_full_name:
            org = (self.github_client.org or "").strip() if self.github_client else ""
            # Guard: if the bare name equals the org, it is the org name, not a repo name
            if org and repo_full_name.lower() != org.lower():
                normalized = f"{org}/{repo_full_name}"
                logger.info("[Code Fetch] Normalized repo name: %s → %s", repo_full_name, normalized)
                return normalized
            elif org and repo_full_name.lower() == org.lower():
                logger.warning(
                    "[Code Fetch] Bare repo name '%s' equals configured org — "
                    "skipping normalization (need full org/repo)",
                    repo_full_name,
                )
        return repo_full_name

    def _find_file_by_filename(self, repo_full_name: str, filename: str) -> Optional[str]:
        """Search the repository tree for a file by filename."""
        if not self.github_client or not filename:
            return None

        repo_full_name = self._normalize_repo_name(repo_full_name)

        try:
            repo = self.github_client.client.get_repo(repo_full_name)
            branch = repo.default_branch
            tree = repo.get_git_tree(branch, recursive=True)

            preferred_prefixes = [
                "src/main/mule/",
                "src/main/app/",
                "src/main/resources/dataweave/",
                "src/main/resources/dw/",
                "src/main/resources/properties/",
                "src/main/java/",
            ]

            # Handle truncated trees (repos with > 100,000 entries)
            tree_items = tree.tree
            if tree.truncated:
                logger.warning(
                    "[Code Fetch] Repository tree truncated for %s — results may be incomplete",
                    repo_full_name,
                )

            matches = [
                item.path for item in tree_items
                if item.type == "blob" and (
                    item.path.endswith(f"/{filename}") or item.path == filename
                )
            ]

            if not matches:
                logger.warning("Repository tree search found no match for filename: %s", filename)
                return None

            for prefix in preferred_prefixes:
                for match in matches:
                    if match.startswith(prefix):
                        logger.info("Repository tree search matched preferred path: %s", match)
                        return match

            logger.info("Repository tree search matched path: %s", matches[0])
            return matches[0]

        except Exception as exc:
            logger.warning("Repository tree search failed for %s/%s: %s", repo_full_name, filename, exc)
            return None

    def fetch_mulesoft_project_context(self, repo_full_name: str) -> Dict[str, str]:
        """
        Fetch all relevant MuleSoft project source files from the repository.

        Collects:
        - All XML flow files from src/main/mule or src/main/app
        - All DataWeave (.dwl) scripts from src/main/resources/dataweave or dw
        - pom.xml and mule-artifact.json for project metadata

        Returns:
            Dict mapping file_path → file_content for all discovered project files.
        """
        if not self.github_client:
            return {}

        repo_full_name = self._normalize_repo_name(repo_full_name)
        project_files: Dict[str, str] = {}

        try:
            repo = self.github_client.client.get_repo(repo_full_name)
            branch = repo.default_branch
            tree = repo.get_git_tree(branch, recursive=True)

            mule_dirs = {"src/main/mule", "src/main/app"}
            dw_dirs = {"src/main/resources/dataweave", "src/main/resources/dw"}
            metadata_files = {"pom.xml", "mule-artifact.json"}

            target_paths = [
                item.path for item in tree.tree
                if item.type == "blob" and (
                    any(item.path.startswith(d + "/") for d in mule_dirs | dw_dirs)
                    or item.path in metadata_files
                )
            ]

            for path in target_paths:
                content = self.github_client.get_file_content(repo_full_name, path)
                if content:
                    project_files[path] = content
                    logger.info("[Code Fetch] Fetched project file: %s", path)

            logger.info(
                "[Code Fetch] Fetched %d MuleSoft project files from %s",
                len(project_files), repo_full_name,
            )
        except Exception as exc:
            logger.warning("[Code Fetch] Failed to fetch MuleSoft project context: %s", exc)

        return project_files
    
    def extract_error_file_info(self, raw_log: str, stack_trace: str, error_title: str, metadata: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Extract file path, line number, and error type from logs.
        
        Priority:
        1. metadata['custom_attributes'] (structured OTLP data from MuleSoft)
        2. Regex patterns in raw_log
        3. Regex patterns in stack_trace
        
        Args:
            raw_log: Raw log text
            stack_trace: Stack trace text
            error_title: Error title/type
            metadata: Optional metadata dict with custom_attributes from OTLP
            
        Returns:
            Dict with file_path, line_number, error_type, file_type
        """
        file_info = None
        
        # PRIORITY 0: Check structured metadata from OTLP (MuleSoft custom attributes)
        if metadata and 'custom_attributes' in metadata:
            attrs = metadata['custom_attributes']
            
            # Check for GitHub-specific attributes FIRST (highest priority)
            file_path = (
                attrs.get('github.file_path') or
                attrs.get('github_file_path') or
                attrs.get('error.file') or 
                attrs.get('error_file') or
                attrs.get('source.file') or
                attrs.get('mule.flow.file') or
                attrs.get('file')
            )
            
            line_number = (
                attrs.get('error.line_number') or 
                attrs.get('error_line') or
                attrs.get('line_number') or
                attrs.get('source.line') or
                attrs.get('line')
            )
            
            # Convert line_number to int if it's a string
            if line_number:
                if isinstance(line_number, str):
                    try:
                        line_number = int(line_number)
                    except ValueError:
                        line_number = None
                elif not isinstance(line_number, int):
                    line_number = None
            
            if file_path:
                # Normalize path: if it's just a filename, add appropriate directory prefix
                if '/' not in file_path:
                    # Just a filename - add standard Mule path based on extension
                    if file_path.endswith('.xml'):
                        file_path = f'src/main/mule/{file_path}'
                    elif file_path.endswith('.dwl'):
                        file_path = f'src/main/resources/dataweave/{file_path}'
                    elif file_path.endswith(('.yaml', '.yml')):
                        file_path = f'src/main/resources/properties/{file_path}'
                    elif file_path.endswith('.java'):
                        file_path = f'src/main/java/{file_path}'
                    logger.info(f"[Code Fetch] Normalized bare filename to: {file_path}")
                
                file_info = {
                    'file_path': file_path,
                    'line_number': line_number,
                    'source': 'otlp_metadata'
                }
                logger.info(f"[Code Fetch] ✓ Found file info in OTLP metadata: {file_path}:{line_number}")
        
        # Pattern 1: Separate File and Line (CloudHub 2.0 multi-line format)
        # Example:
        # File: src/main/mule/order-flows.xml
        # Line: 9
        multi_line_pattern = r'File:\s*([^\n\r]+)[\r\n]+Line:\s*(\d+)'
        match = re.search(multi_line_pattern, raw_log)
        if match:
            file_info = {
                'file_path': match.group(1).strip(),
                'line_number': int(match.group(2)),
                'source': 'raw_log_multiline'
            }

        # Pattern 1: From raw_log (single-line format)
        # Example: "File: src/main/resources/dataweave/map-customer-data.dwl:7"
        if not file_info:
            log_pattern = r'File:\s*([^\s:]+):(\d+)'
            match = re.search(log_pattern, raw_log)
            if match:
                file_info = {
                    'file_path': match.group(1),
                    'line_number': int(match.group(2)),
                    'source': 'raw_log_singleline'
                }
        
        # Pattern 2: From stack trace (DataWeave errors)
        # Example: "at transform-payment-request.dwl:8 (billingAddress.street)"
        if not file_info:
            dwl_pattern = r'at\s+([^\s:]+\.dwl):(\d+)'
            match = re.search(dwl_pattern, stack_trace)
            if match:
                filename = match.group(1)
                line_num = int(match.group(2))
                # Reconstruct full path
                file_info = {
                    'file_path': f'src/main/resources/dataweave/{filename}',
                    'line_number': line_num,
                    'source': 'stack_trace_dwl'
                }
        
        # Pattern 3: From stack trace (Mule XML errors)
        # Example: "at org.mule.extension.db.internal.DbConnector.select(payment-processing.xml:19)"
        if not file_info:
            xml_pattern = r'\(([^\s:]+\.xml):(\d+)\)'
            match = re.search(xml_pattern, stack_trace)
            if match:
                filename = match.group(1)
                line_num = int(match.group(2))
                file_info = {
                    'file_path': f'src/main/mule/{filename}',
                    'line_number': line_num,
                    'source': 'stack_trace_xml'
                }
        
        # Pattern 4: From stack trace (YAML errors)
        # Example: "in 'reader', line 7, column 5"
        if not file_info and 'YAML' in error_title:
            yaml_pattern = r'line\s+(\d+)'
            match = re.search(yaml_pattern, stack_trace)
            if match:
                line_num = int(match.group(1))
                # Try to find YAML file name in log
                yaml_file_pattern = r'([^\s]+\.yaml)'
                file_match = re.search(yaml_file_pattern, raw_log)
                if file_match:
                    filename = file_match.group(1)
                    # Check if filename already contains full path
                    if 'src/main' in filename:
                        file_path = filename
                    elif '/' in filename:
                        # Has some path, use as-is
                        file_path = filename
                    else:
                        # Just filename, prepend standard path
                        file_path = f'src/main/resources/properties/{filename}'
                    
                    file_info = {
                        'file_path': file_path,
                        'line_number': line_num,
                        'source': 'yaml_error'
                    }
        
        # Pattern 5: Java stack trace
        # Example: "at org.mule.runtime.core.internal.processor.LoggerMessageProcessor.process(card-validation.xml:11)"
        if not file_info:
            java_pattern = r'at\s+[\w.]+\(([\w.-]+\.(java|xml)):(\d+)\)'
            match = re.search(java_pattern, stack_trace)
            if match:
                filename = match.group(1)
                line_num = int(match.group(3))
                file_info = {
                    'file_path': f'src/main/mule/{filename}' if filename.endswith('.xml') else f'src/main/java/{filename}',
                    'line_number': line_num,
                    'source': 'java_stack'
                }
        
        if file_info:
            # Determine file type
            file_path = file_info['file_path']
            if file_path.endswith('.dwl'):
                file_info['file_type'] = 'dataweave'
            elif file_path.endswith('.xml'):
                file_info['file_type'] = 'mule_xml'
            elif file_path.endswith('.yaml') or file_path.endswith('.yml'):
                file_info['file_type'] = 'yaml'
            elif file_path.endswith('.java'):
                file_info['file_type'] = 'java'
            else:
                file_info['file_type'] = 'unknown'
            
            logger.info(f"Extracted file info: {file_info}")
        else:
            logger.warning("Could not extract file info from logs")
        
        return file_info
    
    def fetch_code_for_analysis(
        self,
        repo_full_name: str,
        file_path: str,
        line_number: Optional[int] = None,
        context_lines: int = 20
    ) -> Optional[Dict[str, Any]]:
        """
        Fetch code from GitHub for analysis.
        
        Args:
            repo_full_name: Full repo name (org/repo)
            file_path: Path to file in repo
            line_number: Line number where error occurred
            context_lines: Number of lines before/after to include
            
        Returns:
            Dict with full_content, context_snippet, metadata
        """
        if not self.github_client:
            logger.warning("GitHub client not configured")
            return None

        try:
            filename = file_path.split('/')[-1]

            # Ensure repo_full_name contains org/repo (not just repo name)
            repo_full_name = self._normalize_repo_name(repo_full_name)

            # Fetch full file content (with fallback path attempts)
            full_content = self.github_client.get_file_content(repo_full_name, file_path)

            # If not found, try common alternate paths based on file type / conventions
            if not full_content:
                alternatives: List[str] = []

                # DataWeave: sometimes stored under different folder name
                if file_path.startswith('src/main/resources/dataweave/'):
                    alternatives.append(file_path.replace('src/main/resources/dataweave/', 'src/main/resources/dw/'))
                    alternatives.append(file_path.replace('src/main/resources/dataweave/', 'src/main/resources/'))
                    alternatives.append(f"src/main/resources/dataweave/{filename}")
                    alternatives.append(f"src/main/resources/dw/{filename}")

                # Mule XML: prefer the standard MuleSoft source location src/main/mule.
                # Some repos may use src/main/app, but XML should not be fetched from resources.
                if file_path.startswith('src/main/mule/') or file_path.startswith('src/main/app/'):
                    alternatives.append(file_path.replace('src/main/app/', 'src/main/mule/'))
                    alternatives.append(file_path.replace('src/main/mule/', 'src/main/app/'))
                    alternatives.append(f"src/main/mule/{filename}")
                    alternatives.append(f"src/main/app/{filename}")

                # YAML: sometimes stored in config/ or directly under resources
                if file_path.endswith(('.yaml', '.yml')):
                    alternatives.append(f"src/main/resources/{filename}")
                    alternatives.append(f"config/{filename}")

                # De-dupe while preserving order, and avoid retrying the same path
                seen = {file_path}
                alternatives = [p for p in alternatives if p and p not in seen and not seen.add(p)]

                for alt_path in alternatives:
                    logger.info(f"Retrying fetch with alternate path: {alt_path}")
                    full_content = self.github_client.get_file_content(repo_full_name, alt_path)
                    if full_content:
                        logger.info(f"✓ Fetched using alternate path: {alt_path}")
                        file_path = alt_path
                        break

            if not full_content:
                discovered_path = self._find_file_by_filename(repo_full_name, filename)
                if discovered_path and discovered_path != file_path:
                    logger.info(f"Retrying fetch using repository tree match: {discovered_path}")
                    full_content = self.github_client.get_file_content(repo_full_name, discovered_path)
                    if full_content:
                        file_path = discovered_path

            if not full_content:
                logger.warning(f"Could not fetch {file_path} from {repo_full_name} (including alternate paths)")
                return None
            
            result = {
                'full_content': full_content,
                'file_path': file_path,
                'repo': repo_full_name,
                'line_count': len(full_content.splitlines())
            }
            
            # If line number specified, extract context
            if line_number:
                lines = full_content.splitlines()
                total_lines = len(lines)
                
                start_line = max(1, line_number - context_lines)
                end_line = min(total_lines, line_number + context_lines)
                
                context_lines_list = lines[start_line-1:end_line]
                
                # Highlight the error line
                context_with_markers = []
                for i, line in enumerate(context_lines_list, start=start_line):
                    marker = ">>> " if i == line_number else "    "
                    context_with_markers.append(f"{marker}{i:4d} | {line}")
                
                result['context_snippet'] = '\n'.join(context_with_markers)
                result['error_line_number'] = line_number
                result['context_start'] = start_line
                result['context_end'] = end_line
            else:
                # No specific line, return first N lines as context
                lines = full_content.splitlines()[:50]
                result['context_snippet'] = '\n'.join(f"{i+1:4d} | {line}" for i, line in enumerate(lines))
            
            logger.info(f"Successfully fetched code from {repo_full_name}/{file_path}")
            return result
            
        except Exception as e:
            logger.error(f"Error fetching code: {str(e)}")
            return None
    
    def get_related_files(
        self,
        repo_full_name: str,
        main_file_path: str,
        file_type: str
    ) -> List[str]:
        """
        Get related files that might be relevant for analysis.
        
        Args:
            repo_full_name: Full repo name
            main_file_path: Main file with error
            file_type: Type of file (dataweave, mule_xml, yaml, java)
            
        Returns:
            List of related file paths
        """
        related = []
        
        try:
            if file_type == 'dataweave':
                # For DataWeave errors, fetch the calling Mule flow
                dir_path = '/'.join(main_file_path.split('/')[:-1])
                related.append(f"{dir_path}/*.xml")
            
            elif file_type == 'mule_xml':
                # For Mule XML, fetch related DataWeave scripts
                flow_name = main_file_path.split('/')[-1].replace('.xml', '')
                related.append(f"src/main/resources/dataweave/*")
            
            elif file_type == 'yaml':
                # For YAML config, fetch the main application file
                related.append("src/main/mule/*.xml")
                related.append("src/main/app/*.xml")
            
        except Exception as e:
            logger.error(f"Error finding related files: {str(e)}")
        
        return related
