"""LLM 层测试：respx mock SSE 流。"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from xg.llm.client import LlmError
from xg.llm.openai_compat import OpenAICompatClient
from xg.llm.types import Message

API_URL = "https://api.test/v1/chat/completions"


def sse_response(*chunks: dict) -> httpx.Response:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode("utf-8"), headers={"Content-Type": "text/event-stream"})


async def collect(client, messages):
    return [e async for e in client.stream_chat(messages)]


class TestContentStreaming:
    @respx.mock
    async def test_content_deltas_and_done(self, settings):
        respx.post(API_URL).mock(return_value=sse_response(
            {"choices": [{"delta": {"content": "你"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "好"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}},
        ))
        client = OpenAICompatClient(settings.api_base, settings.api_key, settings.model)
        events = await collect(client, [Message(role="user", content="hi")])

        assert [e.kind for e in events] == ["content", "content", "done"]
        assert events[0].text == "你"
        assert events[1].text == "好"
        assert events[2].finish_reason == "stop"
        assert events[2].usage.total_tokens == 12

    @respx.mock
    async def test_reasoning_content_is_normalized_to_thinking(self, settings):
        respx.post(API_URL).mock(return_value=sse_response(
            {"choices": [{"delta": {"reasoning_content": "先检查配置"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "检查完成"}, "finish_reason": "stop"}]},
        ))
        client = OpenAICompatClient(settings.api_base, settings.api_key, settings.model)
        events = await collect(client, [Message(role="user", content="inspect")])
        assert [event.kind for event in events] == ["thinking", "content", "done"]
        assert events[0].text == "先检查配置"


class TestToolCallAggregation:
    @respx.mock
    async def test_tool_call_fragments_merged(self, settings):
        """tool_call 的 name 与 arguments 分片应正确聚合。"""
        respx.post(API_URL).mock(return_value=sse_response(
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "read_", "arguments": "{\"pa"}}
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"name": "file", "arguments": "th\": \"a.py\"}"}}
            ]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ))
        client = OpenAICompatClient(settings.api_base, settings.api_key, settings.model)
        events = await collect(client, [Message(role="user", content="read a.py")])

        tool_events = [e for e in events if e.kind == "tool_call"]
        assert len(tool_events) == 1
        tc = tool_events[0].tool_call
        assert tc.id == "call_1"
        assert tc.name == "read_file"
        assert tc.parsed_arguments() == {"path": "a.py"}
        assert events[-1].kind == "done"

    @respx.mock
    async def test_parallel_tool_calls_kept_in_index_order(self, settings):
        respx.post(API_URL).mock(return_value=sse_response(
            {"choices": [{"delta": {"tool_calls": [
                {"index": 1, "id": "c2", "function": {"name": "tool_b", "arguments": "{}"}}
            ]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "c1", "function": {"name": "tool_a", "arguments": "{}"}}
            ]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ))
        client = OpenAICompatClient(settings.api_base, settings.api_key, settings.model)
        events = await collect(client, [Message(role="user", content="go")])

        calls = [e.tool_call for e in events if e.kind == "tool_call"]
        assert [c.name for c in calls] == ["tool_a", "tool_b"]


class TestErrors:
    @respx.mock
    async def test_http_error_surfaces_body(self, settings):
        respx.post(API_URL).mock(return_value=httpx.Response(401, text='{"error": "bad key"}'))
        client = OpenAICompatClient(settings.api_base, settings.api_key, settings.model)
        with pytest.raises(LlmError, match="401"):
            await collect(client, [Message(role="user", content="hi")])

    async def test_missing_config_raises(self):
        with pytest.raises(LlmError, match="XG_<PROVIDER>_API_BASE"):
            OpenAICompatClient("", "", "m")


class TestMessageFormat:
    def test_assistant_tool_calls_to_api(self):
        from xg.llm.types import ToolCall

        msg = Message(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="c1", name="read_file", arguments='{"path": "a"}')],
        )
        api = msg.to_api()
        assert api["tool_calls"][0]["function"]["name"] == "read_file"
        assert api["content"] is None

    def test_tool_result_to_api(self):
        msg = Message(role="tool", content="ok", tool_call_id="c1")
        api = msg.to_api()
        assert api["tool_call_id"] == "c1"
        assert api["content"] == "ok"
