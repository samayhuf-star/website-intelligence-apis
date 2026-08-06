#!/usr/bin/env python3
"""DO Droplet deployer for Website Intelligence APIs."""
import os, sys, time, json, tarfile, io, logging, subprocess, secrets
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)
DO_TOKEN = os.getenv("DO_TOKEN", "")
if not DO_TOKEN: logger.error("DO_TOKEN not set"); sys.exit(1)
DROPLET_NAME = os.getenv("DO_DROPLET_NAME", "website-intelligence-apis")
REGION = "nyc1"; SIZE = "s-1vcpu-1gb"
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
ADMIN_KEY = "wia_admin_" + secrets.token_hex(16)
DEV_KEY = "wia_dev_" + secrets.token_hex(8)
ENV_CONTENT = f"""PORT=8000
API_KEYS='{{"{ADMIN_KEY}": {{"tier": "admin", "owner": "admin"}}, "{DEV_KEY}": {{"tier": "enterprise", "owner": "developer"}}}}'
ADMIN_API_KEY={ADMIN_KEY}
USAGE_DB_PATH=/opt/website-intel-apis/usage.db
"""
from digitalocean import Manager, Droplet, SSHKey
import requests
manager = Manager(token=DO_TOKEN)
account = manager.get_account()
logger.info(f"Account: {account.email}")
ssh_keys = manager.get_all_sshkeys(); key_ids = [k.id for k in ssh_keys if k.name == "agent37-deploy"]
if not key_ids:
    subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", os.path.expanduser("~/.ssh/id_ed25519"), "-N", "", "-C", "agent37-deploy"], check=True)
    with open(os.path.expanduser("~/.ssh/id_ed25519.pub")) as f: pubkey = f.read().strip()
    key = SSHKey(token=DO_TOKEN, name="agent37-deploy", public_key=pubkey); key.create(); key_ids = [key.id]

def build_tarball():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for root, dirs, files in os.walk(PROJECT_DIR):
            rel = os.path.relpath(root, PROJECT_DIR)
            dirs[:] = [d for d in dirs if d not in ("venv", "__pycache__", ".git")]
            for fname in files:
                if fname.endswith(".pyc") or fname == ".env": continue
                tar.add(os.path.join(root, fname), arcname=os.path.join(rel, fname))
    return buf.getvalue()

def deploy_app(ip):
    tarball = build_tarball(); local_tar = "/tmp/wia-deploy.tar.gz"
    with open(local_tar, "wb") as f: f.write(tarball)
    logger.info(f"SCP: {len(tarball)/1024:.0f} KB")
    subprocess.run(["scp", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "ConnectTimeout=15", local_tar, f"root@{ip}:/tmp/wia.tar.gz"], check=True, timeout=60)
    os.unlink(local_tar)
    return True

print("See execute_do_token for deployment")
