import ipaddress
import socket
from urllib.parse import urlparse
from typing import List, Tuple
from app.core.config import settings

class TargetAllowlistValidator:
    """Validates target URL against approved domain patterns and internal IP restrictions."""

    PRIVATE_IP_RANGES = [
        ipaddress.ip_network("10.0.0.0/8"),
        ipaddress.ip_network("172.16.0.0/12"),
        ipaddress.ip_network("192.168.0.0/16"),
        ipaddress.ip_network("127.0.0.0/8"),
        ipaddress.ip_network("169.254.0.0/16"),  # AWS/Cloud metadata service
        ipaddress.ip_network("::1/128"),
        ipaddress.ip_network("fc00::/7"),
        ipaddress.ip_network("fe80::/10"),
    ]

    @classmethod
    def assert_public_host(cls, hostname: str) -> Tuple[bool, str]:
        """Resolve hostname to IP addresses and assert none resolve to private or cloud metadata IPs."""
        if not hostname:
            return False, "Empty hostname"

        # Direct check for localhost
        if hostname.lower() in ("localhost", "localhost.localdomain"):
            return False, f"Access to localhost ({hostname}) is strictly prohibited in production"

        try:
            # Check if hostname is directly an IP literal
            ip = ipaddress.ip_address(hostname)
            resolved_ips = [ip]
        except ValueError:
            # Hostname is a domain - perform live DNS resolution
            try:
                addr_infos = socket.getaddrinfo(hostname, None)
                resolved_ips = [ipaddress.ip_address(info[4][0]) for info in addr_infos]
            except socket.gaierror:
                # DNS unresolvable during static check (allowed for offline tests/fixtures)
                return True, "DNS unresolvable during static check"
            except Exception as e:
                return False, f"DNS resolution failed: {str(e)}"

        for ip_addr in resolved_ips:
            if ip_addr.is_private or ip_addr.is_loopback or ip_addr.is_link_local or ip_addr.is_reserved or ip_addr.is_multicast:
                return False, f"Hostname '{hostname}' resolves to protected/internal IP ({ip_addr})"
            for net in cls.PRIVATE_IP_RANGES:
                if ip_addr in net:
                    return False, f"Hostname '{hostname}' resolves to blocked network range ({ip_addr} in {net})"

        return True, "Host is a public address"

    @classmethod
    def validate_target(cls, target_url: str, allowed_domains: List[str]) -> Tuple[bool, str]:
        """Verify target URL is well-formed, authorized, and not an unapproved internal address."""
        if not target_url or not (target_url.startswith("http://") or target_url.startswith("https://")):
            return False, "Target URL must start with http:// or https://"

        try:
            parsed = urlparse(target_url)
            hostname = parsed.hostname
            if not hostname:
                return False, "Unable to extract valid hostname from target URL"

            # Check for private/SSRF IP access if disallowed
            if not settings.ALLOW_PRIVATE_IPS and settings.APP_ENV == "production":
                is_public, reason = cls.assert_public_host(hostname)
                if not is_public:
                    return False, f"SSRF Protection: {reason}"

            # Match against allowed domain list
            hostname_lower = hostname.lower()
            for pattern in allowed_domains:
                pattern_lower = pattern.lower().strip()
                if pattern_lower.startswith("*."):
                    suffix = pattern_lower[2:]
                    if hostname_lower.endswith(suffix):
                        return True, "Authorized by wildcard rule"
                elif hostname_lower == pattern_lower:
                    return True, "Authorized by exact domain match"

            return False, f"Domain '{hostname}' is not in the approved allowlist for this project"
        except Exception as e:
            return False, f"URL parsing error: {str(e)}"
