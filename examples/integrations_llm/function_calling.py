"""Function calling 示例：将 capabilities 转为 tools、传入 complete、解析 function_calls（mock 模式）。"""

import asyncio
from pathlib import Path

from owlclaw.integrations.llm import (
    LLMClient,
    LLMConfig,
    PromptBuilder,
    ToolsConverter,
)


async def main() -> None:
    config_path = Path(__file__).resolve().parent.parent.parent / "docs" / "llm" / "owlclaw.llm.example.yaml"
    config = LLMConfig.from_yaml(config_path) if config_path.exists() else LLMConfig.default_for_owlclaw()
    config.mock_mode = True
    # Mock 返回文本（当前 mock 不支持 function_calls 响应，仅演示 tools 传入与 API 用法）
    config.mock_responses = {"default": "I would call get_weather if I could."}

    capabilities = [
        {
            "name": "get_weather",
            "description": "Get current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "City name"}},
                "required": ["city"],
            },
        },
    ]
    tools = ToolsConverter.capabilities_to_tools(capabilities)

    client = LLMClient(config)
    messages = [
        PromptBuilder.build_system_message("You are a helpful assistant. Use tools when needed."),
        PromptBuilder.build_user_message("What is the weather in Beijing?"),
    ]
    resp = await client.complete(messages, tools=tools)
    print("Model:", resp.model)
    print("Content:", resp.content)
    print("Function calls:", resp.function_calls)
    print("Tools passed:", len(tools), "tool(s)")


if __name__ == "__main__":
    asyncio.run(main())
