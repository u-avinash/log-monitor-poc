"""Jira integration client for ticket management."""
import logging
from typing import Optional, Dict, Any, List
from atlassian import Jira
from atlassian.errors import ApiError
from config.settings import get_settings
from utils.retry_handler import retry_with_backoff

logger = logging.getLogger(__name__)
settings = get_settings()


class JiraClient:
    """
    Jira client for incident ticket management.
    
    Features:
    - Ticket creation with rich formatting
    - Linking to GitHub PRs
    - Status updates
    - Comment management
    - Custom field support
    """
    
    def __init__(
        self,
        url: Optional[str] = None,
        username: Optional[str] = None,
        api_token: Optional[str] = None
    ):
        """
        Initialize Jira client.
        
        Args:
            url: Jira instance URL (defaults to settings)
            username: Jira username/email (defaults to settings)
            api_token: Jira API token (defaults to settings)
        """
        self.url = url or settings.jira_url
        self.username = username or settings.jira_email
        self.api_token = api_token or settings.jira_api_token
        
        if not all([self.url, self.username, self.api_token]):
            logger.warning("Jira credentials not fully configured. Set JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN in .env")
            self.client = None
        else:
            try:
                self.client = Jira(
                    url=self.url,
                    username=self.username,
                    password=self.api_token,
                    cloud=True
                )
                logger.info(f"Jira client initialized for {self.url}")
            except Exception as e:
                logger.error(f"Failed to initialize Jira client: {str(e)}")
                self.client = None
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def create_incident_ticket(
        self,
        incident_id: int,
        app_name: str,
        environment: str,
        error_title: str,
        error_description: str,
        severity: str,
        stack_trace: Optional[str] = None,
        rca_text: Optional[str] = None,
        proposed_fix: Optional[str] = None,
        pr_url: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Create a Jira ticket for an incident.
        
        Args:
            incident_id: Incident ID from database
            app_name: Application name
            environment: Environment (production, staging, etc.)
            error_title: Error title/summary
            error_description: Detailed error description
            severity: Severity level (CRITICAL, HIGH, MEDIUM, LOW)
            stack_trace: Stack trace (optional)
            rca_text: Root cause analysis (optional)
            proposed_fix: Proposed fix description (optional)
            pr_url: GitHub PR URL (optional)
            
        Returns:
            Dict with ticket_key, ticket_url, ticket_id
        """
        if not self.client:
            logger.error("Jira client not initialized")
            return None
        
        try:
            # Map severity to Jira priority
            priority_map = {
                "CRITICAL": "Highest",
                "HIGH": "High",
                "MEDIUM": "Medium",
                "LOW": "Low"
            }
            priority = priority_map.get(severity, "Medium")
            
            # Build description with rich formatting
            description = self._build_ticket_description(
                incident_id=incident_id,
                app_name=app_name,
                environment=environment,
                error_description=error_description,
                stack_trace=stack_trace,
                rca_text=rca_text,
                proposed_fix=proposed_fix,
                pr_url=pr_url
            )
            
            # Sanitize error_title and app_name: remove newlines and extra whitespace
            sanitized_title = " ".join(error_title.replace("\r", "").replace("\n", " ").split())
            sanitized_app_name = " ".join(app_name.replace("\r", "").replace("\n", " ").split())
            sanitized_environment = " ".join(environment.replace("\r", "").replace("\n", " ").split())
            sanitized_severity = " ".join(severity.replace("\r", "").replace("\n", " ").split())
            
            # Create ticket
            issue_dict = {
                "project": {"key": settings.jira_project_key},
                "summary": f"[{sanitized_app_name}] {sanitized_title} (Incident #{incident_id})",
                "description": description,
                "issuetype": {"name": "Bug"},
                "priority": {"name": priority},
                "labels": [
                    "auto-generated",
                    "prism",
                    f"incident-{incident_id}",
                    f"app-{sanitized_app_name.lower().replace(' ', '-')}",
                    f"env-{sanitized_environment.lower()}",
                    f"severity-{sanitized_severity.lower()}"
                ]
            }
            
            # Add assignee if configured
            if settings.jira_default_assignee and settings.jira_default_assignee != "unassigned":
                issue_dict["assignee"] = {"name": settings.jira_default_assignee}
            
            # Create issue
            issue = self.client.issue_create(fields=issue_dict)
            
            ticket_key = issue["key"]
            ticket_id = issue["id"]
            ticket_url = f"{self.url}/browse/{ticket_key}"
            
            logger.info(f"Created Jira ticket {ticket_key} for incident {incident_id}")
            
            return {
                "ticket_key": ticket_key,
                "ticket_id": ticket_id,
                "ticket_url": ticket_url
            }
            
        except ApiError as e:
            logger.error(f"Jira API error: {e.status_code} - {e.reason}")
            return None
        except Exception as e:
            logger.error(f"Failed to create Jira ticket: {str(e)}")
            return None
    
    def _build_ticket_description(
        self,
        incident_id: int,
        app_name: str,
        environment: str,
        error_description: str,
        stack_trace: Optional[str] = None,
        rca_text: Optional[str] = None,
        proposed_fix: Optional[str] = None,
        pr_url: Optional[str] = None
    ) -> str:
        """
        Build rich-formatted Jira ticket description.
        
        Uses Jira's Atlassian Document Format (ADF) for rich formatting.
        """
        # Use Jira Wiki markup for older instances or ADF for cloud
        description_parts = [
            f"h2. Incident Summary",
            f"*Incident ID:* {incident_id}",
            f"*Application:* {app_name}",
            f"*Environment:* {environment}",
            f"*Auto-detected by:* Prism AI",
            "",
            f"h2. Error Description",
            error_description,
            ""
        ]
        
        if stack_trace:
            description_parts.extend([
                "h2. Stack Trace",
                "{code:java}",
                stack_trace[:2000],  # Limit length
                "{code}",
                ""
            ])
        
        if rca_text:
            description_parts.extend([
                "h2. Root Cause Analysis",
                rca_text,
                ""
            ])
        
        if proposed_fix:
            description_parts.extend([
                "h2. Proposed Fix",
                proposed_fix[:1000],  # Limit length
                ""
            ])
        
        if pr_url:
            description_parts.extend([
                "h2. Pull Request",
                f"A fix has been automatically generated and submitted:",
                f"[View Pull Request|{pr_url}]",
                ""
            ])
        
        description_parts.extend([
            "----",
            "_This ticket was automatically created by Prism AI. Please review and verify the analysis._"
        ])
        
        return "\n".join(description_parts)
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def add_comment(
        self,
        ticket_key: str,
        comment: str
    ) -> bool:
        """
        Add a comment to a Jira ticket.
        
        Args:
            ticket_key: Jira ticket key (e.g., MULE-123)
            comment: Comment text
            
        Returns:
            True if successful
        """
        if not self.client:
            return False
        
        try:
            self.client.issue_add_comment(ticket_key, comment)
            logger.info(f"Added comment to {ticket_key}")
            return True
        except Exception as e:
            logger.error(f"Failed to add comment to {ticket_key}: {str(e)}")
            return False
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def update_status(
        self,
        ticket_key: str,
        status: str
    ) -> bool:
        """
        Update ticket status.
        
        Args:
            ticket_key: Jira ticket key
            status: New status (e.g., "In Progress", "Done")
            
        Returns:
            True if successful
        """
        if not self.client:
            return False
        
        try:
            # Get available transitions
            transitions = self.client.get_issue_transitions(ticket_key)
            
            # Find matching transition
            transition_id = None
            for transition in transitions:
                if transition["name"].lower() == status.lower():
                    transition_id = transition["id"]
                    break
            
            if not transition_id:
                logger.warning(f"Status '{status}' not found for {ticket_key}")
                return False
            
            # Perform transition
            self.client.issue_transition(ticket_key, transition_id)
            logger.info(f"Updated {ticket_key} status to {status}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to update status for {ticket_key}: {str(e)}")
            return False
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def link_to_pr(
        self,
        ticket_key: str,
        pr_url: str
    ) -> bool:
        """
        Link a Jira ticket to a GitHub PR.
        
        Args:
            ticket_key: Jira ticket key
            pr_url: GitHub PR URL
            
        Returns:
            True if successful
        """
        if not self.client:
            return False
        
        try:
            comment = f"h3. Pull Request Created\n\nA fix has been automatically generated:\n[View Pull Request|{pr_url}]\n\n_Please review the changes before merging._"
            
            self.client.issue_add_comment(ticket_key, comment)
            logger.info(f"Linked PR to {ticket_key}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to link PR to {ticket_key}: {str(e)}")
            return False
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def add_rca_update(
        self,
        ticket_key: str,
        rca_text: str,
        confidence: Optional[float] = None
    ) -> bool:
        """
        Add RCA findings as a comment.
        
        Args:
            ticket_key: Jira ticket key
            rca_text: Root cause analysis text
            confidence: RCA confidence score (0-1)
            
        Returns:
            True if successful
        """
        if not self.client:
            return False
        
        try:
            confidence_str = f" (Confidence: {confidence:.1%})" if confidence else ""
            comment = f"h3. Root Cause Analysis{confidence_str}\n\n{rca_text}"
            
            self.client.issue_add_comment(ticket_key, comment)
            logger.info(f"Added RCA to {ticket_key}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to add RCA to {ticket_key}: {str(e)}")
            return False
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    def get_ticket(
        self,
        ticket_key: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get ticket details.
        
        Args:
            ticket_key: Jira ticket key
            
        Returns:
            Ticket details dict
        """
        if not self.client:
            return None
        
        try:
            issue = self.client.issue(ticket_key)
            
            return {
                "key": issue["key"],
                "id": issue["id"],
                "summary": issue["fields"]["summary"],
                "status": issue["fields"]["status"]["name"],
                "priority": issue["fields"]["priority"]["name"],
                "assignee": issue["fields"].get("assignee", {}).get("displayName"),
                "created": issue["fields"]["created"],
                "updated": issue["fields"]["updated"],
                "url": f"{self.url}/browse/{issue['key']}"
            }
            
        except Exception as e:
            logger.error(f"Failed to get ticket {ticket_key}: {str(e)}")
            return None
    
    def search_incidents(
        self,
        app_name: Optional[str] = None,
        environment: Optional[str] = None,
        max_results: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search for incident tickets.
        
        Args:
            app_name: Filter by application name
            environment: Filter by environment
            max_results: Maximum results to return
            
        Returns:
            List of ticket summaries
        """
        if not self.client:
            return []
        
        try:
            # Build JQL query
            jql_parts = [
                f"project = {settings.jira_project_key}",
                'labels = "prism"'
            ]
            
            if app_name:
                jql_parts.append(f'labels = "app-{app_name.lower().replace(" ", "-")}"')
            
            if environment:
                jql_parts.append(f'labels = "env-{environment.lower()}"')
            
            jql = " AND ".join(jql_parts)
            jql += " ORDER BY created DESC"
            
            # Execute search
            issues = self.client.jql(jql, limit=max_results)
            
            results = []
            for issue in issues.get("issues", []):
                results.append({
                    "key": issue["key"],
                    "summary": issue["fields"]["summary"],
                    "status": issue["fields"]["status"]["name"],
                    "created": issue["fields"]["created"],
                    "url": f"{self.url}/browse/{issue['key']}"
                })
            
            logger.info(f"Found {len(results)} tickets matching criteria")
            return results
            
        except Exception as e:
            logger.error(f"Failed to search tickets: {str(e)}")
            return []
