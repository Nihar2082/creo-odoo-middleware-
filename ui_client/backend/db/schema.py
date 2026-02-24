import sqlite3

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS parts (
  external_id TEXT PRIMARY KEY,
  module TEXT NOT NULL,
  prefix TEXT NOT NULL,
  number INTEGER NOT NULL,
  revision TEXT,
  name_original TEXT NOT NULL,
  name_norm TEXT NOT NULL,
  canonical_key TEXT NOT NULL,
  item_type TEXT NOT NULL,
  price REAL,
  description TEXT
);

-- Simple settings store (used for UX helpers like remembering the last prefix).
CREATE TABLE IF NOT EXISTS module_settings (
  module TEXT PRIMARY KEY,
  last_prefix TEXT
);

CREATE TABLE IF NOT EXISTS aliases (
  alias_norm TEXT PRIMARY KEY,
  external_id TEXT NOT NULL,
  FOREIGN KEY(external_id) REFERENCES parts(external_id)
);

CREATE TABLE IF NOT EXISTS module_counters (
  module TEXT PRIMARY KEY,
  prefix TEXT NOT NULL,
  last_number INTEGER NOT NULL
);

-- Category list for user-driven "Type of Item" classification
CREATE TABLE IF NOT EXISTS item_categories (
  name TEXT PRIMARY KEY
);
"""

def init_db(db_path: str) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)

        # Lightweight migrations for existing DBs (safe no-ops on fresh installs).
        cur = conn.cursor()
        cols = [r[1] for r in cur.execute("PRAGMA table_info(parts)").fetchall()]
        if "canonical_key" not in cols:
            cur.execute("ALTER TABLE parts ADD COLUMN canonical_key TEXT")
            # Backfill to something reasonable for old rows.
            cur.execute("UPDATE parts SET canonical_key = name_norm || '|' || item_type WHERE canonical_key IS NULL")
            conn.commit()
        if "price" not in cols:
            cur.execute("ALTER TABLE parts ADD COLUMN price REAL")
            conn.commit()
        # Seed default categories if table is empty
        cur.execute("SELECT COUNT(1) FROM item_categories")
        if (cur.fetchone() or [0])[0] == 0:
            cur.executemany(
                "INSERT INTO item_categories(name) VALUES (?)",
                [("Manufactured",), ("Bought Part",)],
            )
        conn.commit()
    finally:
        conn.close()
