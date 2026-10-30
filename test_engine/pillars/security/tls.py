import ssl
import socket
from urllib.parse import urlparse
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Any
from test_engine.pillars.base import PillarFinding
from app.common.constants import FindingSeverity

class TLSScanner:
    """Audits SSL/TLS certificate validity, expiration, protocol version, and ciphers."""

    @staticmethod
    def _probe_protocol(hostname: str, port: int, version: ssl.TLSVersion) -> Tuple[bool, str]:
        """Attempt one bounded handshake for a specific legacy TLS version."""
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            context.minimum_version = version
            context.maximum_version = version
            try:
                context.set_ciphers("ALL:@SECLEVEL=0")
            except ssl.SSLError:
                pass
            with socket.create_connection((hostname, port), timeout=2.0) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as tls_socket:
                    return True, tls_socket.version() or version.name
        except (ssl.SSLError, OSError, ValueError):
            return False, ""

    @staticmethod
    def audit_tls(target_url: str) -> Tuple[List[PillarFinding], float, Dict[str, Any]]:
        findings: List[PillarFinding] = []
        metrics: Dict[str, Any] = {"tls_enabled": False}

        parsed = urlparse(target_url)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port or (443 if scheme == "https" else 80)

        if not hostname:
            return findings, 100.0, metrics

        if scheme != "https":
            findings.append(PillarFinding(
                id="SEC-NO-TLS",
                title="Target site does not use HTTPS",
                description=f"The site URL ({target_url}) communicates over plain unencrypted HTTP.",
                severity=FindingSeverity.HIGH,
                selector_or_url=target_url,
                suggested_fix="Configure an SSL/TLS certificate and enforce automatic HTTP-to-HTTPS redirect."
            ))
            return findings, 50.0, metrics

        metrics["tls_enabled"] = True

        # Probe TLS handshake
        context = ssl.create_default_context()
        try:
            with socket.create_connection((hostname, port), timeout=5.0) as sock:
                with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                    cert = ssock.getpeercert()
                    cipher_info = ssock.cipher()
                    protocol_version = ssock.version()

                    metrics["protocol_version"] = protocol_version
                    if cipher_info:
                        metrics["cipher_name"] = cipher_info[0]
                        metrics["cipher_bits"] = cipher_info[2]

                    # 1. Audit Expiration Date
                    if cert and "notAfter" in cert:
                        # Format: 'May 12 18:24:00 2026 GMT'
                        expiry_str = cert["notAfter"]
                        expiry_date = datetime.strptime(expiry_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
                        now = datetime.now(timezone.utc)
                        days_remaining = (expiry_date - now).days
                        metrics["cert_days_remaining"] = days_remaining
                        metrics["cert_issuer"] = str(cert.get("issuer", ""))

                        if days_remaining < 0:
                            findings.append(PillarFinding(
                                id="SEC-TLS-EXPIRED",
                                title="SSL/TLS Certificate is Expired",
                                description=f"The certificate expired {abs(days_remaining)} days ago on {expiry_str}.",
                                severity=FindingSeverity.CRITICAL,
                                selector_or_url=hostname,
                                suggested_fix="Renew the SSL/TLS certificate immediately."
                            ))
                        elif days_remaining < 15:
                            findings.append(PillarFinding(
                                id="SEC-TLS-EXPIRING-SOON",
                                title=f"SSL/TLS Certificate Expires in {days_remaining} Days",
                                description=f"Certificate expiration is imminent on {expiry_str}.",
                                severity=FindingSeverity.HIGH,
                                selector_or_url=hostname,
                                suggested_fix="Trigger automated certificate renewal (e.g. Certbot / Let's Encrypt)."
                            ))
                        elif days_remaining < 30:
                            findings.append(PillarFinding(
                                id="SEC-TLS-EXPIRING-WARNING",
                                title=f"SSL/TLS Certificate Expires in {days_remaining} Days",
                                description=f"Certificate expires in less than 30 days ({expiry_str}).",
                                severity=FindingSeverity.MEDIUM,
                                selector_or_url=hostname,
                                suggested_fix="Plan certificate renewal."
                            ))

                    # 2. Record the modern client's negotiated cipher.
                    if cipher_info and any(weak in cipher_info[0].upper() for weak in ["RC4", "3DES", "DES", "MD5", "NULL", "EXPORT"]):
                        findings.append(PillarFinding(
                            id="SEC-TLS-WEAK-CIPHER",
                            title=f"Weak Cipher Suite ({cipher_info[0]})",
                            description="Negotiated cipher suite is considered cryptographically insecure.",
                            severity=FindingSeverity.HIGH,
                            selector_or_url=hostname,
                            suggested_fix="Update cipher suite configuration to use modern AEAD ciphers (GCM, CHACHA20)."
                        ))

        except ssl.SSLCertVerificationError as e:
            findings.append(PillarFinding(
                id="SEC-TLS-INVALID-CERT",
                title="Untrusted or Self-Signed SSL/TLS Certificate",
                description=f"Certificate verification failed: {e.verify_message}",
                severity=FindingSeverity.HIGH,
                selector_or_url=hostname,
                suggested_fix="Install a valid SSL certificate signed by a recognized Certificate Authority (CA)."
            ))
        except (socket.timeout, socket.gaierror, ConnectionRefusedError, OSError) as e:
            # Network or connection issues during test/offline run
            metrics["tls_probe_error"] = str(e)

        legacy_protocols = []
        if scheme == "https":
            versions = (
                ("TLSv1", getattr(ssl.TLSVersion, "TLSv1", None)),
                ("TLSv1.1", getattr(ssl.TLSVersion, "TLSv1_1", None)),
            )
            for label, version in versions:
                if version is None:
                    continue
                supported, negotiated = TLSScanner._probe_protocol(hostname, port, version)
                if supported:
                    legacy_protocols.append(negotiated or label)
                    findings.append(PillarFinding(
                        id=f"SEC-TLS-DEPRECATED-{label.replace('.', '-')}",
                        title=f"Server accepts deprecated {label}",
                        description=f"A direct TLS handshake succeeded while restricted to {label}.",
                        severity=FindingSeverity.HIGH,
                        selector_or_url=hostname,
                        suggested_fix="Disable TLS 1.0 and TLS 1.1 in the server or load balancer configuration; allow TLS 1.2 or newer.",
                    ))
        metrics["legacy_protocols_supported"] = legacy_protocols

        # Calculate score deductions
        deductions = 0.0
        for f in findings:
            if f.severity == FindingSeverity.CRITICAL:
                deductions += 40.0
            elif f.severity == FindingSeverity.HIGH:
                deductions += 20.0
            elif f.severity == FindingSeverity.MEDIUM:
                deductions += 10.0
            elif f.severity == FindingSeverity.LOW:
                deductions += 5.0

        score = max(0.0, 100.0 - deductions)
        return findings, score, metrics
