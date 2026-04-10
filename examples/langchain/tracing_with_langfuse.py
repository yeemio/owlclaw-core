"""Example 5: Enable tracing and Langfuse linkage for runnable execution."""

from owlclaw import OwlClaw


class TraceRunnable:
    async def ainvoke(self, payload: dict) -> dict:
        return {"result": payload["text"], "trace": True}


def build_app() -> OwlClaw:
    app = OwlClaw("langchain-tracing")
    app.mount_skills("./examples/capabilities")
    app.configure(
        langchain={
            "tracing": {
                "enabled": True,
                "langfuse_integration": True,
            },
            "privacy": {
                "mask_inputs": True,
                "mask_outputs": True,
            },
        }
    )
    app.register_langchain_runnable(
        name="entry-monitor",
        runnable=TraceRunnable(),
        description="Tracing-enabled runnable",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        enable_tracing=True,
    )
    return app
