"""History Store — SQLite-based point value history with ring buffer.

Features:
  - WAL mode for safe concurrent reads/writes
  - Ring buffer: max records per point (oldest auto-deleted)
  - Global DB size limit with auto-vacuum
  - Configurable retention by time (days)
  - Periodic cleanup loop
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "history.db"


class HistoryStore:
    """Manages point value history in SQLite with ring-buffer protection."""

    def __init__(
        self,
        db_path: Path | None = None,
        max_records_per_point: int = 10_000,
        max_db_size_mb: int = 500,
        retention_days: int = 30,
    ):
        self._db_path = db_path or _DB_PATH
        self.max_records_per_point = max_records_per_point
        self.max_db_size_mb = max_db_size_mb
        self.retention_days = retention_days
        self._conn: sqlite3.Connection | None = None
        self._cleanup_task: asyncio.Task | None = None
        self._record_count = 0  # rough in-memory counter for fast checks
        self._event_count = 0
        self.max_events = 10_000
        # RLock: reentrant so that methods calling each other (e.g. record → ring_buffer) don't deadlock
        self._write_lock = threading.RLock()

    # ── Init / Close ──────────────────────────
    def init(self) -> None:
        """Create DB directory, open connection, create tables."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA cache_size=2000")  # ~8MB cache
        self._conn.execute("PRAGMA auto_vacuum=INCREMENTAL")

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS point_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mapping_id TEXT NOT NULL,
                value REAL,
                value_text TEXT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_history_mapping_ts
            ON point_history (mapping_id, timestamp)
        """)

        # Event log table
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS event_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                event_type TEXT NOT NULL,
                device_id INTEGER,
                mapping_id TEXT,
                severity TEXT NOT NULL DEFAULT 'info',
                message TEXT,
                data_json TEXT
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_event_type_ts
            ON event_log (event_type, timestamp)
        """)
        self._conn.commit()

        # Get initial record count
        row = self._conn.execute("SELECT COUNT(*) FROM point_history").fetchone()
        self._record_count = row[0] if row else 0
        row2 = self._conn.execute("SELECT COUNT(*) FROM event_log").fetchone()
        self._event_count = row2[0] if row2 else 0
        logger.info(
            "History store initialized: %s (%d records, %d events, %.1f MB)",
            self._db_path,
            self._record_count,
            self._event_count,
            self._get_db_size_mb(),
        )

    def close(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Record a value ────────────────────────
    def record(self, mapping_id: str, value: Any) -> None:
        """Insert a new history record. Applies ring-buffer if needed."""
        if self._conn is None:
            return

        with self._write_lock:
            # Check DB size limit (~every 100 records)
            if self._record_count % 100 == 0:
                db_size = self._get_db_size_mb()
                if db_size > self.max_db_size_mb:
                    logger.warning(
                        "DB size %.1fMB > limit %dMB — purging oldest 10%%",
                        db_size, self.max_db_size_mb,
                    )
                    self._purge_oldest_global(percent=10)

            # Store numeric if possible, else text
            value_num = None
            value_text = None
            try:
                value_num = float(value)
            except (TypeError, ValueError):
                value_text = str(value) if value is not None else None

            ts = datetime.now(timezone.utc).isoformat()
            self._conn.execute(
                "INSERT INTO point_history (mapping_id, value, value_text, timestamp) VALUES (?, ?, ?, ?)",
                (mapping_id, value_num, value_text, ts),
            )
            self._conn.commit()
            self._record_count += 1

            # Ring buffer: check per-point count (every 50 inserts to reduce overhead)
            if self._record_count % 50 == 0:
                self._apply_ring_buffer(mapping_id)

    def _apply_ring_buffer(self, mapping_id: str) -> None:
        """Delete oldest records for a point if exceeding max_records_per_point."""
        if self._conn is None:
            return
        row = self._conn.execute(
            "SELECT COUNT(*) FROM point_history WHERE mapping_id = ?",
            (mapping_id,),
        ).fetchone()
        count = row[0] if row else 0
        if count > self.max_records_per_point:
            excess = count - self.max_records_per_point
            self._conn.execute(
                """DELETE FROM point_history WHERE id IN (
                    SELECT id FROM point_history
                    WHERE mapping_id = ?
                    ORDER BY timestamp ASC LIMIT ?
                )""",
                (mapping_id, excess),
            )
            self._conn.commit()
            self._record_count -= excess
            logger.info("Ring buffer: purged %d old records for %s", excess, mapping_id)

    def _purge_oldest_global(self, percent: int = 10) -> None:
        """Purge oldest N% of all records to free space."""
        if self._conn is None:
            return
        with self._write_lock:
            to_delete = max(1, self._record_count * percent // 100)
            self._conn.execute(
                "DELETE FROM point_history WHERE id IN (SELECT id FROM point_history ORDER BY timestamp ASC LIMIT ?)",
                (to_delete,),
            )
            self._conn.execute("PRAGMA incremental_vacuum(1000)")
            self._conn.commit()
            self._record_count = max(0, self._record_count - to_delete)
            logger.info("Global purge: deleted %d records", to_delete)

    # ── Query ─────────────────────────────────
    def query(
        self,
        mapping_id: str,
        start: str | None = None,
        end: str | None = None,
        limit: int = 500,
    ) -> list[dict]:
        """Query history for a point within time range."""
        if self._conn is None:
            return []

        sql = "SELECT timestamp, value, value_text FROM point_history WHERE mapping_id = ?"
        params: list[Any] = [mapping_id]

        if start:
            sql += " AND timestamp >= ?"
            params.append(start)
        if end:
            sql += " AND timestamp <= ?"
            params.append(end)

        sql += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "timestamp": r[0],
                "value": r[1] if r[1] is not None else r[2],
            }
            for r in reversed(rows)  # chronological order
        ]

    def export_range(
        self,
        mapping_ids: list[str] | None = None,
        start: str | None = None,
        end: str | None = None,
        limit: int = 50_000,
    ) -> list[dict]:
        """Export history for multiple points in a date range (for CSV/JSON export)."""
        if self._conn is None:
            return []

        sql = "SELECT mapping_id, timestamp, value, value_text FROM point_history WHERE 1=1"
        params: list[Any] = []

        if mapping_ids:
            placeholders = ",".join("?" * len(mapping_ids))
            sql += f" AND mapping_id IN ({placeholders})"
            params.extend(mapping_ids)
        if start:
            sql += " AND timestamp >= ?"
            params.append(start)
        if end:
            sql += " AND timestamp <= ?"
            params.append(end)

        sql += " ORDER BY timestamp ASC LIMIT ?"
        params.append(limit)

        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "mapping_id": r[0],
                "timestamp": r[1],
                "value": r[2] if r[2] is not None else r[3],
            }
            for r in rows
        ]

    def get_stats(self) -> dict:
        """Return DB stats: size, record counts, per-point counts."""
        if self._conn is None:
            return {}

        db_size = self._get_db_size_mb()
        total = self._conn.execute("SELECT COUNT(*) FROM point_history").fetchone()[0]
        per_point = self._conn.execute(
            "SELECT mapping_id, COUNT(*) as cnt FROM point_history GROUP BY mapping_id ORDER BY cnt DESC LIMIT 20"
        ).fetchall()

        oldest = self._conn.execute(
            "SELECT MIN(timestamp) FROM point_history"
        ).fetchone()[0]
        newest = self._conn.execute(
            "SELECT MAX(timestamp) FROM point_history"
        ).fetchone()[0]

        return {
            "db_size_mb": round(db_size, 2),
            "max_db_size_mb": self.max_db_size_mb,
            "total_records": total,
            "max_records_per_point": self.max_records_per_point,
            "retention_days": self.retention_days,
            "oldest_record": oldest,
            "newest_record": newest,
            "per_point": [{"mapping_id": r[0], "count": r[1]} for r in per_point],
        }

    def purge_mapping(self, mapping_id: str, keep_count: int = 0) -> int:
        """Purge history for a specific mapping. keep_count=0 means delete all."""
        if self._conn is None:
            return 0
        with self._write_lock:
            if keep_count > 0:
                deleted = self._conn.execute(
                    """DELETE FROM point_history WHERE mapping_id = ? AND id NOT IN (
                        SELECT id FROM point_history WHERE mapping_id = ?
                        ORDER BY timestamp DESC LIMIT ?
                    )""",
                    (mapping_id, mapping_id, keep_count),
                ).rowcount
            else:
                deleted = self._conn.execute(
                    "DELETE FROM point_history WHERE mapping_id = ?",
                    (mapping_id,),
                ).rowcount
            self._conn.commit()
            self._record_count = max(0, self._record_count - deleted)
            return deleted

    # ── Periodic cleanup ──────────────────────
    async def start_cleanup_loop(self, interval_minutes: int = 60) -> None:
        """Run periodic cleanup: retention purge + vacuum."""
        self._cleanup_task = asyncio.current_task()
        while True:
            try:
                await asyncio.sleep(interval_minutes * 60)
                self._run_retention_cleanup()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Cleanup loop error: %s", e)

    def _run_retention_cleanup(self) -> None:
        """Delete records older than retention_days."""
        if self._conn is None:
            return
        with self._write_lock:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=self.retention_days)).isoformat()
            deleted = self._conn.execute(
                "DELETE FROM point_history WHERE timestamp < ?", (cutoff,)
            ).rowcount
            if deleted > 0:
                self._conn.execute("PRAGMA incremental_vacuum(500)")
                self._conn.commit()
                self._record_count = max(0, self._record_count - deleted)
                logger.info("Retention cleanup: deleted %d records older than %d days", deleted, self.retention_days)

    # ── Event Log ─────────────────────────────
    def log_event(
        self,
        event_type: str,
        message: str,
        *,
        device_id: int | None = None,
        mapping_id: str | None = None,
        severity: str = "info",
        data: dict | None = None,
    ) -> None:
        """Log an event to the event_log table."""
        if self._conn is None:
            return
        with self._write_lock:
            ts = datetime.now(timezone.utc).isoformat()
            data_json = json.dumps(data) if data else None
            self._conn.execute(
                "INSERT INTO event_log (timestamp, event_type, device_id, mapping_id, severity, message, data_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ts, event_type, device_id, mapping_id, severity, message, data_json),
            )
            self._conn.commit()
            self._event_count += 1
            # Ring buffer for events
            if self._event_count > self.max_events and self._event_count % 100 == 0:
                self._purge_old_events()

    def query_events(
        self,
        event_type: str | None = None,
        device_id: int | None = None,
        severity: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Query events with optional filters."""
        if self._conn is None:
            return []
        sql = "SELECT id, timestamp, event_type, device_id, mapping_id, severity, message, data_json FROM event_log WHERE 1=1"
        params: list[Any] = []
        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        if device_id is not None:
            sql += " AND device_id = ?"
            params.append(device_id)
        if severity:
            sql += " AND severity = ?"
            params.append(severity)
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self._conn.execute(sql, params).fetchall()
        return [
            {
                "id": r[0], "timestamp": r[1], "event_type": r[2],
                "device_id": r[3], "mapping_id": r[4], "severity": r[5],
                "message": r[6], "data": json.loads(r[7]) if r[7] else None,
            }
            for r in rows
        ]

    def _purge_old_events(self) -> None:
        """Keep only max_events most recent events."""
        if self._conn is None:
            return
        excess = self._event_count - self.max_events
        if excess <= 0:
            return
        self._conn.execute(
            "DELETE FROM event_log WHERE id IN (SELECT id FROM event_log ORDER BY timestamp ASC LIMIT ?)",
            (excess,),
        )
        self._conn.commit()
        self._event_count -= excess
        logger.info("Event log purged %d old events", excess)

    # ── Helpers ───────────────────────────────
    def _get_db_size_mb(self) -> float:
        try:
            return os.path.getsize(str(self._db_path)) / (1024 * 1024)
        except OSError:
            return 0.0

    def get_config(self) -> dict:
        return {
            "max_records_per_point": self.max_records_per_point,
            "max_db_size_mb": self.max_db_size_mb,
            "retention_days": self.retention_days,
        }

    def update_config(self, **kwargs) -> dict:
        if "max_records_per_point" in kwargs:
            self.max_records_per_point = int(kwargs["max_records_per_point"])
        if "max_db_size_mb" in kwargs:
            self.max_db_size_mb = int(kwargs["max_db_size_mb"])
        if "retention_days" in kwargs:
            self.retention_days = int(kwargs["retention_days"])
        return self.get_config()
