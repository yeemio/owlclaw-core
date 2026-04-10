"""Example 2: Use @app.handler with runnable shortcut."""

from owlclaw import OwlClaw


class QARunnable:
    async def ainvoke(self, payload: dict) -> dict:
        question = payload["question"]
        context = payload["context"]
        return {"answer": f"Q: {question} | context: {context}"}


def build_app() -> OwlClaw:
    app = OwlClaw("langchain-decorator")
    app.mount_skills("./examples/capabilities")

    @app.handler(
        "morning-decision",
        runnable=QARunnable(),
        description="QA runnable via decorator",
        input_schema={
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "context": {"type": "string"},
            },
            "required": ["question", "context"],
        },
    )
    def placeholder() -> None:
        return None

    return app
