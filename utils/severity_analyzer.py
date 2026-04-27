"""Severity analysis and auto-fix decision logic."""
import re
from typing import Tuple
from storage.models import Severity
from config.settings import get_settings
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


class SeverityAnalyzer:
    """
    Analyze error severity and determine if auto-fix should be triggered.
    """
    
    # Keyword patterns for severity classification
    CRITICAL_KEYWORDS = [
        r'outofmemory', r'fatal', r'critical', r'security', r'authentication',
        r'database.*down', r'connection.*refused', r'unable to connect',
        r'service.*unavailable', r'deadlock', r'corruption'
    ]
    
    HIGH_KEYWORDS = [
        r'exception', r'error', r'failed', r'timeout', 
        r'null.*(pointer|object|reference)', r'cannot access property',
        r'undefined.*object', r'nullpointerexception',
        r'resource.*exhausted', r'permission.*denied', r'access.*denied',
        r'invalid.*credentials', r'quota.*exceeded', r'cannot.*null'
    ]
    
    MEDIUM_KEYWORDS = [
        r'warning', r'deprecated', r'retry', r'slow', r'performance',
        r'rate.*limit', r'validation.*failed', r'bad.*request'
    ]
    
    def __init__(self):
        """Initialize severity analyzer."""
        self.auto_fix_enabled = settings.auto_fix_enabled
        self.severity_threshold = Severity[settings.auto_fix_severity_threshold]
        self.burst_window = settings.error_burst_window_minutes
        self.burst_threshold = settings.error_burst_threshold
    
    def analyze_severity(
        self,
        error_title: str,
        error_description: str,
        stack_trace: str,
        app_name: str = None
    ) -> Tuple[Severity, float]:
        """
        Analyze error severity based on keywords and patterns.
        
        Args:
            error_title: Error title/message
            error_description: Error description
            stack_trace: Stack trace
            app_name: Application name (optional)
            
        Returns:
            Tuple of (Severity, confidence_score)
        """
        combined_text = f"{error_title} {error_description} {stack_trace}".lower()
        
        # Check for CRITICAL severity
        critical_score = self._calculate_keyword_score(combined_text, self.CRITICAL_KEYWORDS)
        if critical_score > 0:
            logger.info(f"Classified as CRITICAL (score: {critical_score:.2f})")
            return Severity.CRITICAL, min(critical_score, 1.0)
        
        # Check for HIGH severity
        high_score = self._calculate_keyword_score(combined_text, self.HIGH_KEYWORDS)
        if high_score > 0.5:
            logger.info(f"Classified as HIGH (score: {high_score:.2f})")
            return Severity.HIGH, min(high_score, 1.0)
        
        # Check for MEDIUM severity
        medium_score = self._calculate_keyword_score(combined_text, self.MEDIUM_KEYWORDS)
        if medium_score > 0.3:
            logger.info(f"Classified as MEDIUM (score: {medium_score:.2f})")
            return Severity.MEDIUM, min(medium_score, 1.0)
        
        # Default to LOW
        logger.info("Classified as LOW (no strong indicators)")
        return Severity.LOW, 0.5
    
    def _calculate_keyword_score(self, text: str, keywords: list) -> float:
        """
        Calculate keyword match score.
        
        Args:
            text: Text to analyze
            keywords: List of regex patterns
            
        Returns:
            Score between 0 and 1
        """
        matches = 0
        for pattern in keywords:
            if re.search(pattern, text, re.IGNORECASE):
                matches += 1
        
        # Normalize to 0-1 range (multiple matches increase confidence)
        return min(matches / len(keywords) * 3, 1.0)
    
    def should_auto_fix(
        self,
        severity: Severity,
        recent_error_count: int = 0,
        is_duplicate: bool = False
    ) -> Tuple[bool, str]:
        """
        Determine if auto-fix should be triggered.
        
        Args:
            severity: Error severity
            recent_error_count: Number of recent similar errors
            is_duplicate: Whether this is a duplicate error
            
        Returns:
            Tuple of (should_fix, reason)
        """
        if not self.auto_fix_enabled:
            return False, "Auto-fix is disabled in configuration"
        
        # Don't auto-fix duplicates
        if is_duplicate:
            return False, "Error is a duplicate of existing incident"
        
        # Check severity threshold
        severity_order = {
            Severity.LOW: 1,
            Severity.MEDIUM: 2,
            Severity.HIGH: 3,
            Severity.CRITICAL: 4
        }
        
        if severity_order[severity] < severity_order[self.severity_threshold]:
            return False, f"Severity {severity.value} is below threshold {self.severity_threshold.value}"
        
        # Check error burst (multiple occurrences in time window)
        if recent_error_count >= self.burst_threshold:
            return True, f"Error burst detected: {recent_error_count} errors in {self.burst_window} minutes"
        
        # High/Critical severity always triggers auto-fix
        if severity in [Severity.HIGH, Severity.CRITICAL]:
            return True, f"Severity is {severity.value}"
        
        return False, "No auto-fix trigger conditions met"
    
    def estimate_fix_complexity(self, stack_trace: str, error_description: str) -> str:
        """
        Estimate fix complexity based on error characteristics.
        
        Args:
            stack_trace: Stack trace
            error_description: Error description
            
        Returns:
            Complexity estimate: "LOW", "MEDIUM", or "HIGH"
        """
        combined = f"{stack_trace} {error_description}".lower()
        
        # HIGH complexity indicators
        if any(keyword in combined for keyword in [
            'concurrency', 'race condition', 'deadlock', 'memory leak',
            'security', 'authentication', 'encryption', 'database schema'
        ]):
            return "HIGH"
        
        # MEDIUM complexity indicators
        if any(keyword in combined for keyword in [
            'configuration', 'timeout', 'retry', 'validation',
            'parsing', 'serialization', 'format'
        ]):
            return "MEDIUM"
        
        # Default to LOW
        return "LOW"
    
    def get_priority_score(self, severity: Severity, recent_count: int) -> int:
        """
        Calculate priority score for incident triage.
        
        Args:
            severity: Error severity
            recent_count: Recent error count
            
        Returns:
            Priority score (higher = more urgent)
        """
        base_score = {
            Severity.CRITICAL: 100,
            Severity.HIGH: 75,
            Severity.MEDIUM: 50,
            Severity.LOW: 25
        }
        
        # Add points for frequency
        frequency_bonus = min(recent_count * 5, 25)
        
        return base_score[severity] + frequency_bonus


# Helper function for easy use
def analyze_severity(
    error_title: str,
    error_description: str = "",
    stack_trace: str = "",
    environment: str = None
) -> str:
    """
    Analyze error severity (convenience function).
    
    Args:
        error_title: Error title/message
        error_description: Error description (optional)
        stack_trace: Stack trace (optional)
        environment: Environment name (optional)
        
    Returns:
        Severity string: "CRITICAL", "HIGH", "MEDIUM", or "LOW"
    """
    analyzer = SeverityAnalyzer()
    
    severity, confidence = analyzer.analyze_severity(
        error_title=error_title,
        error_description=error_description or "",
        stack_trace=stack_trace or "",
        app_name=None
    )
    
    return severity.value
