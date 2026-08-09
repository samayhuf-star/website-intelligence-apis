"""Crypto payment system for AI Agent consumption.

Flow:
1. Agent requests an invoice → gets {invoice_id, amount, treasury_wallet, memo}
2. Agent sends USDC on Solana to treasury_wallet with memo
3. Agent (or system) calls verify → system checks Solana RPC for tx with matching memo
4. Credits added to API key → agent can call APIs

Supported chains: Solana (USDC) — default. Configurable.
"""

import os
import json
import time
import hmac
import hashlib
import secrets
import logging
import sqlite3
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path
from typing import Optional, Dict, List
from datetime import datetime, date

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (from env)
# ---------------------------------------------------------------------------

SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")
TREASURY_WALLET = os.getenv("TREASURY_WALLET", "")  # e.g. "8x2z... (Solana USDC treasury)"
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"  # Solana USDC mainnet

# Payment processing
MIN_CONFIRMATIONS = 1
CONFIRMATION_TIMEOUT = 300  # 5 minutes max wait

# Pricing per endpoint (in USDC cents — $0.001 = 0.1 cent)
PRICING = {
    "website_to_markdown": 0.05,   # $0.0005
    "website_metadata": 0.02,      # $0.0002
    "technology_detector": 0.03,   # $0.0003
    "contact_extractor": 0.05,     # $0.0005
    "ai_website_summary": 0.20,    # $0.002 (AI inference cost)
    "opengraph_extractor": 0.02,   # $0.0002
    "robots_txt_parser": 0.02,     # $0.0002
    "sitemap_parser": 0.05,        # $0.0005
    "ssl_checker": 0.02,           # $0.0002
    "dns_lookup": 0.02,            # $0.0002
}

DENOMINATIONS = {
    "usdc": {"decimals": 6, "symbol": "USDC"},
    "sol": {"decimals": 9, "symbol": "SOL"},
}

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

_PAYMENTS_DB = Path(os.getenv("PAYMENTS_DB_PATH", "/home/node/.website-intel/payments.db"))


def _ensure_db():
    _PAYMENTS_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_PAYMENTS_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id TEXT PRIMARY KEY,
            api_key TEXT NOT NULL,
            amount_usdc REAL NOT NULL,
            token TEXT NOT NULL DEFAULT 'usdc',
            treasury_wallet TEXT NOT NULL,
            memo TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            tx_signature TEXT,
            sender_wallet TEXT,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            confirmed_at REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS credits (
            api_key TEXT PRIMARY KEY,
            balance_usdc REAL NOT NULL DEFAULT 0,
            total_spent REAL NOT NULL DEFAULT 0,
            total_deposited REAL NOT NULL DEFAULT 0,
            last_updated REAL NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS usage_charges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            amount_usdc REAL NOT NULL,
            timestamp REAL NOT NULL,
            invoice_ref TEXT
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoices_status ON invoices(status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_invoices_memo ON invoices(memo)
    """)
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Invoice management
# ---------------------------------------------------------------------------

def generate_invoice(api_key: str, amount_usdc: float = None,
                     token: str = "usdc") -> dict:
    """Generate a payment invoice for an API key.

    If amount_usdc is None, generates a 'top-up any amount' invoice.
    If amount_usdc is set, generates a fixed-amount invoice.
    """
    _ensure_db()
    invoice_id = "inv_" + secrets.token_hex(12)
    memo = "wia_" + secrets.token_hex(6)

    if not TREASURY_WALLET:
        return {"error": "TREASURY_WALLET not configured. Set env var."}

    amount = amount_usdc if amount_usdc else 0  # 0 = any amount
    now = time.time()

    conn = sqlite3.connect(str(_PAYMENTS_DB))
    conn.execute(
        "INSERT INTO invoices (id, api_key, amount_usdc, token, treasury_wallet, memo, "
        "status, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)",
        (invoice_id, api_key, amount, token, TREASURY_WALLET, memo, now, now + 86400)
    )
    conn.commit()
    conn.close()

    return {
        "invoice_id": invoice_id,
        "amount_usdc": amount if amount else "any",
        "token": token.upper(),
        "treasury_wallet": TREASURY_WALLET,
        "memo": memo,
        "chain": "solana",
        "instructions": (
            f"Send USDC (SPL) on Solana to address {TREASURY_WALLET} "
            f"with memo '{memo}'. "
            f"Then call /api/v1/payments/verify with tx_signature."
        ),
        "expires_at": now + 86400,
        "status": "pending",
    }


def verify_transaction(tx_signature: str, expected_memo: str) -> dict:
    """Verify a Solana transaction via RPC.

    Checks:
    1. Transaction has a memo matching expected_memo
    2. Recipient is TREASURY_WALLET
    3. Token transfer is USDC

    Returns {'verified': True, 'amount': float, 'sender': str} or error.
    """
    payload = json.dumps({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            tx_signature,
            {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}
        ]
    }).encode()

    req = urllib.request.Request(SOLANA_RPC_URL, data=payload,
                                 headers={"Content-Type": "application/json"},
                                 method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
    except Exception as e:
        return {"verified": False, "error": f"RPC error: {str(e)}"}

    if "error" in data:
        return {"verified": False, "error": f"Solana RPC error: {data['error']}"}

    tx = data.get("result")
    if not tx:
        return {"verified": False, "error": "Transaction not found"}

    # Check confirmations
    if tx.get("confirmations", 0) < MIN_CONFIRMATIONS:
        return {"verified": False, "error": "Not enough confirmations"}

    # Extract memo from instruction data
    memo_found = None
    amount_transferred = 0.0
    sender = None
    recipient = TREASURY_WALLET

    meta = tx.get("meta", {})
    if meta.get("err"):
        return {"verified": False, "error": f"Transaction failed: {meta['err']}"}

    # Parse instructions for memo and token transfer
    tx_json = tx.get("transaction", {})
    message = tx_json.get("message", {})

    # Check memo instruction
    for ix in message.get("instructions", []):
        program_id = ix.get("programId", "")
        if "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr" in program_id:
            import base64
            try:
                memo_bytes = base64.b64decode(ix.get("data", ""))
                memo_found = memo_bytes.decode("utf-8").strip()
            except Exception:
                pass

    # Parse token balances to find USDC transfer
    pre_balances = meta.get("preTokenBalances", [])
    post_balances = meta.get("postTokenBalances", [])

    for pre, post in zip(pre_balances, post_balances):
        if post.get("mint") == USDC_MINT and post.get("owner", "").lower() == TREASURY_WALLET.lower():
            pre_amt = float(pre.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
            post_amt = float(post.get("uiTokenAmount", {}).get("uiAmount", 0) or 0)
            amount_transferred = post_amt - pre_amt
            sender = pre.get("owner", "")

    if not amount_transferred:
        for bal in post_balances:
            if bal.get("mint") == USDC_MINT and bal.get("owner", "").lower() == TREASURY_WALLET.lower():
                amount_transferred = float(
                    bal.get("uiTokenAmount", {}).get("uiAmount", 0) or 0
                )

    return {
        "verified": memo_found == expected_memo and amount_transferred > 0,
        "amount_usdc": amount_transferred,
        "sender": sender or "unknown",
        "memo_found": memo_found,
        "expected_memo": expected_memo,
        "tx_signature": tx_signature,
        "confirmations": tx.get("confirmations", 0),
    }


def confirm_invoice(invoice_id: str, tx_signature: str) -> dict:
    _ensure_db()
    conn = sqlite3.connect(str(_PAYMENTS_DB))
    conn.row_factory = sqlite3.Row
    invoice = conn.execute(
        "SELECT * FROM invoices WHERE id = ?", (invoice_id,)
    ).fetchone()
    if not invoice:
        conn.close()
        return {"error": "Invoice not found"}
    if invoice["status"] != "pending":
        conn.close()
        return {"error": f"Invoice already {invoice['status']}"}
    now = time.time()
    if now > invoice["expires_at"]:
        conn.execute("UPDATE invoices SET status = 'expired' WHERE id = ?", (invoice_id,))
        conn.commit()
        conn.close()
        return {"error": "Invoice expired"}
    memo = invoice["memo"]
    result = verify_transaction(tx_signature, memo)
    if not result.get("verified"):
        conn.close()
        return {"error": result.get("error", "Transaction verification failed"), "details": result}
    amount = result["amount_usdc"]
    api_key = invoice["api_key"]
    conn.execute(
        "UPDATE invoices SET status = 'confirmed', tx_signature = ?, sender_wallet = ?, confirmed_at = ? WHERE id = ?",
        (tx_signature, result.get("sender", ""), now, invoice_id)
    )
    credit_row = conn.execute(
        "SELECT * FROM credits WHERE api_key = ?", (api_key,)
    ).fetchone()
    if credit_row:
        conn.execute(
            "UPDATE credits SET balance_usdc = balance_usdc + ?, total_deposited = total_deposited + ?, last_updated = ? WHERE api_key = ?",
            (amount, amount, now, api_key)
        )
    else:
        conn.execute(
            "INSERT INTO credits (api_key, balance_usdc, total_spent, total_deposited, last_updated) VALUES (?, ?, 0, ?, ?)",
            (api_key, amount, amount, now)
        )
    conn.commit()
    conn.close()
    return {
        "status": "confirmed", "invoice_id": invoice_id, "amount_usdc": amount,
        "tx_signature": tx_signature, "credits_added": True,
        "new_balance": get_balance(api_key),
    }


def get_balance(api_key: str) -> dict:
    _ensure_db()
    conn = sqlite3.connect(str(_PAYMENTS_DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM credits WHERE api_key = ?", (api_key,)).fetchone()
    conn.close()
    if not row:
        return {"api_key": api_key[:12] + "...", "balance_usdc": 0, "total_spent": 0, "total_deposited": 0}
    return {
        "api_key": api_key[:12] + "...", "balance_usdc": row["balance_usdc"],
        "total_spent": row["total_spent"], "total_deposited": row["total_deposited"],
        "last_updated": row["last_updated"],
    }


def deduct_credit(api_key: str, endpoint: str) -> bool:
    cost = PRICING.get(endpoint, 0.02)
    _ensure_db()
    conn = sqlite3.connect(str(_PAYMENTS_DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT balance_usdc FROM credits WHERE api_key = ?", (api_key,)).fetchone()
    if not row or row["balance_usdc"] < cost:
        conn.close()
        return False
    now = time.time()
    conn.execute(
        "UPDATE credits SET balance_usdc = balance_usdc - ?, total_spent = total_spent + ?, last_updated = ? WHERE api_key = ?",
        (cost, cost, now, api_key)
    )
    conn.execute(
        "INSERT INTO usage_charges (api_key, endpoint, amount_usdc, timestamp) VALUES (?, ?, ?, ?)",
        (api_key, endpoint, cost, now)
    )
    conn.commit()
    conn.close()
    return True


def get_invoice(invoice_id: str) -> Optional[dict]:
    _ensure_db()
    conn = sqlite3.connect(str(_PAYMENTS_DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_payment_history(api_key: str, limit: int = 20) -> List[dict]:
    _ensure_db()
    conn = sqlite3.connect(str(_PAYMENTS_DB))
    conn.row_factory = sqlite3.Row
    invoices = conn.execute(
        "SELECT * FROM invoices WHERE api_key = ? ORDER BY created_at DESC LIMIT ?",
        (api_key, limit)
    ).fetchall()
    charges = conn.execute(
        "SELECT * FROM usage_charges WHERE api_key = ? ORDER BY timestamp DESC LIMIT ?",
        (api_key, limit)
    ).fetchall()
    conn.close()
    return {"invoices": [dict(i) for i in invoices], "charges": [dict(c) for c in charges]}


def get_pricing() -> dict:
    return {
        endpoint: {"price_usdc": amount, "price_display": f"${amount/100:.6f}"}
        for endpoint, amount in PRICING.items()
    }
