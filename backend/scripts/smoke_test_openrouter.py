"""Live smoke test against OpenRouter -- makes one real, billed API call.

Requires OPENROUTER_API_KEY set in backend/.env (see backend/.env.example).

Run from the backend/ directory:
    .venv/Scripts/python.exe scripts/smoke_test_openrouter.py
"""

import asyncio

from app.llm import ChatMessage, estimate_cost_usd, get_llm_client


async def main() -> None:
    client = get_llm_client()
    settings = client._settings
    print(f"Calling {settings.llm_model} via {settings.llm_base_url} ...")

    result = await client.complete(
        [ChatMessage(role="user", content="Reply with exactly one word: pong")],
        operation="smoke_test",
    )

    cost = estimate_cost_usd(result.model, result.usage)
    print("model:         ", result.model)
    print("finish_reason: ", result.finish_reason)
    print("text:          ", result.text)
    print("usage:         ", result.usage)
    print("estimated cost:", f"${cost:.6f}" if cost is not None else "unknown (unpriced model)")

    if settings.llm_usage_log_enabled:
        print(f"\nRecorded to {settings.usage_log_path} -- run scripts/usage_report.py to see a summary.")


if __name__ == "__main__":
    asyncio.run(main())
