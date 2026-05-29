"""PDF report generator for incident analysis."""
import logging
from typing import Optional, Dict, Any
from datetime import datetime
from pathlib import Path
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    PageBreak,
    Image as RLImage,
    KeepTogether,
    XPreformatted,
)
from reportlab.lib.colors import HexColor
from config.settings import get_settings

import re

logger = logging.getLogger(__name__)
settings = get_settings()


def _escape_html(text: str) -> str:
    """Escape HTML special characters for safe PDF rendering."""
    if not text:
        return ""
    # Replace HTML special characters in correct order (ampersand first!)
    text = str(text)
    text = text.replace('&', '&' + 'amp;')
    text = text.replace('<', '&' + 'lt;')
    text = text.replace('>', '&' + 'gt;')
    text = text.replace('"', '&' + 'quot;')
    text = text.replace("'", '&' + '#39;')
    return text


class IncidentPDFGenerator:
    """
    Generate professional PDF reports for incidents.
    
    Features:
    - Executive summary
    - Detailed error analysis
    - Root cause analysis with confidence scores
    - Code fix with quality metrics
    - Visual severity indicators
    - Actionable recommendations
    """
    
    def __init__(self):
        """Initialize PDF generator."""
        self.styles = getSampleStyleSheet()
        self._setup_custom_styles()
    
    def _setup_custom_styles(self):
        """Set up custom paragraph styles."""
        # Title style
        self.styles.add(ParagraphStyle(
            name='CustomTitle',
            parent=self.styles['Heading1'],
            fontSize=24,
            textColor=HexColor('#1a1a1a'),
            spaceAfter=30,
            alignment=TA_CENTER,
            fontName='Helvetica-Bold'
        ))
        
        # Section heading
        self.styles.add(ParagraphStyle(
            name='SectionHeading',
            parent=self.styles['Heading2'],
            fontSize=16,
            leading=20,
            textColor=HexColor('#2c3e50'),
            spaceAfter=12,
            spaceBefore=20,
            fontName='Helvetica-Bold',
            borderColor=HexColor('#3498db'),
            borderWidth=0,
            borderPadding=5,
            keepWithNext=1
        ))
        
        # Code style (using custom name to avoid conflict)
        self.styles.add(ParagraphStyle(
            name='CustomCode',
            parent=self.styles['Code'],
            fontSize=9,
            fontName='Courier',
            textColor=HexColor('#2c3e50'),
            backColor=HexColor('#f8f9fa'),
            borderColor=HexColor('#dee2e6'),
            borderWidth=1,
            borderPadding=10,
            leftIndent=10,
            rightIndent=10,
            spaceAfter=10
        ))
        
        # Field label (e.g., "Explanation:", "Stack Trace:", etc.)
        self.styles.add(ParagraphStyle(
            name='FieldLabel',
            parent=self.styles['BodyText'],
            fontSize=11,
            leading=14,
            fontName='Helvetica-Bold',
            textColor=HexColor('#1a1a1a'),
            spaceBefore=6,
            spaceAfter=4,
            keepWithNext=1
        ))

        # Info box
        self.styles.add(ParagraphStyle(
            name='InfoBox',
            parent=self.styles['BodyText'],
            fontSize=10,
            leading=14,
            alignment=TA_LEFT,
            textColor=HexColor('#0c5460'),
            backColor=HexColor('#d1ecf1'),
            borderColor=HexColor('#bee5eb'),
            borderWidth=1,
            borderPadding=10,
            leftIndent=0,
            rightIndent=0,
            spaceBefore=2,
            spaceAfter=8
        ))
    
    def generate_incident_report(
        self,
        incident_id: int,
        app_name: str,
        environment: str,
        error_title: str,
        error_description: Optional[str] = None,
        severity: str = "MEDIUM",
        stack_trace: Optional[str] = None,
        rca_text: Optional[str] = None,
        rca_confidence: Optional[float] = None,
        proposed_fix: Optional[str] = None,
        fix_explanation: Optional[str] = None,
        fix_quality_score: Optional[float] = None,
        pr_url: Optional[str] = None,
        jira_ticket_url: Optional[str] = None,
        created_at: Optional[datetime] = None,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Generate comprehensive PDF report for an incident.
        
        Args:
            incident_id: Incident ID
            app_name: Application name
            environment: Environment
            error_title: Error title
            error_description: Detailed error description
            severity: Severity level
            stack_trace: Stack trace
            rca_text: Root cause analysis
            rca_confidence: RCA confidence score (0-1)
            proposed_fix: Proposed code fix
            fix_explanation: Fix explanation
            fix_quality_score: Fix quality score (0-1)
            pr_url: GitHub PR URL
            jira_ticket_url: Jira ticket URL
            created_at: Incident creation time
            additional_data: Additional metadata
            
        Returns:
            Path to generated PDF file
        """
        try:
            # Ensure output directory exists
            output_dir = Path(settings.pdf_output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            
            # Generate filename
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"incident_{incident_id}_{timestamp}.pdf"
            filepath = output_dir / filename
            
            # Create PDF document
            doc = SimpleDocTemplate(
                str(filepath),
                pagesize=letter,
                rightMargin=72,
                leftMargin=72,
                topMargin=72,
                bottomMargin=18,
            )
            
            # Build content
            story = []
            
            # Title page
            story.extend(self._build_title_page(
                incident_id, app_name, environment, severity, created_at
            ))
            
            # Executive summary
            story.extend(self._build_executive_summary(
                error_title, severity, rca_confidence, fix_quality_score
            ))
            
            # Error details
            story.extend(self._build_error_section(
                error_title, error_description, stack_trace
            ))
            
            # Root cause analysis
            if rca_text:
                story.extend(self._build_rca_section(
                    rca_text, rca_confidence
                ))
            
            # Proposed fix
            if proposed_fix:
                story.extend(self._build_fix_section(
                    proposed_fix, fix_explanation, fix_quality_score
                ))
            
            # Actions and links
            story.extend(self._build_actions_section(
                pr_url, jira_ticket_url
            ))
            
            # Build PDF
            doc.build(story)
            
            logger.info(f"Generated PDF report: {filepath}")
            return str(filepath)
            
        except Exception as e:
            logger.error(f"Failed to generate PDF report: {str(e)}")
            return None
    
    def _build_title_page(
        self,
        incident_id: int,
        app_name: str,
        environment: str,
        severity: str,
        created_at: Optional[datetime]
    ) -> list:
        """Build title page content."""
        content = []
        
        # Title
        content.append(Paragraph(
            "Incident Analysis Report",
            self.styles['CustomTitle']
        ))
        content.append(Spacer(1, 0.3 * inch))
        
        # Incident metadata table
        severity_colors = {
            "CRITICAL": HexColor('#dc3545'),
            "HIGH": HexColor('#fd7e14'),
            "MEDIUM": HexColor('#ffc107'),
            "LOW": HexColor('#28a745')
        }
        
        data = [
            ["Incident ID", f"#{incident_id}"],
            ["Application", app_name],
            ["Environment", environment],
            ["Severity", severity],
            ["Generated", datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")],
        ]
        
        if created_at:
            data.append(["Occurred", created_at.strftime("%Y-%m-%d %H:%M:%S UTC")])
        
        table = Table(data, colWidths=[2*inch, 4*inch])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (0, -1), HexColor('#f8f9fa')),
            ('BACKGROUND', (0, 3), (0, 3), severity_colors.get(severity, HexColor('#ffc107'))),
            ('TEXTCOLOR', (0, 3), (0, 3), colors.white),
            ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, -1), 11),
            ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('GRID', (0, 0), (-1, -1), 1, HexColor('#dee2e6')),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        
        content.append(table)
        content.append(Spacer(1, 0.5 * inch))
        
        return content
    
    def _build_executive_summary(
        self,
        error_title: str,
        severity: str,
        rca_confidence: Optional[float],
        fix_quality_score: Optional[float]
    ) -> list:
        """Build executive summary section."""
        content = []
        
        content.append(Paragraph("Executive Summary", self.styles['SectionHeading']))
        
        summary_text = f"<b>Error:</b> {error_title}<br/><br/>"
        summary_text += f"<b>Severity Level:</b> {severity}<br/><br/>"
        
        if rca_confidence is not None:
            summary_text += f"<b>Root Cause Confidence:</b> {rca_confidence:.1%}<br/><br/>"
        
        if fix_quality_score is not None:
            summary_text += f"<b>Fix Quality Score:</b> {fix_quality_score:.1%}<br/><br/>"
        
        summary_text += "This report contains automated analysis generated by Prism AI. "
        summary_text += "All findings should be reviewed and validated by the development team."
        
        content.append(Paragraph(summary_text, self.styles['BodyText']))
        content.append(Spacer(1, 0.3 * inch))
        
        return content
    
    def _build_error_section(
        self,
        error_title: str,
        error_description: Optional[str],
        stack_trace: Optional[str]
    ) -> list:
        """Build error details section."""
        content = []
        
        content.append(Paragraph("Error Details", self.styles['SectionHeading']))
        
        # Error title
        content.append(Paragraph(f"<b>Title:</b> {error_title}", self.styles['BodyText']))
        content.append(Spacer(1, 0.1 * inch))
        
        # Error description
        if error_description:
            content.append(Paragraph(f"<b>Description:</b>", self.styles['BodyText']))
            escaped_desc = _escape_html(error_description)
            content.append(Paragraph(escaped_desc, self.styles['BodyText']))
            content.append(Spacer(1, 0.1 * inch))
        
        # Stack trace
        if stack_trace:
            content.append(Paragraph("<b>Stack Trace:</b>", self.styles['BodyText']))
            
            # Truncate stack trace if too long
            truncated_trace = stack_trace[:3000] if len(stack_trace) > 3000 else stack_trace
            if len(stack_trace) > 3000:
                truncated_trace += "\n\n... (truncated for readability)"
            
            # Format for PDF - escape HTML and replace newlines
            escaped_trace = _escape_html(truncated_trace)
            formatted_trace = escaped_trace.replace('\n', '<br/>')
            content.append(Paragraph(formatted_trace, self.styles['CustomCode']))
        
        content.append(Spacer(1, 0.3 * inch))
        
        return content
    
    def _build_rca_section(
        self,
        rca_text: str,
        rca_confidence: Optional[float]
    ) -> list:
        """Build root cause analysis section."""
        content = []

        title = "Root Cause Analysis"
        if rca_confidence is not None:
            title += f" (Confidence: {rca_confidence:.1%})"

        content.append(Paragraph(title, self.styles["SectionHeading"]))

        # Improve readability:
        # - Preserve line breaks
        # - Convert common markdown/wikilike bullets into real bullets
        # - Render inline code using monospace
        # - Add spacing between logical paragraphs
        formatted = (rca_text or "").strip()

        # Normalize line endings
        formatted = formatted.replace("\r\n", "\n").replace("\r", "\n")

        # Basic markdown-ish transformations
        # 1) Inline code: `code` -> <font face="Courier">code</font>
        # (escape first, then re-introduce formatting safely)
        escaped = _escape_html(formatted)

        # Recreate inline-code blocks after escaping (handles backticks)
        # We do this on the original string but build output in a safe way.
        # Simple approach: split by backtick and alternate formatting.
        parts = formatted.split("`")
        rebuilt = []
        for i, part in enumerate(parts):
            if i % 2 == 0:
                rebuilt.append(_escape_html(part))
            else:
                rebuilt.append(f"<font face='Courier'>{_escape_html(part)}</font>")
        escaped = "".join(rebuilt)

        # 2) Bullets: lines starting with "- " or "* " -> bullet character
        lines = escaped.split("\n")
        out_lines = []
        for line in lines:
            stripped = line.lstrip()
            if stripped.startswith("- "):
                out_lines.append(f"• {stripped[2:]}")
            elif stripped.startswith("* "):
                out_lines.append(f"• {stripped[2:]}")
            else:
                out_lines.append(line)

        escaped = "\n".join(out_lines)

        # 3) Add extra spacing between paragraphs (blank lines become spacer breaks)
        # Use <br/> for new lines and <br/><br/> for blank lines
        # Replace double newlines first to keep separation
        escaped = escaped.replace("\n\n", "<br/><br/>")
        escaped = escaped.replace("\n", "<br/>")

        # 4) If the RCA is extremely long, render in a shaded box to improve scanability
        # (reuse InfoBox style)
        content.append(Paragraph(escaped, self.styles["InfoBox"]))
        content.append(Spacer(1, 0.3 * inch))

        return content
    
    def _build_fix_section(
        self,
        proposed_fix: str,
        fix_explanation: Optional[str],
        fix_quality_score: Optional[float]
    ) -> list:
        """Build proposed fix section."""
        content = []

        title = "Proposed Fix"
        if fix_quality_score is not None:
            title += f" (Quality Score: {fix_quality_score:.1%})"

        content.append(Paragraph(title, self.styles["SectionHeading"]))

        # Fix explanation (improve readability similar to RCA)
        if fix_explanation:
            # Use a dedicated label style with keepWithNext so the label doesn't get separated/overlapped
            content.append(KeepTogether([
                Paragraph("Explanation:", self.styles["FieldLabel"]),
                Spacer(1, 0.12 * inch)
            ]))

            formatted = (fix_explanation or "").strip()
            formatted = formatted.replace("\r\n", "\n").replace("\r", "\n")

            # Inline code formatting using backticks
            parts = formatted.split("`")
            rebuilt = []
            for i, part in enumerate(parts):
                if i % 2 == 0:
                    rebuilt.append(_escape_html(part))
                else:
                    rebuilt.append(f"<font face='Courier'>{_escape_html(part)}</font>")
            escaped = "".join(rebuilt)

            # Bullets (work off the *raw* newline separators, not HTML)
            lines = escaped.split("\n")
            out_lines = []
            for line in lines:
                stripped = line.lstrip()
                if stripped.startswith("- "):
                    out_lines.append(f"• {stripped[2:]}")
                elif stripped.startswith("* "):
                    out_lines.append(f"• {stripped[2:]}")
                else:
                    out_lines.append(line)
            escaped = "\n".join(out_lines)

            # Paragraph spacing and line breaks
            # Important: ReportLab Paragraph supports <br/> but not literal "\n" rendering.
            # Convert any visible escaped newline sequences too (e.g., "\\n" coming from some sources).
            escaped = escaped.replace("\\n\\n", "<br/><br/>")
            escaped = escaped.replace("\\n", "<br/>")
            escaped = escaped.replace("\n\n", "<br/><br/>")
            escaped = escaped.replace("\n", "<br/>")

            content.append(KeepTogether([
                Paragraph(escaped, self.styles["InfoBox"]),
                Spacer(1, 0.15 * inch),
            ]))

        # Code changes as a diff (no label - goes directly to content)
        diff_text = self._format_fix_as_diff(proposed_fix)

        # Truncate if too long (diffs can be verbose)
        truncated_diff = diff_text[:5000] if len(diff_text) > 5000 else diff_text
        if len(diff_text) > 5000:
            truncated_diff += "\n\n... (truncated for readability - see patch file for complete changes)"

        # Use XPreformatted for proper whitespace preservation.
        #
        # Important: XPreformatted still interprets a subset of ReportLab's inline markup.
        # The proposed_fix currently contains markdown ("**...**") and fenced code blocks
        # which can affect text metrics / wrapping and visually look "misaligned".
        #
        # To ensure consistent alignment we:
        # 1) strip markdown bold markers and code-fence lines from the rendered diff
        # 2) DO NOT use <font> markup (color/bold) inside XPreformatted; pure text only
        from reportlab.lib.styles import ParagraphStyle as PS

        xpre_style = PS(
            name="DiffStyle",
            fontName="Courier",
            fontSize=8.5,
            textColor=HexColor("#2c3e50"),
            backColor=HexColor("#f8f9fa"),
            borderColor=HexColor("#dee2e6"),
            borderWidth=1,
            borderPadding=10,
            leftIndent=0,
            rightIndent=0,
            leading=10,
            spaceAfter=10,
        )

        cleaned_lines = []
        for raw_line in truncated_diff.split("\n"):
            line = raw_line.rstrip("\r")

            # Drop markdown code fence lines entirely
            if line.strip().startswith("```"):
                continue

            # Remove markdown bold markers while keeping the text
            line = line.replace("**", "")

            # Expand tabs to spaces to avoid tab-width differences in PDF rendering
            line = line.expandtabs(4)

            cleaned_lines.append(line)

        formatted_diff = "\n".join(cleaned_lines)
        content.append(XPreformatted(_escape_html(formatted_diff), xpre_style))

        content.append(Spacer(1, 0.3 * inch))

        return content
    
    def _format_fix_as_diff(self, proposed_fix: str) -> str:
        """
        Convert proposed_fix into something that looks like a unified diff for the PDF.

        Inputs we can see in this project:
        - patch_generator produces a real unified diff and stores it in state['patch_path']
        - fix_generator stores a "targeted fix" response with two code blocks:
          an "original/context" block and a "fixed" block.

        The PDF node currently passes only `proposed_fix`, so we:
        1) Prefer: if proposed_fix already includes diff markers (---/+++/@@), return as-is.
        2) Else: extract code blocks and create a lightweight pseudo-diff:
           - unchanged context lines prefixed with space
           - removed lines prefixed with '-'
           - added lines prefixed with '+'
        """
        text = (proposed_fix or "").strip()
        if not text:
            return ""

        # If it's already a diff, don't transform.
        if (
            "\n---" in text
            or "\n+++" in text
            or text.startswith("---")
            or text.startswith("+++")
            or "\n@@" in text
            or text.startswith("@@")
        ):
            return text

        # Extract fenced code blocks (```lang ... ```)
        code_blocks = re.findall(r"```(?:\w+)?\n(.*?)\n```", text, flags=re.DOTALL)
        if not code_blocks:
            # Fallback: show whatever we have; users at least see the changes.
            return text

        # If there are 2+ blocks, treat first as "before" and last as "after".
        before = code_blocks[0]
        after = code_blocks[-1]

        before_lines = before.splitlines()
        after_lines = after.splitlines()

        import difflib

        # Prefer a unified diff (more familiar and includes line numbers/hunks).
        uni = list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile="before",
                tofile="after",
                lineterm="",
                n=3,
            )
        )
        if uni:
            return "\n".join(uni)

        # Fallback: ndiff if unified diff returns empty for some reason
        diff_lines = []
        diff_lines.append("--- before")
        diff_lines.append("+++ after")

        for line in difflib.ndiff(before_lines, after_lines):
            if line.startswith("? "):
                continue
            if line.startswith("  "):
                diff_lines.append(" " + line[2:])
            elif line.startswith("- "):
                diff_lines.append("-" + line[2:])
            elif line.startswith("+ "):
                diff_lines.append("+" + line[2:])

        return "\n".join(diff_lines)

    def _build_actions_section(
        self,
        pr_url: Optional[str],
        jira_ticket_url: Optional[str]
    ) -> list:
        """Build actions and next steps section."""
        content = []
        
        content.append(Paragraph("Actions & Links", self.styles['SectionHeading']))
        
        actions_text = ""
        
        if pr_url:
            actions_text += f"<b>Pull Request:</b> <link href='{pr_url}' color='blue'>{pr_url}</link><br/><br/>"
        
        if jira_ticket_url:
            actions_text += f"<b>Jira Ticket:</b> <link href='{jira_ticket_url}' color='blue'>{jira_ticket_url}</link><br/><br/>"
        
        if not actions_text:
            actions_text = "No actions have been taken yet. Review the analysis above and take appropriate action.<br/><br/>"
        
        actions_text += "<b>Recommended Next Steps:</b><br/>"
        actions_text += "1. Review the root cause analysis and validate findings<br/>"
        actions_text += "2. Examine the proposed fix for correctness and completeness<br/>"
        actions_text += "3. Test the fix in a development or staging environment<br/>"
        actions_text += "4. If approved, merge the pull request<br/>"
        actions_text += "5. Monitor the application after deployment<br/>"
        
        content.append(Paragraph(actions_text, self.styles['BodyText']))
        content.append(Spacer(1, 0.3 * inch))
        
        # Footer
        footer_text = "<i>Generated by Prism AI - Automated Incident Analysis & Resolution</i>"
        content.append(Paragraph(footer_text, self.styles['BodyText']))
        
        return content
