"""GitHub integration client — credentials loaded exclusively from project DB config."""
import base64
import logging
import re
from typing import Optional, List, Dict, Any

from github import Github, GithubException

from utils.retry_handler import retry_with_backoff

logger = logging.getLogger(__name__)


class GitHubClient:
    """
    GitHub client for repository operations.

    All credentials are loaded from the per-project DB config (stored encrypted).
    Repo mappings are loaded from the project's repo_mappings DB section.
    No fallback to environment variables, settings.py, or YAML files.
    """

    def __init__(self, project_id: Optional[str] = None, token: Optional[str] = None):
        """
        Initialise GitHub client.

        Credentials are resolved in this order:
          1. Explicit token kwarg
          2. Per-project DB config for project_id

        Raises ValueError if the token is missing.
        """
        self.project_id = project_id
        project_cfg = self._load_project_github_config(project_id)

        self.token = (token or project_cfg.get("token") or "").strip()
        self.org = (project_cfg.get("org") or "").strip()
        self.default_branch = (project_cfg.get("default_branch") or "main").strip()

        if not self.token:
            raise ValueError(
                "GitHub token is not configured"
                + (f" for project '{project_id}'" if project_id else "")
                + ". Configure via Team Admin → Project Configuration → GitHub."
            )

        self.client = Github(self.token)
        logger.info("GitHub client initialised (project_id=%s)", project_id)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_project_github_config(self, project_id: Optional[str]) -> dict:
        if not project_id:
            return {}
        try:
            from storage.auth_store import get_project_config
            config = get_project_config(project_id) or {}
            return config.get("github") or {}
        except Exception as exc:
            logger.warning("Failed to load project GitHub config for %s: %s", project_id, exc)
            return {}

    def _get_repo_mappings(self) -> dict:
        """Return the project's app→repo mapping from DB."""
        if not self.project_id:
            return {}
        try:
            from storage.auth_store import get_app_repo_mapping
            return get_app_repo_mapping(self.project_id)
        except Exception as exc:
            logger.warning("Failed to load repo mappings for project %s: %s", self.project_id, exc)
            return {}

    # ── Repo resolution ───────────────────────────────────────────────────────

    def _is_full_repo_name(self, value: Optional[str]) -> bool:
        """Return True only for valid GitHub owner/repo values."""
        repo_value = (value or "").strip().strip("/")
        if not repo_value or "/" not in repo_value:
            return False

        owner, repo = repo_value.split("/", 1)
        if not owner or not repo:
            return False

        owner = owner.strip()
        repo = repo.strip().removesuffix(".git")
        if not owner or not repo:
            return False

        return True

    def _clean_repo_full_name(self, value: Optional[str]) -> str:
        """Normalize repo strings to a canonical owner/repo form."""
        repo_value = (value or "").strip().strip("/")
        if not repo_value:
            return ""

        repo_value = re.sub(r"^(?:https?://)?github\.com/", "", repo_value, flags=re.IGNORECASE)
        repo_value = re.sub(r"^git@github\.com:", "", repo_value, flags=re.IGNORECASE)
        repo_value = repo_value.removesuffix(".git").strip().strip("/")

        return repo_value

    def _derive_repo_from_org_only_mapping(self, repo_value: str, app_name: str) -> str:
        """
        Convert an org-only mapping into a likely owner/repo candidate using app_name.

        Example:
            repo_value='acme', app_name='orders' -> 'acme/orders'
        """
        cleaned_value = self._clean_repo_full_name(repo_value)
        app_name_norm = (app_name or "").strip().strip("/")
        if not cleaned_value or not app_name_norm or "/" in cleaned_value:
            return ""

        if self.org and cleaned_value.lower() != self.org.lower():
            return ""

        app_name_norm = app_name_norm.removesuffix(".git")
        return f"{cleaned_value}/{app_name_norm}" if app_name_norm else ""

    def extract_repo_from_log(self, log_text: str, app_name: str) -> Optional[str]:
        """
        Resolve the GitHub repository full name for an application.

        Resolution order:
          1. Project's DB repo_mappings (exact match, then case-insensitive)
          2. Explicit GitHub URL patterns in the log text
          3. Search by app name in the configured organisation
          4. If nothing found: raise ValueError — no silent fallbacks
        """
        app_name_norm = (app_name or "").strip()

        # ── 1. DB repo mappings (primary source) ──────────────────────────────
        mappings = self._get_repo_mappings()
        if mappings:
            checked_keys: set[str] = set()

            def _resolve_mapping(mapping_key: str, mapping_value: dict, case_insensitive: bool = False) -> Optional[str]:
                repo_full_name = self._clean_repo_full_name(mapping_value.get("repo", ""))
                if self._is_full_repo_name(repo_full_name):
                    if case_insensitive:
                        logger.info(
                            "Repo resolved from DB mapping (case-insensitive): %s → %s",
                            app_name_norm, repo_full_name,
                        )
                    else:
                        logger.info("Repo resolved from DB mapping: %s → %s", app_name_norm, repo_full_name)
                    return repo_full_name

                derived_repo = self._derive_repo_from_org_only_mapping(repo_full_name, app_name_norm)
                if self._is_full_repo_name(derived_repo):
                    logger.info(
                        "Repo derived from org-only DB mapping: %s → %s",
                        app_name_norm, derived_repo,
                    )
                    return derived_repo

                if repo_full_name:
                    if case_insensitive:
                        logger.warning(
                            "Repo mapping for app '%s' (matched by key '%s') is not a full owner/repo value: %s",
                            app_name_norm, mapping_key, repo_full_name,
                        )
                    else:
                        logger.warning(
                            "Repo mapping for app '%s' is not a full owner/repo value: %s",
                            app_name_norm, repo_full_name,
                        )
                return None

            # Exact match
            if app_name_norm in mappings:
                checked_keys.add(app_name_norm.lower())
                resolved_repo = _resolve_mapping(app_name_norm, mappings[app_name_norm])
                if resolved_repo:
                    return resolved_repo

            # Case-insensitive match
            app_lower = app_name_norm.lower()
            if app_lower not in checked_keys:
                for key, val in mappings.items():
                    if (key or "").strip().lower() == app_lower:
                        checked_keys.add(app_lower)
                        resolved_repo = _resolve_mapping(key, val, case_insensitive=True)
                        if resolved_repo:
                            return resolved_repo
                        break

        # ── 2. GitHub URL patterns in log text ────────────────────────────────
        for pattern in (
            r"github\.com[:/]([^/\s]+/[^/\s]+)",
            r"git@github\.com:([^/\s]+/[^/\s]+)",
        ):
            match = re.search(pattern, log_text, re.IGNORECASE)
            if match:
                repo_full_name = self._clean_repo_full_name(match.group(1))
                if self._is_full_repo_name(repo_full_name):
                    logger.info("Repo extracted from log URL pattern: %s", repo_full_name)
                    return repo_full_name

        # ── 3. Search in configured organisation ─────────────────────────────
        if self.org:
            try:
                org = self.client.get_organization(self.org)
                # Exact name match
                try:
                    repo = org.get_repo(app_name_norm)
                    logger.info("Repo found by exact name in org: %s", repo.full_name)
                    return self._clean_repo_full_name(repo.full_name)
                except GithubException:
                    pass
                # Fuzzy match
                for repo in org.get_repos():
                    if app_name_norm.lower() in repo.name.lower():
                        logger.info("Repo found by fuzzy match in org: %s", repo.full_name)
                        return self._clean_repo_full_name(repo.full_name)
            except Exception as exc:
                logger.warning("GitHub org search failed: %s", exc)

        # ── 4. No mapping found ───────────────────────────────────────────────
        raise ValueError(
            f"Cannot resolve GitHub repository for application '{app_name_norm}'. "
            "Add an app→repo mapping via Team Admin → Project Configuration → Repo Mappings."
        )

    # ── File operations ───────────────────────────────────────────────────────

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def get_file_content(
        self,
        repo_full_name: str,
        file_path: str,
        branch: Optional[str] = None,
    ) -> Optional[str]:
        """Fetch file content from repository."""
        try:
            repo = self.client.get_repo(repo_full_name)
            ref = branch or repo.default_branch
            logger.info("Fetching %s from %s@%s", file_path, repo_full_name, ref)
            contents = repo.get_contents(file_path, ref=ref)
            if isinstance(contents, list):
                logger.warning("%s is a directory, not a file", file_path)
                return None
            content = base64.b64decode(contents.content).decode("utf-8")
            logger.info("Fetched %s (%d bytes)", file_path, len(content))
            return content
        except GithubException as exc:
            if exc.status == 404:
                logger.warning("File not found: %s", file_path)
            else:
                logger.error("GitHub API error fetching file: %s - %s", exc.status, exc.data)
            return None
        except Exception as exc:
            logger.error("Failed to fetch file: %s", exc)
            return None

    def find_file_by_stacktrace(
        self,
        repo_full_name: str,
        stack_trace: str,
    ) -> List[Dict[str, Any]]:
        """Extract file paths and line numbers from a stack trace."""
        files: list[dict] = []
        patterns = [
            ("java",   r"at\s+[\w.]+\(([\w.]+\.java):(\d+)\)"),
            ("mule",   r'at\s+(?:flow|processor)\s+"[^"]+"\s+\(([^:]+\.xml):(\d+)\)'),
            ("python", r'File\s+"([^"]+\.py)",\s+line\s+(\d+)'),
        ]
        for lang, pattern in patterns:
            for match in re.finditer(pattern, stack_trace):
                file_path = match.group(1)
                line_number = int(match.group(2))
                if lang == "java" and "/" not in file_path:
                    file_path = f"src/main/java/{file_path}"
                files.append({"file_path": file_path, "line_number": line_number, "language": lang})
        logger.info("Extracted %d file references from stack trace", len(files))
        return files

    def get_code_context(
        self,
        repo_full_name: str,
        file_path: str,
        line_number: int,
        context_lines: int = 10,
    ) -> Optional[Dict[str, Any]]:
        """Get code context around a specific line number."""
        content = self.get_file_content(repo_full_name, file_path)
        if not content:
            return None
        lines = content.split("\n")
        total = len(lines)
        start = max(1, line_number - context_lines)
        end = min(total, line_number + context_lines)
        return {
            "full_content": content,
            "context_snippet": "\n".join(lines[start - 1:end]),
            "start_line": start,
            "end_line": end,
            "target_line": line_number,
            "total_lines": total,
        }

    # ── Branch & commit operations ────────────────────────────────────────────

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def create_branch(
        self,
        repo_full_name: str,
        branch_name: str,
        from_branch: Optional[str] = None,
    ) -> bool:
        try:
            repo = self.client.get_repo(repo_full_name)
            source = from_branch or repo.default_branch
            sha = repo.get_branch(source).commit.sha
            repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=sha)
            logger.info("Created branch %s from %s", branch_name, source)
            return True
        except GithubException as exc:
            if exc.status == 422:
                logger.warning("Branch %s already exists", branch_name)
                return True
            logger.error("Failed to create branch: %s - %s", exc.status, exc.data)
            return False
        except Exception as exc:
            logger.error("Failed to create branch: %s", exc)
            return False

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def update_file(
        self,
        repo_full_name: str,
        file_path: str,
        content: str,
        commit_message: str,
        branch: str,
    ) -> Optional[Dict[str, Any]]:
        """Update or create a file in the repository."""
        try:
            repo = self.client.get_repo(repo_full_name)
            try:
                current = repo.get_contents(file_path, ref=branch)
                sha = current.sha
            except GithubException:
                sha = None

            if sha:
                result = repo.update_file(
                    path=file_path, message=commit_message,
                    content=content, sha=sha, branch=branch,
                )
                logger.info("Updated %s on %s", file_path, branch)
            else:
                result = repo.create_file(
                    path=file_path, message=commit_message,
                    content=content, branch=branch,
                )
                logger.info("Created %s on %s", file_path, branch)

            commit = result.get("commit") if isinstance(result, dict) else None
            return {
                "commit_sha": getattr(commit, "sha", None),
                "commit_url": getattr(commit, "html_url", None),
            }
        except Exception as exc:
            logger.error("Failed to update file: %s", exc)
            return None

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def create_pull_request(
        self,
        repo_full_name: str,
        title: str,
        body: str,
        head_branch: str,
        base_branch: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> Optional[str]:
        """Create a pull request and return its URL."""
        try:
            repo = self.client.get_repo(repo_full_name)
            base = base_branch or repo.default_branch
            pr = repo.create_pull(title=title, body=body, head=head_branch, base=base)
            if labels:
                try:
                    pr.add_to_labels(*labels)
                except Exception as exc:
                    logger.warning("Failed to add PR labels: %s", exc)
            logger.info("Created PR: %s", pr.html_url)
            return pr.html_url
        except GithubException as exc:
            logger.error("Failed to create PR: %s - %s", exc.status, exc.data)
            return None
        except Exception as exc:
            logger.error("Failed to create PR: %s", exc)
            return None

    def create_fix_pr(
        self,
        incident_id: str,
        branch_name: str,
        file_path: str,
        file_content: str,
        commit_message: str,
        pr_title: str,
        pr_body: str,
        base_branch: Optional[str] = None,
        repo_full_name: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        End-to-end fix PR workflow: create branch → commit → open PR.

        repo_full_name must be supplied (resolved upstream via extract_repo_from_log).
        """
        if not repo_full_name:
            raise ValueError(
                "repo_full_name is required for create_fix_pr. "
                "Resolve it first via extract_repo_from_log()."
            )

        try:
            repo = self.client.get_repo(repo_full_name)
        except GithubException as exc:
            if exc.status == 404:
                logger.error("Repository not found: %s", repo_full_name)
            else:
                logger.error("Cannot access repository %s: %s - %s", repo_full_name, exc.status, exc.data)
            return None

        base = base_branch or repo.default_branch

        if not self.create_branch(repo_full_name, branch_name, base):
            logger.error("Failed to create fix branch %s", branch_name)
            return None

        commit_info = self.update_file(
            repo_full_name=repo_full_name,
            file_path=file_path,
            content=file_content,
            commit_message=commit_message,
            branch=branch_name,
        )
        if not commit_info:
            logger.error("Failed to commit fix to %s", file_path)
            return None

        pr_url = self.create_pull_request(
            repo_full_name=repo_full_name,
            title=pr_title,
            body=pr_body,
            head_branch=branch_name,
            base_branch=base,
            labels=["auto-fix", "incident"],
        )
        if not pr_url:
            logger.error("Failed to create pull request")
            return None

        return {
            "pr_url": pr_url,
            "pr_number": int(pr_url.split("/")[-1]),
            "branch_name": branch_name,
            "commit_sha": commit_info.get("commit_sha"),
            "commit_url": commit_info.get("commit_url"),
        }
