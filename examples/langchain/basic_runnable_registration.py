"""Example 1: Register a basic LangChain runnable."""

from owlclaw import OwlClaw


class EchoRunnable:
    async def ainvoke(self, payload: dict) -> dict:
        return {"text": payload["text"], "source": "langchain"}


def build_app() -> OwlClaw:
    app = OwlClaw("langchain-basic")
    app.mount_skills("./examples/capabilities")
    app.register_langchain_runnable(
        name="entry-monitor",
        runnable=EchoRunnable(),
        description="Echo user text",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
    return app
