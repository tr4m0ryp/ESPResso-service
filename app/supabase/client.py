"""Async Supabase PostgREST client using httpx."""

import logging
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


class SupabaseClient:
    """Lightweight async client for Supabase PostgREST API.

    Uses the service_role key to bypass RLS -- the ESPResso service
    authenticates callers at its own API layer (Bearer token).
    """

    def __init__(self, settings: Settings) -> None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
            raise ValueError(
                "SUPABASE_URL and SUPABASE_SERVICE_KEY must be configured"
            )
        self._base_url = settings.SUPABASE_URL.rstrip("/")
        self._rest_url = f"{self._base_url}/rest/v1"
        self._headers = {
            "apikey": settings.SUPABASE_SERVICE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def select(
        self,
        table: str,
        columns: str = "*",
        params: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a PostgREST SELECT query.

        Args:
            table: Table name.
            columns: Comma-separated column selection.
            params: PostgREST filter parameters (e.g. {"id": "in.(a,b)"}).

        Returns:
            List of row dicts.
        """
        query_params = {"select": columns}
        if params:
            query_params.update(params)

        resp = await self._client.get(
            f"{self._rest_url}/{table}",
            params=query_params,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
        return resp.json()

    async def upsert(
        self,
        table: str,
        rows: list[dict[str, Any]],
        on_conflict: str = "",
    ) -> None:
        """Upsert rows into a table via PostgREST.

        Args:
            table: Table name.
            rows: List of row dicts to upsert.
            on_conflict: Comma-separated conflict columns for ON CONFLICT.
        """
        if not rows:
            return

        headers = {
            "Accept": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        }
        params = {}
        if on_conflict:
            params["on_conflict"] = on_conflict

        resp = await self._client.post(
            f"{self._rest_url}/{table}",
            json=rows,
            headers=headers,
            params=params,
        )
        resp.raise_for_status()

    async def insert_if_not_exists(
        self,
        table: str,
        rows: list[dict[str, Any]],
        on_conflict: str = "",
    ) -> None:
        """Insert rows, ignoring conflicts (no update on duplicate).

        Args:
            table: Table name.
            rows: List of row dicts to insert.
            on_conflict: Comma-separated conflict columns.
        """
        if not rows:
            return

        headers = {
            "Accept": "application/json",
            "Prefer": "resolution=ignore-duplicates,return=minimal",
        }
        params = {}
        if on_conflict:
            params["on_conflict"] = on_conflict

        resp = await self._client.post(
            f"{self._rest_url}/{table}",
            json=rows,
            headers=headers,
            params=params,
        )
        resp.raise_for_status()

    async def table_exists(self, table: str) -> bool:
        """Check if a table is accessible via PostgREST."""
        try:
            resp = await self._client.head(
                f"{self._rest_url}/{table}",
                params={"limit": "0"},
            )
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
