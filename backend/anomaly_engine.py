"""
AnomalyEngine — Point Scenario Monitoring for BACnet-MQTT Gateway

Design:
    - Rules are loaded from config_manager (stored in config.json alongside schedulers)
    - Called after every successful BACnet poll via gateway_engine._check_anomalies()
    - Uses a simple state machine per rule:
        IDLE → TRIGGERED (trigger condition met) → WAITING (grace period)
        → ALARM (expected point didn't respond) | CLEAR (expected response seen)
    - On ALARM: logs event to HistoryStore and publishes MQTT alert
    - Thread-safe: all state modifications under asyncio (single event loop)

Rule condition format (trigger_condition field):
    "gt:26.0"   — greater than 26.0
    "lt:0"      — less than 0
    "eq:active" — equals 'active' (string or numeric)
    "ne:0"      — not equal 0
    "gte:100"   — greater than or equal
    "lte:50"    — less than or equal
"""

import asyncio
import logging
import json
import time
from datetime import datetime, timezone
from typing import Any, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class AnomalyRule:
    """A single scenario monitoring rule."""
    id: str
    name: str
    trigger_mapping_id: str       # Point that triggers the check
    trigger_condition: str        # e.g. "gt:26.0", "eq:active", "ne:0"
    expected_mapping_id: str = "" # Point that should respond (optional)
    expected_value: Any = ""      # Expected value (str/float, optional)
    tolerance_seconds: int = 0    # Grace period; 0 = alarm immediately
    severity: str = "warning"     # "warning" | "critical"
    enabled: bool = True
    notify_topic: str = ""        # MQTT topic for alert (optional)


@dataclass
class RuleState:
    """Runtime state for one rule."""
    status: str = "IDLE"          # IDLE | TRIGGERED | ALARM | CLEAR
    triggered_at: Optional[float] = None
    last_alarm_at: Optional[float] = None
    alarm_count: int = 0


def _evaluate_condition(value: Any, condition: str) -> bool:
    """Parse and evaluate 'op:operand' condition string."""
    try:
        op, operand_str = condition.split(":", 1)
    except ValueError:
        logger.warning(f"Invalid condition format: {condition!r}")
        return False

    # Try numeric comparison first
    try:
        num_val = float(str(value))
        num_op = float(operand_str)
        return {
            "gt": num_val > num_op,
            "gte": num_val >= num_op,
            "lt": num_val < num_op,
            "lte": num_val <= num_op,
            "eq": num_val == num_op,
            "ne": num_val != num_op,
        }.get(op, False)
    except (TypeError, ValueError):
        # String comparison
        str_val = str(value).lower().strip()
        str_op = operand_str.lower().strip()
        return {
            "eq": str_val == str_op,
            "ne": str_val != str_op,
        }.get(op, False)


def _values_match(actual: Any, expected: Any) -> bool:
    """Check if actual == expected (type-coerced comparison)."""
    if actual is None:
        return False
    try:
        return float(str(actual)) == float(str(expected))
    except (TypeError, ValueError):
        return str(actual).lower().strip() == str(expected).lower().strip()


class AnomalyEngine:
    """
    Scenario-based anomaly detector for BACnet points.

    Usage:
        engine = AnomalyEngine(config_manager, history_store, mqtt_service)
        await engine.evaluate(mapping_id, value)  # Call after each poll
    """

    def __init__(self, config_manager, history_store, mqtt_service=None):
        self._cm = config_manager
        self._history = history_store
        self._mqtt = mqtt_service
        self._states: dict[str, RuleState] = {}   # rule_id → RuleState
        self._latest: dict[str, Any] = {}          # mapping_id → latest value
        self._rules: list[AnomalyRule] = []
        self._load_rules()
        logger.info(f"AnomalyEngine initialized with {len(self._rules)} rules")

    # ── Rule persistence ─────────────────────────────────────
    def _load_rules(self):
        """Load rules from config manager's anomaly_rules field."""
        raw = getattr(self._cm.config, "anomaly_rules", []) or []
        self._rules = []
        for r in raw:
            try:
                self._rules.append(AnomalyRule(**r if isinstance(r, dict) else r.__dict__))
            except Exception as e:
                logger.warning(f"Skipping malformed anomaly rule: {e}")

    def get_rules(self) -> list[dict]:
        return [r.__dict__ for r in self._rules]

    def add_rule(self, rule_dict: dict) -> AnomalyRule:
        import uuid
        import dataclasses
        rule_dict.setdefault("id", str(uuid.uuid4())[:8])
        # Strip unknown keys to prevent TypeError on AnomalyRule construction
        valid_fields = {f.name for f in dataclasses.fields(AnomalyRule)}
        filtered = {k: v for k, v in rule_dict.items() if k in valid_fields}
        try:
            rule = AnomalyRule(**filtered)
        except (TypeError, ValueError) as err:
            raise ValueError(f"Invalid anomaly rule data: {err}") from err
        self._rules.append(rule)
        self._save_rules()
        return rule

    def delete_rule(self, rule_id: str) -> bool:
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.id != rule_id]
        if len(self._rules) < before:
            self._save_rules()
            self._states.pop(rule_id, None)
            return True
        return False

    def update_rule(self, rule_id: str, updates: dict) -> Optional[AnomalyRule]:
        for i, r in enumerate(self._rules):
            if r.id == rule_id:
                for k, v in updates.items():
                    if hasattr(r, k):
                        setattr(r, k, v)
                self._save_rules()
                return r
        return None

    def _save_rules(self):
        try:
            self._cm.config.anomaly_rules = [r.__dict__ for r in self._rules]
            self._cm.save()
        except Exception as e:
            logger.error(f"Failed to save anomaly rules: {e}")

    # ── Core evaluation ──────────────────────────────────────
    async def evaluate(self, mapping_id: str, value: Any):
        """
        Called by GatewayEngine after each successful poll.
        Evaluates all rules that reference this mapping_id.

        Two modes:
          1. With response point: TRIGGER → wait tolerance → check expected_mapping_id → ALARM
          2. Alarm-only (no response point): TRIGGER → wait tolerance → ALARM immediately
        """
        self._latest[mapping_id] = value

        for rule in self._rules:
            if not rule.enabled:
                continue
            state = self._states.setdefault(rule.id, RuleState())
            alarm_only = not rule.expected_mapping_id  # no response point = simple alarm

            # ─ This point is the TRIGGER
            if rule.trigger_mapping_id == mapping_id:
                triggered = _evaluate_condition(value, rule.trigger_condition)
                if triggered and state.status == "IDLE":
                    state.status = "TRIGGERED"
                    state.triggered_at = time.monotonic()
                    logger.debug(f"[Anomaly] Rule '{rule.name}' TRIGGERED by {mapping_id}={value}")
                    # Alarm-only with no grace period → fire immediately
                    if alarm_only and rule.tolerance_seconds == 0:
                        state.status = "ALARM"
                        state.last_alarm_at = time.monotonic()
                        state.alarm_count += 1
                        logger.warning(f"[Anomaly] ALARM (immediate) rule='{rule.name}' trigger={value}")
                        await self._fire_alarm(rule, value)
                elif not triggered and state.status not in ("IDLE",):
                    # Trigger cleared → reset
                    if state.status == "ALARM":
                        logger.info(f"[Anomaly] Rule '{rule.name}' CLEARED (trigger gone)")
                        await self._record_clear(rule)
                    state.status = "IDLE"
                    state.triggered_at = None

            # ─ This point is the EXPECTED RESPONSE (only for rules with response point)
            if (not alarm_only and rule.expected_mapping_id == mapping_id
                    and state.status in ("TRIGGERED", "ALARM")):
                if _values_match(value, rule.expected_value):
                    logger.info(f"[Anomaly] Rule '{rule.name}' RESOLVED — expected response received")
                    if state.status == "ALARM":
                        await self._record_clear(rule)
                    state.status = "IDLE"
                    state.triggered_at = None

            # ─ Check timeout (grace period expired → alarm)
            if state.status == "TRIGGERED" and state.triggered_at is not None:
                elapsed = time.monotonic() - state.triggered_at
                if elapsed >= rule.tolerance_seconds:
                    if alarm_only:
                        # No response point: alarm after grace period
                        actual = self._latest.get(rule.trigger_mapping_id)
                        state.status = "ALARM"
                        state.last_alarm_at = time.monotonic()
                        state.alarm_count += 1
                        logger.warning(f"[Anomaly] ALARM rule='{rule.name}' trigger_value={actual}")
                        await self._fire_alarm(rule, actual)
                    else:
                        # With response point: alarm if expected response not seen
                        actual = self._latest.get(rule.expected_mapping_id)
                        if not _values_match(actual, rule.expected_value):
                            state.status = "ALARM"
                            state.last_alarm_at = time.monotonic()
                            state.alarm_count += 1
                            logger.warning(f"[Anomaly] ALARM rule='{rule.name}' expected={rule.expected_value} actual={actual}")
                            await self._fire_alarm(rule, actual)

    async def _fire_alarm(self, rule: AnomalyRule, actual_value: Any):
        """Log event and publish MQTT alert."""
        if rule.expected_mapping_id:
            msg = (
                f"[ANOMALY] {rule.name}: trigger={rule.trigger_mapping_id}, "
                f"expected {rule.expected_mapping_id}={rule.expected_value!r} "
                f"but got {actual_value!r} after {rule.tolerance_seconds}s"
            )
        else:
            msg = (
                f"[ANOMALY] {rule.name}: trigger={rule.trigger_mapping_id} "
                f"value={actual_value!r} matched condition '{rule.trigger_condition}'"
            )
        try:
            if self._history:
                self._history.log_event(
                    event_type="anomaly",
                    message=msg,
                    mapping_id=rule.trigger_mapping_id,
                    severity=rule.severity,
                    data={
                        "rule_id": rule.id,
                        "rule_name": rule.name,
                        "actual_value": str(actual_value) if actual_value is not None else None,
                        "expected_value": str(rule.expected_value) if rule.expected_value != "" else None,
                        "condition": rule.trigger_condition,
                    },
                )
        except Exception as e:
            logger.warning(f"Could not log anomaly event: {e}")

        if self._mqtt and self._mqtt.connected:
            topic = rule.notify_topic or "bacnet/alerts/anomaly"
            payload = json.dumps({
                "rule_id": rule.id,
                "rule_name": rule.name,
                "severity": rule.severity,
                "trigger_mapping": rule.trigger_mapping_id,
                "expected_mapping": rule.expected_mapping_id,
                "expected_value": rule.expected_value,
                "actual_value": str(actual_value) if actual_value is not None else None,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            try:
                self._mqtt.publish(topic, payload)
            except Exception as e:
                logger.warning(f"MQTT anomaly publish failed: {e}")

    async def _record_clear(self, rule: AnomalyRule):
        """Log resolution event."""
        try:
            if self._history:
                self._history.log_event(
                    event_type="anomaly_clear",
                    message=f"[CLEAR] {rule.name} resolved",
                    mapping_id=rule.trigger_mapping_id,
                    severity="info",
                    data={"rule_id": rule.id, "rule_name": rule.name},
                )
        except Exception as e:
            logger.warning(f"Could not log anomaly clear: {e}")

    # ── Status queries ────────────────────────────────────────
    def get_active_alarms(self) -> list[dict]:
        """Return currently active alarm states with rule info."""
        out = []
        for rule in self._rules:
            state = self._states.get(rule.id)
            if state and state.status == "ALARM":
                out.append({
                    "rule_id": rule.id,
                    "rule_name": rule.name,
                    "severity": rule.severity,
                    "trigger_mapping": rule.trigger_mapping_id,
                    "expected_mapping": rule.expected_mapping_id,
                    "expected_value": rule.expected_value,
                    "actual_value": self._latest.get(rule.expected_mapping_id),
                    "alarm_count": state.alarm_count,
                    "last_alarm_at": (
                        datetime.fromtimestamp(state.last_alarm_at, timezone.utc).isoformat()
                        if state.last_alarm_at else None
                    ),
                })
        return out
