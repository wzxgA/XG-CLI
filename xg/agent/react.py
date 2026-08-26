"""ReAct 循环：LLM ↔ tool_calls ↔ tool_result 回灌。

单轮 run() 产出事件流：
  content（增量文本）→ tool_call / approval / tool_result（交替）→ … → done
步数上限与 token 预算触发时以终止事件收尾，循环安全停止。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import AsyncIterator, Literal

from xg.config.settings import Settings
from xg.llm.client import LlmClient, LlmError
from xg.llm.types import Message, ToolCall, ToolResult
from xg.memory.context import ConversationContext
from xg.memory.manager import MemoryManager
from xg.safety.hitl import ApprovalDecision, HITLPolicy
from xg.tool.registry import ToolRegistry

DEFAULT_SYSTEM_PROMPT = (
    "你是 XG，一个终端里的编程助手。你可以调用工具完成文件读写、搜索和命令执行等任务。"
    "遇到工具报错时，阅读错误信息并自行修正参数重试。"
    "回答保持简洁，用中文。"
)


@dataclass
class AgentEvent:
    """Agent 事件流单元。kind 含义：

    - content: 增量文本
    - tool_call: 模型发起一次工具调用（即将执行）
    - approval: HITL 审批结果（approved / rejected / modified）
    - tool_result: 工具执行完成（含被拒绝的 USER_REJECTED）
    - step_limit: 达到步数上限，循环终止
    - context_compacted: 历史已自动压缩
    - context_warning: 共享记忆被截断或记忆功能不可用
    - context_overflow / budget_exceeded: 上下文仍超限，循环终止
    - error: LLM 请求失败
    - done: 本轮正常结束
    """

    kind: Literal[
        "content", "tool_call", "approval", "tool_result", "step_limit",
        "budget_exceeded", "context_compacted", "context_warning",
        "context_overflow", "error", "done"
    ]
    text: str = ""
    tool_call: ToolCall | None = None
    tool_result: ToolResult | None = None
    decision: ApprovalDecision | None = None


class ReActAgent:
    def __init__(
        self,
        llm: LlmClient,
        tools: ToolRegistry,
        settings: Settings,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
        approval_policy: HITLPolicy | None = None,
        audit=None,
        memory_manager: MemoryManager | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.settings = settings
        self.approval_policy = approval_policy
        self.audit = audit
        self.memory_manager = memory_manager
        self.context = ConversationContext(
            system_prompt,
            settings,
            shared_provider=memory_manager.shared_sections if memory_manager else None,
        )
        self._reported_memory_warnings: set[str] = set()
        # 保持前四期公开属性兼容：外部追加 messages 会直接进入短期历史。
        self.messages = self.context.history

    def clear(self) -> None:
        """清空短期对话与摘要（保留基础 prompt 和共享记忆）。"""
        self.context.clear()

    def estimate_tokens(self) -> int:
        return self.context.estimate_request_tokens(self.tools.schemas())

    async def run(self, user_input: str) -> AsyncIterator[AgentEvent]:
        """执行一轮 ReAct 循环。"""
        self.context.append(Message(role="user", content=user_input))

        for _step in range(self.settings.tool_steps):
            if self.memory_manager is not None:
                for warning in self.memory_manager.warnings():
                    if warning not in self._reported_memory_warnings:
                        self._reported_memory_warnings.add(warning)
                        yield AgentEvent(kind="context_warning", text=warning)
            budget = await self.context.ensure_budget(self.llm, self.tools.schemas())
            for warning in budget.warnings:
                yield AgentEvent(kind="context_warning", text=warning)
            if budget.status == "compacted":
                yield AgentEvent(kind="context_compacted", text=budget.message)
            elif budget.status == "error":
                yield AgentEvent(kind="context_warning", text=budget.message)
            if not budget.proceed:
                # context_overflow 是第五期语义事件；budget_exceeded 保留给前四期
                # 调用方，确保旧 CLI/测试仍能识别安全终止。
                yield AgentEvent(kind="context_overflow", text=budget.message)
                yield AgentEvent(kind="budget_exceeded", text=budget.message)
                return

            request_messages = self.context.build_messages()

            content_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            try:
                async for event in self.llm.stream_chat(request_messages, self.tools.schemas()):
                    if event.kind == "content" and event.text:
                        content_parts.append(event.text)
                        yield AgentEvent(kind="content", text=event.text)
                    elif event.kind == "tool_call" and event.tool_call:
                        tool_calls.append(event.tool_call)
            except LlmError as e:
                yield AgentEvent(kind="error", text=str(e))
                return

            if not tool_calls:
                # 无工具调用：本轮结束
                self.context.append(Message(role="assistant", content="".join(content_parts)))
                yield AgentEvent(kind="done")
                return

            # 记录 assistant 消息（含 tool_calls）
            self.context.append(
                Message(role="assistant", content="".join(content_parts), tool_calls=tool_calls)
            )
            for call in tool_calls:
                yield AgentEvent(kind="tool_call", tool_call=call)

            # HITL 审批：逐调用决策，被拒的不执行（未启用策略时静默放行）
            to_execute: list[ToolCall] = []
            rejected: dict[str, ToolResult] = {}
            for call in tool_calls:
                args = call.parsed_arguments()
                decision: ApprovalDecision | None = None
                if self.approval_policy is not None:
                    decision = await self.approval_policy.decide(call.name, args)
                    if self.audit is not None:
                        self.audit.approval(
                            tool=call.name, args=args,
                            decision="approve" if decision.allow else "deny",
                            reason=decision.reason,
                        )
                if decision is not None and not decision.allow:
                    rejected[call.id] = ToolResult(
                        tool_call_id=call.id, name=call.name, ok=False,
                        error=f"USER_REJECTED（{decision.reason or 'user_rejected'}）",
                    )
                    yield AgentEvent(
                        kind="approval", tool_call=call, decision=decision, text="rejected"
                    )
                    continue
                final_call = call
                if decision is not None:
                    if decision.args is not None:
                        final_call = ToolCall(
                            id=call.id, name=call.name,
                            arguments=json.dumps(decision.args, ensure_ascii=False),
                        )
                        yield AgentEvent(
                            kind="approval", tool_call=final_call, decision=decision, text="modified"
                        )
                    else:
                        yield AgentEvent(
                            kind="approval", tool_call=call, decision=decision, text="approved"
                        )
                to_execute.append(final_call)

            # 并行执行已批准的调用（默认 4 并发，统一超时），结果按原始顺序回灌
            executed = await self.tools.aexecute_calls(
                to_execute,
                concurrency=self.settings.max_parallel,
                timeout=self.settings.tool_timeout,
            )
            results = {r.tool_call_id: r for r in executed}
            for call in tool_calls:
                result = results.get(call.id) or rejected.get(call.id) or ToolResult(
                    tool_call_id=call.id, name=call.name, ok=False, error="未执行"
                )
                yield AgentEvent(kind="tool_result", tool_result=result)
                self.context.append(
                    Message(
                        role="tool",
                        content=result.to_message_content(),
                        tool_call_id=result.tool_call_id,
                    )
                )

        # 步数用尽仍未结束
        yield AgentEvent(kind="step_limit")
