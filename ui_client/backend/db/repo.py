from __future__ import annotations
import sqlite3
from typing import Dict, List, Optional, Tuple, Any

class Repo:
    def __init__(self, db_path: str, api_url: str = None, api_session: Any = None):
        """Initialize Repo with optional backend API for PostgreSQL-based registry.
        
        Args:
            db_path: Path to local SQLite database (legacy, kept for counter management)
            api_url: Backend API URL (e.g., http://localhost:8000)
            api_session: requests.Session object for API calls
        """
        self.db_path = db_path
        self.api_url = api_url
        self.api_session = api_session

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def load_registry_from_backend(self) -> Optional[Dict]:
        """Load registry (parts + aliases) from PostgreSQL via backend API.
        
        Returns: {"parts": [...], "aliases": {...}} or None if API unavailable
        """
        if not self.api_url or not self.api_session:
            return None
        
        try:
            # Fetch all parts from backend
            resp = self.api_session.get(f"{self.api_url}/parts", timeout=20)
            resp.raise_for_status()
            records = resp.json() or []
            
            # Build registry from records
            parts = []
            aliases = {}
            
            for rec in records:
                external_id = rec.get("external_id", "")
                if not external_id:
                    continue
                
                part_name = rec.get("part_name", "")
                item_type = rec.get("item_type", "")
                data = rec.get("data", {}) or {}
                
                # Extract prefix and number from external_id (e.g., PS_000453 -> PS, 453)
                prefix = ""
                number = 0
                if "_" in external_id:
                    parts_split = external_id.split("_")
                    prefix = parts_split[0]
                    try:
                        number = int(parts_split[1])
                    except (ValueError, IndexError):
                        number = 0
                
                # Build part entry with normalized name
                from matching_logic.core.normalize import normalize_name, canonical_key as make_canonical_key
                name_norm = normalize_name(part_name)
                ckey = make_canonical_key(part_name, data.get("revision"))
                
                parts.append({
                    "external_id": external_id,
                    "name_norm": name_norm,
                    "canonical_key": ckey,
                    "item_type": item_type,
                    "module": prefix or "DEFAULT",
                    "revision": data.get("revision"),
                })
                
                # Add aliases for matching
                aliases[name_norm] = external_id
                aliases[ckey] = external_id
            
            # Sort by prefix and number for consistency
            parts.sort(key=lambda p: (p.get("module", ""), int(p.get("external_id", "").split("_")[1] or 0)))
            
            return {"parts": parts, "aliases": aliases}
        except Exception as e:
            print(f"Warning: Failed to load registry from backend: {e}")
            return None

    def load_registry(self) -> Dict:
        """Load registry from backend API if available, otherwise from SQLite.
        
        This is the main entry point for matching logic.
        """
        # Try backend first (PostgreSQL)
        backend_registry = self.load_registry_from_backend()
        if backend_registry is not None:
            return backend_registry
        
        # Fallback to SQLite (for offline or testing)
        conn = self._connect()
        try:
            aliases = {}
            for r in conn.execute("SELECT alias_norm, external_id FROM aliases"):
                aliases[r["alias_norm"]] = r["external_id"]

            parts = []
            for r in conn.execute(
                "SELECT external_id, name_norm, canonical_key, item_type, module, revision, prefix, number FROM parts ORDER BY prefix ASC, number ASC"
            ):
                parts.append(
                    {
                        "external_id": r["external_id"],
                        "name_norm": r["name_norm"],
                        "canonical_key": r["canonical_key"],
                        "item_type": r["item_type"],
                        "module": r["module"],
                        "revision": r["revision"],
                    }
                )
            return {"aliases": aliases, "parts": parts}
        finally:
            conn.close()

    def get_last_prefix(self, module: str) -> Optional[str]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT last_prefix FROM module_settings WHERE module = ?",
                (module,),
            ).fetchone()
            return (row["last_prefix"] if row is not None else None)
        finally:
            conn.close()

    def set_last_prefix(self, module: str, last_prefix: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO module_settings(module, last_prefix)
                VALUES(?,?)
                ON CONFLICT(module) DO UPDATE SET last_prefix=excluded.last_prefix
                """,
                (module, last_prefix),
            )
            conn.commit()
        finally:
            conn.close()

    def get_module_counter(self, module: str, prefix: str) -> int:
        """Read the last used number for a module/prefix.

        IMPORTANT: This is intentionally **read-only**.
        We do not create or mutate DB state until the user exports.
        """
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT last_number FROM module_counters WHERE module = ?",
                (module,),
            ).fetchone()
            return int(row["last_number"]) if row is not None else 0
        finally:
            conn.close()

    def upsert_module_counter(self, module: str, prefix: str, last_number: int) -> None:
        """Upsert a module counter.

        Used during export commit to persist the highest number used for each prefix.
        """
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO module_counters(module, prefix, last_number)
                VALUES(?,?,?)
                ON CONFLICT(module) DO UPDATE SET
                    prefix=excluded.prefix,
                    last_number=excluded.last_number
                """,
                (module, prefix, int(last_number)),
            )
            conn.commit()
        finally:
            conn.close()

    def commit_export(self, parts: List[Dict], counters: Dict[str, int]) -> None:
        """Persist exported parts + counters in a single transaction.

        This is the *only* place the application should write generated part IDs.
        """
        if not parts and not counters:
            return

        conn = self._connect()
        try:
            conn.execute("BEGIN")

            # Prevent duplicate IDs from being inserted
            existing_ids = {r["external_id"] for r in conn.execute("SELECT external_id FROM parts")}

            for p in parts:
                ext_id = p["external_id"]
                if ext_id in existing_ids:
                    raise ValueError(f"External ID already exists in DB: {ext_id}")
                existing_ids.add(ext_id)
                conn.execute(
                    """
                    INSERT INTO parts(
                        external_id, module, prefix, number, revision,
                        name_original, name_norm, canonical_key, item_type, price, description
                    )
                    VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        p["external_id"],
                        p["module"],
                        p["prefix"],
                        int(p["number"]),
                        p.get("revision"),
                        p["name_original"],
                        p["name_norm"],
                        p["canonical_key"],
                        p["item_type"],
                        p.get("price"),
                        p.get("description"),
                    ),
                )

                # Store both the full displayed name and the canonical key as aliases for matching.
                conn.execute(
                    "INSERT OR IGNORE INTO aliases(alias_norm, external_id) VALUES(?,?)",
                    (p["name_norm"], p["external_id"]),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO aliases(alias_norm, external_id) VALUES(?,?)",
                    (p["canonical_key"], p["external_id"]),
                )

            for prefix, last_number in counters.items():
                conn.execute(
                    """
                    INSERT INTO module_counters(module, prefix, last_number)
                    VALUES(?,?,?)
                    ON CONFLICT(module) DO UPDATE SET
                        prefix=excluded.prefix,
                        last_number=excluded.last_number
                    """,
                    (prefix, prefix, int(last_number)),
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def increment_module_counter(self, module: str, prefix: str) -> int:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT last_number FROM module_counters WHERE module = ?",
                (module,),
            ).fetchone()
            if row is None:
                new_num = 1
                conn.execute(
                    "INSERT INTO module_counters(module, prefix, last_number) VALUES(?,?,?)",
                    (module, prefix, new_num),
                )
            else:
                new_num = int(row["last_number"]) + 1
                conn.execute(
                    "UPDATE module_counters SET last_number = ?, prefix = ? WHERE module = ?",
                    (new_num, prefix, module),
                )
            conn.commit()
            return new_num
        finally:
            conn.close()

    def insert_part(
        self,
        external_id: str,
        module: str,
        prefix: str,
        number: int,
        revision: str | None,
        name_original: str,
        name_norm: str,
        canonical_key: str,
        item_type: str,
        price: float | None,
        description: str | None,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO parts(
                    external_id, module, prefix, number, revision,
                    name_original, name_norm, canonical_key, item_type, price, description
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    external_id,
                    module,
                    prefix,
                    number,
                    revision,
                    name_original,
                    name_norm,
                    canonical_key,
                    item_type,
                    price,
                    description,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def upsert_alias(self, alias_norm: str, external_id: str) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """
                INSERT INTO aliases(alias_norm, external_id)
                VALUES(?,?)
                ON CONFLICT(alias_norm) DO UPDATE SET external_id=excluded.external_id
                """,
                (alias_norm, external_id),
            )
            conn.commit()
        finally:
            conn.close()

    def list_item_categories(self) -> List[str]:
        """Return the user-maintained list of item categories."""
        conn = self._connect()
        try:
            rows = conn.execute("SELECT name FROM item_categories ORDER BY name ASC").fetchall()
            return [r["name"] for r in rows]
        finally:
            conn.close()

    def add_item_category(self, name: str) -> None:
        """Add a new category name (no-op if it already exists)."""
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO item_categories(name) VALUES (?)",
                (name,),
            )
            conn.commit()
        finally:
            conn.close()


    def remove_item_category(self, name: str) -> None:
        """Remove a category name (no-op if it doesn't exist).

        Note: This only deletes the category from the user-maintained list. Any already-assigned
        values in the current UI rows should be cleared by the caller if needed.
        """
        conn = self._connect()
        try:
            conn.execute("DELETE FROM item_categories WHERE name = ?", (name,))
            conn.commit()
        finally:
            conn.close()

    # -----------------
    # DEV/TEST helpers
    # -----------------
    def reset_module_counters(self) -> None:
        """Delete all module counters.

        This effectively restarts numbering for each prefix (PS/STD/...).
        In production, counters should never be reset.
        """
        conn = self._connect()
        try:
            conn.execute("DELETE FROM module_counters")
            conn.commit()
        finally:
            conn.close()

    def get_all_parts_sorted(self) -> List[Dict]:
        """Get all parts sorted sequentially by prefix and number.
        
        Returns parts in order: PS_0001, PS_0002, ..., MD_0001, MD_0002, ..., STD_0001, ...
        This is useful for visualization in database viewers and reports.
        """
        conn = self._connect()
        try:
            parts = []
            for r in conn.execute(
                """SELECT external_id, prefix, number, name_original, name_norm, 
                          canonical_key, item_type, module, revision, price, description 
                   FROM parts 
                   ORDER BY prefix ASC, number ASC"""
            ):
                parts.append({
                    "external_id": r["external_id"],
                    "prefix": r["prefix"],
                    "number": r["number"],
                    "name_original": r["name_original"],
                    "name_norm": r["name_norm"],
                    "canonical_key": r["canonical_key"],
                    "item_type": r["item_type"],
                    "module": r["module"],
                    "revision": r["revision"],
                    "price": r["price"],
                    "description": r["description"],
                })
            return parts
        finally:
            conn.close()

    def get_part_by_external_id(self, external_id: str) -> Optional[Dict]:
        """Get a single part by its external_id."""
        conn = self._connect()
        try:
            r = conn.execute(
                """SELECT external_id, prefix, number, name_original, name_norm, 
                          canonical_key, item_type, module, revision, price, description, qty, status
                   FROM parts 
                   WHERE external_id = ?""",
                (external_id,)
            ).fetchone()
            if r is None:
                return None
            return {
                "external_id": r["external_id"],
                "prefix": r["prefix"],
                "number": r["number"],
                "name_original": r["name_original"],
                "name_norm": r["name_norm"],
                "canonical_key": r["canonical_key"],
                "item_type": r["item_type"],
                "module": r["module"],
                "revision": r["revision"],
                "price": r["price"],
                "description": r["description"],
            }
        finally:
            conn.close()
