"""OpenAI-compatible Chat Completions 实现（httpx SSE 流式 + tool calling 增量聚合）。

所有 provider（GLM / DeepSeek / Kimi / OpenAI 兼容）共享此模板。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from xg.llm.client import LlmClient, LlmError
from xg.llm.types import Message, StreamEvent, ToolCall, Usage

DEFAULT_TIMEOUT = 120.0


class OpenAICompatClient(LlmClient):
    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not api_base or not api_key:
            raise LlmError("缺少 API 配置：请设置 XG_API_BASE / XG_API_KEY（见 .env.example）")
        self.api_base = api_base.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def stream_chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> AsyncIterator[StreamEvent]:
        return self._stream(messages, tools)

    async def _stream(
        self, messages: list[Message], tools: list[dict] | None
    ) -> AsyncIterator[StreamEvent]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_api() for m in messages],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": t} for t in tools
            ]

        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code != 200:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                        raise LlmError(f"API 返回 {resp.status_code}: {body[:500]}")
                    async for event in self._parse_sse(resp):
                        yield event
        except httpx.TimeoutException as e:
            raise LlmError(f"请求超时（{self.timeout}s）: {e}") from e
        except httpx.HTTPError as e:
            raise LlmError(f"网络错误: {e}") from e

    async def _parse_sse(self, resp: httpx.Response) -> AsyncIterator[StreamEvent]:
        """解析 SSE 流，聚合 content 与 tool_call 增量分片。"""
        # 按 index 聚合中的 tool_call 分片
        pending: dict[int, dict[str, str]] = {}
        finish_reason = ""
        usage = Usage()

        async for line in resp.aiter_lines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except json.JSONDecodeError:
                continue

            if chunk.get("usage"):
                u = chunk["usage"]
                usage = Usage(
                    prompt_tokens=u.get("prompt_tokens", 0),
                    completion_tokens=u.get("completion_tokens", 0),
                    total_tokens=u.get("total_tokens", 0),
                )

            for choice in chunk.get("choices", []):
                delta = choice.get("delta") or {}
                if delta.get("content"):
                    yield StreamEvent(kind="content", text=delta["content"])

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    frag = pending.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        frag["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        frag["name"] = frag["name"] + fn["name"]
                    if fn.get("arguments"):
                        frag["arguments"] = frag["arguments"] + fn["arguments"]

                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]

        # 流结束：统一发出聚合后的 tool_call，再发 done
        for idx in sorted(pending):
            frag = pending[idx]
            if not frag["name"] and not frag["arguments"]:
                continue
            yield StreamEvent(
                kind="tool_call",
                tool_call=ToolCall(
                    id=frag["id"] or f"call_{idx}",
                    name=frag["name"],
                    arguments=frag["arguments"],
                ),
            )
        yield StreamEvent(kind="done", finish_reason=finish_reason, usage=usage)
