"""RepoRunner REST and SSE API Client."""

from __future__ import annotations

import json
from typing import Any, Dict, Generator, Optional

import httpx

BASE_TIMEOUT = 10.0


def _get_base_url() -> str:
    """Retrieve backend URL from configuration."""
    from .config import load_config
    cfg = load_config()
    return cfg.get("backend_url", "http://localhost:8000")


def _client(timeout: float = BASE_TIMEOUT) -> httpx.Client:
    return httpx.Client(base_url=_get_base_url(), timeout=timeout)


def request(
    method: str,
    path: str,
    *,
    json_body: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: Optional[float] = None,
) -> Dict[str, Any]:
    """Perform an HTTP request to the backend."""
    t = timeout or (1.0 if path == "/health" else BASE_TIMEOUT)
    with _client(timeout=t) as client:
        resp = client.request(method, path, json=json_body, params=params)
        resp.raise_for_status()
        if resp.content:
            return resp.json()
        return {}


def stream(path: str, *, params: Optional[Dict[str, Any]] = None) -> Generator[str, None, None]:
    """Stream Server-Sent Events from the backend. Yields SSE data payload string."""
    with _client() as client:
        with client.stream("GET", path, params=params) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    data = line.decode().strip()
                except AttributeError:
                    data = line.strip()
                if data.startswith("data:"):
                    yield data[5:].strip()
