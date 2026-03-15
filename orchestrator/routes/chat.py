"""Chat endpoints: /ask and /v1/chat/completions."""

import json
import time
from typing import Any
from typing import Generator
from typing import List
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from loop import ask
from pydantic import BaseModel

router = APIRouter()


class AskRequest(BaseModel):
    text: str


class AskResponse(BaseModel):
    answer: str


@router.post("/ask", response_model=AskResponse)
def ask_endpoint(body: AskRequest) -> AskResponse:
    answer = ask(body.text, history=[])
    return AskResponse(answer=answer)


class ChatMessage(BaseModel):
    role: str
    content: Optional[str | List[Any]] = None


class ChatCompletionsRequest(BaseModel):
    model: str = "lumogis-1"
    messages: List[ChatMessage]
    stream: bool = False


def _content_to_str(content: Optional[str | List[Any]]) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text", ""))
        elif isinstance(part, str):
            parts.append(part)
    return "".join(parts)


def _sse_chunk(chunk_id: str, created: int, model: str, delta: dict, finish: str | None) -> str:
    payload = {
        "id": chunk_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload)}\n\n"


def stream_completion(answer: str, model: str) -> Generator[str, None, None]:
    cid = "chatcmpl-lumogis"
    created = int(time.time())
    yield _sse_chunk(cid, created, model, {"role": "assistant", "content": ""}, None)
    for word in answer.split():
        yield _sse_chunk(cid, created, model, {"content": word + " "}, None)
    yield _sse_chunk(cid, created, model, {}, "stop")
    yield "data: [DONE]\n\n"


@router.post("/v1/chat/completions")
def chat_completions(body: ChatCompletionsRequest) -> Any:
    if not body.messages:
        answer = ""
    else:
        last = body.messages[-1]
        question = _content_to_str(last.content)
        history = []
        for m in body.messages[:-1]:
            text = _content_to_str(m.content)
            history.append({"role": m.role, "content": text})
        use_tools = body.model != "local"
        answer = ask(question, history=history, model=body.model, use_tools=use_tools)

    if body.stream:
        return StreamingResponse(
            stream_completion(answer, body.model),
            media_type="text/event-stream",
        )

    return {
        "id": "chatcmpl-lumogis",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
