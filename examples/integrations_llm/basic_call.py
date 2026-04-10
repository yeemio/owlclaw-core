"""基本调用示例：加载配置、构建消息、调用 LLMClient.complete（mock 模式无需 API Key）。"""

import asyncio
from pathlib import Path

from owlclaw.integrations.llm import LLMClient, LLMConfig, PromptBuilder


async def main() -> None:
    # 方式一：从 YAML 加载（示例配置在 docs/llm/）
    config_path = Path(__file__).resolve().parent.parent.parent / "docs" / "llm" / "owlclaw.llm.example.yaml"
    config = LLMConfig.from_yaml(config_path) if config_path.exists() else LLMConfig.default_for_owlclaw()
    # Mock 模式：不发起真实请求，返回预定义内容
    config.mock_mode = True
    config.mock_responses = {"default": "Hello from mock. This is a basic completion."}

    client = LLMClient(config)
    messages = [
        PromptBuilder.build_system_message("You are a helpful assistant."),
        PromptBuilder.build_user_message("Say hello in one sentence."),
    ]
    resp = await client.complete(messages)
    print("Model:", resp.model)
    print("Content:", resp.content)
    print("Tokens:", resp.prompt_tokens, "+", resp.completion_tokens, "cost:", resp.cost)


if __name__ == "__main__":
    asyncio.run(main())
