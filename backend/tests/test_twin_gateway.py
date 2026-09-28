"""Epic 20: unit tests for the Tool Gateway / Policy Engine
(app/twin/gateway.py) -- pure-function/dataclass tests, no DB/HTTP, same
convention as test_orchestrator.py's module-level tests."""

from app.schemas.agents import ResourcePermission, RuntimeGuardrails, ToolContract
from app.twin.gateway import GuardrailExceeded, GuardrailTracker, check_tool_permission

from tests.test_orchestrator import _definition


# -- check_tool_permission ----------------------------------------------------


def test_check_tool_permission_allows_when_no_contracts_declared():
    """An artifact that never opted into Epic 20 governance (tool_contracts
    empty) behaves exactly like it did before this epic -- any tool the
    model was offered may be called."""
    artifact = _definition(tool_contracts=[], permissions=[])
    decision = check_tool_permission(artifact, "CRM system")
    assert decision.allowed is True


def test_check_tool_permission_denies_undeclared_tool():
    artifact = _definition(
        tool_contracts=[ToolContract(system_name="CRM system", resource="customer", action="read")],
        permissions=[ResourcePermission(resource="customer", actions={"read": True})],
    )
    decision = check_tool_permission(artifact, "Payment system")
    assert decision.allowed is False
    assert "no declared tool contract" in decision.reason


def test_check_tool_permission_denies_when_action_not_granted():
    artifact = _definition(
        tool_contracts=[ToolContract(system_name="Payment system", resource="payment", action="initiate")],
        permissions=[ResourcePermission(resource="payment", actions={"initiate": False})],
    )
    decision = check_tool_permission(artifact, "Payment system")
    assert decision.allowed is False
    assert "lacks 'initiate' permission" in decision.reason


def test_check_tool_permission_denies_when_resource_has_no_permission_entry():
    artifact = _definition(
        tool_contracts=[ToolContract(system_name="CRM system", resource="customer", action="read")],
        permissions=[],
    )
    decision = check_tool_permission(artifact, "CRM system")
    assert decision.allowed is False


def test_check_tool_permission_allows_when_action_granted():
    artifact = _definition(
        tool_contracts=[ToolContract(system_name="CRM system", resource="customer", action="read")],
        permissions=[ResourcePermission(resource="customer", actions={"read": True, "update": False})],
    )
    decision = check_tool_permission(artifact, "CRM system")
    assert decision.allowed is True


# -- GuardrailTracker -----------------------------------------------------------


def test_guardrail_tracker_allows_calls_within_limits():
    tracker = GuardrailTracker(guardrails=RuntimeGuardrails(max_model_calls=2, max_tool_calls=2))
    tracker.before_model_call()
    tracker.before_tool_call("CRM system", '{"id": "1"}')
    assert tracker.model_calls == 1
    assert tracker.tool_calls == 1


def test_guardrail_tracker_raises_on_max_model_calls():
    tracker = GuardrailTracker(guardrails=RuntimeGuardrails(max_model_calls=1))
    tracker.before_model_call()
    try:
        tracker.before_model_call()
        assert False, "expected GuardrailExceeded"
    except GuardrailExceeded as exc:
        assert "max_model_calls" in exc.reason


def test_guardrail_tracker_raises_on_max_tool_calls():
    tracker = GuardrailTracker(guardrails=RuntimeGuardrails(max_tool_calls=1))
    tracker.before_tool_call("CRM system", "{}")
    try:
        tracker.before_tool_call("CRM system", "{}")
        assert False, "expected GuardrailExceeded"
    except GuardrailExceeded as exc:
        assert "max_tool_calls" in exc.reason


def test_guardrail_tracker_loop_detection_flags_repeated_identical_calls():
    tracker = GuardrailTracker(guardrails=RuntimeGuardrails(max_tool_calls=100, retry_max_attempts=1))
    tracker.before_tool_call("CRM system", '{"id": "1"}')
    tracker.before_tool_call("CRM system", '{"id": "1"}')  # original + 1 retry: still allowed
    try:
        tracker.before_tool_call("CRM system", '{"id": "1"}')  # a second retry: loop
        assert False, "expected GuardrailExceeded"
    except GuardrailExceeded as exc:
        assert "Loop detected" in exc.reason


def test_guardrail_tracker_loop_detection_ignores_different_arguments():
    tracker = GuardrailTracker(guardrails=RuntimeGuardrails(max_tool_calls=100, retry_max_attempts=0))
    tracker.before_tool_call("CRM system", '{"id": "1"}')
    tracker.before_tool_call("CRM system", '{"id": "2"}')  # different args -- not a loop
    assert tracker.tool_calls == 2


def test_guardrail_tracker_loop_detection_can_be_disabled():
    tracker = GuardrailTracker(guardrails=RuntimeGuardrails(max_tool_calls=100, retry_max_attempts=0, loop_detection_enabled=False))
    for _ in range(5):
        tracker.before_tool_call("CRM system", '{"id": "1"}')
    assert tracker.tool_calls == 5


def test_guardrail_tracker_raises_on_max_tokens():
    tracker = GuardrailTracker(guardrails=RuntimeGuardrails(max_tokens=100))
    tracker.record_usage(60, 0.01)
    try:
        tracker.record_usage(60, 0.01)
        assert False, "expected GuardrailExceeded"
    except GuardrailExceeded as exc:
        assert "max_tokens" in exc.reason


def test_guardrail_tracker_raises_on_max_cost():
    tracker = GuardrailTracker(guardrails=RuntimeGuardrails(max_cost_usd=0.10))
    tracker.record_usage(10, 0.06)
    try:
        tracker.record_usage(10, 0.06)
        assert False, "expected GuardrailExceeded"
    except GuardrailExceeded as exc:
        assert "max_cost_usd" in exc.reason


def test_guardrail_tracker_never_enforces_cost_ceiling_when_cost_unknown():
    """Same 'unknown beats a wrong partial number' rule app/twin/engine.py's
    own cost accounting follows -- once any call's cost is unknown, the
    running total is no longer trustworthy, so the cost guardrail must
    stop firing rather than compare against a partial number."""
    tracker = GuardrailTracker(guardrails=RuntimeGuardrails(max_cost_usd=0.01))
    tracker.record_usage(10, None)  # unknown-priced model
    tracker.record_usage(10, 1.00)  # would blow the ceiling if enforced
    assert tracker.cost_incomplete is True
