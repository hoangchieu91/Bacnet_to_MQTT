"""
Tests for HistoryStore — init failure, record, ring buffer, log_event, cleanup.
"""
import pytest
import sqlite3
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

from backend.history_store import HistoryStore


@pytest.fixture
def store(tmp_path):
    """Create a HistoryStore backed by a tmp SQLite file."""
    s = HistoryStore(db_path=tmp_path / "history.db")
    s.init()
    yield s
    s.close()


class TestHistoryStoreInit:
    def test_init_creates_tables(self, store):
        """Tables point_history and event_log must exist after init."""
        assert store._conn is not None
        tables = store._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {t[0] for t in tables}
        assert "point_history" in names
        assert "event_log" in names

    def test_init_failure_sets_conn_none(self, tmp_path):
        """If SQLite can't open the file, _conn must be None (not raise)."""
        bad_path = tmp_path / "no_such_dir" / "sub" / "history.db"
        s = HistoryStore(db_path=bad_path)
        # Patch mkdir to simulate disk-full or permission denied
        with patch("pathlib.Path.mkdir", side_effect=PermissionError("disk full")):
            s.init()  # Must NOT raise
        assert s._conn is None, "_conn must be None when init fails"


class TestHistoryStoreRecord:
    def test_record_inserts_row(self, store):
        store.record("mapping-1", 42.0)
        row = store._conn.execute(
            "SELECT value FROM point_history WHERE mapping_id='mapping-1'"
        ).fetchone()
        assert row is not None
        assert row[0] == pytest.approx(42.0)

    def test_record_text_value(self, store):
        store.record("mapping-2", "active")
        row = store._conn.execute(
            "SELECT value_text FROM point_history WHERE mapping_id='mapping-2'"
        ).fetchone()
        assert row[0] == "active"

    def test_record_skips_when_conn_none(self):
        """No crash if _conn is None (history store init failed)."""
        s = HistoryStore()
        s._conn = None
        s.record("m1", 10.0)  # must not raise

    def test_ring_buffer_trims_old_records(self, tmp_path):
        """Ring buffer runs at every 50th insert — test with 110 inserts to trigger twice."""
        max_rec = 5
        s = HistoryStore(db_path=tmp_path / "ring.db", max_records_per_point=max_rec)
        s.init()
        # Ring buffer check runs at insert % 50 == 0 → triggers at 50 and 100
        for i in range(110):
            s.record("pt", float(i))
        count = s._conn.execute(
            "SELECT COUNT(*) FROM point_history WHERE mapping_id='pt'"
        ).fetchone()[0]
        # After 2 trims, count should be well below 2*50 + max_rec
        assert count <= 60, f"Ring buffer failed to cap after 110 inserts: {count} rows"
        s.close()

    def test_thread_safety(self, store):
        """Concurrent writes from multiple threads must not raise."""
        errors = []

        def write_batch(n):
            try:
                for i in range(20):
                    store.record(f"m{n}", float(i))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=write_batch, args=(t,)) for t in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Thread safety errors: {errors}"


class TestHistoryStoreLogEvent:
    def test_log_event_inserts_row(self, store):
        store.log_event("anomaly", "High temp alarm", severity="critical")
        row = store._conn.execute(
            "SELECT event_type, severity FROM event_log"
        ).fetchone()
        assert row[0] == "anomaly"
        assert row[1] == "critical"

    def test_log_event_with_data_dict(self, store):
        store.log_event("schedule", "Ran schedule", data={"value": "22", "priority": 8})
        row = store._conn.execute(
            "SELECT data_json FROM event_log WHERE event_type='schedule'"
        ).fetchone()
        import json
        data = json.loads(row[0])
        assert data["priority"] == 8

    def test_log_event_skips_when_conn_none(self):
        s = HistoryStore()
        s._conn = None
        s.log_event("test", "should not crash")  # must not raise
