"""API 10: DNS Lookup."""
import socket, time, asyncio
from urllib.parse import urlparse

async def lookup_dns(domain: str, proxy: str = None) -> dict:
    if domain.startswith(("http://","https://")):
        domain = urlparse(domain).netloc
    if ":" in domain:
        domain = domain.split(":")[0]
    start = time.time()
    records = {}
    try:
        loop = asyncio.get_event_loop()
        addr = await loop.getaddrinfo(domain, None, socket.AF_INET)
        records["A"] = list(set(a[4][0] for a in addr))
    except: records["A"] = []
    try:
        addr6 = await loop.getaddrinfo(domain, None, socket.AF_INET6)
        records["AAAA"] = list(set(a[4][0] for a in addr6))
    except: records["AAAA"] = []
    import subprocess
    for rtype, flag in [("MX", "mx"), ("NS", "ns"), ("CNAME", "cname"), ("TXT", "txt")]:
        try:
            r = subprocess.run(["dig", "+short", "+timeout=5", domain, flag], capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                lines = [l.strip() for l in r.stdout.strip().split("\n") if l.strip()]
                records[rtype] = lines
            else:
                records[rtype] = []
        except Exception:
            records[rtype] = []
    latency = round((time.time() - start) * 1000, 1)
    subdomains = []
    for prefix in ["www","mail","blog","api","dev","admin","app","cdn","shop","ftp","m","test","webmail","support","help","status","portal","docs","forum","wiki","store","media","images","static","secure","login","beta","host","mx"]:
        try:
            r = subprocess.run(["dig", "+short", "+timeout=3", f"{prefix}.{domain}", "a"], capture_output=True, text=True, timeout=6)
            if r.returncode == 0 and r.stdout.strip():
                for ip in r.stdout.strip().split("\n"):
                    if ip.strip() and not ip.startswith(";"):
                        subdomains.append({"subdomain": f"{prefix}.{domain}", "ip": ip.strip()})
        except: pass
    return {"domain": domain, "success": True, "records": {k: v for k, v in records.items() if v}, "latency_ms": latency, "subdomains": subdomains, "subdomain_count": len(subdomains), "ip_providers": _ip_providers(records.get("A",[]))}

def _ip_providers(ips):
    providers = {}
    for ip in ips:
        try:
            import subprocess
            r = subprocess.run(["dig", "+short", "+timeout=3", "-x", ip], capture_output=True, text=True, timeout=6)
            if r.stdout.strip():
                host = r.stdout.strip().split("\n")[0]
                if "cloudflare" in host.lower(): providers[ip] = "Cloudflare"
                elif "amazon" in host.lower() or "aws" in host.lower(): providers[ip] = "AWS"
                elif "google" in host.lower(): providers[ip] = "Google"
                else: providers[ip] = host.rstrip(".")
        except: pass
    return providers
