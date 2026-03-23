"""
Broker Account Store — JSON-file backed CRUD for broker accounts.
Supports multiple accounts per broker (Dhan, Zerodha).
Auto-imports credentials from .env / config on first use.
"""

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional

logger = logging.getLogger("broker_accounts")

# ─── Broker Field Definitions ────────────────────────────────────────────────

BROKER_DEFINITIONS = {
    "dhan": {
        "label": "Dhan",
        "fields": [
            {"key": "client_id", "label": "Client ID", "type": "text", "required": True, "placeholder": "e.g. 1234567890"},
            {"key": "access_token", "label": "Access Token", "type": "password", "required": True, "placeholder": "Your Dhan access token"},
        ],
        "icon": "dhan",
        "color": "#00b386",
        "description": "Connect your Dhan trading account",
    },
    "zerodha": {
        "label": "Zerodha",
        "fields": [
            {"key": "api_key", "label": "API Key", "type": "text", "required": True, "placeholder": "Your Kite API key"},
            {"key": "api_secret", "label": "API Secret", "type": "password", "required": True, "placeholder": "Your Kite API secret"},
            {"key": "user_id", "label": "User ID", "type": "text", "required": True, "placeholder": "e.g. AB1234"},
            {"key": "password", "label": "Password", "type": "password", "required": True, "placeholder": "Your Zerodha password"},
            {"key": "totp_secret", "label": "TOTP Secret", "type": "password", "required": True, "placeholder": "Base32 TOTP secret"},
        ],
        "icon": "zerodha",
        "color": "#387ed1",
        "description": "Connect your Zerodha Kite account",
    },
}


def _default_store_path() -> Path:
    configured = os.getenv("BROKER_ACCOUNTS_FILE", "data/broker_accounts.json").strip()
    return Path(configured)


class BrokerAccountStore:
    """Thread-safe JSON-file backed store for broker accounts."""

    def __init__(self, path: Optional[Path] = None):
        self._path = path or _default_store_path()
        self._lock = Lock()
        self._accounts: list[dict] = []
        self._loaded = False

    # ── persistence ──────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._accounts = data if isinstance(data, list) else []
            except Exception as e:
                logger.warning("Failed to load broker accounts from %s: %s", self._path, e)
                self._accounts = []
        else:
            self._auto_import_from_env()
        self._loaded = True

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._accounts, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("Failed to save broker accounts to %s: %s", self._path, e)

    def _auto_import_from_env(self) -> None:
        """Import broker credentials from environment variables on first use."""
        imported = []

        # Dhan
        dhan_client = os.getenv("DHAN_CLIENT_ID", "").strip()
        dhan_token = os.getenv("DHAN_ACCESS_TOKEN", "").strip()
        if dhan_client and dhan_token:
            imported.append({
                "id": str(uuid.uuid4()),
                "broker": "dhan",
                "label": "Dhan (imported from .env)",
                "credentials": {
                    "client_id": dhan_client,
                    "access_token": dhan_token,
                },
                "status": "imported",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            })
            logger.info("Auto-imported Dhan account from environment variables")

        # Zerodha
        z_key = os.getenv("ZERODHA_API_KEY", "").strip()
        z_secret = os.getenv("ZERODHA_API_SECRET", "").strip()
        z_user = os.getenv("ZERODHA_USER_ID", "").strip()
        z_pass = os.getenv("ZERODHA_PASSWORD", "").strip()
        z_totp = os.getenv("ZERODHA_TOTP_SECRET", "").strip()
        if z_key and z_user:
            imported.append({
                "id": str(uuid.uuid4()),
                "broker": "zerodha",
                "label": "Zerodha (imported from .env)",
                "credentials": {
                    "api_key": z_key,
                    "api_secret": z_secret,
                    "user_id": z_user,
                    "password": z_pass,
                    "totp_secret": z_totp,
                },
                "status": "imported",
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            })
            logger.info("Auto-imported Zerodha account from environment variables")

        if imported:
            self._accounts = imported
            self._save()

    # ── CRUD ─────────────────────────────────────────────────

    def list_accounts(self, *, mask_credentials: bool = True) -> list[dict]:
        with self._lock:
            self._ensure_loaded()
            result = []
            for acc in self._accounts:
                entry = {**acc}
                if mask_credentials and "credentials" in entry:
                    entry["credentials"] = _mask_credentials(entry["credentials"])
                result.append(entry)
            return result

    def get_account(self, account_id: str, *, mask_credentials: bool = True) -> Optional[dict]:
        with self._lock:
            self._ensure_loaded()
            for acc in self._accounts:
                if acc["id"] == account_id:
                    entry = {**acc}
                    if mask_credentials and "credentials" in entry:
                        entry["credentials"] = _mask_credentials(entry["credentials"])
                    return entry
            return None

    def add_account(self, broker: str, label: str, credentials: dict) -> dict:
        if broker not in BROKER_DEFINITIONS:
            raise ValueError(f"Unsupported broker: {broker}")

        # Validate required fields
        defn = BROKER_DEFINITIONS[broker]
        for field_def in defn["fields"]:
            if field_def["required"] and not credentials.get(field_def["key"], "").strip():
                raise ValueError(f"Missing required field: {field_def['label']}")

        account = {
            "id": str(uuid.uuid4()),
            "broker": broker,
            "label": label or f"{defn['label']} Account",
            "credentials": credentials,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
        }

        with self._lock:
            self._ensure_loaded()
            self._accounts.append(account)
            self._save()

        return {**account, "credentials": _mask_credentials(account["credentials"])}

    def update_account(self, account_id: str, label: Optional[str] = None, credentials: Optional[dict] = None) -> Optional[dict]:
        with self._lock:
            self._ensure_loaded()
            for acc in self._accounts:
                if acc["id"] == account_id:
                    if label is not None:
                        acc["label"] = label
                    if credentials is not None:
                        # Merge: only update non-empty fields
                        for k, v in credentials.items():
                            if v and v.strip():
                                acc["credentials"][k] = v.strip()
                    acc["updated_at"] = datetime.now().isoformat()
                    acc["status"] = "pending"
                    self._save()
                    return {**acc, "credentials": _mask_credentials(acc["credentials"])}
            return None

    def delete_account(self, account_id: str) -> bool:
        with self._lock:
            self._ensure_loaded()
            before = len(self._accounts)
            self._accounts = [a for a in self._accounts if a["id"] != account_id]
            if len(self._accounts) < before:
                self._save()
                return True
            return False

    def get_raw_credentials(self, account_id: str) -> Optional[dict]:
        """Get unmasked credentials for connection testing."""
        with self._lock:
            self._ensure_loaded()
            for acc in self._accounts:
                if acc["id"] == account_id:
                    return acc.get("credentials", {})
            return None

    def update_status(self, account_id: str, status: str) -> None:
        with self._lock:
            self._ensure_loaded()
            for acc in self._accounts:
                if acc["id"] == account_id:
                    acc["status"] = status
                    acc["updated_at"] = datetime.now().isoformat()
                    self._save()
                    return


def _mask_credentials(creds: dict) -> dict:
    """Mask sensitive credential values, showing only the last 4 characters."""
    masked = {}
    for key, value in creds.items():
        if not value or len(str(value)) <= 4:
            masked[key] = "••••"
        else:
            s = str(value)
            masked[key] = "••••" + s[-4:]
    return masked


async def test_broker_connection(broker: str, credentials: dict) -> dict:
    """Test broker connectivity with given credentials."""
    try:
        if broker == "dhan":
            return await _test_dhan_connection(credentials)
        elif broker == "zerodha":
            return await _test_zerodha_connection(credentials)
        else:
            return {"success": False, "message": f"Unsupported broker: {broker}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


async def _test_dhan_connection(credentials: dict) -> dict:
    """Test Dhan API connectivity."""
    client_id = credentials.get("client_id", "")
    access_token = credentials.get("access_token", "")
    if not client_id or not access_token:
        return {"success": False, "message": "Missing client_id or access_token"}

    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.dhan.co/v2/fundlimit",
                headers={
                    "Content-Type": "application/json",
                    "access-token": access_token,
                    "client-id": client_id,
                },
            )
            if resp.status_code == 200:
                return {"success": True, "message": "Connected successfully to Dhan"}
            else:
                body = resp.text[:200]
                return {"success": False, "message": f"Dhan API error ({resp.status_code}): {body}"}
    except ImportError:
        return {"success": False, "message": "httpx not installed — cannot test connection"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {e}"}


async def _test_zerodha_connection(credentials: dict) -> dict:
    """Test Zerodha API connectivity."""
    api_key = credentials.get("api_key", "")
    user_id = credentials.get("user_id", "")
    if not api_key or not user_id:
        return {"success": False, "message": "Missing api_key or user_id"}

    # Zerodha needs a login flow (TOTP), so we do a basic API key validity check
    try:
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"https://api.kite.trade/session/token?api_key={api_key}",
            )
            # A 403 or 400 (not 404) means the API key is recognized
            if resp.status_code in (200, 400, 403):
                return {"success": True, "message": "Zerodha API key is valid. Full login requires TOTP authentication."}
            elif resp.status_code == 404:
                return {"success": False, "message": "Invalid Zerodha API key"}
            else:
                return {"success": False, "message": f"Zerodha API error ({resp.status_code})"}
    except ImportError:
        return {"success": False, "message": "httpx not installed — cannot test connection"}
    except Exception as e:
        return {"success": False, "message": f"Connection failed: {e}"}


# ─── Singleton ───────────────────────────────────────────────────────────────

_store: Optional[BrokerAccountStore] = None


def get_broker_account_store() -> BrokerAccountStore:
    global _store
    if _store is None:
        _store = BrokerAccountStore()
    return _store
