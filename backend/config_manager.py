"""Configuration manager — load / save gateway config from JSON file."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

from backend.models import GatewayConfig, PointMapping, ChartConfig, GroupConfig, ScheduleEntry

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "default_config.json"
_RUNTIME_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "runtime_config.json"


class ConfigManager:
    """Manages gateway configuration persistence."""

    def __init__(self, config_path: Path | None = None):
        self._path = config_path or _RUNTIME_CONFIG_PATH
        self._config: GatewayConfig | None = None
        self._mappings: list[PointMapping] = []
        self._charts: list[ChartConfig] = []
        self._groups: list[GroupConfig] = []
        self._schedules: list[ScheduleEntry] = []

    # ── load / save ────────────────────────────
    def load(self) -> GatewayConfig:
        """Load config from runtime file, falling back to defaults on any error."""
        if not self._path.exists():
            logger.info("No runtime config found – copying defaults.")
            try:
                shutil.copy(_DEFAULT_CONFIG_PATH, self._path)
            except Exception as copy_err:
                logger.error("Cannot copy default config: %s — using built-in defaults", copy_err)
                self._config = GatewayConfig()
                return self._config

        raw: dict = {}
        try:
            with open(self._path, "r") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, OSError) as parse_err:
            # Corrupt file: back it up and start from defaults
            backup = self._path.with_suffix(".json.bak")
            logger.error(
                "Config file corrupt or unreadable (%s). "
                "Backing up to %s and starting from defaults.",
                parse_err, backup,
            )
            try:
                shutil.copy(self._path, backup)
                shutil.copy(_DEFAULT_CONFIG_PATH, self._path)
                with open(self._path, "r") as f:
                    raw = json.load(f)
            except Exception as fallback_err:
                logger.error("Cannot load defaults either: %s — using built-in defaults", fallback_err)
                self._config = GatewayConfig()
                return self._config

        try:
            self._config = GatewayConfig(**raw)
        except Exception as model_err:
            logger.error(
                "Config schema validation failed: %s. Using built-in defaults.", model_err
            )
            self._config = GatewayConfig()

        # Hydrate sub-lists (each individually guarded so one bad entry doesn't abort all)
        raw_mappings = raw.get("gateway", {}).get("mappings", [])
        self._mappings = []
        for m in raw_mappings:
            try:
                self._mappings.append(PointMapping(**m))
            except Exception as e:
                logger.warning("Skipping invalid mapping %s: %s", m.get('id', '?'), e)

        raw_charts = raw.get("charts", [])
        self._charts = []
        for c in raw_charts:
            try:
                self._charts.append(ChartConfig(**c))
            except Exception as e:
                logger.warning("Skipping invalid chart %s: %s", c.get('id', '?'), e)

        raw_groups = raw.get("groups", [])
        self._groups = []
        for g in raw_groups:
            try:
                self._groups.append(GroupConfig(**g))
            except Exception as e:
                logger.warning("Skipping invalid group %s: %s", g.get('id', '?'), e)

        raw_schedules = raw.get("schedules", [])
        self._schedules = []
        for s in raw_schedules:
            try:
                self._schedules.append(ScheduleEntry(**s))
            except Exception as e:
                logger.warning("Skipping invalid schedule %s: %s", s.get('id', '?'), e)

        logger.info(
            "Configuration loaded from %s (%d mappings, %d schedules, %d groups)",
            self._path, len(self._mappings), len(self._schedules), len(self._groups),
        )
        return self._config

    def save(self) -> None:
        """Atomically persist current config + mappings to disk."""
        if self._config is None:
            self._config = GatewayConfig()

        data = self._config.model_dump()
        data["gateway"]["mappings"] = [m.model_dump() for m in self._mappings]
        data["charts"] = [c.model_dump() for c in self._charts]
        data["groups"] = [g.model_dump() for g in self._groups]
        data["schedules"] = [s.model_dump() for s in self._schedules]

        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Write to tmp file first, then rename — prevents corruption on crash
        tmp = self._path.with_suffix(".json.tmp")
        try:
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2, default=str)
            tmp.replace(self._path)  # atomic on POSIX
            logger.info("Configuration saved to %s", self._path)
        except Exception as save_err:
            logger.error("Failed to save config: %s", save_err)
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    # ── accessors ──────────────────────────────
    @property
    def config(self) -> GatewayConfig:
        if self._config is None:
            self.load()
        assert self._config is not None
        return self._config

    @property
    def runtime_config(self) -> dict:
        """Return the raw config as a dict (for service checks, diagnostics)."""
        if self._config is None:
            self.load()
        return self._config.model_dump() if self._config else {}

    @property
    def mappings(self) -> list[PointMapping]:
        return self._mappings

    @property
    def groups(self) -> list[GroupConfig]:
        return self._groups

    @property
    def schedules(self) -> list[ScheduleEntry]:
        return self._schedules

    # ── MQTT helpers ───────────────────────────
    def update_mqtt(self, **kwargs: Any) -> None:
        cfg = self.config
        for k, v in kwargs.items():
            if hasattr(cfg.mqtt, k):
                setattr(cfg.mqtt, k, v)
        self.save()

    # ── BACnet helpers ─────────────────────────
    def update_bacnet(self, **kwargs: Any) -> None:
        cfg = self.config
        for k, v in kwargs.items():
            if hasattr(cfg.bacnet, k):
                setattr(cfg.bacnet, k, v)
        self.save()

    # ── Mapping CRUD ───────────────────────────
    def add_mapping(self, mapping: PointMapping) -> PointMapping:
        # De-duplicate: skip if same device + type + instance already exists
        for existing in self._mappings:
            if (existing.device_id == mapping.device_id
                    and existing.object_type == mapping.object_type
                    and existing.object_instance == mapping.object_instance):
                logger.info("Skipping duplicate mapping: %s %s:%d",
                            mapping.device_id, mapping.object_type, mapping.object_instance)
                return existing  # Return existing instead of creating duplicate

        if not mapping.id:
            import uuid
            mapping.id = uuid.uuid4().hex[:8]
        self._mappings.append(mapping)
        self.save()
        return mapping

    def remove_mapping(self, mapping_id: str) -> bool:
        before = len(self._mappings)
        self._mappings = [m for m in self._mappings if m.id != mapping_id]
        removed = len(self._mappings) < before
        if removed:
            self.save()
        return removed

    def update_mapping(self, mapping_id: str, **kwargs: Any) -> PointMapping | None:
        for m in self._mappings:
            if m.id == mapping_id:
                for k, v in kwargs.items():
                    if hasattr(m, k):
                        setattr(m, k, v)
                self.save()
                return m
        return None

    def get_mapping(self, mapping_id: str) -> PointMapping | None:
        for m in self._mappings:
            if m.id == mapping_id:
                return m
        return None

    def export_config(self) -> dict:
        """Return full config as dict (for export endpoint)."""
        data = self.config.model_dump()
        data["gateway"]["mappings"] = [m.model_dump() for m in self._mappings]
        data["groups"] = [g.model_dump() for g in self._groups]
        return data

    # ── Group CRUD ─────────────────────────────
    def add_group(self, group: GroupConfig) -> GroupConfig:
        if not group.id:
            import uuid
            group.id = uuid.uuid4().hex[:8]
        # Check if already exists
        for existing in self._groups:
            if existing.id == group.id or existing.name.lower() == group.name.lower():
                return existing
        self._groups.append(group)
        self.save()
        return group

    def remove_group(self, group_id: str) -> bool:
        before = len(self._groups)
        self._groups = [g for g in self._groups if g.id != group_id]
        removed = len(self._groups) < before
        if removed:
            self.save()
        return removed

    def update_group(self, group_id: str, **kwargs: Any) -> GroupConfig | None:
        for g in self._groups:
            if g.id == group_id:
                for k, v in kwargs.items():
                    if hasattr(g, k):
                        setattr(g, k, v)
                self.save()
                return g
        return None

    def get_group(self, group_id: str) -> GroupConfig | None:
        for g in self._groups:
            if g.id == group_id:
                return g
        return None

    def import_config(self, data: dict) -> None:
        """Import a full config dict."""
        self._config = GatewayConfig(**data)
        raw_mappings = data.get("gateway", {}).get("mappings", [])
        self._mappings = [PointMapping(**m) for m in raw_mappings]
        raw_charts = data.get("charts", [])
        self._charts = [ChartConfig(**c) for c in raw_charts]
        raw_groups = data.get("groups", [])
        self._groups = [GroupConfig(**g) for g in raw_groups]
        raw_schedules = data.get("schedules", [])
        self._schedules = [ScheduleEntry(**s) for s in raw_schedules]
        self.save()

    # ── Chart Config CRUD ──────────────────────
    @property
    def charts(self) -> list[ChartConfig]:
        return self._charts

    def add_chart(self, chart: ChartConfig) -> ChartConfig:
        if not chart.id:
            import uuid
            chart.id = uuid.uuid4().hex[:8]
        self._charts.append(chart)
        self.save()
        return chart

    def remove_chart(self, chart_id: str) -> bool:
        before = len(self._charts)
        self._charts = [c for c in self._charts if c.id != chart_id]
        removed = len(self._charts) < before
        if removed:
            self.save()
        return removed

    def update_chart(self, chart_id: str, **kwargs: Any) -> ChartConfig | None:
        for c in self._charts:
            if c.id == chart_id:
                for k, v in kwargs.items():
                    if hasattr(c, k):
                        setattr(c, k, v)
                self.save()
                return c
        return None

    # ── Schedule CRUD ─────────────────────────
    def add_schedule(self, sched: ScheduleEntry) -> ScheduleEntry:
        if not sched.id:
            import uuid
            sched.id = uuid.uuid4().hex[:8]
        self._schedules.append(sched)
        self.save()
        return sched

    def remove_schedule(self, sched_id: str) -> bool:
        before = len(self._schedules)
        self._schedules = [s for s in self._schedules if s.id != sched_id]
        removed = len(self._schedules) < before
        if removed:
            self.save()
        return removed

    def update_schedule(self, sched_id: str, **kwargs: Any) -> ScheduleEntry | None:
        for s in self._schedules:
            if s.id == sched_id:
                for k, v in kwargs.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
                self.save()
                return s
        return None
