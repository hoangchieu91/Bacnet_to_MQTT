"""
Extended tests for HistoryStore — query, export_range, get_stats,
purge_mapping, query_events, manual_cleanup, update_config.
"""
import json
import pytest
from backend.history_store import HistoryStore


@pytest.fixture
def store(tmp_path):
    s = HistoryStore(db_path=tmp_path / "history.db", retention_days=30)
    s.init()
    yield s
    s.close()


@pytest.fixture
def populated_store(store):
    """Store with a few points and events already inserted."""
    store.record("m1", 22.5)
    store.record("m1", 23.0)
    store.record("m2", 100.0)
    store.record("m2", 101.0)
    store.log_event("alarm", "High temp", severity="critical", mapping_id="m1", device_id=10)
    store.log_event("poll", "Normal read", severity="info", mapping_id="m2")
    return store


# ─────────────────────────────────────────────────────────────────
# query()
# ─────────────────────────────────────────────────────────────────
class TestQuery:
    def test_query_returns_correct_point(self, populated_store):
        rows = populated_store.query("m1")
        assert len(rows) == 2
        for r in rows:
            assert "timestamp" in r
            assert "value" in r

    def test_query_order_chronological(self, populated_store):
        """Results must be in ascending timestamp order."""
        rows = populated_store.query("m1")
        assert rows[0]["value"] == pytest.approx(22.5)
        assert rows[1]["value"] == pytest.approx(23.0)

    def test_query_with_limit(self, populated_store):
        rows = populated_store.query("m1", limit=1)
        assert len(rows) == 1

    def test_query_unknown_mapping_returns_empty(self, populated_store):
        rows = populated_store.query("unknown-mapping")
        assert rows == []

    def test_query_returns_empty_when_conn_none(self):
        s = HistoryStore()
        s._conn = None
        assert s.query("m1") == []


# ─────────────────────────────────────────────────────────────────
# export_range()
# ─────────────────────────────────────────────────────────────────
class TestExportRange:
    def test_export_all_points(self, populated_store):
        rows = populated_store.export_range()
        assert len(rows) == 4  # 2 for m1, 2 for m2

    def test_export_filter_by_mapping_ids(self, populated_store):
        rows = populated_store.export_range(mapping_ids=["m1"])
        assert all(r["mapping_id"] == "m1" for r in rows)
        assert len(rows) == 2

    def test_export_returns_empty_when_conn_none(self):
        s = HistoryStore()
        s._conn = None
        assert s.export_range() == []


# ─────────────────────────────────────────────────────────────────
# get_stats()
# ─────────────────────────────────────────────────────────────────
class TestGetStats:
    def test_stats_total_records(self, populated_store):
        stats = populated_store.get_stats()
        assert stats["total_records"] == 4

    def test_stats_has_required_keys(self, populated_store):
        stats = populated_store.get_stats()
        for key in ("db_size_mb", "total_records", "oldest_record", "newest_record", "per_point"):
            assert key in stats, f"Missing key: {key}"

    def test_stats_per_point_list(self, populated_store):
        stats = populated_store.get_stats()
        mapping_ids = {p["mapping_id"] for p in stats["per_point"]}
        assert "m1" in mapping_ids
        assert "m2" in mapping_ids

    def test_stats_returns_empty_when_conn_none(self):
        s = HistoryStore()
        s._conn = None
        assert s.get_stats() == {}

    def test_stats_with_retention(self, populated_store):
        stats = populated_store.get_stats_with_retention()
        assert "retention_days" in stats
        assert "event_count" in stats
        assert stats["event_count"] == 2


# ─────────────────────────────────────────────────────────────────
# purge_mapping()
# ─────────────────────────────────────────────────────────────────
class TestPurgeMapping:
    def test_purge_all_deletes_all(self, populated_store):
        deleted = populated_store.purge_mapping("m1", keep_count=0)
        assert deleted == 2
        rows = populated_store.query("m1")
        assert rows == []

    def test_purge_with_keep_count(self, populated_store):
        deleted = populated_store.purge_mapping("m1", keep_count=1)
        assert deleted == 1
        rows = populated_store.query("m1")
        assert len(rows) == 1

    def test_purge_nonexistent_returns_zero(self, populated_store):
        deleted = populated_store.purge_mapping("nonexistent")
        assert deleted == 0

    def test_purge_returns_zero_when_conn_none(self):
        s = HistoryStore()
        s._conn = None
        assert s.purge_mapping("m1") == 0


# ─────────────────────────────────────────────────────────────────
# query_events()
# ─────────────────────────────────────────────────────────────────
class TestQueryEvents:
    def test_query_all_events(self, populated_store):
        events = populated_store.query_events()
        assert len(events) == 2

    def test_filter_by_event_type(self, populated_store):
        events = populated_store.query_events(event_type="alarm")
        assert len(events) == 1
        assert events[0]["event_type"] == "alarm"

    def test_filter_by_severity(self, populated_store):
        events = populated_store.query_events(severity="critical")
        assert len(events) == 1
        assert events[0]["severity"] == "critical"

    def test_filter_by_device_id(self, populated_store):
        events = populated_store.query_events(device_id=10)
        assert len(events) == 1
        assert events[0]["device_id"] == 10

    def test_event_with_data_json(self, store):
        store.log_event("anomaly", "temp spike", data={"peak": 99.9, "duration": 5})
        events = store.query_events(event_type="anomaly")
        assert len(events) == 1
        assert events[0]["data"]["peak"] == pytest.approx(99.9)

    def test_query_events_returns_empty_when_conn_none(self):
        s = HistoryStore()
        s._conn = None
        assert s.query_events() == []


# ─────────────────────────────────────────────────────────────────
# manual_cleanup()
# ─────────────────────────────────────────────────────────────────
class TestManualCleanup:
    def test_cleanup_returns_stats_dict(self, populated_store):
        result = populated_store.manual_cleanup()
        assert "history_deleted" in result
        assert "events_deleted" in result
        assert "freed_mb" in result

    def test_cleanup_with_no_old_data_deletes_nothing(self, populated_store):
        """Fresh data is within retention — nothing deleted."""
        result = populated_store.manual_cleanup()
        assert result["history_deleted"] == 0
        assert result["events_deleted"] == 0

    def test_cleanup_returns_error_when_conn_none(self):
        s = HistoryStore()
        s._conn = None
        result = s.manual_cleanup()
        assert "error" in result


# ─────────────────────────────────────────────────────────────────
# update_config()
# ─────────────────────────────────────────────────────────────────
class TestUpdateConfig:
    def test_update_retention_days(self, store):
        store.update_config(retention_days=90)
        assert store.retention_days == 90

    def test_update_max_records(self, store):
        store.update_config(max_records_per_point=5000)
        assert store.max_records_per_point == 5000

    def test_get_config_returns_dict(self, store):
        cfg = store.get_config()
        assert "max_records_per_point" in cfg
        assert "retention_days" in cfg
        assert "max_db_size_mb" in cfg
