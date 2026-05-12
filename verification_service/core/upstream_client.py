"""
upstream_client.py  (verification_service)
Identical interface to capturing_service/core/upstream_client.py.
Kept as a separate copy so the two services remain fully independent.
"""

import httpx
from typing import Any, Dict, List, Optional
from fastapi import HTTPException

_BASE_HEADERS = {"Content-Type": "application/json"}


def _raise_on_error(resp: httpx.Response, context: str) -> None:
    if resp.status_code < 400:
        return
    try:
        msg = resp.json().get("message", resp.text)
    except Exception:
        msg = resp.text
    raise HTTPException(status_code=resp.status_code, detail=f"[{context}] {msg}")


class MasterClient:
    def __init__(self, server_url: str, public_key: str, private_key: str):
        self.base = server_url.rstrip("/")
        self.headers = {**_BASE_HEADERS, "Identity": public_key, "Secret": private_key}

    async def get_domains(self) -> List[Dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.base}/api/v1/domain/all", headers=self.headers)
        _raise_on_error(resp, "get_domains")
        body = resp.json()
        if not body.get("success"):
            raise HTTPException(status_code=400, detail=body.get("message", "Master error"))
        return body.get("data", [])


class DomainClient:
    def __init__(self, host: str, identity: str, secret: str):
        self.base = host.rstrip("/")
        self.headers = {**_BASE_HEADERS, "Identity": identity, "Secret": secret}

    async def get_departments(self) -> List[Dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.base}/api/biometric/departments", headers=self.headers)
        _raise_on_error(resp, "get_departments")
        return resp.json()

    async def get_programme_types(self) -> List[Dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self.base}/api/biometric/programmes-types", headers=self.headers)
        _raise_on_error(resp, "get_programme_types")
        return resp.json()

    async def get_levels(self, programme_type_id: int) -> List[Dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base}/api/biometric/levels",
                headers=self.headers,
                params={"programmeType": programme_type_id},
            )
        _raise_on_error(resp, "get_levels")
        return resp.json()

    async def get_users(
        self,
        department_id: int,
        level_id: Optional[int] = None,
        search: Optional[str] = None,
    ) -> List[Dict]:
        params: Dict[str, Any] = {"department": department_id}
        if level_id is not None:
            params["level"] = level_id
        if search:
            params["user"] = search
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base}/api/biometric/users",
                headers=self.headers,
                params=params,
            )
        _raise_on_error(resp, "get_users")
        return resp.json()
