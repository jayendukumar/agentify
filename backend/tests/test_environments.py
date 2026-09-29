"""Epic 21, US21.2: unit tests for the fault-injection catalog resolution
(`app/twin/environments.py`), independent of the full run_scenario loop --
see tests/test_twin_engine.py for the integration-level coverage of a
fault actually intercepting a live run."""

import random
from datetime import datetime, timezone

from app.schemas.environment import FaultConfig, TwinEnvironment
from app.twin.environments import roll_fault


def _environment(**overrides) -> TwinEnvironment:
    defaults = dict(
        id="twinenv_1",
        process_id="proc_1",
        name="Test env",
        version=1,
        systems=[],
        datasets={},
        personas={},
        events=[],
        faults={},
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return TwinEnvironment(**defaults)


def test_roll_fault_returns_none_when_no_faults_declared():
    env = _environment(faults={})
    assert roll_fault(env, "CRM system", random.Random(1)) is None


def test_roll_fault_returns_none_when_probability_never_hits():
    env = _environment(faults={"CRM system": [FaultConfig(type="http_500", probability=0.0)]})
    assert roll_fault(env, "CRM system", random.Random(1)) is None


def test_roll_fault_http_500_is_an_error_with_deterministic_probability_one():
    env = _environment(faults={"CRM system": [FaultConfig(type="http_500", probability=1.0)]})
    outcome = roll_fault(env, "CRM system", random.Random(1))
    assert outcome is not None
    assert outcome.type == "http_500"
    assert outcome.is_error is True
    assert outcome.result["error"] == "http_500"


def test_roll_fault_missing_data_is_not_an_error():
    env = _environment(
        faults={"CRM system": [FaultConfig(type="missing_data", probability=1.0, config={"response": {"id": "123"}})]}
    )
    outcome = roll_fault(env, "CRM system", random.Random(1))
    assert outcome is not None
    assert outcome.is_error is False
    assert outcome.result == {"id": "123"}


def test_roll_fault_duplicate_event_tags_result():
    env = _environment(faults={"CRM system": [FaultConfig(type="duplicate_event", probability=1.0)]})
    outcome = roll_fault(env, "CRM system", random.Random(1))
    assert outcome is not None
    assert outcome.is_error is False
    assert outcome.result["event_marker"] == "duplicate_event"


def test_roll_fault_only_applies_to_the_named_system():
    env = _environment(faults={"CRM system": [FaultConfig(type="http_500", probability=1.0)]})
    assert roll_fault(env, "ERP system", random.Random(1)) is None
