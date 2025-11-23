# core/polygon_client.py
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import requests


log = logging.getLogger(__name__)


class PolygonClient:
    BASE_URL = "https://api.polygon.io"

    def __init__(self, api_key: str, timeout: float = 5.0, max_retries: int = 3):
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if params is None:
            params = {}
        params.setdefault("apiKey", self.api_key)

        url = f"{self.BASE_URL}{path}"
        last_exc: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.get(url, params=params, timeout=self.timeout)
                if resp.status_code >= 500:
                    raise RuntimeError(f"Server error {resp.status_code}: {resp.text}")
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                log.warning("Polygon GET failed (%s) attempt %s: %s", path, attempt, exc)

        raise RuntimeError(f"Polygon GET failed after {self.max_retries} tries: {last_exc}")
