"""Notification client — credentials loaded exclusively from project DB config."""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional, Union

import requests

from utils.retry_handler import retry_with_backoff

logger = logging.getLogger(__name__)


class NotificationClient:
    """
    Multi-channel notification client supporting Slack, Microsoft Teams, and Email.

    All credentials / webhook URLs are loaded from the per-project DB config.
    No fallback to environment variables or settings.py.
    """

    def __init__(self, project_id: Optional[str] = None):
        """
        Initialise the notification client for a project.

        Loads Slack, Teams, and email config from the project's DB config.
        Channels that are not configured are silently skipped when sending.
        """
        self.project_id = project_id
        project_cfg = self._load_project_notification_config(project_id)

        self.slack_webhook = (project_cfg.get("slack_webhook_url") or "").strip()
        self.slack_channel = (project_cfg.get("slack_channel") or "").strip()
        self.teams_webhook = (project_cfg.get("teams_webhook_url") or "").strip()

        email_cfg = project_cfg.get("email") or {}
        self.email_enabled = bool(email_cfg.get("enabled", False))
        self.email_config = {
            "smtp_host": email_cfg.get("smtp_host", ""),
            "smtp_port": int(email_cfg.get("smtp_port", 587)),
            "from_email": email_cfg.get("from_email", ""),
            "to_email": email_cfg.get("to_email", ""),
            "password": email_cfg.get("password", ""),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _load_project_notification_config(self, project_id: Optional[str]) -> dict:
        """
        Load notification settings from project DB config.
        Returns a merged dict with keys from both the 'slack' and 'teams' sections.
        """
        if not project_id:
            return {}
        try:
            from storage.auth_store import get_project_config
            config = get_project_config(project_id) or {}
            slack_cfg = config.get("slack") or {}
            teams_cfg = config.get("teams") or {}
            return {
                "slack_webhook_url": slack_cfg.get("webhook_url", ""),
                "slack_channel": slack_cfg.get("channel", ""),
                "teams_webhook_url": teams_cfg.get("webhook_url", ""),
                "email": config.get("email") or {},
            }
        except Exception as exc:
            logger.warning(
                "Failed to load notification config for project %s: %s", project_id, exc
            )
            return {}

    # ── Public send interface ─────────────────────────────────────────────────

    def send_alert(
        self,
        title: str,
        message: str,
        channels: Optional[List[str]] = None,
        severity: str = "HIGH",
        incident_id: Optional[Union[int, str]] = None,
        jira_url: Optional[str] = None,
    ) -> List[str]:
        """
        Send an alert to the specified channels (or all configured channels).

        Returns a list of channel names where the alert was successfully sent.
        Channels that are not configured for the project are silently skipped.
        """
        if channels is None:
            channels = []
            if self.slack_webhook:
                channels.append("slack")
            if self.teams_webhook:
                channels.append("teams")
            if self.email_enabled:
                channels.append("email")

        successful: list[str] = []
        for channel in channels:
            try:
                if channel == "slack" and self.slack_webhook:
                    self._send_slack(title, message, severity, incident_id, jira_url)
                    successful.append("slack")
                    logger.info("Alert sent to Slack (project=%s)", self.project_id)

                elif channel == "teams" and self.teams_webhook:
                    self._send_teams(title, message, severity, incident_id, jira_url)
                    successful.append("teams")
                    logger.info("Alert sent to Teams (project=%s)", self.project_id)

                elif channel == "email" and self.email_enabled:
                    self._send_email(title, message, severity, incident_id, jira_url)
                    successful.append("email")
                    logger.info("Alert sent via Email (project=%s)", self.project_id)

            except Exception as exc:
                logger.error("Failed to send alert to %s: %s", channel, exc)

        return successful

    # ── Slack ─────────────────────────────────────────────────────────────────

    @retry_with_backoff(max_retries=2, base_delay=1.0)
    def _send_slack(
        self,
        title: str,
        message: str,
        severity: str,
        incident_id: Optional[Union[int, str]],
        jira_url: Optional[str],
    ) -> None:
        color_map = {
            "CRITICAL": "#FF0000",
            "HIGH": "#FF6600",
            "MEDIUM": "#FFCC00",
            "LOW": "#36A64F",
        }
        color = color_map.get(severity, "#808080")

        incident_text = (
            f"*Incident ID:*\n{str(incident_id).strip()}"
            if incident_id not in (None, "")
            else "*Incident ID:*\nN/A"
        )

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"🚨 {title}", "emoji": True},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Severity:*\n{severity}"},
                    {"type": "mrkdwn", "text": incident_text},
                ],
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Error Details:*\n{message[:500]}..."},
            },
        ]
        if jira_url:
            blocks.append(
                {"type": "section", "text": {"type": "mrkdwn", "text": f"<{jira_url}|View Jira Ticket>"}}
            )

        payload: dict = {"attachments": [{"color": color, "blocks": blocks}]}
        response = requests.post(self.slack_webhook, json=payload, timeout=10)
        response.raise_for_status()

    # ── Teams ─────────────────────────────────────────────────────────────────

    @retry_with_backoff(max_retries=2, base_delay=1.0)
    def _send_teams(
        self,
        title: str,
        message: str,
        severity: str,
        incident_id: Optional[Union[int, str]],
        jira_url: Optional[str],
    ) -> None:
        color_map = {
            "CRITICAL": "FF0000",
            "HIGH": "FF6600",
            "MEDIUM": "FFCC00",
            "LOW": "36A64F",
        }
        theme_color = color_map.get(severity, "808080")

        id_value = str(incident_id).strip() if incident_id not in (None, "") else "N/A"
        card: dict = {
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
                        {"name": "Incident ID", "value": id_value},
                    ],
                    "text": message[:500] + "...",
                }
            ],
        }
        if jira_url:
            card["potentialAction"] = [
                {
                    "@type": "OpenUri",
                    "name": "View Jira Ticket",
                    "targets": [{"os": "default", "uri": jira_url}],
                }
            ]

        response = requests.post(self.teams_webhook, json=card, timeout=10)
        response.raise_for_status()

    # ── Email ─────────────────────────────────────────────────────────────────

    @retry_with_backoff(max_retries=2, base_delay=1.0)
    def _send_email(
        self,
        title: str,
        message: str,
        severity: str,
        incident_id: Optional[Union[int, str]],
        jira_url: Optional[str],
    ) -> None:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[{severity}] {title}"
        msg["From"] = self.email_config["from_email"]
        msg["To"] = self.email_config["to_email"]

        id_display = str(incident_id).strip() if incident_id not in (None, "") else "N/A"
        color = "red" if severity in ("CRITICAL", "HIGH") else "orange"
        jira_link = f'<p><a href="{jira_url}" style="color:#0052CC;">View Jira Ticket</a></p>' if jira_url else ""

        html_body = f"""
        <html>
          <body style="font-family:Arial,sans-serif;">
            <h2 style="color:#333;">🚨 Error Alert: {title}</h2>
            <table style="border-collapse:collapse;margin:20px 0;">
              <tr>
                <td style="padding:8px;font-weight:bold;">Severity:</td>
                <td style="padding:8px;color:{color};">{severity}</td>
              </tr>
              <tr>
                <td style="padding:8px;font-weight:bold;">Incident ID:</td>
                <td style="padding:8px;">{id_display}</td>
              </tr>
            </table>
            <h3>Error Details:</h3>
            <pre style="background:#f5f5f5;padding:15px;border-radius:5px;overflow-x:auto;">{message}</pre>
            {jira_link}
          </body>
        </html>
        """
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(self.email_config["smtp_host"], self.email_config["smtp_port"]) as server:
            server.starttls()
            server.login(self.email_config["from_email"], self.email_config["password"])
            server.send_message(msg)


# ── Convenience wrappers ──────────────────────────────────────────────────────

def send_slack_alert(
    incident_id: Union[int, str],
    title: str,
    message: str,
    severity: str = "HIGH",
    jira_url: Optional[str] = None,
    app_name: Optional[str] = None,
    environment: Optional[str] = None,
    project_id: Optional[str] = None,
) -> bool:
    """Send a Slack alert for a project. Returns True if delivered."""
    try:
        full_title = f"[{app_name}] {title}" if app_name else title
        if environment:
            full_title = f"{full_title} ({environment})"
        client = NotificationClient(project_id=project_id)
        successful = client.send_alert(
            title=full_title,
            message=message,
            channels=["slack"],
            severity=severity,
            incident_id=incident_id,
            jira_url=jira_url,
        )
        return "slack" in successful
    except Exception as exc:
        logger.error("send_slack_alert failed: %s", exc)
        return False


def send_teams_alert(
    incident_id: Union[int, str],
    title: str,
    message: str,
    severity: str = "HIGH",
    jira_url: Optional[str] = None,
    app_name: Optional[str] = None,
    environment: Optional[str] = None,
    project_id: Optional[str] = None,
) -> bool:
    """Send a Teams alert for a project. Returns True if delivered."""
    try:
        full_title = f"[{app_name}] {title}" if app_name else title
        if environment:
            full_title = f"{full_title} ({environment})"
        client = NotificationClient(project_id=project_id)
        successful = client.send_alert(
            title=full_title,
            message=message,
            channels=["teams"],
            severity=severity,
            incident_id=incident_id,
            jira_url=jira_url,
        )
        return "teams" in successful
    except Exception as exc:
        logger.error("send_teams_alert failed: %s", exc)
        return False
