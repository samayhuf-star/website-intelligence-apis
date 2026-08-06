"""API 9: SSL Checker."""
import ssl, socket, time
from datetime import datetime, timezone
from urllib.parse import urlparse

async def check_ssl(domain: str, proxy: str = None) -> dict:
    if domain.startswith(("http://","https://")):
        domain = urlparse(domain).netloc
    if ":" in domain:
        domain = domain.split(":")[0]
    clean_domain = domain.lower().lstrip("www.")
    try:
        context = ssl.create_default_context()
        context.check_hostname = False
        with socket.create_connection((domain, 443), timeout=15) as sock:
            with context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert(binary_form=False)
                cipher = ssock.cipher()
                protocol = ssock.version()
                subject = dict(x[0] for x in cert.get("subject", []))
                issuer = dict(x[0] for x in cert.get("issuer", []))
                cn = subject.get("commonName", "")
                san_list = [ext[1] for ext in cert.get("subjectAltName", ())]
                nb = _parse_date(cert.get("notBefore", ""))
                na = _parse_date(cert.get("notAfter", ""))
                now = datetime.now(timezone.utc)
                days = (na - now).days if na else None
                return {"domain": domain, "success": True, "has_ssl": True, "certificate": {"common_name": cn, "organization": subject.get("organizationName",""), "issuer": issuer.get("organizationName",""), "valid_from": nb.isoformat() if nb else None, "valid_to": na.isoformat() if na else None, "days_remaining": days, "is_expired": na and na < now, "subject_alt_names": san_list}, "connection": {"protocol": protocol, "cipher": cipher[0] if cipher else "", "cipher_bits": cipher[2] if cipher and len(cipher) > 2 else 0}, "security": {"grade": _grade(max(0, 100 if "TLSv1.2" in str(protocol) or "TLSv1.3" in str(protocol) else 70))}}
    except Exception as e:
        return {"domain": domain, "success": False, "error": str(e)}

def _parse_date(s):
    if not s: return None
    try:
        for fmt in ["%b %d %H:%M:%S %Y %Z", "%Y%m%d%H%M%S", "%y%m%d%H%M%S"]:
            try: return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
            except: pass
    except: pass
    return None

def _grade(score):
    if score >= 90: return "A+"
    if score >= 80: return "A"
    if score >= 70: return "B"
    if score >= 60: return "C"
    return "F"
