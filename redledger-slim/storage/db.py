"""
Clean SQLite WAL Storage Layer for RedLedger.
Thread-safe single/multi-threaded transaction execution with full idempotency support.
"""
from __future__ import annotations

import os
import json
import sqlite3
import threading
from typing import Any
from datetime import datetime

try:
    from core.engine import Round, BetEntry, Member, PlayerRoundSummary
except ImportError:
    from .engine import Round, BetEntry, Member, PlayerRoundSummary

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 15000;

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id TEXT NOT NULL,
    group_name TEXT DEFAULT '',
    title TEXT DEFAULT '',
    banker_member_id INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    trend TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    started_at TEXT NOT NULL,
    ended_at TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    round_no INTEGER NOT NULL,
    display_round_no INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    result TEXT DEFAULT '',
    route TEXT DEFAULT '',
    opened_at TEXT NOT NULL,
    sealed_at TEXT DEFAULT NULL,
    settled_at TEXT DEFAULT NULL,
    settlement_revision INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wxid TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    avatar_url TEXT DEFAULT '',
    is_banker INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    round_id INTEGER NOT NULL,
    member_id INTEGER NOT NULL,
    source_message_id TEXT DEFAULT '',
    sub_index INTEGER DEFAULT 0,
    raw_text TEXT NOT NULL,
    token TEXT DEFAULT '',
    normalized TEXT DEFAULT '',
    picks TEXT NOT NULL,
    play_type TEXT DEFAULT '',
    amount INTEGER NOT NULL,
    hit_count INTEGER DEFAULT 0,
    gross_result INTEGER DEFAULT 0,
    water INTEGER DEFAULT 0,
    net_result INTEGER DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'accepted',
    reject_reason TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    revoked_at TEXT DEFAULT NULL,
    revoke_reason TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS raw_inbound_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE,
    group_id TEXT NOT NULL DEFAULT '',
    sender_wxid TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'processed',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS report_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL,
    round_id INTEGER,
    source_group_id TEXT NOT NULL,
    target_group_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    artifact_path TEXT DEFAULT '',
    artifact_text TEXT DEFAULT '',
    render_snapshot TEXT DEFAULT '',
    render_ms INTEGER DEFAULT 0,
    trigger_at TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT '',
    sent_at TEXT DEFAULT NULL,
    error TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_rounds_session ON rounds(session_id, id);
CREATE INDEX IF NOT EXISTS idx_bets_round ON bets(round_id, status);
CREATE INDEX IF NOT EXISTS idx_report_jobs_status ON report_jobs(status, target_group_id, id);
"""

def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

class Database:
    def __init__(self, db_path: str, query_only: bool = False):
        self.db_path = db_path
        self.query_only = query_only
        self._lock = threading.RLock()
        self._conn = None
        self._init_db()

    def _init_db(self):
        with self._lock:
            conn = self.get_connection()
            if not self.query_only:
                conn.executescript(SCHEMA_SQL)
                # Ensure columns exist in case of legacy schema
                try:
                    conn.execute("ALTER TABLE sessions ADD COLUMN title TEXT DEFAULT ''")
                except Exception:
                    pass
                try:
                    conn.execute("ALTER TABLE report_jobs ADD COLUMN updated_at TEXT DEFAULT ''")
                except Exception:
                    pass
                try:
                    conn.execute("ALTER TABLE bets ADD COLUMN token TEXT DEFAULT ''")
                except Exception:
                    pass
                try:
                    conn.execute("ALTER TABLE bets ADD COLUMN normalized TEXT DEFAULT ''")
                except Exception:
                    pass
                try:
                    conn.execute("ALTER TABLE bets ADD COLUMN sub_index INTEGER DEFAULT 0")
                except Exception:
                    pass
                conn.commit()

    def get_connection(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                if self.query_only:
                    uri_path = f"file:{os.path.abspath(self.db_path)}?mode=ro"
                    self._conn = sqlite3.connect(uri_path, uri=True, timeout=15.0, check_same_thread=False)
                else:
                    self._conn = sqlite3.connect(self.db_path, timeout=15.0, check_same_thread=False)
                    if self.db_path != ":memory:":
                        self._conn.execute("PRAGMA journal_mode = WAL;")
                        self._conn.execute("PRAGMA synchronous = NORMAL;")
                        self._conn.execute("PRAGMA busy_timeout = 15000;")
                self._conn.row_factory = sqlite3.Row
            return self._conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            conn = self.get_connection()
            return conn.execute(sql, params)

    def executemany(self, sql: str, seq_of_params: list[tuple]) -> sqlite3.Cursor:
        with self._lock:
            conn = self.get_connection()
            return conn.executemany(sql, seq_of_params)

    def fetch_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            cur = self.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def fetch_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]

    def commit(self):
        with self._lock:
            if self._conn and not self.query_only:
                self._conn.commit()

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    # Domain helper methods
    def load_members(self) -> dict[int, Member]:
        rows = self.fetch_all("SELECT * FROM members")
        return {
            r["id"]: Member(
                id=r["id"],
                wxid=r["wxid"],
                display_name=r["display_name"],
                avatar_url=r["avatar_url"] or "",
                is_banker=bool(r.get("is_banker", 0))
            )
            for r in rows
        }

    def get_or_create_member(self, wxid: str, display_name: str, is_banker: bool = False) -> Member:
        """Atomically find or insert member and return Member with valid DB auto-increment ID."""
        with self._lock:
            conn = self.get_connection()
            # 1. Try finding by wxid first
            if wxid:
                row = self.fetch_one("SELECT * FROM members WHERE wxid = ?", (wxid,))
                if row:
                    return Member(
                        id=row["id"],
                        wxid=row["wxid"],
                        display_name=row["display_name"],
                        avatar_url=row["avatar_url"] or "",
                        is_banker=bool(row.get("is_banker", 0))
                    )

            # 2. Try finding by display_name if wxid didn't match
            dname = (display_name or wxid or "player").strip()
            row = self.fetch_one("SELECT * FROM members WHERE display_name = ?", (dname,))
            if row:
                if wxid and not row.get("wxid"):
                    try:
                        conn.execute("UPDATE members SET wxid = ?, updated_at = ? WHERE id = ?", (wxid, now_iso(), row["id"]))
                        conn.commit()
                    except Exception:
                        pass
                return Member(
                    id=row["id"],
                    wxid=row["wxid"] or wxid,
                    display_name=row["display_name"],
                    avatar_url=row["avatar_url"] or "",
                    is_banker=bool(row.get("is_banker", 0))
                )
            
            # 3. Insert new member ensuring display_name uniqueness
            ts = now_iso()
            candidate_dname = dname
            suffix = 1
            while self.fetch_one("SELECT id FROM members WHERE display_name = ?", (candidate_dname,)):
                candidate_dname = f"{dname}_{suffix}"
                suffix += 1

            cur = conn.execute(
                "INSERT INTO members (wxid, display_name, is_banker, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (wxid or None, candidate_dname, 1 if is_banker else 0, ts, ts)
            )
            new_id = cur.lastrowid
            conn.commit()
            return Member(
                id=new_id,
                wxid=wxid,
                display_name=candidate_dname,
                avatar_url="",
                is_banker=is_banker
            )

    def load_settings(self) -> dict[str, Any]:
        rows = self.fetch_all("SELECT key, value FROM settings")
        s = {r["key"]: r["value"] for r in rows}
        
        # Parse emoji overrides from both emoji_overrides and emoji_classification_overrides
        emoji_overrides: dict[str, str] = {}
        for k in ("emoji_overrides", "emoji_classification_overrides"):
            if k in s:
                try:
                    raw_map = json.loads(s[k])
                    if isinstance(raw_map, dict):
                        emoji_overrides.update({str(mk).lower(): str(mv) for mk, mv in raw_map.items()})
                except Exception:
                    pass

        # Also query emoji_result_mappings table if present
        try:
            mapping_rows = self.fetch_all("SELECT fingerprint, result FROM emoji_result_mappings")
            for mr in mapping_rows:
                fp = str(mr.get("fingerprint") or "").lower().strip()
                res = str(mr.get("result") or "").strip()
                if fp and res:
                    emoji_overrides[fp] = res
        except Exception:
            pass

        return {
            "water_rate_bp": int(s.get("water_rate_bp", 400)),
            "water_unit": int(s.get("water_unit", 10)),
            "water_free_max_win": int(s.get("water_free_max_win", 100)),
            "water_free_half_multiplier": bool(int(s.get("water_free_half_multiplier", "1"))),
            "water_profile": str(s.get("water_profile", "default")),
            "water_rules": str(s.get("water_rules", "")),
            "water_rules_enabled": bool(int(s.get("water_rules_enabled", "0"))),
            "special_water_rules": str(s.get("special_water_rules", "")),
            "special_water_rules_enabled": bool(int(s.get("special_water_rules_enabled", "0"))),
            "report_target_group_id": str(s.get("report_target_group_id", "")),
            "report_target_group_name": str(s.get("report_target_group_name", "")),
            "auto_send_round_report": bool(int(s.get("auto_send_round_report", "1"))),
            "emoji_overrides": emoji_overrides,
            "banker_wxids": [x.strip() for x in str(s.get("banker_wxids", "")).split(",") if x.strip()],
            "banker_names": [x.strip() for x in str(s.get("banker_names", "")).split(",") if x.strip()],
            "control_image_seal_fingerprints": [str(x).lower().strip() for x in json.loads(s.get("control_image_seal_fingerprints", "[]")) if str(x).strip()] if isinstance(json.loads(s.get("control_image_seal_fingerprints", "[]") if s.get("control_image_seal_fingerprints", "").startswith("[") else "[]"), list) else [],
            "control_image_start_fingerprints": [str(x).lower().strip() for x in json.loads(s.get("control_image_start_fingerprints", "[]")) if str(x).strip()] if isinstance(json.loads(s.get("control_image_start_fingerprints", "[]") if s.get("control_image_start_fingerprints", "").startswith("[") else "[]"), list) else [],
            "control_image_class_end_fingerprints": [str(x).lower().strip() for x in json.loads(s.get("control_image_class_end_fingerprints", "[]")) if str(x).strip()] if isinstance(json.loads(s.get("control_image_class_end_fingerprints", "[]") if s.get("control_image_class_end_fingerprints", "").startswith("[") else "[]"), list) else [],
        }

    def recover_historical_pnl(self, session_id: int) -> dict[int, int]:
        """Recover cumulative player pnl from all settled rounds in the session."""
        with self._lock:
            rows = self.fetch_all("""
                SELECT member_id, SUM(net_result) as total_pnl
                FROM bets
                WHERE session_id = ? AND status = 'accepted' AND round_id IN (
                    SELECT id FROM rounds WHERE session_id = ? AND status = 'settled'
                )
                GROUP BY member_id
            """, (session_id, session_id))
            return {r["member_id"]: int(r["total_pnl"] or 0) for r in rows}

    def settle_and_create_next_round_tx(
        self,
        session_id: int,
        settled_round: Round,
        summaries: list[PlayerRoundSummary],
        next_round_no: int,
        next_disp_no: int,
        next_route: str,
        report_job_data: dict[str, Any] | None = None
    ) -> int:
        """Atomically persist round settlement, calculated bet values, next round creation and report job in a single transaction."""
        with self._lock:
            conn = self.get_connection()
            with conn:
                # 1. Settle current round
                conn.execute("""
                    UPDATE rounds 
                    SET status = 'settled', result = ?, route = ?, settled_at = ?
                    WHERE id = ?
                """, (settled_round.result, settled_round.route, settled_round.settled_at or now_iso(), settled_round.id))

                conn.execute("""
                    UPDATE sessions SET trend = ? WHERE id = ?
                """, (settled_round.route, session_id))

                # 2. Batch update bets
                params = [
                    (b.hit_count, b.gross_result, b.water, b.net_result, b.id)
                    for b in settled_round.bets if not b.is_revoked and b.id > 0 and getattr(b, 'status', 'accepted') != 'rejected'
                ]
                if params:
                    conn.executemany("""
                        UPDATE bets
                        SET hit_count = ?, gross_result = ?, water = ?, net_result = ?
                        WHERE id = ?
                    """, params)

                # 3. Create next round
                row = conn.execute("SELECT MAX(round_no), MAX(display_round_no) FROM rounds WHERE session_id = ?", (session_id,)).fetchone()
                if row:
                    max_r = row[0]
                    max_d = row[1]
                    if max_r is not None:
                        next_round_no = max(next_round_no, int(max_r) + 1)
                    if max_d is not None:
                        next_disp_no = max(next_disp_no, int(max_d) + 1)

                cur = conn.execute("""
                    INSERT INTO rounds (session_id, round_no, display_round_no, status, result, route, opened_at)
                    VALUES (?, ?, ?, 'open', '', ?, ?)
                """, (session_id, next_round_no, next_disp_no, next_route, now_iso()))
                next_round_id = cur.lastrowid

                # 4. Optional report job insertion
                if report_job_data:
                    now_str = now_iso()
                    conn.execute("""
                        INSERT INTO report_jobs (session_id, round_id, source_group_id, target_group_id, artifact_type, status, artifact_path, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'queued', ?, ?, ?)
                    """, (
                        session_id,
                        settled_round.id,
                        report_job_data.get('source_group_id', ''),
                        report_job_data.get('target_group_id', ''),
                        report_job_data.get('artifact_type', 'image'),
                        report_job_data.get('artifact_path', ''),
                        now_str,
                        now_str
                    ))

                return next_round_id

    def save_round_settlement(self, round_obj: Round, player_summaries: list[PlayerRoundSummary]):
        """Persist settled round & updated bet calculation records in a single fast transaction."""
        with self._lock:
            conn = self.get_connection()
            with conn:
                conn.execute("""
                    UPDATE rounds 
                    SET status = 'settled', result = ?, route = ?, settled_at = ?
                    WHERE id = ?
                """, (round_obj.result, round_obj.route, round_obj.settled_at or now_iso(), round_obj.id))
                
                # Batch update bets
                params = [
                    (b.hit_count, b.gross_result, b.water, b.net_result, b.id)
                    for b in round_obj.bets if not b.is_revoked and b.id > 0
                ]
                if params:
                    conn.executemany("""
                        UPDATE bets
                        SET hit_count = ?, gross_result = ?, water = ?, net_result = ?
                        WHERE id = ?
                    """, params)
