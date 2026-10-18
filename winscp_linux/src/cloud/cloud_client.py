"""
Cloud API Client for WinSCP-Linux.
Handles uploading and downloading encrypted Base64 session payloads with a 7-day retention period.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

DEFAULT_CLOUD_URL = os.environ.get("WINSCP_CLOUD_URL", "https://api.winscp-linux.cloud")


@dataclass
class UploadResult:
    share_id: str
    share_url: str
    expires_in_days: int = 7
    raw_response: Optional[Dict[str, Any]] = None


class CloudAPIError(Exception):
    """Raised when cloud API request fails."""


class CloudClient:
    """
    HTTP client for interacting with the encrypted configuration cloud sync service.
    Features automatic fallback to a local mock repository for demonstration and offline testing.
    """

    # In-memory mock storage shared across client instances for testing/demo
    _local_mock_store: Dict[str, Dict[str, Any]] = {}

    def __init__(self, base_url: str = DEFAULT_CLOUD_URL, timeout: float = 10.0, use_mock_fallback: bool = True):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.use_mock_fallback = use_mock_fallback

    def upload_encrypted_config(
        self,
        b64_payload: str,
        retention_days: int = 7,
    ) -> UploadResult:
        """
        Uploads an encrypted Base64 payload to the cloud service.

        Endpoint: POST /api/v1/config/upload
        Body: {"payload": b64_payload, "expires_in_days": retention_days}
        """
        if not b64_payload:
            raise ValueError("Payload cannot be empty.")

        url = f"{self.base_url}/api/v1/config/upload"
        req_data = {
            "payload": b64_payload,
            "expires_in_days": retention_days,
            "retention": f"{retention_days}d",
        }

        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=req_data)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    share_id = data.get("share_id") or data.get("id") or str(uuid.uuid4())[:8]
                    share_url = data.get("share_url") or f"{self.base_url}/s/{share_id}"
                    return UploadResult(share_id=share_id, share_url=share_url, expires_in_days=retention_days, raw_response=data)
                else:
                    raise CloudAPIError(f"Cloud upload failed (HTTP {resp.status_code}): {resp.text}")
        except (httpx.RequestError, httpx.HTTPStatusError) as exc:
            if self.use_mock_fallback:
                # Store in local mock store so offline testing / demonstration continues seamlessly
                share_id = f"demo-{uuid.uuid4().hex[:8]}"
                share_url = f"{self.base_url}/s/{share_id}"
                self._local_mock_store[share_id] = {
                    "payload": b64_payload,
                    "created_at": time.time(),
                    "expires_in_days": retention_days,
                }
                return UploadResult(
                    share_id=share_id,
                    share_url=share_url,
                    expires_in_days=retention_days,
                    raw_response={"mock": True, "notice": "Stored in local session memory (cloud endpoint offline)"},
                )
            raise CloudAPIError(f"Network error connecting to cloud service: {exc}") from exc

    def download_encrypted_config(self, share_id: str) -> str:
        """
        Retrieves the Base64 encrypted payload for the given share_id.

        Endpoint: GET /api/v1/config/download/<share_id> or /api/v1/config/<share_id>
        """
        clean_id = share_id.strip()
        if not clean_id:
            raise ValueError("Share ID cannot be empty.")

        # Check mock store first if it exists
        if clean_id in self._local_mock_store:
            return self._local_mock_store[clean_id]["payload"]

        # Try download endpoint
        endpoints = [
            f"{self.base_url}/api/v1/config/download/{clean_id}",
            f"{self.base_url}/api/v1/config/{clean_id}",
            f"{self.base_url}/download/{clean_id}",
        ]

        last_error = None
        for url in endpoints:
            try:
                with httpx.Client(timeout=self.timeout) as client:
                    resp = client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        payload = data.get("payload")
                        if payload:
                            return payload
                        elif isinstance(data, str):
                            return data
                    elif resp.status_code == 404:
                        continue
            except Exception as e:
                last_error = e

        if clean_id in self._local_mock_store:
            return self._local_mock_store[clean_id]["payload"]

        raise CloudAPIError(f"Share ID '{clean_id}' not found or could not be downloaded ({last_error or '404 Not Found'}).")
