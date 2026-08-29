"""Multi-Agent Team 编排（第 10 期 MVP）。

Team 是建立在现有 Plan/ReAct 之上的协作控制面：
- Planner 生成带角色、资源范围和验收标准的任务 DAG；
- Supervisor 按依赖和资源冲突调度隔离上下文的 Worker；
- Reviewer 基于 Artifact 和执行证据做任务级审查；
- 失败任务生成有边界的 Repair Worker，最多重试两次。

Worker 仍然通过 ReActAgent -> ToolRegistry -> Guard/HITL -> Audit 执行，
Team 层不提供绕过现有安全链路的内部通道。
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import re
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, AsyncIterator, Awaitable, Callable, Literal, Protocol

from xg.agent.plan import PlanError, ReviewDecision, build_batches
from xg.agent.react import AgentEvent, DEFAULT_SYSTEM_PROMPT, ReActAgent
from xg.config.settings import Settings
from xg.llm.client import LlmClient, LlmError
from xg.llm.types import Message, ToolCall, ToolResult, Usage
from xg.memory.context import ConversationContext
from xg.memory.manager import MemoryManager
from xg.safety.hitl import HITLPolicy
from xg.tool.registry import ToolRegistry

if TYPE_CHECKING:
    from xg.mcp.manager import McpManager


TEAM_MAX_RETRIES = 2
TEAM_RESULT_LIMIT = 2000
TEAM_ARTIFACT_LIMIT = 4000
TEAM_REVIEW_LIMIT = 4000
TEAM_PLAN_MAX_RETRIES = 2


TEAM_PLANNER_PROMPT = (
    "你是 XG 的团队任务规划器。将用户任务拆解为可执行的 DAG，"
    "为每个任务指定角色、工具范围、资源范围和可验证的验收标准。"
    "只输出一个 JSON 对象，不要输出其他文本或 markdown，格式：\n"
    "{\"tasks\": [{\"id\": \"t1\", \"title\": \"一句话标题\", "
    "\"description\": \"执行说明\", \"deps\": [], "
    "\"owner_role\": \"coder\", "
    "\"allowed_tools\": [\"read_file\", \"write_file\"], "
    "\"resource_claims\": [{\"pattern\": \"src/*\", "
    "\"access\": \"write\", \"exclusive\": false}], "
    "\"acceptance_criteria\": [\"可验证条件\"]}]}\n"
    "规则：\n"
    "- id 全局唯一，形如 t1/t2；deps 只能引用其他任务 id；\n"
    "- 依赖必须是无环 DAG；\n"
    "- owner_role 使用 coder、researcher、tester、reviewer 或 repairer；\n"
    "- 只读任务不得声明 write 工具；\n"
    "- 无法判断命令副作用时使用 exclusive=true；\n"
    "- 每个任务必须有至少一条 acceptance_criteria。"
)

TEAM_REVIEWER_PROMPT = (
    "你是严格的任务审查 Agent。你不能修改文件，只能根据任务验收标准、"
    "实际工具结果和任务产物判断是否通过。只输出 JSON："
    '{"verdict":"pass|fail|needs_input","findings":["问题"],'
    '"required_fixes":["定向修复要求"],"evidence":["证据"]}。'
    "不要把 Worker 的主观汇报当成测试通过证据。"
)


@dataclass
class ResourceClaim:
    """任务对项目资源的访问声明。"""

    pattern: str
    access: Literal["read", "write"] = "read"
    exclusive: bool = False

    def normalized(self) -> str:
        return self.pattern.replace("\\", "/").lstrip("./") or "**"


@dataclass
class AgentProfile:
    """一个可注册的 Agent 角色配置。"""

    name: str
    system_prompt: str
    allowed_tools: tuple[str, ...] = ()  # 空 tuple 表示使用所有已注册工具
    default_model: str | None = None
    max_steps: int | None = None
    can_write: bool = False
    is_reviewer: bool = False


@dataclass
class Artifact:
    """Worker 产生的可传递、可验证任务产物。"""

    id: str
    task_id: str
    kind: str
    uri: str = ""
    summary: str = ""
    checksum: str = ""
    producer_agent_id: str = ""
    version: int = 1
    parent_artifacts: list[str] = field(default_factory=list)
    verification_records: list[str] = field(default_factory=list)


@dataclass
class ReviewResult:
    task_id: str
    verdict: Literal["pass", "fail", "needs_input"]
    findings: list[str] = field(default_factory=list)
    required_fixes: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


@dataclass
class TeamTask:
    id: str
    title: str
    description: str
    deps: list[str]
    owner_role: str = "coder"
    allowed_tools: list[str] = field(default_factory=list)
    resource_claims: list[ResourceClaim] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    input_artifacts: list[str] = field(default_factory=list)
    output_artifacts: list[str] = field(default_factory=list)
    status: str = "pending"
    attempts: int = 0
    result: str = ""
    artifacts: list[str] = field(default_factory=list)


@dataclass
class TeamPlan:
    goal: str
    tasks: list[TeamTask]
    batches: list[list[str]]

    def task_by_id(self, task_id: str) -> TeamTask | None:
        return next((task for task in self.tasks if task.id == task_id), None)


@dataclass
class TeamEvent:
    """Team 层事件；内部 AgentEvent 通过 agent_event 嵌套转发。"""

    kind: Literal[
        "team_started", "team_plan_generated", "team_review", "approved",
        "replanned", "batch_started", "task_started", "task_done",
        "task_failed", "agent_started", "agent_done", "agent_failed",
        "subtask_event", "artifact_produced", "task_review_started",
        "task_review_done", "repair_requested", "team_done", "team_failed",
        "cancelled",
    ]
    team_id: str = ""
    plan: TeamPlan | None = None
    batch: list[str] = field(default_factory=list)
    task: TeamTask | None = None
    agent_id: str = ""
    role: str = ""
    artifact: Artifact | None = None
    review: ReviewResult | None = None
    agent_event: AgentEvent | None = None
    message: str = ""
    usage: Usage | None = None


class Planner(Protocol):
    async def create_plan(self, goal: str) -> TeamPlan: ...


class AgentFactory(Protocol):
    def create(self, profile: AgentProfile, task: TeamTask) -> ReActAgent: ...


class ArtifactStore(Protocol):
    async def publish(self, artifact: Artifact) -> None: ...
    async def get(self, artifact_id: str) -> Artifact | None: ...
    async def for_task(self, task_id: str) -> list[Artifact]: ...


class Reviewer(Protocol):
    async def review(self, task: TeamTask, artifacts: list[Artifact]) -> ReviewResult: ...


class Scheduler(Protocol):
    async def schedule(self, plan: TeamPlan) -> list[list[str]]: ...


class InMemoryArtifactStore:
    """MVP 的进程内 ArtifactStore；后续可替换为 SQLite 或文件实现。"""

    def __init__(self) -> None:
        self._items: dict[str, Artifact] = {}

    async def publish(self, artifact: Artifact) -> None:
        self._items[artifact.id] = artifact

    async def get(self, artifact_id: str) -> Artifact | None:
        return self._items.get(artifact_id)

    async def for_task(self, task_id: str) -> list[Artifact]:
        return [item for item in self._items.values() if item.task_id == task_id]

    async def get_many(self, artifact_ids: list[str]) -> list[Artifact]:
        return [self._items[item_id] for item_id in artifact_ids if item_id in self._items]


class ScopedToolRegistry:
    """给 Worker 暴露工具和资源范围的受限视图。"""

    def __init__(self, base: ToolRegistry, task: TeamTask, project_root: Path, profile: AgentProfile) -> None:
        self._base = base
        self._task = task
        self._project_root = project_root.resolve()
        self._profile = profile

    def schemas(self) -> list[dict]:
        schemas = self._base.schemas()
        allowed = self._allowed_tools()
        if not allowed:
            return schemas
        return [schema for schema in schemas if schema.get("name") in allowed]

    async def aexecute_calls(self, calls: list[ToolCall], concurrency: int = 4, timeout: float = 120.0) -> list[ToolResult]:
        allowed = self._allowed_tools()
        executable: list[ToolCall] = []
        rejected: dict[str, ToolResult] = {}
        for call in calls:
            if allowed and call.name not in allowed:
                rejected[call.id] = ToolResult(
                    tool_call_id=call.id, name=call.name, ok=False,
                    error=f"角色 {self._profile.name} 不允许调用工具: {call.name}",
                )
                continue
            if not self._resource_allowed(call):
                rejected[call.id] = ToolResult(
                    tool_call_id=call.id, name=call.name, ok=False,
                    error=f"任务资源范围拒绝工具调用: {call.name}",
                )
                continue
            executable.append(call)
        results = await self._base.aexecute_calls(executable, concurrency=concurrency, timeout=timeout)
        by_id = {result.tool_call_id: result for result in results}
        by_id.update(rejected)
        return [by_id.get(call.id) or ToolResult(
            tool_call_id=call.id, name=call.name, ok=False, error="工具未执行"
        ) for call in calls]

    def _allowed_tools(self) -> set[str]:
        profile_tools = set(self._profile.allowed_tools)
        task_tools = set(self._task.allowed_tools)
        if profile_tools and task_tools:
            return profile_tools & task_tools
        return profile_tools or task_tools

    def _resource_allowed(self, call: ToolCall) -> bool:
        claims = self._task.resource_claims
        if not claims or call.name not in {"read_file", "write_file", "list_dir", "glob_files", "grep_code"}:
            return True
        args = call.parsed_arguments()
        raw = str(args.get("path", ""))
        if not raw:
            return True
        path = Path(raw)
        if path.is_absolute():
            try:
                relative = path.resolve().relative_to(self._project_root).as_posix()
            except ValueError:
                return False
        else:
            relative = PurePosixPath(raw.replace("\\", "/")).as_posix().lstrip("./")
        wants_write = call.name == "write_file"
        for claim in claims:
            pattern = claim.normalized()
            matches = fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(relative, pattern.rstrip("/*") + "/**")
            if matches and (not wants_write or claim.access == "write"):
                return True
        return False

    def __getattr__(self, name: str):
        return getattr(self._base, name)


def default_profiles() -> dict[str, AgentProfile]:
    all_tools = ()
    readonly = ("read_file", "list_dir", "glob_files", "grep_code", "web_search", "web_fetch", "load_skill")
    writable = ("read_file", "write_file", "list_dir", "glob_files", "grep_code", "execute_command", "web_search", "web_fetch", "load_skill")
    return {
        "coder": AgentProfile(
            name="coder",
            system_prompt="你是一名谨慎的代码实现 Agent。只处理当前任务，先阅读相关代码，再实现并验证；不要处理其他任务。",
            allowed_tools=writable,
            can_write=True,
        ),
        "researcher": AgentProfile(
            name="researcher",
            system_prompt="你是一名研究 Agent。只读取和分析资料，输出有来源的结论，不修改项目文件。",
            allowed_tools=readonly,
        ),
        "tester": AgentProfile(
            name="tester",
            system_prompt="你是一名测试 Agent。负责编写或执行当前任务的测试，并准确报告测试命令和结果。",
            allowed_tools=writable,
            can_write=True,
        ),
        "reviewer": AgentProfile(
            name="reviewer",
            system_prompt=TEAM_REVIEWER_PROMPT,
            allowed_tools=readonly,
            is_reviewer=True,
        ),
        "repairer": AgentProfile(
            name="repairer",
            system_prompt="你是一名定向修复 Agent。只修复 Reviewer 列出的 required_fixes，不扩大任务范围。",
            allowed_tools=writable,
            can_write=True,
        ),
        "synthesizer": AgentProfile(
            name="synthesizer",
            system_prompt="你是一名结果汇总 Agent。只汇总已经验证的任务产物，不修改项目文件。",
            allowed_tools=all_tools,
        ),
    }


def _strip_json(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[\w-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    return text[start:end + 1] if start >= 0 and end > start else text


def parse_team_tasks(
    raw: str,
    max_tasks: int = 12,
    profiles: dict[str, AgentProfile] | None = None,
) -> tuple[list[TeamTask], list[str]]:
    """解析并校验 Team Planner 输出。"""
    try:
        data = json.loads(_strip_json(raw))
    except json.JSONDecodeError as exc:
        raise PlanError(f"Team 计划 JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list) or not data["tasks"]:
        raise PlanError('Team 计划顶层结构必须是 {"tasks": [...]} 且不能为空')

    profiles = profiles or default_profiles()
    tasks: list[TeamTask] = []
    warnings: list[str] = []
    for index, item in enumerate(data["tasks"]):
        if not isinstance(item, dict):
            raise PlanError(f"tasks[{index}] 必须是对象")
        task_id = str(item.get("id", "")).strip()
        title = str(item.get("title", "")).strip()
        if not task_id or not title:
            raise PlanError(f"tasks[{index}] 缺少 id 或 title")
        deps_raw = item.get("deps", [])
        if not isinstance(deps_raw, list):
            raise PlanError(f"tasks[{index}].deps 必须是数组")
        deps = list(dict.fromkeys(str(dep).strip() for dep in deps_raw if str(dep).strip()))
        role = str(item.get("owner_role") or "coder").strip().lower()
        if role not in profiles:
            warnings.append(f"任务 {task_id} 使用未知角色 {role}，已回退为 coder")
            role = "coder"
        allowed_raw = item.get("allowed_tools", [])
        allowed = [str(name).strip() for name in allowed_raw if str(name).strip()] if isinstance(allowed_raw, list) else []
        claims_raw = item.get("resource_claims", [])
        claims: list[ResourceClaim] = []
        if isinstance(claims_raw, list):
            for claim in claims_raw:
                if not isinstance(claim, dict):
                    continue
                pattern = str(claim.get("pattern", "")).strip()
                access = str(claim.get("access", "read")).strip().lower()
                if pattern and access in {"read", "write"}:
                    claims.append(ResourceClaim(pattern, access, bool(claim.get("exclusive", False))))
        criteria_raw = item.get("acceptance_criteria", [])
        criteria = [str(value).strip() for value in criteria_raw if str(value).strip()] if isinstance(criteria_raw, list) else []
        if not criteria:
            criteria = [f"完成任务：{title}"]
            warnings.append(f"任务 {task_id} 缺少验收标准，已使用标题作为最低验收标准")
        description = str(item.get("description") or title).strip()
        tasks.append(TeamTask(
            id=task_id,
            title=title,
            description=description,
            deps=deps,
            owner_role=role,
            allowed_tools=allowed,
            resource_claims=claims,
            acceptance_criteria=criteria,
            input_artifacts=[str(value) for value in item.get("input_artifacts", []) if str(value)] if isinstance(item.get("input_artifacts", []), list) else [],
            output_artifacts=[str(value) for value in item.get("output_artifacts", []) if str(value)] if isinstance(item.get("output_artifacts", []), list) else [],
        ))

    if len({task.id for task in tasks}) != len(tasks):
        raise PlanError("Team 计划存在重复的任务 id")
    if len(tasks) > max_tasks:
        warnings.append(f"Team 任务数 {len(tasks)} 超过上限 {max_tasks}，已截断")
        kept = {task.id for task in tasks[:max_tasks]}
        tasks = tasks[:max_tasks]
        for task in tasks:
            task.deps = [dep for dep in task.deps if dep in kept]
    known = {task.id for task in tasks}
    for task in tasks:
        for dep in list(task.deps):
            if dep == task.id:
                task.deps.remove(dep)
                warnings.append(f"任务 {task.id} 自依赖，已移除")
            elif dep not in known:
                task.deps.remove(dep)
                warnings.append(f"任务 {task.id} 引用了不存在的依赖 {dep}，已移除")
    try:
        build_batches(tasks)  # TeamTask 使用同样的 id/deps 接口
    except PlanError as exc:
        raise PlanError(f"Team 依赖无效：{exc}") from exc
    return tasks, warnings


def _resource_conflicts(left: TeamTask, right: TeamTask) -> bool:
    for a in left.resource_claims:
        for b in right.resource_claims:
            if not (a.exclusive or b.exclusive or a.access == "write" or b.access == "write"):
                continue
            pa, pb = a.normalized(), b.normalized()
            if fnmatch.fnmatch(pa, pb) or fnmatch.fnmatch(pb, pa) or pa.startswith(pb.rstrip("*").rstrip("/")) or pb.startswith(pa.rstrip("*").rstrip("/")):
                return True
    # 未声明资源的写入任务保守处理：无法知道它是否修改了相同资源。
    return bool(not left.resource_claims and not right.resource_claims and _task_may_write(left) and _task_may_write(right))


def _task_may_write(task: TeamTask) -> bool:
    if task.owner_role in {"coder", "tester", "repairer"}:
        return True
    return any(claim.access == "write" for claim in task.resource_claims)


def conflict_safe_batches(tasks: list[TeamTask]) -> list[list[str]]:
    """在 DAG 批次上进一步按资源冲突做确定性串行化。"""
    raw_batches = build_batches(tasks)
    by_id = {task.id: task for task in tasks}
    output: list[list[str]] = []
    for batch in raw_batches:
        safe: list[str] = []
        for task_id in batch:
            task = by_id[task_id]
            conflict_index = next(
                (index for index, existing_id in enumerate(safe) if _resource_conflicts(task, by_id[existing_id])),
                None,
            )
            if conflict_index is None:
                safe.append(task_id)
                continue
            # 把冲突任务放入新批次；保持确定性和原有排序。
            output.append(safe)
            safe = [task_id]
        if safe:
            output.append(safe)
    return output


TaskReviewCallback = Callable[[TeamTask, list[Artifact]], Awaitable[ReviewResult]]


class TeamExecutor:
    """Team 计划生成、调度、Worker 执行、审查和修复。"""

    def __init__(
        self,
        llm: LlmClient,
        tools: ToolRegistry,
        settings: Settings,
        reviewer: Callable[[TeamPlan], Awaitable[ReviewDecision]] | None = None,
        task_reviewer: TaskReviewCallback | None = None,
        approval_policy: HITLPolicy | None = None,
        audit=None,
        memory_manager: MemoryManager | None = None,
        mcp_manager: "McpManager | None" = None,
        profiles: dict[str, AgentProfile] | None = None,
        artifact_store: ArtifactStore | None = None,
        project_root: Path | None = None,
        team_id: str | None = None,
        agent_factory: AgentFactory | None = None,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.settings = settings
        self.reviewer = reviewer
        self.task_reviewer = task_reviewer
        self.approval_policy = approval_policy
        self.audit = audit
        self.memory_manager = memory_manager
        self.mcp_manager = mcp_manager
        self.profiles = profiles or default_profiles()
        self.artifacts: ArtifactStore = artifact_store or InMemoryArtifactStore()
        self.project_root = (project_root or Path.cwd()).resolve()
        self.team_id = team_id or f"team-{uuid.uuid4().hex[:8]}"
        self.agent_factory = agent_factory

    async def run(self, goal: str) -> AsyncIterator[TeamEvent]:
        if self.mcp_manager is not None:
            try:
                await self.mcp_manager.ensure_started()
                goal = await self.mcp_manager.expand_references(goal)
            except Exception as exc:
                yield TeamEvent(kind="team_failed", team_id=self.team_id, message=f"MCP resource 处理失败: {exc}")
                return

        yield TeamEvent(kind="team_started", team_id=self.team_id, message=goal)
        self._audit("team_started", team_id=self.team_id, goal=goal)
        try:
            plan, warnings = await self._generate_plan(goal)
        except LlmError as exc:
            yield TeamEvent(kind="team_failed", team_id=self.team_id, message=f"团队计划生成失败: {exc}")
            return
        if plan is None:
            yield TeamEvent(kind="team_failed", team_id=self.team_id, message="团队计划生成失败，建议改用 /plan 或普通 ReAct。")
            return

        yield TeamEvent(kind="team_plan_generated", team_id=self.team_id, plan=plan, message="；".join(warnings))
        if self.reviewer is None:
            yield TeamEvent(kind="cancelled", team_id=self.team_id, plan=plan, message="无计划审阅回调，Team 自动取消（fail closed）")
            return
        yield TeamEvent(kind="team_review", team_id=self.team_id, plan=plan, message="等待用户审阅团队计划")
        decision = await self.reviewer(plan)
        if decision.action == "cancel":
            yield TeamEvent(kind="cancelled", team_id=self.team_id, plan=plan, message="用户取消团队计划")
            return
        if decision.action == "replan":
            # MVP 只在首次审阅阶段支持一次显式重规划循环，保持和 /plan 一致。
            yield TeamEvent(kind="replanned", team_id=self.team_id, plan=plan, message=decision.feedback)
            try:
                plan, warnings = await self._generate_plan(goal, decision.feedback, plan)
            except LlmError as exc:
                yield TeamEvent(kind="team_failed", team_id=self.team_id, message=f"团队重规划失败: {exc}")
                return
            if plan is None:
                yield TeamEvent(kind="team_failed", team_id=self.team_id, message="团队重规划失败，建议改用 /plan 或普通 ReAct。")
                return
            yield TeamEvent(kind="team_plan_generated", team_id=self.team_id, plan=plan, message="；".join(warnings))
            yield TeamEvent(kind="team_review", team_id=self.team_id, plan=plan, message="等待用户审阅重规划")
            decision = await self.reviewer(plan)
            if decision.action != "execute":
                yield TeamEvent(kind="cancelled", team_id=self.team_id, plan=plan, message="团队计划未获批准")
                return

        yield TeamEvent(kind="approved", team_id=self.team_id, plan=plan, message="团队计划已批准，开始执行")
        plan.batches = conflict_safe_batches(plan.tasks)
        for batch_number, batch in enumerate(plan.batches, 1):
            yield TeamEvent(
                kind="batch_started", team_id=self.team_id, plan=plan, batch=batch,
                message=f"第 {batch_number} 轮 / 共 {len(plan.batches)} 轮",
            )
            async for event in self._run_batch(plan, batch):
                yield event
            failed = [task for task in plan.tasks if task.status == "failed"]
            if len(failed) > self.settings.plan_max_failures:
                yield TeamEvent(
                    kind="team_failed", team_id=self.team_id, plan=plan,
                    message=f"失败任务数 {len(failed)} 超过上限 {self.settings.plan_max_failures}",
                )
                return

        done = sum(task.status == "done" for task in plan.tasks)
        if done != len(plan.tasks):
            yield TeamEvent(kind="team_failed", team_id=self.team_id, plan=plan, message=f"Team 未完成：{done}/{len(plan.tasks)} 个任务通过")
            return
        yield TeamEvent(kind="team_done", team_id=self.team_id, plan=plan, message=f"Team 完成：{done}/{len(plan.tasks)} 个任务通过")

    async def _generate_plan(
        self, goal: str, feedback: str = "", previous: TeamPlan | None = None
    ) -> tuple[TeamPlan | None, list[str]]:
        context = ConversationContext(
            TEAM_PLANNER_PROMPT,
            self.settings,
            shared_provider=self.memory_manager.shared_sections if self.memory_manager else None,
        )
        parts = [f"任务：{goal}", f"请拆解为不超过 {self.settings.plan_max_subtasks} 个团队任务。"]
        if previous is not None:
            parts.append("上一版计划任务：\n" + "\n".join(f"- {task.id}: {task.title}" for task in previous.tasks))
        if feedback:
            parts.append(f"用户反馈（必须满足）：{feedback}")
        context.append(Message(role="user", content="\n\n".join(parts)))
        budget = await context.ensure_budget(self.llm)
        if not budget.proceed:
            raise LlmError(budget.message or "团队规划上下文超出模型窗口")
        messages = context.build_messages()
        warnings: list[str] = []
        for _ in range(1 + TEAM_PLAN_MAX_RETRIES):
            raw = await self._llm_text(messages)
            try:
                tasks, task_warnings = parse_team_tasks(
                    raw, self.settings.plan_max_subtasks, profiles=self.profiles
                )
            except PlanError as exc:
                messages.extend([
                    Message(role="assistant", content=raw),
                    Message(role="user", content=f"上面的 Team JSON 无效：{exc}。请修复并重新输出完整 JSON。"),
                ])
                continue
            warnings.extend(task_warnings)
            return TeamPlan(goal=goal, tasks=tasks, batches=conflict_safe_batches(tasks)), warnings
        return None, warnings

    async def _run_batch(self, plan: TeamPlan, batch: list[str]) -> AsyncIterator[TeamEvent]:
        queue: asyncio.Queue[TeamEvent | None] = asyncio.Queue()

        async def runner(task_id: str) -> None:
            try:
                await self._run_task(plan, task_id, queue)
            finally:
                queue.put_nowait(None)

        semaphore = asyncio.Semaphore(max(1, getattr(self.settings, "team_max_agents", 4)))

        async def limited_runner(task_id: str) -> None:
            async with semaphore:
                await runner(task_id)

        jobs = [asyncio.create_task(limited_runner(task_id)) for task_id in batch]
        completed = 0
        while completed < len(jobs):
            item = await queue.get()
            if item is None:
                completed += 1
            else:
                yield item
        await asyncio.gather(*jobs, return_exceptions=True)

    async def _run_task(self, plan: TeamPlan, task_id: str, queue: asyncio.Queue[TeamEvent | None]) -> None:
        task = plan.task_by_id(task_id)
        if task is None:
            return
        dependencies = [plan.task_by_id(dep) for dep in task.deps]
        if any(dep is None or dep.status != "done" for dep in dependencies):
            task.status = "failed"
            task.result = "依赖任务未通过审查，未执行"
            queue.put_nowait(TeamEvent(kind="task_failed", team_id=self.team_id, plan=plan, task=task, message=task.result))
            return

        task.status = "running"
        self._audit("team_task_started", team_id=self.team_id, task_id=task.id, role=task.owner_role)
        queue.put_nowait(TeamEvent(kind="task_started", team_id=self.team_id, plan=plan, task=task, role=task.owner_role))
        result, artifacts, agent_id, error = await self._execute_worker(plan, task, queue)
        if error:
            task.status = "failed"
            task.result = error[:TEAM_RESULT_LIMIT]
            self._audit("team_task_failed", team_id=self.team_id, task_id=task.id, role=task.owner_role, error=task.result)
            queue.put_nowait(TeamEvent(kind="agent_failed", team_id=self.team_id, plan=plan, task=task, agent_id=agent_id, role=task.owner_role, message=task.result))
            queue.put_nowait(TeamEvent(kind="task_failed", team_id=self.team_id, plan=plan, task=task, message=task.result))
            return
        task.result = result[:TEAM_RESULT_LIMIT]
        for artifact in artifacts:
            await self.artifacts.publish(artifact)
            task.artifacts.append(artifact.id)
            queue.put_nowait(TeamEvent(kind="artifact_produced", team_id=self.team_id, plan=plan, task=task, agent_id=agent_id, role=task.owner_role, artifact=artifact))
        try:
            review = await self._review(plan, task, artifacts, queue)
        except Exception as exc:
            review = ReviewResult(task.id, "fail", [f"Reviewer 执行失败：{exc}"], ["重新执行任务审查"], [])
        if review.verdict == "pass":
            task.status = "done"
            self._audit("team_task_done", team_id=self.team_id, task_id=task.id, role=task.owner_role, result=task.result)
            queue.put_nowait(TeamEvent(kind="task_done", team_id=self.team_id, plan=plan, task=task, message=task.result))
            return

        max_repairs = max(0, getattr(self.settings, "team_max_repairs", TEAM_MAX_RETRIES))
        for attempt in range(1, max_repairs + 1):
            task.attempts = attempt
            repair = replace(
                task,
                id=f"{task.id}-repair-{attempt}",
                title=f"修复：{task.title}",
                description="\n".join(review.required_fixes or review.findings) or "根据审查结果修复任务",
                owner_role="repairer",
                deps=[],
                status="pending",
                result="",
                artifacts=[],
            )
            queue.put_nowait(TeamEvent(kind="repair_requested", team_id=self.team_id, plan=plan, task=task, role="repairer", message=repair.description))
            repair_result, repair_artifacts, repair_agent_id, repair_error = await self._execute_worker(plan, repair, queue)
            if repair_error:
                review = ReviewResult(task.id, "fail", [repair_error], [repair_error], [])
            else:
                for artifact in repair_artifacts:
                    await self.artifacts.publish(artifact)
                    task.artifacts.append(artifact.id)
                    queue.put_nowait(TeamEvent(kind="artifact_produced", team_id=self.team_id, plan=plan, task=task, agent_id=repair_agent_id, role="repairer", artifact=artifact))
                task.result = repair_result[:TEAM_RESULT_LIMIT]
                try:
                    review = await self._review(plan, task, repair_artifacts, queue)
                except Exception as exc:
                    review = ReviewResult(task.id, "fail", [f"Reviewer 执行失败：{exc}"], ["重新执行任务审查"], [])
            if review.verdict == "pass":
                task.status = "done"
                queue.put_nowait(TeamEvent(kind="task_done", team_id=self.team_id, plan=plan, task=task, message=f"修复后通过：{task.result}"))
                return
        task.status = "failed"
        task.result = "; ".join(review.findings or review.required_fixes)[:TEAM_RESULT_LIMIT] or "审查未通过且修复次数已用尽"
        self._audit("team_task_failed", team_id=self.team_id, task_id=task.id, role=task.owner_role, error=task.result)
        queue.put_nowait(TeamEvent(kind="task_failed", team_id=self.team_id, plan=plan, task=task, review=review, message=task.result))

    async def _execute_worker(
        self, plan: TeamPlan, task: TeamTask, queue: asyncio.Queue[TeamEvent | None]
    ) -> tuple[str, list[Artifact], str, str]:
        profile = self.profiles.get(task.owner_role) or self.profiles["coder"]
        agent_id = f"agent-{uuid.uuid4().hex[:8]}"
        queue.put_nowait(TeamEvent(kind="agent_started", team_id=self.team_id, plan=plan, task=task, agent_id=agent_id, role=profile.name))
        sub_settings = replace(
            self.settings,
            tool_steps=profile.max_steps or self.settings.plan_subtask_steps,
        )
        scoped_tools = ScopedToolRegistry(self.tools, task, self.project_root, profile)
        prompt = self._worker_system_prompt(plan, task, profile)
        if self.agent_factory is not None:
            agent = self.agent_factory.create(profile, task)
        else:
            agent = ReActAgent(
                llm=self.llm,
                tools=scoped_tools,  # type: ignore[arg-type]
                settings=sub_settings,
                system_prompt=prompt,
                approval_policy=self.approval_policy,
                audit=self.audit,
                memory_manager=self.memory_manager,
                mcp_manager=self.mcp_manager,
            )
        artifacts: list[Artifact] = []
        try:
            async for event in agent.run(self._worker_user_prompt(task)):
                if event.kind in {
                    "thinking", "content", "tool_call", "approval", "tool_result",
                    "context_compacted", "context_warning", "context_usage", "usage",
                }:
                    queue.put_nowait(TeamEvent(kind="subtask_event", team_id=self.team_id, plan=plan, task=task, agent_id=agent_id, role=profile.name, agent_event=event))
                    if event.kind == "tool_result" and event.tool_result:
                        result = event.tool_result
                        summary = (result.output or result.error)[:TEAM_ARTIFACT_LIMIT]
                        kind = "test" if "test" in result.name.lower() or "pytest" in summary.lower() else "tool"
                        artifacts.append(Artifact(
                            id=f"artifact-{uuid.uuid4().hex[:10]}", task_id=task.id, kind=kind,
                            uri=str(event.tool_result.name), summary=summary,
                            producer_agent_id=agent_id,
                            verification_records=["tool_result:ok" if result.ok else "tool_result:failed"],
                        ))
                elif event.kind == "error":
                    return "", artifacts, agent_id, event.text or "Worker 执行失败"
                elif event.kind == "step_limit":
                    return "", artifacts, agent_id, f"达到 Worker 步数上限（{sub_settings.tool_steps}）"
                elif event.kind in {"budget_exceeded", "context_overflow"}:
                    return "", artifacts, agent_id, event.text or "Worker 上下文超限"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return "", artifacts, agent_id, f"{type(exc).__name__}: {exc}"
        final = next((message.content for message in reversed(agent.messages) if message.role == "assistant" and message.content.strip()), "")
        artifacts.append(Artifact(
            id=f"artifact-{uuid.uuid4().hex[:10]}", task_id=task.id, kind="report",
            summary=final[:TEAM_ARTIFACT_LIMIT], producer_agent_id=agent_id,
        ))
        queue.put_nowait(TeamEvent(kind="agent_done", team_id=self.team_id, plan=plan, task=task, agent_id=agent_id, role=profile.name, message=final[:TEAM_RESULT_LIMIT]))
        return final, artifacts, agent_id, ""

    async def _review(
        self, plan: TeamPlan, task: TeamTask, artifacts: list[Artifact], queue: asyncio.Queue[TeamEvent | None]
    ) -> ReviewResult:
        queue.put_nowait(TeamEvent(kind="task_review_started", team_id=self.team_id, plan=plan, task=task, role="reviewer"))
        if not getattr(self.settings, "team_review", True):
            result = ReviewResult(task.id, "pass", [], [], ["Team 任务级审查已关闭"])
        elif any(record == "tool_result:failed" for artifact in artifacts for record in artifact.verification_records):
            result = ReviewResult(task.id, "fail", ["存在工具执行失败证据"], ["修复工具失败并重新验证"], [])
        elif self.task_reviewer is not None:
            result = await self.task_reviewer(task, artifacts)
        else:
            result = await self._llm_review(task, artifacts)
        queue.put_nowait(TeamEvent(kind="task_review_done", team_id=self.team_id, plan=plan, task=task, role="reviewer", review=result, message="；".join(result.findings)))
        self._audit("team_task_review", team_id=self.team_id, task_id=task.id, verdict=result.verdict, findings=result.findings)
        return result

    async def _llm_review(self, task: TeamTask, artifacts: list[Artifact]) -> ReviewResult:
        evidence = "\n".join(
            f"- [{artifact.kind}] {artifact.uri}: {artifact.summary[:TEAM_ARTIFACT_LIMIT]}"
            for artifact in artifacts
        ) or "（没有产物）"
        prompt = (
            f"任务：{task.title}\n说明：{task.description}\n"
            f"验收标准：\n- " + "\n- ".join(task.acceptance_criteria) +
            f"\n执行产物和证据：\n{evidence}\n"
            "请严格输出 JSON。"
        )
        context = [Message(role="system", content=TEAM_REVIEWER_PROMPT), Message(role="user", content=prompt)]
        raw = await self._llm_text(context)
        try:
            data = json.loads(_strip_json(raw))
            verdict = str(data.get("verdict", "fail"))
            if verdict not in {"pass", "fail", "needs_input"}:
                verdict = "fail"
            return ReviewResult(
                task_id=task.id, verdict=verdict,  # type: ignore[arg-type]
                findings=[str(item) for item in data.get("findings", []) if str(item)],
                required_fixes=[str(item) for item in data.get("required_fixes", []) if str(item)],
                evidence=[str(item) for item in data.get("evidence", []) if str(item)],
            )
        except (json.JSONDecodeError, AttributeError, TypeError) as exc:
            return ReviewResult(task.id, "fail", [f"Reviewer 输出无效：{exc}"], ["重新输出结构化审查结果"], [])

    def _worker_system_prompt(self, plan: TeamPlan, task: TeamTask, profile: AgentProfile) -> str:
        parts = [DEFAULT_SYSTEM_PROMPT, "", f"# 你的角色：{profile.name}", profile.system_prompt, "", f"# Team 总目标\n{plan.goal}", f"# 当前任务\n{task.title}\n{task.description}", "# 验收标准\n- " + "\n- ".join(task.acceptance_criteria)]
        if task.resource_claims:
            parts.append("# 资源范围\n" + "\n".join(f"- {claim.access}: {claim.pattern}" for claim in task.resource_claims))
        dependencies: list[str] = []
        for dep_id in task.deps:
            dep = plan.task_by_id(dep_id)
            if dep is not None:
                dependencies.append(f"[{dep.id}] {dep.result[:TEAM_RESULT_LIMIT]}")
        if dependencies:
            parts.append("# 已通过审查的依赖结果\n" + "\n".join(dependencies))
        return "\n".join(parts)

    @staticmethod
    def _worker_user_prompt(task: TeamTask) -> str:
        return f"请执行任务 {task.id}（{task.title}）：\n{task.description}\n完成后简要汇报结果和验证证据，只处理这个任务。"

    async def _llm_text(self, messages: list[Message]) -> str:
        parts: list[str] = []
        async for event in self.llm.stream_chat(messages, tools=None):
            if event.kind == "content" and event.text:
                parts.append(event.text)
        return "".join(parts)

    def _audit(self, action: str, **fields) -> None:
        if self.audit is not None:
            self.audit.record(action, **fields)
