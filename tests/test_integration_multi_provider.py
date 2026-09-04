"""多 provider 集成测试：respx mock 两个 provider 端点，同一任务均可完成；切换后继续对话。"""

from __future__ import annotations

import json

import httpx
import respx

from xg.agent.react import ReActAgent
from xg.cli.app import _handle_command
from xg.config.manager import ConfigManager
from xg.config.settings import Settings
from xg.tool.builtin import build_registry

from tests.conftest import seed_config

BASE_A = "https://api.openai.com/v1"
BASE_B = "https://api.deepseek.com/v1"

WRITE_ARGS = json.dumps({"path": "out.txt", "content": "hello multi-provider"})


def sse_response(*chunks) -> httpx.Response:
    body = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, content=body.encode("utf-8"), headers={"Content-Type": "text/event-stream"})


def tool_call_chunk(tool_name: str, arguments: str) -> dict:
    return {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {"index": 0, "id": "call_1", "function": {"name": tool_name, "arguments": arguments}}
                    ]
                }
            }
        ]
    }


def content_chunk(text: str) -> dict:
    return {"choices": [{"delta": {"content": text}}]}


def finish_chunk(reason: str) -> dict:
    return {"choices": [{"delta": {}, "finish_reason": reason}]}


def make_manager(tmp_path, env: dict) -> ConfigManager:
    user_dir = tmp_path / "user_xg"
    project_dir = tmp_path / "proj_xg"
    user_dir.mkdir(exist_ok=True)
    project_dir.mkdir(exist_ok=True)
    # 注入默认自定义 providers（openai/deepseek/glm/kimi），并把 env 里的
    # XG_<NAME>_API_KEY 迁到 providers.<name>.api_key（provider 身份/key 一律来自 config）。
    cfg_path = user_dir / "config.json"
    cfg = seed_config({}) if not cfg_path.exists() else json.loads(cfg_path.read_text(encoding="utf-8"))
    for pname, pdef in cfg["providers"].items():
        key = env.get(f"XG_{pname.upper()}_API_KEY")
        if key:
            pdef["api_key"] = key
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    return ConfigManager(user_dir=user_dir, project_dir=project_dir, env=dict(env), load_env=False)


@respx.mock
async def test_same_task_on_two_providers(tmp_path):
    """同一多步任务（写文件）在两个 provider 上分别跑通。"""
    requests_a: list[dict] = []
    requests_b: list[dict] = []

    def handler_a(request: httpx.Request) -> httpx.Response:
        requests_a.append(json.loads(request.content))
        if len(requests_a) == 1:
            return sse_response(tool_call_chunk("write_file", WRITE_ARGS), finish_chunk("tool_calls"))
        return sse_response(content_chunk("A 完成。"), finish_chunk("stop"))

    def handler_b(request: httpx.Request) -> httpx.Response:
        requests_b.append(json.loads(request.content))
        if len(requests_b) == 1:
            return sse_response(tool_call_chunk("write_file", WRITE_ARGS), finish_chunk("tool_calls"))
        return sse_response(content_chunk("B 完成。"), finish_chunk("stop"))

    respx.post(f"{BASE_A}/chat/completions").mock(side_effect=handler_a)
    respx.post(f"{BASE_B}/chat/completions").mock(side_effect=handler_b)

    from xg.llm.factory import create_client

    for base, key, model in (
        (BASE_A, "ka", "gpt-4o-mini"),
        (BASE_B, "kb", "deepseek-chat"),
    ):
        settings = Settings(provider="x", api_base=base, api_key=key, model=model, context_window=128_000)
        agent = ReActAgent(llm=create_client(base, key, model), tools=build_registry(base_dir=tmp_path), settings=settings)

        events = [e async for e in agent.run("写一个文件")]
        assert events[-1].kind == "done"
        assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello multi-provider"
        (tmp_path / "out.txt").unlink()

    assert len(requests_a) == 2
    assert len(requests_b) == 2
    # 两个 provider 的第二次请求都带上了 tool 结果
    for requests in (requests_a, requests_b):
        roles = [m["role"] for m in requests[1]["messages"]]
        assert roles == ["system", "user", "assistant", "tool"]


@respx.mock
async def test_switch_mid_conversation_preserves_history(tmp_path):
    """对话中途 /model 切换 provider，历史（含 tool 结果）保留并继续。"""
    requests_a: list[dict] = []
    requests_b: list[dict] = []

    def handler_a(request: httpx.Request) -> httpx.Response:
        requests_a.append(json.loads(request.content))
        if len(requests_a) == 1:
            return sse_response(tool_call_chunk("write_file", WRITE_ARGS), finish_chunk("tool_calls"))
        return sse_response(content_chunk("A 回复"), finish_chunk("stop"))

    def handler_b(request: httpx.Request) -> httpx.Response:
        requests_b.append(json.loads(request.content))
        return sse_response(content_chunk("B 收到历史。"), finish_chunk("stop"))

    respx.post(f"{BASE_A}/chat/completions").mock(side_effect=handler_a)
    respx.post(f"{BASE_B}/chat/completions").mock(side_effect=handler_b)

    from xg.llm.factory import create_client

    env = {"XG_API_KEY": "ka", "XG_DEEPSEEK_API_KEY": "kb"}
    manager = make_manager(tmp_path, env)
    settings = Settings(
        provider="openai", api_base=BASE_A, api_key="ka", model="gpt-4o-mini", context_window=128_000
    )
    agent = ReActAgent(
        llm=create_client(BASE_A, "ka", "gpt-4o-mini"),
        tools=build_registry(base_dir=tmp_path),
        settings=settings,
    )

    # 第一轮：写文件（走 provider A）
    events = [e async for e in agent.run("写文件")]
    assert events[-1].kind == "done"
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "hello multi-provider"

    # 中途切换 base：openai -> deepseek（/provider switch）
    message, _ = _handle_command(agent, settings, manager, "/provider switch deepseek")
    assert "已切换" in message
    assert agent.llm.api_base == BASE_B  # type: ignore[attr-defined]

    # 第二轮：继续对话（走 provider B），历史应包含第一轮的 tool 消息
    events = [e async for e in agent.run("继续")]
    assert events[-1].kind == "done"

    b_messages = requests_b[0]["messages"]
    roles = [m["role"] for m in b_messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant", "user"]
    tool_msg = next(m for m in b_messages if m["role"] == "tool")
    assert "out.txt" in tool_msg["content"]
    # B 的请求里不应出现 A 的 base url 相关痕迹（模型名跟随切换）
    assert b_messages[0]["role"] == "system"
