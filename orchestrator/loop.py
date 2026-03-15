"""
Tool-calling loop for the Lumogis orchestrator.

Calls the Anthropic API (via LiteLLM proxy) with the configured tools,
handles tool_use blocks by executing tools and appending results, and
repeats until the model returns end_turn. Returns the final assistant text.
"""

from clients.litellm import get_client
from dotenv import load_dotenv
from services.tools import TOOLS
from services.tools import run_tool

load_dotenv()


def ask(
    question: str,
    history: list | None = None,
    model: str = "claude",
    use_tools: bool = True,
) -> str:
    client = get_client()
    messages = list(history) if history else []
    messages.append({"role": "user", "content": question})

    while True:
        create_kwargs = {
            "model": model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if use_tools:
            create_kwargs["tools"] = TOOLS
        response = client.messages.create(**create_kwargs)

        text_parts = []
        tool_results = []

        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                result = run_tool(block.name, block.input)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )

        assistant_content = [
            block.model_dump() if hasattr(block, "model_dump") else block
            for block in response.content
        ]
        messages.append({"role": "assistant", "content": assistant_content})

        if response.stop_reason == "end_turn":
            return "".join(text_parts)

        if response.stop_reason != "tool_use" or not tool_results:
            return "".join(text_parts) if text_parts else ""

        messages.append({"role": "user", "content": tool_results})
