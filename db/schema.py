import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def get_storage_root() -> Path:
    """Uses Railway persistent volume if mounted, otherwise local project root."""
    vol = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH") or os.environ.get("DATA_DIR")
    if vol:
        p = Path(vol)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return PROJECT_ROOT


DEFAULT_DB_PATH = get_storage_root() / "db" / "state.sqlite"
DEFAULT_OUTPUT_DIR = get_storage_root() / "output"


def get_public_base_url(config: dict) -> str:
    """Resolves public base URL from env (`PUBLIC_BASE_URL` or `RAILWAY_PUBLIC_DOMAIN`) or config."""
    if os.environ.get("PUBLIC_BASE_URL"):
        return os.environ["PUBLIC_BASE_URL"].rstrip("/")
    if os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
        return f"https://{os.environ['RAILWAY_PUBLIC_DOMAIN'].rstrip('/')}"
    return config.get("public_base_url", "https://audit.aegissas.io").rstrip("/")


@contextmanager
def get_connection(db_path: Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                channel TEXT NOT NULL,
                source_url TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                target_app_url TEXT,
                author_handle TEXT,
                intent_score INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT,
                discovered_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS prospects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                slug TEXT NOT NULL UNIQUE,
                company_name TEXT NOT NULL,
                app_url TEXT NOT NULL UNIQUE,
                founder_handle TEXT,
                contact_email TEXT,
                social_url TEXT,
                source_channel TEXT NOT NULL,
                intent_score INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'DISCOVERED',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS audits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
                evidence_id TEXT NOT NULL UNIQUE,
                sha256_digest TEXT NOT NULL,
                engine_used TEXT NOT NULL,
                total_issues INTEGER NOT NULL,
                unlocked_issue_json TEXT NOT NULL,
                locked_issues_json TEXT NOT NULL,
                audited_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS campaigns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
                audit_id INTEGER NOT NULL REFERENCES audits(id) ON DELETE CASCADE,
                report_html_path TEXT NOT NULL,
                outreach_subject TEXT NOT NULL DEFAULT 'Quick bug flag on your live signup flow',
                outreach_message TEXT NOT NULL,
                mirofish_score REAL NOT NULL,
                persona_feedback_json TEXT NOT NULL,
                approved_by_human INTEGER NOT NULL DEFAULT 0 CHECK (approved_by_human IN (0, 1)),
                human_approver_timestamp TEXT,
                delivery_channel TEXT DEFAULT 'ses_webhook',
                ses_message_id TEXT,
                tracker_status TEXT NOT NULL DEFAULT 'QUEUED',
                status TEXT NOT NULL DEFAULT 'PENDING_APPROVAL' CHECK (
                    status != 'SENT' OR (approved_by_human = 1 AND human_approver_timestamp IS NOT NULL)
                ),
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prospect_id INTEGER NOT NULL REFERENCES prospects(id) ON DELETE CASCADE,
                week_label TEXT NOT NULL,
                deploys_tested INTEGER NOT NULL DEFAULT 14,
                regressions_caught INTEGER NOT NULL DEFAULT 2,
                poached_leads_count INTEGER NOT NULL DEFAULT 0,
                poached_leads_json TEXT NOT NULL,
                roi_report_path TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        existing_p_cols = {r["name"] for r in conn.execute("PRAGMA table_info(prospects);").fetchall()}
        if "contact_email" not in existing_p_cols:
            conn.execute("ALTER TABLE prospects ADD COLUMN contact_email TEXT;")
        if "social_url" not in existing_p_cols:
            conn.execute("ALTER TABLE prospects ADD COLUMN social_url TEXT;")

        existing_c_cols = {r["name"] for r in conn.execute("PRAGMA table_info(campaigns);").fetchall()}
        if "outreach_subject" not in existing_c_cols:
            conn.execute(
                "ALTER TABLE campaigns ADD COLUMN outreach_subject TEXT NOT NULL DEFAULT 'Quick bug flag on your live app';"
            )
        if "delivery_channel" not in existing_c_cols:
            conn.execute("ALTER TABLE campaigns ADD COLUMN delivery_channel TEXT DEFAULT 'ses_webhook';")
        if "ses_message_id" not in existing_c_cols:
            conn.execute("ALTER TABLE campaigns ADD COLUMN ses_message_id TEXT;")
        if "tracker_status" not in existing_c_cols:
            conn.execute("ALTER TABLE campaigns ADD COLUMN tracker_status TEXT NOT NULL DEFAULT 'QUEUED';")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
