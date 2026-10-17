"""Epic 39, US39.8: the run instructions an agent gets alongside its own
prompt -- the same text in the twin and in production.

The stored system prompt is never changed. This second system message
says how to use the tools, how to ask for review and which keys to answer
with. An agent's twin evidence was earned with it, so production sends it
too. Only the header differs: the twin's says this is a test run.
"""

from __future__ import annotations

import json

# Epic 36, US36.3: the output field an agent sets when the gateway after it
# is agent-routed.
ROUTE_FIELD = "route"

_TEST_HEADER = (
    "Digital twin simulation instructions for this test run only (not part of your normal "
    "operating instructions above):"
)
_LIVE_HEADER = "Run instructions for this case (not part of your normal operating instructions above):"


def run_instructions(
    *, has_checkpoint: bool, output_fields: list[str], decision_field: str | None = None,
    routing: tuple[str, list[str]] | None = None, test_run: bool,
) -> str:
    lines = [
        _TEST_HEADER if test_run else _LIVE_HEADER,
        "- Every system you have access to is available as a callable tool -- call the matching "
        "tool instead of describing what you would do.",
    ]
    if has_checkpoint:
        lines.append(
            '- When you need human review/approval as instructed above, call the '
            '"request_human_decision" tool instead of stopping -- you will get back a decision of '
            '"approve" or "reject" and should continue accordingly.'
        )
    if output_fields:
        lines.append(
            "- Once you are completely finished and have no more tool calls to make, respond with "
            f"ONLY a JSON object whose keys are exactly: {output_fields}. Do not include any other text."
        )
    else:
        lines.append(
            "- Once you are completely finished and have no more tool calls to make, respond with "
            "ONLY a short JSON object summarizing what you did. Do not include any other text."
        )
    if decision_field:
        lines.append(
            f'- Set "{decision_field}" to how you decided the case: "approve", "reject", or "escalate" '
            "(you could not decide it yourself and passed it to someone else)."
        )
    if routing:
        question, options = routing
        lines.append(
            f'- Set "{ROUTE_FIELD}" to exactly one of {json.dumps(options)}: the branch the next decision point '
            f'("{question}") should take for this case, based on what you found.'
        )
    return "\n".join(lines)
