"""Notification client for Slack, Teams, and Email alerts."""
import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
from config.settings import get_settings
from utils.retry_handler import retry_with_backoff
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


class NotificationClient:
    """
    Multi-channel notification client.
    Supports: Slack, Microsoft Teams, Email.
    """
    
    def __init__(self):
        """Initialize notification client."""
        self.slack_webhook = settings.slack_webhook_url
        self.teams_webhook = settings.teams_webhook_url
        self.email_enabled = settings.email_enabled
        self.email_config = {
            "smtp_host": settings.email_smtp_host,
            "smtp_port": settings.email_smtp_port,
            "from_email": settings.email_from,
            "to_email": settings.email_to,
            "password": settings.email_password
        }
    
    def send_alert(
        self,
        title: str,
        message: str,
        channels: List[str] = None,
        severity: str = "HIGH",
        incident_id: Optional[int] = None,
        jira_url: Optional[str] = None
    ) -> List[str]:
        """
        Send alert to specified channels.
        
        Args:
            title: Alert title
            message: Alert message
            channels: List of channels (slack, teams, email). If None, sends to all configured
            severity: Severity level
            incident_id: Optional incident ID
            jira_url: Optional Jira ticket URL
            
        Returns:
            List of channels where alert was successfully sent
        """
        if channels is None:
            channels = []
            if self.slack_webhook:
                channels.append("slack")
            if self.teams_webhook:
                channels.append("teams")
            if self.email_enabled:
                channels.append("email")
        
        successful_channels = []
        
        for channel in channels:
            try:
                if channel == "slack" and self.slack_webhook:
                    self._send_slack(title, message, severity, incident_id, jira_url)
                    successful_channels.append("slack")
                    logger.info("Alert sent to Slack")
                
                elif channel == "teams" and self.teams_webhook:
                    self._send_teams(title, message, severity, incident_id, jira_url)
                    successful_channels.append("teams")
                    logger.info("Alert sent to Teams")
                
                elif channel == "email" and self.email_enabled:
                    self._send_email(title, message, severity, incident_id, jira_url)
                    successful_channels.append("email")
                    logger.info("Alert sent via Email")
            
            except Exception as e:
                logger.error(f"Failed to send alert to {channel}: {e}")
        
        return successful_channels
    
    @retry_with_backoff(max_retries=2, base_delay=1.0)
    def _send_slack(
        self,
        title: str,
        message: str,
        severity: str,
        incident_id: Optional[int],
        jira_url: Optional[str]
    ):
        """Send notification to Slack via webhook."""
        # Color based on severity
        color_map = {
            "CRITICAL": "#FF0000",
            "HIGH": "#FF6600",
            "MEDIUM": "#FFCC00",
            "LOW": "#36A64F"
        }
        color = color_map.get(severity, "#808080")
        
        # Build Slack blocks
        blocks = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"🚨 {title}",
                    "emoji": True
                }
            },
            {
                "type": "section",
                "fields": [
                    {
                        "type": "mrkdwn",
                        "text": f"*Severity:*\n{severity}"
                    },
                    {
                        "type": "mrkdwn",
                        "text": f"*Incident ID:*\n#{incident_id}" if incident_id else "*Incident ID:*\nN/A"
                    }
                ]
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Error Details:*\n{message[:500]}..."
                }
            }
        ]
        
        # Add Jira link if available
        if jira_url:
            blocks.append({
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"<{jira_url}|View Jira Ticket>"
                }
            })
        
        payload = {
            "attachments": [{
                "color": color,
                "blocks": blocks
            }]
        }
        
        response = requests.post(self.slack_webhook, json=payload, timeout=10)
        response.raise_for_status()
    
    @retry_with_backoff(max_retries=2, base_delay=1.0)
    def _send_teams(
        self,
        title: str,
        message: str,
        severity: str,
        incident_id: Optional[int],
        jira_url: Optional[str]
    ):
        """Send notification to Microsoft Teams via webhook."""
        # Color based on severity
        color_map = {
            "CRITICAL": "FF0000",
            "HIGH": "FF6600",
            "MEDIUM": "FFCC00",
            "LOW": "36A64F"
        }
        theme_color = color_map.get(severity, "808080")
        
        # Build Teams message card
        card = {
            "@type": "MessageCard",
            "@context": "https://schema.org/extensions",
            "summary": title,
            "themeColor": theme_color,
            "title": f"🚨 {title}",
            "sections": [
                {
                    "activityTitle": "Error Alert",
                    "facts": [
                        {"name": "Severity", "value": severity},
                        {"name": "Incident ID", "value": f"#{incident_id}" if incident_id else "N/A"}
                    ],
                    "text": message[:500] + "..."
                }
            ]
        }
        
        # Add Jira link if available
        if jira_url:
            card["potentialAction"] = [{
                "@type": "OpenUri",
                "name": "View Jira Ticket",
                "targets": [{"os": "default", "uri": jira_url}]
            }]
        
        response = requests.post(self.teams_webhook, json=card, timeout=10)
        response.raise_for_status()
    
    @retry_with_backoff(max_retries=2, base_delay=1.0)
    def _send_email(
        self,
        title: str,
        message: str,
        severity: str,
        incident_id: Optional[int],
        jira_url: Optional[str]
    ):
        """Send email notification via SMTP."""
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[{severity}] {title}"
        msg["From"] = self.email_config["from_email"]
        msg["To"] = self.email_config["to_email"]
        
        # Create HTML email body
        html_body = f"""
        <html>
            <body style="font-family: Arial, sans-serif;">
                <h2 style="color: #333;">🚨 Error Alert: {title}</h2>
                <table style="border-collapse: collapse; margin: 20px 0;">
                    <tr>
                        <td style="padding: 8px; font-weight: bold;">Severity:</td>
                        <td style="padding: 8px; color: {'red' if severity in ['CRITICAL', 'HIGH'] else 'orange'};">{severity}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; font-weight: bold;">Incident ID:</td>
                        <td style="padding: 8px;">#{incident_id if incident_id else 'N/A'}</td>
                    </tr>
                </table>
                <h3>Error Details:</h3>
                <pre style="background-color: #f5f5f5; padding: 15px; border-radius: 5px; overflow-x: auto;">{message}</pre>
        """
        
        if jira_url:
            html_body += f'<p><a href="{jira_url}" style="color: #0052CC;">View Jira Ticket</a></p>'
        
        html_body += """
            </body>
        </html>
        """
        
        msg.attach(MIMEText(html_body, "html"))
        
        # Send email
        with smtplib.SMTP(self.email_config["smtp_host"], self.email_config["smtp_port"]) as server:
            server.starttls()
            server.login(self.email_config["from_email"], self.email_config["password"])
            server.send_message(msg)


# Backward compatibility wrapper functions for legacy code
def send_slack_alert(incident_id: int, title: str, message: str, severity: str = "HIGH", jira_url: Optional[str] = None, app_name: Optional[str] = None, environment: Optional[str] = None) -> bool:
    """
    Send Slack alert (legacy function for backward compatibility).
    
    Args:
        incident_id: Incident ID
        title: Alert title
        message: Alert message
        severity: Severity level
        jira_url: Optional Jira ticket URL
        app_name: Application name (optional, for compatibility)
        environment: Environment (optional, for compatibility)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Include app_name and environment in title if provided
        full_title = title
        if app_name:
            full_title = f"[{app_name}] {title}"
        if environment:
            full_title = f"{full_title} ({environment})"
        
        client = NotificationClient()
        successful_channels = client.send_alert(
            title=full_title,
            message=message,
            channels=["slack"],
            severity=severity,
            incident_id=incident_id,
            jira_url=jira_url
        )
        return "slack" in successful_channels
    except Exception as e:
        logger.error(f"send_slack_alert failed: {e}")
        return False


def send_teams_alert(incident_id: int, title: str, message: str, severity: str = "HIGH", jira_url: Optional[str] = None, app_name: Optional[str] = None, environment: Optional[str] = None) -> bool:
    """
    Send Teams alert (legacy function for backward compatibility).
    
    Args:
        incident_id: Incident ID
        title: Alert title
        message: Alert message
        severity: Severity level
        jira_url: Optional Jira ticket URL
        app_name: Application name (optional, for compatibility)
        environment: Environment (optional, for compatibility)
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Include app_name and environment in title if provided
        full_title = title
        if app_name:
            full_title = f"[{app_name}] {title}"
        if environment:
            full_title = f"{full_title} ({environment})"
        
        client = NotificationClient()
        successful_channels = client.send_alert(
            title=full_title,
            message=message,
            channels=["teams"],
            severity=severity,
            incident_id=incident_id,
            jira_url=jira_url
        )
        return "teams" in successful_channels
    except Exception as e:
        logger.error(f"send_teams_alert failed: {e}")
        return False
