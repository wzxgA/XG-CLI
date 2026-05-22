"""ToolRegistry：工具注册、schema 导出、按原始 tool_call 顺序执行。

第 1 期为顺序执行的简单注册表；并行执行在第 3 期引入。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Callable

from xg.llm.types import ToolCall, ToolResult


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema（properties / required）
    handler: Callable[[dict], ToolResult]


class ToolRegistry:
    def __init__(self, max_output_chars: int = 20_000) -> None:
        self._tools: dict[str, Tool] = {}
        self.max_output_chars = max_output_chars

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict]:
        """导出为 OpenAI tools 参数所需的 function 定义列表。"""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            }
            for t in self._tools.values()
        ]

    def execute(self, name: str, args: dict) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                tool_call_id="", name=name, ok=False,
                error=f"未知工具: {name}，可用工具: {', '.join(self._tools)}",
            )
        try:
            result = tool.handler(args)
        except Exception as e:  # 工具内部异常统一转为 error 回灌
            result = ToolResult(tool_call_id="", name=name, ok=False, error=f"{type(e).__name__}: {e}")
        if len(result.output) > self.max_output_chars:
            result.output = (
                result.output[: self.max_output_chars]
                + f"\n... (输出已截断，原始长度 {len(result.output)} 字符)"
            )
        return result

    def execute_calls(self, calls: list[ToolCall]) -> list[ToolResult]:
        """按原始 tool_call 顺序执行（结果保序）。"""
        results: list[ToolResult] = []
        for call in calls:
            result = self.execute(call.name, call.parsed_arguments())
            result.tool_call_id = call.id
            results.append(result)
        return results
