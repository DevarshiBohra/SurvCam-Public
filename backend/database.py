import os
from dotenv import load_dotenv
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
DB_PATH = Path(os.environ.get("SURVCAM_DATABASE_PATH", Path(__file__).resolve().parent / "data" / "survcam.db"))

HEARTBEAT_RETENTION_DAYS = 7
PROBE_RETENTION_DAYS = 7
NOTIFICATION_HISTORY_RETENTION_DAYS = 30


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.row_factory = sqlite3.Row
    return conn


_keepalive_conn = None


def init_db():
    global _keepalive_conn
    if _keepalive_conn is None:
        _keepalive_conn = get_connection()  # never closed, keeps the WAL open
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS cameras (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            password TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'offline',
            ip_address TEXT,
            last_seen TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS recordings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            file_path TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            duration_seconds INTEGER,
            size_bytes INTEGER,
            created_at TEXT NOT NULL,
            FOREIGN KEY (camera_id) REFERENCES cameras(camera_id)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS motion_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            duration_seconds INTEGER,
            snapshot_path TEXT,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS login_challenges (
            challenge_id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS heartbeat_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            status TEXT NOT NULL,
            ip_address TEXT,
            rssi INTEGER,
            uptime_ms INTEGER,
            fw_version TEXT,
            recorded_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_heartbeat_history_camera_time
        ON heartbeat_history (camera_id, recorded_at)
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS reachability_probes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            camera_id TEXT NOT NULL,
            ip_address TEXT,
            port INTEGER NOT NULL,
            reachable INTEGER NOT NULL,
            error TEXT,
            checked_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_reachability_probes_camera_time
        ON reachability_probes (camera_id, checked_at)
    """)

    # FCM device tokens - one row per phone that has ever opened the app
    # and registered for push notifications.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS device_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            token TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL DEFAULT 'android',
            muted_until TEXT,
            created_at TEXT NOT NULL
        )
    """)

    try:
        cur.execute("ALTER TABLE device_tokens ADD COLUMN muted_until TEXT")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" not in str(exc):
            raise

    # Every push notification actually sent gets logged here, so the app
    # can show a history screen even if the phone missed the push itself
    # (DND, phone off, notification dismissed, etc).
    cur.execute("""
        CREATE TABLE IF NOT EXISTS notification_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL,
            camera_id TEXT,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_notification_history_time
        ON notification_history (created_at)
    """)

    # Single-row table holding the "mute all notifications until" timestamp.
    # NULL means not muted.
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mute_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            muted_until TEXT
        )
    """)

    cur.execute("INSERT OR IGNORE INTO mute_state (id, muted_until) VALUES (1, NULL)")

    conn.commit()
    conn.close()


def create_login_challenge(
    challenge_id: str,
    username: str,
    code_hash: str,
    expires_at: str,
):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM login_challenges WHERE username = ?", (username,))
    cur.execute("""
        INSERT INTO login_challenges (
            challenge_id, username, code_hash, expires_at, created_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (challenge_id, username, code_hash, expires_at, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()


def get_login_challenge(challenge_id: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT challenge_id, username, code_hash, expires_at, attempts
        FROM login_challenges
        WHERE challenge_id = ?
    """, (challenge_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def increment_login_challenge_attempts(challenge_id: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE login_challenges
        SET attempts = attempts + 1
        WHERE challenge_id = ?
    """, (challenge_id,))
    conn.commit()
    conn.close()


def delete_login_challenge(challenge_id: str):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM login_challenges WHERE challenge_id = ?", (challenge_id,))
    conn.commit()
    conn.close()


def add_camera(camera_id: str, name: str, password: str):
    now = datetime.now().isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO cameras (
            camera_id,
            name,
            password,
            status,
            ip_address,
            last_seen,
            created_at,
            updated_at
        )
        VALUES (?, ?, ?, 'offline', NULL, NULL, ?, ?)
    """, (camera_id, name, password, now, now))

    conn.commit()
    conn.close()


def list_cameras():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            camera_id,
            name,
            status,
            ip_address,
            last_seen,
            created_at,
            updated_at
        FROM cameras
        ORDER BY id ASC
    """)

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def get_camera(camera_id: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            camera_id,
            name,
            status,
            ip_address,
            last_seen,
            created_at,
            updated_at
        FROM cameras
        WHERE camera_id = ?
    """, (camera_id,))

    row = cur.fetchone()
    conn.close()

    if row is None:
        return None

    return dict(row)


def update_camera_status(camera_id: str, status: str, ip_address: str | None = None):
    now = datetime.now().isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE cameras
        SET
            status = ?,
            ip_address = COALESCE(?, ip_address),
            last_seen = ?,
            updated_at = ?
        WHERE camera_id = ?
    """, (status, ip_address, now, now, camera_id))

    updated = cur.rowcount
    conn.commit()
    conn.close()

    return updated > 0


def add_heartbeat_record(
    camera_id: str,
    status: str,
    ip_address: str | None = None,
    rssi: int | None = None,
    uptime_ms: int | None = None,
    fw_version: str | None = None,
):
    now = datetime.now().isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO heartbeat_history (
            camera_id, status, ip_address, rssi, uptime_ms, fw_version, recorded_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (camera_id, status, ip_address, rssi, uptime_ms, fw_version, now))

    conn.commit()
    conn.close()


def list_heartbeat_history(camera_id: str, hours: int = 24, limit: int = 2000):
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT camera_id, status, ip_address, rssi, uptime_ms, fw_version, recorded_at
        FROM heartbeat_history
        WHERE camera_id = ? AND recorded_at >= ?
        ORDER BY recorded_at ASC
        LIMIT ?
    """, (camera_id, cutoff, limit))

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def list_rssi_history(camera_id: str, limit: int = 20):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT recorded_at, rssi
        FROM heartbeat_history
        WHERE camera_id = ?
        ORDER BY recorded_at DESC
        LIMIT ?
    """, (camera_id, limit))

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def cleanup_old_heartbeat_history():
    cutoff = (datetime.now() - timedelta(days=HEARTBEAT_RETENTION_DAYS)).isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM heartbeat_history WHERE recorded_at < ?", (cutoff,))
    deleted = cur.rowcount

    conn.commit()
    conn.close()
    return deleted


def add_reachability_probe(
    camera_id: str,
    ip_address: str | None,
    port: int,
    reachable: bool,
    error: str | None = None,
):
    now = datetime.now().isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO reachability_probes (
            camera_id, ip_address, port, reachable, error, checked_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (camera_id, ip_address, port, 1 if reachable else 0, error, now))

    conn.commit()
    conn.close()


def list_reachability_probes(camera_id: str, hours: int = 24, limit: int = 2000):
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT camera_id, ip_address, port, reachable, error, checked_at
        FROM reachability_probes
        WHERE camera_id = ? AND checked_at >= ?
        ORDER BY checked_at ASC
        LIMIT ?
    """, (camera_id, cutoff, limit))

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def cleanup_old_reachability_probes():
    cutoff = (datetime.now() - timedelta(days=PROBE_RETENTION_DAYS)).isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM reachability_probes WHERE checked_at < ?", (cutoff,))
    deleted = cur.rowcount

    conn.commit()
    conn.close()
    return deleted


# --- Device tokens (FCM) ---

def add_device_token(token: str, platform: str = "android"):
    now = datetime.now().isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO device_tokens (token, platform, muted_until, created_at)
        VALUES (?, ?, NULL, ?)
        ON CONFLICT(token) DO UPDATE SET
            platform = excluded.platform
    """, (token, platform, now))

    conn.commit()
    conn.close()


def list_device_tokens():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT token FROM device_tokens")
    rows = [row["token"] for row in cur.fetchall()]
    conn.close()
    return rows

def list_unmuted_device_tokens():
    now = datetime.now().isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT token
        FROM device_tokens
        WHERE muted_until IS NULL OR muted_until <= ?
    """, (now,))

    rows = [row["token"] for row in cur.fetchall()]
    conn.close()
    return rows


def remove_device_token(token: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM device_tokens WHERE token = ?", (token,))
    conn.commit()
    conn.close()

def set_device_muted_until(token: str, muted_until_iso: str | None):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE device_tokens
        SET muted_until = ?
        WHERE token = ?
    """, (muted_until_iso, token))

    updated = cur.rowcount
    conn.commit()
    conn.close()

    return updated > 0


def get_device_muted_until(token: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT muted_until FROM device_tokens WHERE token = ?", (token,))
    row = cur.fetchone()
    conn.close()

    return row["muted_until"] if row else None


# --- Notification history ---

def add_notification_history(type: str, title: str, body: str, camera_id: str | None = None):
    now = datetime.now().isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO notification_history (type, camera_id, title, body, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (type, camera_id, title, body, now))

    conn.commit()
    conn.close()


def list_notification_history(limit: int = 100):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, type, camera_id, title, body, created_at
        FROM notification_history
        ORDER BY created_at DESC
        LIMIT ?
    """, (limit,))

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def cleanup_old_notification_history():
    cutoff = (datetime.now() - timedelta(days=NOTIFICATION_HISTORY_RETENTION_DAYS)).isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("DELETE FROM notification_history WHERE created_at < ?", (cutoff,))
    deleted = cur.rowcount

    conn.commit()
    conn.close()
    return deleted


# --- Mute state ---

def get_muted_until():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT muted_until FROM mute_state WHERE id = 1")
    row = cur.fetchone()
    conn.close()

    return row["muted_until"] if row else None


def set_muted_until(muted_until_iso: str | None):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("UPDATE mute_state SET muted_until = ? WHERE id = 1", (muted_until_iso,))
    conn.commit()
    conn.close()


def add_recording(camera_id: str, file_path: str, started_at: str):
    now = datetime.now().isoformat()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO recordings (
            camera_id,
            file_path,
            started_at,
            ended_at,
            duration_seconds,
            size_bytes,
            created_at
        )
        VALUES (?, ?, ?, NULL, NULL, NULL, ?)
    """, (camera_id, file_path, started_at, now))

    recording_id = cur.lastrowid
    conn.commit()
    conn.close()

    return recording_id


def list_recordings(camera_id: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            camera_id,
            file_path,
            started_at,
            ended_at,
            duration_seconds,
            size_bytes,
            created_at
        FROM recordings
        WHERE camera_id = ?
        ORDER BY started_at DESC
    """, (camera_id,))

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows

def add_motion_event(camera_id, started_at, ended_at=None, duration_seconds=None, snapshot_path=None):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO motion_events (
            camera_id, started_at, ended_at, duration_seconds, snapshot_path, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        camera_id,
        started_at,
        ended_at,
        duration_seconds,
        snapshot_path,
        datetime.now().isoformat()
    ))

    event_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return event_id


def update_motion_event(event_id, ended_at, duration_seconds):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE motion_events
        SET ended_at = ?, duration_seconds = ?
        WHERE id = ?
    """, (ended_at, duration_seconds, event_id))

    conn.commit()
    conn.close()


def list_motion_events(camera_id=None, limit=100):
    conn = get_connection()
    cursor = conn.cursor()

    if camera_id:
        cursor.execute("""
            SELECT id, camera_id, started_at, ended_at, duration_seconds, snapshot_path, created_at
            FROM motion_events
            WHERE camera_id = ?
            ORDER BY started_at DESC
            LIMIT ?
        """, (camera_id, limit))
    else:
        cursor.execute("""
            SELECT id, camera_id, started_at, ended_at, duration_seconds, snapshot_path, created_at
            FROM motion_events
            ORDER BY started_at DESC
            LIMIT ?
        """, (limit,))

    rows = cursor.fetchall()
    conn.close()

    return [dict(row) for row in rows]


def add_user(username: str, password_hash: str, role: str = "user"):
    now = datetime.now().isoformat()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO users (username, password_hash, role, is_active, created_at, updated_at)
        VALUES (?, ?, ?, 1, ?, ?)
    """, (username, password_hash, role, now, now))

    conn.commit()
    conn.close()


def get_user(username: str):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, username, password_hash, role, is_active, created_at, updated_at
        FROM users
        WHERE username = ?
    """, (username,))

    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def list_users():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, username, role, is_active, created_at, updated_at
        FROM users
        ORDER BY id ASC
    """)

    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def set_user_active(username: str, is_active: bool):
    now = datetime.now().isoformat()
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET is_active = ?, updated_at = ?
        WHERE username = ?
    """, (1 if is_active else 0, now, username))

    updated = cur.rowcount
    conn.commit()
    conn.close()
    return updated > 0