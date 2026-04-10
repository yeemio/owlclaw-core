"""模型路由示例：按 task_type 使用不同模型（mock 下用不同 mock_responses 区分）。"""

import asyncio
from pathlib import Path

from owlclaw.integrations.llm import LLMClient, LLMConfig, PromptBuilder


async def main() -> None:
    config_path = Path(__file__).resolve().parent.parent.parent / "docs" / "llm" / "owlclaw.llm.example.yaml"
    config = LLMConfig.from_yaml(config_path) if config_path.exists() else LLMConfig.default_for_owlclaw()
    config.mock_mode = True
    config.mock_responses = {
        "default": "[default model] reply",
        "trading_decision": "[trading_decision → gpt-4o] mock reply",
        "simple_query": "[simple_query → gpt-4o-mini] mock reply",
    }

    client = LLMClient(config)
    messages = [
        PromptBuilder.build_system_message("You are an assistant."),
        PromptBuilder.build_user_message("One short reply."),
    ]

    # 不指定 task_type → default_model
    r0 = await client.complete(messages)
    print("task_type=None  -> model:", r0.model, "content:", (r0.content or "")[:50])

    # 指定 task_type → 按 task_type_routing 选模型（mock 下仍为 mock，但 key 用 task_type）
    r1 = await client.complete(messages, task_type="trading_decision")
    print("trading_decision -> model:", r1.model, "content:", (r1.content or "")[:50])

    r2 = await client.complete(messages, task_type="simple_query")
    print("simple_query     -> model:", r2.model, "content:", (r2.content or "")[:50])


if __name__ == "__main__":
    asyncio.run(main())
