"""Error fingerprinting and deduplication using simhash."""
import hashlib
import re
from typing import Tuple
from config.settings import get_settings
import logging

logger = logging.getLogger(__name__)
settings = get_settings()


class ErrorDeduplicator:
    """
    Deduplicate errors using fingerprinting.
    Uses simhash algorithm for similarity detection.
    """
    
    def __init__(self, threshold: float = None):
        """
        Initialize deduplicator.
        
        Args:
            threshold: Similarity threshold (0-1), defaults to settings value
        """
        self.threshold = threshold or settings.duplicate_threshold
        self.algorithm = settings.error_fingerprint_algorithm
    
    def generate_fingerprint(self, error_text: str, stack_trace: str) -> str:
        """
        Generate a fingerprint for an error.
        
        Args:
            error_text: Error message/title
            stack_trace: Stack trace
            
        Returns:
            Hexadecimal fingerprint string
        """
        if self.algorithm == "exact":
            return self._exact_fingerprint(error_text, stack_trace)
        elif self.algorithm == "simhash":
            return self._simhash_fingerprint(error_text, stack_trace)
        elif self.algorithm == "minhash":
            return self._minhash_fingerprint(error_text, stack_trace)
        else:
            return self._simhash_fingerprint(error_text, stack_trace)
    
    def _normalize_text(self, text: str) -> str:
        """
        Normalize text for fingerprinting.
        Remove dynamic content like timestamps, UUIDs, numbers.
        """
        # Remove timestamps
        text = re.sub(r'\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(\.\d+)?', '[TIMESTAMP]', text)
        
        # Remove UUIDs
        text = re.sub(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', '[UUID]', text, flags=re.IGNORECASE)
        
        # Remove hex addresses
        text = re.sub(r'0x[0-9a-f]+', '[ADDR]', text, flags=re.IGNORECASE)
        
        # Remove line numbers in stack traces
        text = re.sub(r':(\d+)\)', ':[LINE])', text)
        text = re.sub(r'line (\d+)', 'line [LINE]', text, flags=re.IGNORECASE)
        
        # Remove file paths (keep only filename)
        text = re.sub(r'([A-Z]:\\|/)[^\s:]+[\\/]([^\s:]+)', r'\2', text)
        
        # Normalize whitespace
        text = ' '.join(text.split())
        
        return text.lower()
    
    def _exact_fingerprint(self, error_text: str, stack_trace: str) -> str:
        """Generate exact hash fingerprint."""
        normalized = self._normalize_text(f"{error_text}\n{stack_trace}")
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]
    
    def _simhash_fingerprint(self, error_text: str, stack_trace: str) -> str:
        """
        Generate simhash fingerprint.
        Simhash allows approximate matching of similar errors.
        """
        normalized = self._normalize_text(f"{error_text}\n{stack_trace}")
        
        # Tokenize into words
        tokens = normalized.split()
        
        # Simple simhash implementation
        hashbits = 64
        v = [0] * hashbits
        
        for token in tokens:
            # Hash the token
            h = int(hashlib.md5(token.encode()).hexdigest(), 16)
            
            # Update v based on hash bits
            for i in range(hashbits):
                if h & (1 << i):
                    v[i] += 1
                else:
                    v[i] -= 1
        
        # Generate final fingerprint
        fingerprint = 0
        for i in range(hashbits):
            if v[i] > 0:
                fingerprint |= (1 << i)
        
        return f"{fingerprint:016x}"
    
    def _minhash_fingerprint(self, error_text: str, stack_trace: str) -> str:
        """
        Generate minhash fingerprint.
        Uses multiple hash functions for better similarity detection.
        """
        normalized = self._normalize_text(f"{error_text}\n{stack_trace}")
        tokens = set(normalized.split())
        
        # Use 16 hash functions
        num_hashes = 16
        minhashes = []
        
        for i in range(num_hashes):
            min_hash = float('inf')
            for token in tokens:
                # Create different hash functions by adding salt
                h = int(hashlib.md5(f"{i}{token}".encode()).hexdigest(), 16)
                min_hash = min(min_hash, h)
            minhashes.append(min_hash)
        
        # Combine into single fingerprint
        combined = ''.join(str(h)[:4] for h in minhashes)
        return hashlib.sha256(combined.encode()).hexdigest()[:16]
    
    def calculate_similarity(self, fingerprint1: str, fingerprint2: str) -> float:
        """
        Calculate similarity between two fingerprints.
        
        Args:
            fingerprint1: First fingerprint
            fingerprint2: Second fingerprint
            
        Returns:
            Similarity score between 0 and 1
        """
        if self.algorithm == "exact":
            return 1.0 if fingerprint1 == fingerprint2 else 0.0
        
        # For simhash/minhash, use Hamming distance
        try:
            fp1 = int(fingerprint1, 16)
            fp2 = int(fingerprint2, 16)
            
            # Calculate Hamming distance
            xor = fp1 ^ fp2
            hamming_distance = bin(xor).count('1')
            
            # Convert to similarity (0-1)
            max_distance = len(fingerprint1) * 4  # 4 bits per hex char
            similarity = 1.0 - (hamming_distance / max_distance)
            
            return similarity
        except ValueError:
            logger.error(f"Invalid fingerprint format: {fingerprint1} or {fingerprint2}")
            return 0.0
    
    def is_duplicate(self, fingerprint1: str, fingerprint2: str) -> Tuple[bool, float]:
        """
        Check if two fingerprints represent duplicate errors.
        
        Args:
            fingerprint1: First fingerprint
            fingerprint2: Second fingerprint
            
        Returns:
            Tuple of (is_duplicate, similarity_score)
        """
        similarity = self.calculate_similarity(fingerprint1, fingerprint2)
        is_dup = similarity >= self.threshold
        
        logger.debug(f"Fingerprint comparison: similarity={similarity:.3f}, threshold={self.threshold}, duplicate={is_dup}")
        
        return is_dup, similarity


# Helper function for easy use
def deduplicate_error(error_title: str, stack_trace: str = "", app_name: str = "") -> str:
    """
    Generate a fingerprint for an error (convenience function).
    
    Args:
        error_title: Error title/message
        stack_trace: Stack trace (optional)
        app_name: Application name (optional, can be included in fingerprint)
        
    Returns:
        Fingerprint string
    """
    deduplicator = ErrorDeduplicator()
    
    # Combine error info for fingerprinting
    error_text = f"{app_name}:{error_title}" if app_name else error_title
    
    return deduplicator.generate_fingerprint(error_text, stack_trace or "")
