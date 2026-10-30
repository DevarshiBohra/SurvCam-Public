"""
telemetry.py - stores the extra heartbeat fields (temperature, WiFi diagnostics,
reset reasons, ...) in its own small SQLite file, and answers the queries the
app needs (live values, latest snapshot, reboot/dropout history).

It does NOT touch your existing database.py / survcam.db, so adding it cannot
break anything that already works. Put this file next to main.py.
"""

import math
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH = Path(
    os.environ.get("SURVCAM_TELEMETRY_DB", Path(__file__).resolve().parent / "telemetry.db")
)
RETENTION_DAYS = 14
TS_FMT = "%Y-%m-%d %H:%M:%S"

# Heartbeat key -> python type. Only these keys are stored (the heartbeat also
# carries the camera password, which must never be copied into this table).
FIELDS = {
    "rssi": int,
    "temp_c": float,
    "free_heap": int,
    "uptime_ms": int,
    "channel": int,
    "bssid": str,
    "ip_address": str,
    "fw_version": str,
    "reset_reason_str": str,
    "restart_cause": str,
    "prev_disc_reason": int,
    "disc_reason": int,
    "disc_count": int,
    "ota_state": str,       # idle | downloading | uploading | installed | pending_verify | rolled_back | failed
    "ota_progress": int,    # 0-100 while updating
    "ota_part": str,        # firmware slot the camera is running (ota_0 / ota_1)
    "ota_msg": str,         # last update result, e.g. "ok 0.10.1" or "sha_mismatch"
}

_init_lock = threading.Lock()
_initialised = False
_last_cleanup = 0.0
_keepalive_conn = None


def _connect():
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_telemetry_db():
    """Create the table once. Safe to call many times."""
    global _initialised, _keepalive_conn
    with _init_lock:
        if _initialised:
            return
        conn = _connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            cols = ",\n                ".join(
                f"{name} {'REAL' if typ is float else 'INTEGER' if typ is int else 'TEXT'}"
                for name, typ in FIELDS.items()
            )
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS telemetry (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    {cols}
                )
                """
            )
            # Existing telemetry.db from an earlier version: add any new columns.
            have = {row[1] for row in conn.execute("PRAGMA table_info(telemetry)")}
            for name, typ in FIELDS.items():
                if name not in have:
                    sql_type = "REAL" if typ is float else "INTEGER" if typ is int else "TEXT"
                    conn.execute(f"ALTER TABLE telemetry ADD COLUMN {name} {sql_type}")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_telemetry_cam_id ON telemetry (camera_id, id)"
            )
            conn.commit()
        except BaseException:
            conn.close()
            raise
        _keepalive_conn = conn
        _initialised = True


def _clean(value, typ):
    if value is None:
        return None
    try:
        v = typ(value)
    except (TypeError, ValueError):
        return None
    if typ is float and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def record_telemetry(camera_id, data, recorded_at=None):
    """
    Call this from the MQTT heartbeat handler with the parsed heartbeat dict.
    Never raises: a telemetry problem must not break heartbeat handling.
    Returns True if a row was stored.
    """
    global _last_cleanup
    try:
        if not isinstance(data, dict):
            return False
        # The "offline" last-will message carries stale values - skip it.
        if str(data.get("status", "online")) != "online":
            return False

        init_telemetry_db()
        row = {name: _clean(data.get(name), typ) for name, typ in FIELDS.items()}
        ts = recorded_at or datetime.now().strftime(TS_FMT)

        names = ["camera_id", "recorded_at"] + list(FIELDS.keys())
        values = [camera_id, ts] + [row[n] for n in FIELDS]
        sql = f"INSERT INTO telemetry ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})"

        conn = _connect()
        try:
            conn.execute(sql, values)
            now = time.time()
            if now - _last_cleanup > 6 * 3600:
                _last_cleanup = now
                cutoff = (datetime.now() - timedelta(days=RETENTION_DAYS)).strftime(TS_FMT)
                conn.execute("DELETE FROM telemetry WHERE recorded_at < ?", (cutoff,))
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring
        print(f"[telemetry] could not record heartbeat for {camera_id}: {exc}")
        return False


def _row_to_dict(row):
    d = {k: row[k] for k in row.keys() if k != "id"}
    return d


def list_telemetry(camera_id, limit=20):
    """Newest first."""
    init_telemetry_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM telemetry WHERE camera_id = ? ORDER BY id DESC LIMIT ?",
            (camera_id, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def latest_telemetry(camera_id):
    rows = list_telemetry(camera_id, limit=1)
    return rows[0] if rows else None


def latest_for_all_cameras():
    init_telemetry_db()
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT t.* FROM telemetry t
            JOIN (SELECT camera_id, MAX(id) AS mid FROM telemetry GROUP BY camera_id) m
              ON t.id = m.mid
            ORDER BY t.camera_id
            """
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def first_telemetry_after(camera_id, since_dt):
    """Oldest stored heartbeat recorded at/after since_dt (a datetime), or None."""
    init_telemetry_db()
    since = since_dt.strftime(TS_FMT)
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM telemetry WHERE camera_id = ? AND recorded_at >= ? "
            "ORDER BY id ASC LIMIT 1",
            (camera_id, since),
        ).fetchone()
        return _row_to_dict(row) if row else None
    finally:
        conn.close()


def _parse(ts):
    return datetime.strptime(ts[:19], TS_FMT)


def list_events(camera_id, hours=168, gap_seconds=30, limit=100):
    """
    Reboots and dropouts, newest first, found by walking the heartbeat history:
      - reboot : uptime went DOWN between two heartbeats (camera restarted)
      - dropout: heartbeats stopped for > gap_seconds but uptime kept counting
                 (camera stayed up, e.g. WiFi/MQTT blip - or the server/broker was down)
    'at' is when the camera was last seen before the gap; 'duration_s' is the gap.
    Check several cameras at the same time: if all show the same dropout, it was
    the server, broker or access point - not one camera.
    """
    init_telemetry_db()
    since = (datetime.now() - timedelta(hours=hours)).strftime(TS_FMT)
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT recorded_at, uptime_ms, restart_cause, reset_reason_str,
                   prev_disc_reason, fw_version
            FROM telemetry
            WHERE camera_id = ? AND recorded_at >= ?
            ORDER BY id ASC
            """,
            (camera_id, since),
        ).fetchall()
    finally:
        conn.close()

    events = []
    prev = None
    for row in rows:
        if prev is not None:
            try:
                gap = (_parse(row["recorded_at"]) - _parse(prev["recorded_at"])).total_seconds()
            except ValueError:
                gap = 0
            up_now, up_prev = row["uptime_ms"], prev["uptime_ms"]
            rebooted = up_now is not None and up_prev is not None and up_now < up_prev

            if rebooted:
                events.append(
                    {
                        "type": "reboot",
                        "at": prev["recorded_at"],
                        "duration_s": int(gap),
                        "restart_cause": row["restart_cause"],
                        "reset_reason": row["reset_reason_str"],
                        "prev_disc_reason": row["prev_disc_reason"],
                        "fw_version": row["fw_version"],
                    }
                )
            elif gap > gap_seconds:
                events.append(
                    {
                        "type": "dropout",
                        "at": prev["recorded_at"],
                        "duration_s": int(gap),
                        "restart_cause": None,
                        "reset_reason": None,
                        "prev_disc_reason": None,
                        "fw_version": row["fw_version"],
                    }
                )
        prev = row

    events.reverse()
    return events[:limit]