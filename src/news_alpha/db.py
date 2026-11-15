"""Étape 7 : stockage SQLite, une table par étape (SPEC §5)."""

import sqlite3
from datetime import datetime, timezone

from news_alpha import ROOT
from news_alpha.score import MODEL, PROMPT_VERSION

DB_PATH = ROOT / "data" / "news_alpha.sqlite"

# Chaque table porte created_at (UTC), prompt_version et model (SPEC §5, étape 7).
SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
    accession TEXT PRIMARY KEY,
    cik INTEGER NOT NULL,
    company TEXT,
    form TEXT,
    filing_date TEXT,
    acceptance_et TEXT,
    items TEXT,
    ticker TEXT,
    exchange TEXT,
    sic INTEGER,
    shares_out REAL,
    prev_close REAL,
    market_cap REAL,
    adv20_usd REAL,
    selected INTEGER NOT NULL,
    reject_reason TEXT,
    text_chars INTEGER,
    text_truncated INTEGER,
    created_at TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scores (
    id INTEGER PRIMARY KEY,
    accession TEXT NOT NULL,
    cik INTEGER NOT NULL,
    valid INTEGER NOT NULL,
    reject_reason TEXT,
    raw_output TEXT,
    event_type TEXT,
    direction INTEGER,
    magnitude INTEGER,
    confidence REAL,
    priced_in REAL,
    horizon_days INTEGER,
    key_facts TEXT,
    rationale TEXT,
    composite REAL,
    price_move REAL,
    price_move_timing TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    latency_s REAL,
    prompt_sha256 TEXT,
    created_at TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    UNIQUE (accession, prompt_version, model)
);
CREATE TABLE IF NOT EXISTS prices (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    cik INTEGER,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    PRIMARY KEY (symbol, date, adjustment)
);
CREATE TABLE IF NOT EXISTS returns (
    accession TEXT NOT NULL,
    horizon INTEGER NOT NULL,
    cik INTEGER NOT NULL,
    entry_date TEXT,
    exit_date TEXT,
    early_exit INTEGER,
    ret REAL,
    iwm_ret REAL,
    abnormal_iwm REAL,
    sector_etf TEXT,
    sector_ret REAL,
    abnormal_sector REAL,
    created_at TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    model TEXT NOT NULL,
    PRIMARY KEY (accession, horizon)
);
"""

# Colonnes ajoutées après la création de bases existantes (phase 1, 25/10/2026) ;
# les lignes plus anciennes les gardent vides.
ADDED_COLUMNS = {
    "scores": {"price_move_timing": "TEXT", "prompt_sha256": "TEXT"},
    "returns": {"early_exit": "INTEGER"},
}


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, columns in ADDED_COLUMNS.items():
        present = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    return conn


def upsert(conn: sqlite3.Connection, table: str, row: dict) -> None:
    """INSERT OR REPLACE d'une ligne, horodatée avec la version du prompt et du modèle."""
    row = {
        **row,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
    }
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})", list(row.values())
    )
