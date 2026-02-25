from __future__ import annotations

from typing import Dict, List, Optional, Any


class Repo:
    """API-only repository (PostgreSQL backend).

    The UI talks to the FastAPI backend; no local SQLite is used.
    """

    def __init__(self, api_url: str, api_session: Any):
        self.api_url = api_url
        self.api_session = api_session

    # ---------------- Parts: candidate search (scalable matching) ----------------
    def get_part_candidates(
        self,
        name: str,
        internal_reference: Optional[str] = None,
        item_type: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Return a small candidate list for a single EBOM row."""
        if not self.api_url or not self.api_session:
            return []
        try:
            resp = self.api_session.get(
                f"{self.api_url}/parts/candidates",
                params={
                    "name": name,
                    "internal_reference": internal_reference,
                    "item_type": item_type,
                    "limit": int(limit),
                },
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json() or []
        except Exception:
            return []

    def get_part_candidates_bulk(
        self,
        queries: List[Dict[str, Any]],
        global_limit: int = 50,
    ) -> List[List[Dict[str, Any]]]:
        """Return candidate lists aligned with the given `queries` order.

        Each query dict should look like:
          {"name": "...", "internal_reference": "...", "item_type": "...", "limit": 50}
        """
        if not self.api_url or not self.api_session:
            return [[] for _ in queries]
        try:
            resp = self.api_session.post(
                f"{self.api_url}/parts/candidates/bulk",
                json={"queries": queries, "global_limit": int(global_limit)},
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            return data if isinstance(data, list) else [[] for _ in queries]
        except Exception:
            return [[] for _ in queries]

    # ---------------- UI settings ----------------
    def get_last_prefix(self, module: str = "DEFAULT") -> Optional[str]:
        if not self.api_url or not self.api_session:
            return None
        try:
            resp = self.api_session.get(f"{self.api_url}/ui/last_prefix", params={"module": module}, timeout=10)
            resp.raise_for_status()
            data = resp.json() or {}
            return data.get("last_prefix")
        except Exception:
            return None

    def set_last_prefix(self, prefix: str, module: str = "DEFAULT") -> None:
        if not self.api_url or not self.api_session:
            return
        try:
            self.api_session.put(
                f"{self.api_url}/ui/last_prefix",
                params={"module": module},
                json={"last_prefix": str(prefix)},
                timeout=10,
            )
        except Exception:
            pass

    def list_item_categories(self) -> List[str]:
        if not self.api_url or not self.api_session:
            return []
        try:
            resp = self.api_session.get(f"{self.api_url}/ui/item_categories", timeout=10)
            resp.raise_for_status()
            data = resp.json() or []
            return list(data) if isinstance(data, list) else []
        except Exception:
            return []

    def add_item_category(self, name: str) -> None:
        if not self.api_url or not self.api_session:
            return
        try:
            self.api_session.post(f"{self.api_url}/ui/item_categories", json={"name": str(name)}, timeout=10)
        except Exception:
            pass

    def remove_item_category(self, name: str) -> None:
        if not self.api_url or not self.api_session:
            return
        try:
            self.api_session.delete(f"{self.api_url}/ui/item_categories/{name}", timeout=10)
        except Exception:
            pass

    # ---------------- Admin / dev helpers ----------------
    def reset_module_counters(self) -> None:
        """Reset ID counters in PostgreSQL backend (dev only)."""
        if not self.api_url or not self.api_session:
            return
        try:
            self.api_session.post(f"{self.api_url}/admin/reset_counters", timeout=20)
        except Exception:
            pass
