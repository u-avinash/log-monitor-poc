"""GitHub integration client for repository operations and PR management."""
import os
import logging
import base64
from typing import Optional, List, Dict, Any
from github import Github, GithubException
from config.settings import get_settings
from utils.retry_handler import retry_with_backoff
import re

logger = logging.getLogger(__name__)
settings = get_settings()


class GitHubClient:
    """
    GitHub client for repository operations.
    
    Features:
    - Dynamic repository detection from logs
    - File content fetching
    - Branch management
    - PR creation and management
    - Code search
    """
    
    def __init__(self, token: Optional[str] = None):
        """
        Initialize GitHub client.
        
        Args:
            token: GitHub personal access token (defaults to settings)
        """
        self.token = token or settings.github_token
        if not self.token:
            logger.warning("GitHub token not configured. Set GITHUB_TOKEN in .env")
            self.client = None
        else:
            self.client = Github(self.token)
            logger.info("GitHub client initialized")
    
    def extract_repo_from_log(self, log_text: str, app_name: str) -> Optional[str]:
        """
        Extract repository name from log or derive from app name.
        
        Strategy:
        1. Check app_repo_mapping.yaml for configured mapping
        2. Look for explicit repo mentions in log
        3. Search for repo matching app_name in organization
        4. Fall back to app_name as repo name
        
        Args:
            log_text: Raw log text
            app_name: Application name
            
        Returns:
            Repository full name (org/repo) or None
        """
        # Normalize app name (simulator/logs sometimes include extra whitespace/casing)
        app_name_norm = (app_name or "").strip()
        
        try:
            # Strategy 1: Check app_repo_mapping.yaml (PRIORITY - works without GitHub API)
            try:
                import yaml
                from pathlib import Path
                
                mapping_file = Path(__file__).parent.parent / "config" / "app_repo_mapping.yaml"
                if mapping_file.exists():
                    with open(mapping_file, 'r') as f:
                        mappings = yaml.safe_load(f)
                        
                    if mappings and 'app_mappings' in mappings:
                        app_mappings = mappings['app_mappings'] or {}

                        # Exact match (normalized)
                        if app_name_norm in app_mappings:
                            repo_full_name = app_mappings[app_name_norm]['repo']
                            logger.info(f"✓ Found repo in mapping: {app_name_norm} → {repo_full_name}")
                            return repo_full_name

                        # Case-insensitive match (also handles accidental whitespace differences)
                        app_name_lower = app_name_norm.lower()
                        for k, v in app_mappings.items():
                            if (k or "").strip().lower() == app_name_lower:
                                repo_full_name = v['repo']
                                logger.info(f"✓ Found repo in mapping (case-insensitive): {app_name_norm} → {repo_full_name}")
                                return repo_full_name
            except Exception as e:
                logger.warning(f"Could not load app_repo_mapping.yaml: {e}")
            
            # Check if GitHub client is available for API-based strategies
            if not self.client:
                logger.warning(f"GitHub client not initialized - falling back to default repo")
                # Fallback when no mapping found and no GitHub client
                if settings.github_org and settings.github_default_repo:
                    fallback = f"{settings.github_org}/{settings.github_default_repo}"
                    logger.info(f"Using fallback default repo: {fallback}")
                    return fallback
                elif settings.github_org and app_name_norm:
                    fallback = f"{settings.github_org}/{app_name_norm}"
                    logger.info(f"Using fallback repo based on app name: {fallback}")
                    return fallback
                return None
            
            # Strategy 2: Look for GitHub URL patterns in logs
            url_patterns = [
                r'github\.com[:/]([^/\s]+/[^/\s]+)',
                r'git@github\.com:([^/\s]+/[^/\s]+)',
            ]
            
            for pattern in url_patterns:
                match = re.search(pattern, log_text, re.IGNORECASE)
                if match:
                    repo_full_name = match.group(1).rstrip('.git')
                    logger.info(f"Extracted repo from log: {repo_full_name}")
                    return repo_full_name
            
            # Strategy 3: Search in organization
            org = self.client.get_organization(settings.github_org)
            
            # Try exact match
            try:
                repo = org.get_repo(app_name_norm)
                full_name = repo.full_name
                logger.info(f"Found exact match repo: {full_name}")
                return full_name
            except GithubException:
                pass
            
            # Try fuzzy match
            repos = org.get_repos()
            for repo in repos:
                if app_name_norm.lower() in repo.name.lower():
                    full_name = repo.full_name
                    logger.info(f"Found fuzzy match repo: {full_name}")
                    return full_name
            
            # Strategy 4: Fallback
            # Prefer configured default repo (POC / shared mono-repo) over app_name-derived repo.
            if settings.github_org and settings.github_default_repo:
                fallback = f"{settings.github_org}/{settings.github_default_repo}"
                logger.info(f"Using fallback default repo: {fallback}")
                return fallback

            # Last resort: assume repo name matches app name
            fallback = f"{settings.github_org}/{app_name_norm}"
            logger.info(f"Using fallback repo name: {fallback}")
            return fallback
            
        except Exception as e:
            logger.error(f"Failed to extract repo: {str(e)}")
            return None
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def get_file_content(
        self,
        repo_full_name: str,
        file_path: str,
        branch: str = None
    ) -> Optional[str]:
        """
        Fetch file content from repository.
        
        Args:
            repo_full_name: Full repository name (org/repo)
            file_path: Path to file in repository
            branch: Branch name (defaults to default branch)
            
        Returns:
            File content as string or None
        """
        if not self.client:
            logger.error("GitHub client not initialized")
            return None
        
        try:
            repo = self.client.get_repo(repo_full_name)
            branch = branch or repo.default_branch
            
            logger.info(f"Fetching {file_path} from {repo_full_name}@{branch}")
            
            contents = repo.get_contents(file_path, ref=branch)
            
            if isinstance(contents, list):
                logger.warning(f"{file_path} is a directory, not a file")
                return None
            
            # Decode base64 content
            content = base64.b64decode(contents.content).decode('utf-8')
            logger.info(f"Successfully fetched {file_path} ({len(content)} bytes)")
            return content
            
        except GithubException as e:
            if e.status == 404:
                logger.warning(f"File not found: {file_path}")
            else:
                logger.error(f"GitHub API error: {e.status} - {e.data}")
            return None
        except Exception as e:
            logger.error(f"Failed to fetch file: {str(e)}")
            return None
    
    def find_file_by_stacktrace(
        self,
        repo_full_name: str,
        stack_trace: str
    ) -> List[Dict[str, Any]]:
        """
        Extract file paths and line numbers from stack trace.
        
        Args:
            repo_full_name: Full repository name
            stack_trace: Stack trace text
            
        Returns:
            List of dicts with file_path, line_number, method
        """
        files = []
        
        # Java stack trace pattern
        # Example: at com.mule.payment.PaymentProcessor.process(PaymentProcessor.java:42)
        java_pattern = r'at\s+[\w.]+\(([\w.]+\.java):(\d+)\)'
        
        # MuleSoft XML pattern
        # Example: at flow "payment-flow" (payment-api.xml:15)
        mule_pattern = r'at\s+(?:flow|processor)\s+"[^"]+"\s+\(([^:]+\.xml):(\d+)\)'
        
        # Python pattern
        # Example: File "/path/script.py", line 42, in function_name
        python_pattern = r'File\s+"([^"]+\.py)",\s+line\s+(\d+)'
        
        patterns = [
            ('java', java_pattern),
            ('mule', mule_pattern),
            ('python', python_pattern)
        ]
        
        for lang, pattern in patterns:
            matches = re.finditer(pattern, stack_trace)
            for match in matches:
                file_path = match.group(1)
                line_number = int(match.group(2))
                
                # For Java, convert package.path.File.java to src/main/java/package/path/File.java
                if lang == 'java' and '/' not in file_path:
                    # Assume standard Maven structure
                    file_path = f"src/main/java/{file_path}"
                
                files.append({
                    'file_path': file_path,
                    'line_number': line_number,
                    'language': lang
                })
        
        logger.info(f"Extracted {len(files)} file references from stack trace")
        return files
    
    def get_code_context(
        self,
        repo_full_name: str,
        file_path: str,
        line_number: int,
        context_lines: int = 10
    ) -> Optional[Dict[str, Any]]:
        """
        Get code context around a specific line.
        
        Args:
            repo_full_name: Full repository name
            file_path: Path to file
            line_number: Target line number
            context_lines: Number of lines before/after to include
            
        Returns:
            Dict with full_content, context_snippet, start_line, end_line
        """
        content = self.get_file_content(repo_full_name, file_path)
        if not content:
            return None
        
        lines = content.split('\n')
        total_lines = len(lines)
        
        # Calculate context boundaries
        start_line = max(1, line_number - context_lines)
        end_line = min(total_lines, line_number + context_lines)
        
        # Extract context
        context_lines_list = lines[start_line-1:end_line]
        context_snippet = '\n'.join(context_lines_list)
        
        return {
            'full_content': content,
            'context_snippet': context_snippet,
            'start_line': start_line,
            'end_line': end_line,
            'target_line': line_number,
            'total_lines': total_lines
        }
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def create_branch(
        self,
        repo_full_name: str,
        branch_name: str,
        from_branch: Optional[str] = None
    ) -> bool:
        """
        Create a new branch.
        
        Args:
            repo_full_name: Full repository name
            branch_name: New branch name
            from_branch: Source branch (defaults to default branch)
            
        Returns:
            True if successful
        """
        if not self.client:
            return None
        
        try:
            repo = self.client.get_repo(repo_full_name)
            from_branch = from_branch or repo.default_branch
            
            # Get source branch SHA
            source_branch = repo.get_branch(from_branch)
            source_sha = source_branch.commit.sha
            
            # Create new branch
            ref = f"refs/heads/{branch_name}"
            repo.create_git_ref(ref=ref, sha=source_sha)
            
            logger.info(f"Created branch {branch_name} from {from_branch}")
            return True
            
        except GithubException as e:
            if e.status == 422:
                logger.warning(f"Branch {branch_name} already exists")
                return True  # Branch exists, that's fine
            logger.error(f"Failed to create branch: {e.status} - {e.data}")
            return False
        except Exception as e:
            logger.error(f"Failed to create branch: {str(e)}")
            return False
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def update_file(
        self,
        repo_full_name: str,
        file_path: str,
        content: str,
        commit_message: str,
        branch: str
    ) -> Optional[Dict[str, Any]]:
        """
        Update file content in repository.
        
        Args:
            repo_full_name: Full repository name
            file_path: Path to file
            content: New file content
            commit_message: Commit message
            branch: Target branch
            
        Returns:
            Dict with commit_sha and commit_url if successful, else None
        """
        if not self.client:
            return False
        
        try:
            repo = self.client.get_repo(repo_full_name)
            
            # Get current file to get SHA
            try:
                current_file = repo.get_contents(file_path, ref=branch)
                sha = current_file.sha
            except GithubException:
                # File doesn't exist, create new
                sha = None
            
            # Update or create file
            commit = None
            if sha:
                result = repo.update_file(
                    path=file_path,
                    message=commit_message,
                    content=content,
                    sha=sha,
                    branch=branch
                )
                commit = result.get("commit") if isinstance(result, dict) else None
                logger.info(f"Updated {file_path} on {branch}")
            else:
                result = repo.create_file(
                    path=file_path,
                    message=commit_message,
                    content=content,
                    branch=branch
                )
                commit = result.get("commit") if isinstance(result, dict) else None
                logger.info(f"Created {file_path} on {branch}")
            
            commit_sha = getattr(commit, "sha", None) if commit else None
            commit_url = getattr(commit, "html_url", None) if commit else None
            
            return {
                "commit_sha": commit_sha,
                "commit_url": commit_url,
            }
            
        except Exception as e:
            logger.error(f"Failed to update file: {str(e)}")
            return None
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def create_pull_request(
        self,
        repo_full_name: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: Optional[str] = None,
        labels: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        Create a pull request.
        
        Args:
            repo_full_name: Full repository name
            title: PR title
            body: PR description
            head_branch: Source branch
            base_branch: Target branch (defaults to default branch)
            labels: List of label names
            
        Returns:
            PR URL or None
        """
        if not self.client:
            return None
        
        try:
            repo = self.client.get_repo(repo_full_name)
            base_branch = base_branch or repo.default_branch
            
            # Create PR
            pr = repo.create_pull(
                title=title,
                body=body,
                head=head_branch,
                base=base_branch
            )
            
            # Add labels
            if labels:
                try:
                    pr.add_to_labels(*labels)
                except Exception as e:
                    logger.warning(f"Failed to add labels: {str(e)}")
            
            pr_url = pr.html_url
            logger.info(f"Created PR: {pr_url}")
            return pr_url
            
        except GithubException as e:
            logger.error(f"Failed to create PR: {e.status} - {e.data}")
            return None
        except Exception as e:
            logger.error(f"Failed to create PR: {str(e)}")
            return None
    
    def create_fix_pr(
        self,
        incident_id: int,
        branch_name: str,
        file_path: str,
        file_content: str,
        commit_message: str,
        pr_title: str,
        pr_body: str,
        base_branch: Optional[str] = None,
        repo_full_name: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a complete fix PR workflow (branch + commit + PR).
        
        Args:
            incident_id: Incident ID
            branch_name: Name for the fix branch
            file_path: Path to the file to fix
            file_content: Fixed file content
            commit_message: Commit message
            pr_title: PR title
            pr_body: PR description
            base_branch: Base branch (defaults to default branch)
            repo_full_name: Repository name (defaults to configured repo)
            
        Returns:
            Dict with pr_url and pr_number, or None on failure
        """
        if not self.client:
            logger.error("GitHub client not initialized")
            return None
        
        try:
            # Use default repo if not specified
            if not repo_full_name:
                if not settings.github_org or not settings.github_default_repo:
                    logger.error("GitHub organization or repository not configured. Set GITHUB_ORG and GITHUB_DEFAULT_REPO in .env")
                    return None
                repo_full_name = f"{settings.github_org}/{settings.github_default_repo}"
            
            logger.info(f"Attempting to access repository: {repo_full_name}")
            
            try:
                repo = self.client.get_repo(repo_full_name)
            except GithubException as e:
                if e.status == 404:
                    logger.error(f"Repository not found: {repo_full_name}. Check GITHUB_ORG and GITHUB_REPO settings.")
                else:
                    logger.error(f"Failed to access repository {repo_full_name}: {e.status} - {e.data}")
                return None
            base_branch = base_branch or repo.default_branch
            
            # Step 1: Create branch
            logger.info(f"Creating branch {branch_name}")
            if not self.create_branch(repo_full_name, branch_name, base_branch):
                logger.error("Failed to create branch")
                return None
            
            # Step 2: Commit fix to branch
            logger.info(f"Committing fix to {file_path}")
            commit_info = self.update_file(
                repo_full_name=repo_full_name,
                file_path=file_path,
                content=file_content,
                commit_message=commit_message,
                branch=branch_name
            )
            if not commit_info:
                logger.error("Failed to commit fix")
                return None
            
            # Step 3: Create PR
            logger.info(f"Creating PR from {branch_name} to {base_branch}")
            pr_url = self.create_pull_request(
                repo_full_name=repo_full_name,
                title=pr_title,
                body=pr_body,
                head_branch=branch_name,
                base_branch=base_branch,
                labels=["auto-fix", "incident"]
            )
            
            if not pr_url:
                logger.error("Failed to create PR")
                return None
            
            # Extract PR number from URL
            pr_number = int(pr_url.split('/')[-1])
            
            return {
                "pr_url": pr_url,
                "pr_number": pr_number,
                "branch_name": branch_name,
                "commit_sha": commit_info.get("commit_sha"),
                "commit_url": commit_info.get("commit_url"),
            }
            
        except Exception as e:
            logger.error(f"Failed to create fix PR: {str(e)}")
            return None
