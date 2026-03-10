"""MS/TP Health Monitor — Continuous bus monitoring

Chạy scanner theo chu kỳ, track node up/down events, lưu SQLite.
Broadcast real-time events qua WebSocket cho dashboard.

Dùng:
  monitor = HealthMonitor.from_config("config.yaml")
  await monitor.run()          # blocks, Ctrl+C to stop
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

import yaml

from scanner import MstpScanner, NodeInfo

logger = logging.getLogger(__name__)

_DB_PATH = Path(__file__).parent / "mstp_events.db"


# ── Event storage ─────────────────────────────────────────────────────────────

class EventStore:
    def __init__(self, db_path: Path = _DB_PATH):
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None

    def init(self) -> None:
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS node_events (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                node_id   INTEGER NOT NULL,
                event     TEXT NOT NULL,   -- 'online' | 'offline' | 'scan'
                name      TEXT,
                rtt_ms    REAL,
                details   TEXT             -- JSON
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS node_snapshots (
                node_id   INTEGER PRIMARY KEY,
                updated   REAL NOT NULL,
                data_json TEXT NOT NULL    -- NodeInfo JSON
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_ts ON node_events(timestamp)"
        )
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def log_event(self, node: NodeInfo, event: str) -> None:
        if not self._conn:
            return
        self._conn.execute(
            "INSERT INTO node_events (timestamp, node_id, event, name, rtt_ms) VALUES (?,?,?,?,?)",
            (time.time(), node.address, event, node.name, node.rtt_ms),
        )
        self._conn.commit()

    def save_snapshot(self, node: NodeInfo) -> None:
        if not self._conn:
            return
        self._conn.execute(
            "INSERT OR REPLACE INTO node_snapshots (node_id, updated, data_json) VALUES (?,?,?)",
            (node.address, time.time(), json.dumps(node.to_dict())),
        )
        self._conn.commit()

    def get_snapshots(self) -> list[dict]:
        if not self._conn:
            return []
        rows = self._conn.execute(
            "SELECT data_json FROM node_snapshots ORDER BY node_id"
        ).fetchall()
        result = []
        for (js,) in rows:
            try:
                result.append(json.loads(js))
            except Exception:
                pass
        return result

    def get_events(self, limit: int = 100, node_id: int | None = None) -> list[dict]:
        if not self._conn:
            return []
        if node_id is not None:
            rows = self._conn.execute(
                "SELECT id,timestamp,node_id,event,name,rtt_ms FROM node_events "
                "WHERE node_id=? ORDER BY timestamp DESC LIMIT ?",
                (node_id, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id,timestamp,node_id,event,name,rtt_ms FROM node_events "
                "ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        keys = ["id", "timestamp", "node_id", "event", "name", "rtt_ms"]
        return [dict(zip(keys, row)) for row in rows]

    def get_stats(self) -> dict:
        if not self._conn:
            return {}
        total_nodes   = self._conn.execute("SELECT COUNT(*) FROM node_snapshots").fetchone()[0]
        online_nodes  = self._conn.execute(
            "SELECT COUNT(*) FROM node_snapshots WHERE data_json LIKE '%\"online\": true%'"
        ).fetchone()[0]
        total_events  = self._conn.execute("SELECT COUNT(*) FROM node_events").fetchone()[0]
        offline_events = self._conn.execute(
            "SELECT COUNT(*) FROM node_events WHERE event='offline'"
        ).fetchone()[0]
        return {
            "total_nodes": total_nodes,
            "online_nodes": online_nodes,
            "offline_nodes": total_nodes - online_nodes,
            "total_events": total_events,
            "offline_events": offline_events,
        }


# ── Health Monitor ────────────────────────────────────────────────────────────

class HealthMonitor:
    """Continuously scan the MS/TP bus and track node availability."""

    def __init__(
        self,
        scanner: MstpScanner,
        interval: float = 60.0,
        broadcast_cb: Callable[[dict], Any] | None = None,
    ):
        self._scanner = scanner
        self._interval = interval
        self._broadcast_cb = broadcast_cb  # Called with event dicts for WebSocket
        self._store = EventStore()
        self._prev_state: dict[int, bool] = {}   # {node_id: was_online}
        self._running = False
        self._scan_count = 0

    @classmethod
    def from_config(
        cls,
        config_path: str = "config.yaml",
        broadcast_cb: Callable[[dict], Any] | None = None,
    ) -> "HealthMonitor":
        with open(config_path) as f:
            cfg = yaml.safe_load(f)
        scanner = MstpScanner.from_config(config_path)
        interval = cfg.get("scan", {}).get("interval_seconds", 60)
        return cls(scanner=scanner, interval=interval, broadcast_cb=broadcast_cb)

    # ── Lifecycle ──────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._store.init()
        await self._scanner.start()
        self._running = True
        logger.info("[Monitor] Started — scan interval %.0fs", self._interval)

    async def stop(self) -> None:
        self._running = False
        await self._scanner.stop()
        self._store.close()
        logger.info("[Monitor] Stopped")

    async def run(self) -> None:
        """Main loop — runs until .stop() is called or KeyboardInterrupt."""
        await self.start()
        try:
            while self._running:
                await self._scan_cycle()
                for _ in range(int(self._interval * 2)):
                    if not self._running:
                        break
                    await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass
        finally:
            await self.stop()

    # ── Scan cycle ─────────────────────────────────────────────────────────

    async def _scan_cycle(self) -> None:
        self._scan_count += 1
        logger.info("[Monitor] Scan #%d starting ...", self._scan_count)
        try:
            results = await self._scanner.scan()
        except Exception as exc:
            logger.error("[Monitor] Scan #%d failed: %s", self._scan_count, exc)
            return

        now_online = set(results.keys())
        prev_online = {nid for nid, was in self._prev_state.items() if was}

        went_offline = prev_online - now_online
        came_online  = now_online - prev_online

        # Handle newly offline nodes
        for nid in went_offline:
            node = NodeInfo(address=nid, online=False)
            self._store.log_event(node, "offline")
            self._store.save_snapshot(node)
            self._emit({"type": "offline", "node_id": nid, "timestamp": time.time()})
            logger.warning("[Monitor] Node %d went OFFLINE", nid)

        # Handle newly online / update existing
        for nid, node in results.items():
            node.online = True
            self._store.save_snapshot(node)
            if nid in came_online:
                self._store.log_event(node, "online")
                self._emit({"type": "online", "node_id": nid, "name": node.name,
                             "rtt_ms": node.rtt_ms, "timestamp": time.time()})
                logger.info("[Monitor] Node %d came ONLINE (%s)", nid, node.name)

        self._prev_state = {nid: True for nid in now_online}
        for nid in went_offline:
            self._prev_state[nid] = False

        stats = self._store.get_stats()
        self._emit({"type": "stats", "timestamp": time.time(), **stats})
        logger.info("[Monitor] Scan #%d done: %d online, +%d -%d",
                    self._scan_count, len(now_online), len(came_online), len(went_offline))

    def _emit(self, event: dict) -> None:
        if self._broadcast_cb:
            try:
                asyncio.create_task(self._broadcast_cb(event))   # type: ignore[arg-type]
            except RuntimeError:
                pass  # No event loop (e.g. in sync context)

    # ── Accessors for dashboard ────────────────────────────────────────────

    @property
    def store(self) -> EventStore:
        return self._store

    @property
    def scan_count(self) -> int:
        return self._scan_count


# ── CLI entrypoint ────────────────────────────────────────────────────────────

async def _cli_main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="MS/TP Health Monitor")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--once",   action="store_true", help="Run single scan then exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    monitor = HealthMonitor.from_config(args.config)
    if args.once:
        await monitor.start()
        await monitor._scan_cycle()
        await monitor.stop()
    else:
        await monitor.run()


if __name__ == "__main__":
    try:
        asyncio.run(_cli_main())
    except KeyboardInterrupt:
        print("\nStopped.")
