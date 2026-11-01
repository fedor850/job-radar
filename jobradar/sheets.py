"""Client for the Google Apps Script web app (Google Sheets tracker + Gmail + Calendar bridge)."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import requests

log = logging.getLogger("jobradar.sheets")


class SheetsError(RuntimeError):
    pass


class Sheets:
    def __init__(self, settings):
        self.url = settings.apps_script_url
        self.secret = settings.apps_script_secret
        self.enabled = bool(self.url and self.secret)

    def _call_sync(self, action: str, payload: dict | None = None, timeout: int = 60) -> Any:
        if not self.enabled:
            raise SheetsError("Apps Script is not configured")
        body = {"secret": self.secret, "action": action, **(payload or {})}
        last = ""
        for attempt in range(3):
            # Apps Script answers POST with a 302 to script.googleusercontent.com; requests follows it with GET.
            try:
                r = requests.post(self.url, json=body, timeout=timeout, allow_redirects=True)
            except requests.RequestException as e:
                last = f"{type(e).__name__}: {e}"
                time.sleep(3 + 5 * attempt)
                continue
            if r.status_code == 200:
                try:
                    data = r.json()
                    if isinstance(data, dict):
                        break
                    last = f"Unexpected reply: {str(data)[:150]}"
                except ValueError:
                    last = f"Not JSON (is the web app deployed with access 'Anyone'?): {r.text[:150]}"
            else:
                last = f"HTTP {r.status_code}: {r.text[:150]}"
            time.sleep(3 + 5 * attempt)
        else:
            raise SheetsError(last)
        if not data.get("ok"):
            raise SheetsError(data.get("error") or "unknown error")
        return data.get("result")

    async def call(self, action: str, payload: dict | None = None, timeout: int = 60) -> Any:
        return await asyncio.to_thread(self._call_sync, action, payload, timeout)

    async def safe(self, action: str, payload: dict | None = None) -> Any:
        """Same as call() but never raises (logs instead) — for non-critical writes."""
        if not self.enabled:
            return None
        try:
            return await self.call(action, payload)
        except Exception as e:  # noqa: BLE001
            log.warning("Apps Script %s failed: %s", action, e)
            return None
