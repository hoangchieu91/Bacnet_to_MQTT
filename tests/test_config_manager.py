"""
Tests for ConfigManager — load/save, corrupt JSON fallback, atomic save,
and per-entry validation guards.
"""
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from backend.config_manager import ConfigManager


class TestConfigLoad:
    """Tests for ConfigManager.load()"""

    def test_load_valid_config(self, config_file):
        """Happy path: valid JSON loads with correct types."""
        cm = ConfigManager(config_path=config_file)
        cfg = cm.load()
        assert cfg.mqtt.broker_host == "localhost"
        assert cfg.mqtt.broker_port == 1883
        assert cfg.bacnet.device_id == 599

    def test_load_creates_backup_and_falls_back_on_corrupt_json(
        self, tmp_path, default_config_file
    ):
        """Corrupt JSON → backup created, fall back to defaults."""
        cfg_file = tmp_path / "config" / "runtime_config.json"
        cfg_file.parent.mkdir(exist_ok=True)
        cfg_file.write_text("{this is not json!!!")

        # Patch _DEFAULT_CONFIG_PATH to our fixture
        with patch("backend.config_manager._DEFAULT_CONFIG_PATH", default_config_file):
            cm = ConfigManager(config_path=cfg_file)
            cfg = cm.load()  # must NOT raise

        backup = cfg_file.with_suffix(".json.bak")
        assert backup.exists(), "Backup of corrupt file must be created"
        assert cfg is not None, "Config must fall back to defaults"

    def test_load_missing_file_copies_defaults(self, tmp_path, default_config_file):
        """Missing runtime file → copy default and load."""
        cfg_file = tmp_path / "config" / "runtime_config.json"
        # Don't create the file — it's missing
        with patch("backend.config_manager._DEFAULT_CONFIG_PATH", default_config_file):
            cm = ConfigManager(config_path=cfg_file)
            cfg = cm.load()
        assert cfg is not None
        assert cfg_file.exists(), "runtime_config.json should have been copied from defaults"

    def test_load_skips_invalid_mapping_entries(self, tmp_path, default_config_data):
        """One broken mapping in array → skip it, load valid ones."""
        default_config_data["gateway"]["mappings"] = [
            {
                "id": "good-1", "device_id": 100, "object_type": "analogInput",
                "object_instance": 0, "label": "Room Temp", "enabled": True,
                "read_mode": "poll", "poll_interval": 10,
            },
            {"id": "bad-entry", "device_id": "NOT_AN_INT"},  # invalid
        ]
        cfg_file = tmp_path / "runtime_config.json"
        cfg_file.write_text(json.dumps(default_config_data))

        cm = ConfigManager(config_path=cfg_file)
        cm.load()

        # Only the valid mapping should be loaded
        assert len(cm.mappings) == 1
        assert cm.mappings[0].id == "good-1"


class TestConfigSave:
    """Tests for ConfigManager.save() — atomic write."""

    def test_save_creates_file(self, config_file):
        cm = ConfigManager(config_path=config_file)
        cm.load()
        cm.save()
        assert config_file.exists()
        data = json.loads(config_file.read_text())
        assert "mqtt" in data

    def test_save_no_tmp_left_after_success(self, config_file):
        """Temp file must not exist after successful save."""
        cm = ConfigManager(config_path=config_file)
        cm.load()
        cm.save()
        tmp = config_file.with_suffix(".json.tmp")
        assert not tmp.exists(), ".json.tmp must be cleaned up after save"

    def test_saved_json_is_valid(self, config_file):
        """Saved file must be valid JSON."""
        cm = ConfigManager(config_path=config_file)
        cm.load()
        cm.save()
        content = config_file.read_text()
        parsed = json.loads(content)  # raises if invalid
        assert isinstance(parsed, dict)
