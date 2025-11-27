from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .config import get_db_path


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


def get_connection() -> sqlite3.Connection:
    """
    Open a SQLite connection with sensible defaults for this project.
    """
    db_path = get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def session() -> Iterable[sqlite3.Connection]:
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    full_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'analyst',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    client_name TEXT,
    market TEXT,
    objective TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (owner_user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS kol_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    publish_link TEXT NOT NULL,
    platform TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    raw_payload TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (campaign_id, publish_link),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS influencers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    handle TEXT NOT NULL,
    platform TEXT NOT NULL,
    follower_count INTEGER,
    demographics_json TEXT,
    last_seen_at TEXT NOT NULL,
    UNIQUE (handle, platform)
);

CREATE TABLE IF NOT EXISTS campaign_influencers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id INTEGER NOT NULL,
    influencer_id INTEGER NOT NULL,
    reach_score REAL,
    interest_score REAL,
    engagement_rate REAL,
    engagement_score REAL,
    content_balance REAL,
    organic_posts_l2m REAL,
    sponsored_posts_l2m REAL,
    saturation_rate REAL,
    content_originality REAL,
    content_creativity REAL,
    content_score REAL,
    authority_credentials REAL,
    authority_professionalism REAL,
    authority_reputation REAL,
    authority_score REAL,
    values_alignment REAL,
    values_neutrality REAL,
    values_authenticity REAL,
    values_score REAL,
    total_score REAL,
    qualitative_notes TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (campaign_id, influencer_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE,
    FOREIGN KEY (influencer_id) REFERENCES influencers(id) ON DELETE CASCADE
);
"""


def init_db() -> None:
    """
    Initialize the SQLite database with the expected schema.
    """
    with session() as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def _row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def create_user(email: str, full_name: str, password_hash: str, role: str = "analyst") -> int:
    now = _now()
    normalized_email = email.strip().lower()
    with session() as conn:
        cur = conn.execute(
            """
            INSERT INTO users (email, full_name, password_hash, role, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (normalized_email, full_name.strip(), password_hash, role, now),
        )
        conn.commit()
        return cur.lastrowid


def get_user_by_email(email: str) -> Optional[Dict[str, Any]]:
    normalized_email = email.strip().lower()
    with session() as conn:
        cur = conn.execute("SELECT * FROM users WHERE email = ?", (normalized_email,))
        return _row_to_dict(cur.fetchone())


def create_campaign(owner_user_id: int, name: str, client_name: str, market: str, objective: str) -> int:
    now = _now()
    with session() as conn:
        cur = conn.execute(
            """
            INSERT INTO campaigns (owner_user_id, name, client_name, market, objective, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (owner_user_id, name.strip(), client_name.strip(), market.strip(), objective.strip(), now),
        )
        conn.commit()
        return cur.lastrowid


def list_campaigns_for_user(owner_user_id: int) -> List[Dict[str, Any]]:
    with session() as conn:
        cur = conn.execute(
            """
            SELECT * FROM campaigns
            WHERE owner_user_id = ?
            ORDER BY created_at DESC
            """,
            (owner_user_id,),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def get_campaign(campaign_id: int) -> Optional[Dict[str, Any]]:
    with session() as conn:
        cur = conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))
        return _row_to_dict(cur.fetchone())


def add_kol_source(campaign_id: int, publish_link: str, platform: str, payload: Dict[str, Any], status: str = "ingested") -> None:
    now = _now()
    serialized_payload = json.dumps(payload)
    with session() as conn:
        conn.execute(
            """
            INSERT INTO kol_sources (campaign_id, publish_link, platform, status, raw_payload, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(campaign_id, publish_link) DO UPDATE SET
                platform = excluded.platform,
                status = excluded.status,
                raw_payload = excluded.raw_payload,
                updated_at = excluded.updated_at
            """,
            (campaign_id, publish_link.strip(), platform, status, serialized_payload, now, now),
        )
        conn.commit()


def list_kol_sources(campaign_id: int) -> List[Dict[str, Any]]:
    with session() as conn:
        cur = conn.execute(
            """
            SELECT * FROM kol_sources
            WHERE campaign_id = ?
            ORDER BY created_at DESC
            """,
            (campaign_id,),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def list_all_influencers(search: Optional[str] = None, limit: int = 500) -> List[Dict[str, Any]]:
    """
    List influencers across the entire workspace for reuse.
    """
    search_term = f"%{search.strip().lower()}%" if search else None
    with session() as conn:
        if search_term:
            cur = conn.execute(
                """
                SELECT id, name, handle, platform, follower_count, last_seen_at
                FROM influencers
                WHERE lower(name) LIKE ?
                   OR lower(handle) LIKE ?
                   OR lower(platform) LIKE ?
                ORDER BY last_seen_at DESC
                LIMIT ?
                """,
                (search_term, search_term, search_term, limit),
            )
        else:
            cur = conn.execute(
                """
                SELECT id, name, handle, platform, follower_count, last_seen_at
                FROM influencers
                ORDER BY last_seen_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [_row_to_dict(row) for row in cur.fetchall()]


def upsert_influencer(profile: Dict[str, Any]) -> Dict[str, Any]:
    now = _now()
    handle = profile["handle"].strip().lower()
    platform = profile.get("platform", "Unknown").strip()
    demographics = profile.get("demographics") or {}
    with session() as conn:
        conn.execute(
            """
            INSERT INTO influencers (name, handle, platform, follower_count, demographics_json, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(handle, platform) DO UPDATE SET
                name = excluded.name,
                follower_count = excluded.follower_count,
                demographics_json = excluded.demographics_json,
                last_seen_at = excluded.last_seen_at
            """,
            (
                profile.get("name", profile["handle"]).strip(),
                handle,
                platform,
                profile.get("follower_count"),
                json.dumps(demographics),
                now,
            ),
        )
        conn.commit()
        cur = conn.execute(
            "SELECT * FROM influencers WHERE handle = ? AND platform = ?",
            (handle, platform),
        )
        return _row_to_dict(cur.fetchone())


def ensure_campaign_influencer(campaign_id: int, influencer_id: int) -> Dict[str, Any]:
    now = _now()
    with session() as conn:
        conn.execute(
            """
            INSERT INTO campaign_influencers (campaign_id, influencer_id, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(campaign_id, influencer_id) DO UPDATE SET
                updated_at = excluded.updated_at
            """,
            (campaign_id, influencer_id, now, now),
        )
        conn.commit()
        cur = conn.execute(
            """
            SELECT
                ci.id AS campaign_influencer_id,
                ci.campaign_id,
                ci.influencer_id,
                ci.reach_score,
                ci.interest_score,
                ci.engagement_rate,
                ci.engagement_score,
                ci.content_balance,
                ci.organic_posts_l2m,
                ci.sponsored_posts_l2m,
                ci.saturation_rate,
                ci.content_originality,
                ci.content_creativity,
                ci.content_score,
                ci.authority_score,
                ci.values_score,
                ci.total_score,
                ci.qualitative_notes,
                ci.created_at,
                ci.updated_at,
                i.name,
                i.handle,
                i.platform,
                i.follower_count,
                i.demographics_json
            FROM campaign_influencers ci
            JOIN influencers i ON i.id = ci.influencer_id
            WHERE ci.campaign_id = ? AND ci.influencer_id = ?
            """,
            (campaign_id, influencer_id),
        )
        return _row_to_dict(cur.fetchone())


SCORE_COLUMNS = [
    "reach_score",
    "interest_score",
    "engagement_rate",
    "engagement_score",
    "content_balance",
    "organic_posts_l2m",
    "sponsored_posts_l2m",
    "saturation_rate",
    "content_originality",
    "content_creativity",
    "content_score",
    "authority_credentials",
    "authority_professionalism",
    "authority_reputation",
    "authority_score",
    "values_alignment",
    "values_neutrality",
    "values_authenticity",
    "values_score",
    "total_score",
    "qualitative_notes",
]


def save_campaign_influencer_scores(campaign_influencer_id: int, payload: Dict[str, Any]) -> None:
    now = _now()
    assignments = ", ".join(f"{column} = ?" for column in SCORE_COLUMNS)
    values = [payload.get(column) for column in SCORE_COLUMNS]
    values.append(now)
    values.append(campaign_influencer_id)
    with session() as conn:
        conn.execute(
            f"""
            UPDATE campaign_influencers
            SET {assignments}, updated_at = ?
            WHERE id = ?
            """,
            values,
        )
        conn.commit()


def list_campaign_influencers(campaign_id: int) -> List[Dict[str, Any]]:
    with session() as conn:
        cur = conn.execute(
            """
            SELECT
                ci.id AS campaign_influencer_id,
                i.name,
                i.handle,
                i.platform,
                i.follower_count,
                i.demographics_json,
                ci.reach_score,
                ci.interest_score,
                ci.engagement_rate,
                ci.engagement_score,
                ci.content_balance,
                ci.organic_posts_l2m,
                ci.sponsored_posts_l2m,
                ci.saturation_rate,
                ci.content_originality,
                ci.content_creativity,
                ci.content_score,
                ci.authority_score,
                ci.values_score,
                ci.total_score,
                ci.qualitative_notes,
                ci.updated_at
            FROM campaign_influencers ci
            JOIN influencers i ON i.id = ci.influencer_id
            WHERE ci.campaign_id = ?
            ORDER BY i.name ASC
            """,
            (campaign_id,),
        )
        return [_row_to_dict(row) for row in cur.fetchall()]


def list_dashboard_rows(campaign_id: int) -> List[Dict[str, Any]]:
    """
    Convenience wrapper for dashboard consumption.
    """
    return list_campaign_influencers(campaign_id)
