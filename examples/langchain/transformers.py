"""Example 4: Use custom input/output transformers."""

from datetime import UTC, datetime

from owlclaw import OwlClaw


class SummarizeRunnable:
    async def ainvoke(self, payload: dict) -> str:
        text = payload["text"]
        return text[:60]


def input_transformer(payload: dict) -> dict:
    return {"text": payload["content"]}


def output_transformer(result: str) -> dict:
    return {
        "summary": result,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def build_app() -> OwlClaw:
    app = OwlClaw("langchain-transformers")
    app.mount_skills("./examples/capabilities")
    app.register_langchain_runnable(
        name="entry-monitor",
        runnable=SummarizeRunnable(),
        description="Summarize with custom transformers",
        input_schema={
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
        input_transformer=input_transformer,
        output_transformer=output_transformer,
    )
    return app
