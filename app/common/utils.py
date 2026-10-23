from urllib.parse import urlparse
from typing import Optional

def extract_domain(url: str) -> Optional[str]:
    """Extract normalized hostname from URL."""
    try:
        parsed = urlparse(url)
        return parsed.hostname.lower() if parsed.hostname else None
    except Exception:
        return None

def is_same_domain(url_a: str, url_b: str) -> bool:
    """Check if two URLs belong to the exact same domain."""
    domain_a = extract_domain(url_a)
    domain_b = extract_domain(url_b)
    return bool(domain_a and domain_b and domain_a == domain_b)
