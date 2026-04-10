"""Example 3: Configure fallback and retry policy."""

from owlclaw import OwlClaw


class PrimaryRunnable:
    async def ainvoke(self, payload: dict) -> dict:
        raise TimeoutError("upstream timeout")


async def summarize_simple(session: dict) -> dict:
    text = session.get("text", "")
    return {"summary": text[:80]}


def build_app() -> OwlClaw:
    app = OwlClaw("langchain-fallback")
    app.mount_skills("./examples/capabilities")

    @app.handler("morning-decision")
    async def fallback_handler(session: dict) -> dict:
        return await summarize_simple(session)

    app.register_langchain_runnable(
        name="entry-monitor",
        runnable=PrimaryRunnable(),
        description="Primary runnable with fallback",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        fallback="morning-decision",
        retry_policy={
            "max_attempts": 3,
            "initial_delay_ms": 100,
            "max_delay_ms": 2000,
            "backoff_multiplier": 2.0,
            "retryable_errors": ["TimeoutError"],
        },
    )
    return app
