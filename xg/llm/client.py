"""LlmClient 抽象基类。所有 provider 实现统一的流式对话接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncIterator

from xg.llm.types import Message, StreamEvent


class LlmError(Exception):
    """LLM 请求失败（网络 / 鉴权 / 超时 / 协议错误）。"""


class LlmClient(ABC):
    """流式 Chat Completions 客户端抽象。"""

    @abstractmethod
    def stream_chat(
        self, messages: list[Message], tools: list[dict] | None = None
    ) -> AsyncIterator[StreamEvent]:
        """流式对话。

        产出事件序列：content* → tool_call* → done。
        失败时抛出 LlmError，消息面向用户可直接展示。
        """
