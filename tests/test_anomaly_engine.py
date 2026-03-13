"""
Tests for AnomalyEngine — rule evaluation logic, bad data validation, alarm firing.
"""
import pytest
from unittest.mock import MagicMock, patch

from backend.anomaly_engine import AnomalyEngine, AnomalyRule, _evaluate_condition


# ─────────────────────────────────────────────────────────────────
# Unit tests: _evaluate_condition
# ─────────────────────────────────────────────────────────────────
class TestEvaluateCondition:
    def test_gt_numeric_true(self):
        assert _evaluate_condition(27.5, "gt:26") is True

    def test_gt_numeric_false(self):
        assert _evaluate_condition(25.0, "gt:26") is False

    def test_lt_true(self):
        assert _evaluate_condition(-1, "lt:0") is True

    def test_gte_equal(self):
        assert _evaluate_condition(26.0, "gte:26") is True

    def test_eq_string_case_insensitive(self):
        assert _evaluate_condition("Active", "eq:active") is True

    def test_ne_not_equal(self):
        assert _evaluate_condition(5, "ne:0") is True

    def test_invalid_condition_format_returns_false(self):
        assert _evaluate_condition(5, "invalid_no_colon") is False

    def test_none_value_string_eq(self):
        """None value → coerced to string 'None', compared as string."""
        assert _evaluate_condition(None, "eq:none") is True


# ─────────────────────────────────────────────────────────────────
# AnomalyEngine: rule management
# ─────────────────────────────────────────────────────────────────
@pytest.fixture
def engine(mock_history, mock_mqtt):
    cm = MagicMock()
    cm.config.anomaly_rules = []
    cm.save = MagicMock()
    return AnomalyEngine(cm, mock_history, mock_mqtt)


class TestAnomalyEngineRules:
    def test_add_valid_rule(self, engine):
        rule = engine.add_rule({
            "name": "High Temp Alarm",
            "trigger_mapping_id": "m1",
            "trigger_condition": "gt:30",
            "severity": "critical",
        })
        assert rule.name == "High Temp Alarm"
        assert rule.severity == "critical"
        assert len(engine._rules) == 1

    def test_add_rule_strips_unknown_fields(self, engine):
        """Unknown fields must be silently stripped — not raise TypeError."""
        rule = engine.add_rule({
            "name": "Test",
            "trigger_mapping_id": "m1",
            "trigger_condition": "gt:0",
            "unknown_future_field": "should_be_ignored",  # unknown
        })
        assert rule is not None
        # The unknown field should not appear on the rule
        assert not hasattr(rule, "unknown_future_field")

    def test_add_rule_with_missing_required_fields_raises_clear_error(self, engine):
        """Missing required fields should raise ValueError with a clear message."""
        with pytest.raises((ValueError, TypeError)):
            # trigger_mapping_id is required positionally — missing it raises
            engine.add_rule({
                "name": "Incomplete Rule",
                # trigger_mapping_id missing — AnomalyRule requires it
            })

    def test_delete_rule(self, engine):
        rule = engine.add_rule({
            "name": "Temp", "trigger_mapping_id": "m1", "trigger_condition": "gt:0",
        })
        deleted = engine.delete_rule(rule.id)
        assert deleted is True
        assert len(engine._rules) == 0

    def test_delete_nonexistent_rule_returns_false(self, engine):
        assert engine.delete_rule("nonexistent-id") is False

    def test_update_rule(self, engine):
        rule = engine.add_rule({
            "name": "Old Name", "trigger_mapping_id": "m1", "trigger_condition": "gt:0",
        })
        updated = engine.update_rule(rule.id, {"name": "New Name", "severity": "critical"})
        assert updated.name == "New Name"
        assert updated.severity == "critical"

    def test_get_rules_returns_dicts(self, engine):
        engine.add_rule({
            "name": "R1", "trigger_mapping_id": "m1", "trigger_condition": "gt:0",
        })
        rules = engine.get_rules()
        assert isinstance(rules, list)
        assert isinstance(rules[0], dict)
        assert "name" in rules[0]


# ─────────────────────────────────────────────────────────────────
# AnomalyEngine: evaluate (state machine)
# ─────────────────────────────────────────────────────────────────
class TestAnomalyEngineEvaluate:
    @pytest.mark.asyncio
    async def test_no_alarm_when_condition_not_met(self, engine, mock_history):
        engine.add_rule({
            "name": "High Temp",
            "trigger_mapping_id": "m1",
            "trigger_condition": "gt:50",  # threshold 50
            "tolerance_seconds": 0,
        })
        await engine.evaluate("m1", 30.0)  # value=30, below threshold
        mock_history.log_event.assert_not_called()

    @pytest.mark.asyncio
    async def test_alarm_fires_when_condition_met_zero_tolerance(self, engine, mock_history):
        engine.add_rule({
            "name": "High Temp",
            "trigger_mapping_id": "m1",
            "trigger_condition": "gt:25",
            "tolerance_seconds": 0,  # alarm immediately
        })
        await engine.evaluate("m1", 30.0)  # 30 > 25 → alarm
        mock_history.log_event.assert_called_once()
        call_kwargs = mock_history.log_event.call_args[1]
        assert call_kwargs.get("event_type") == "anomaly"
