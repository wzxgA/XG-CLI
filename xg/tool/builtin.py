"""内置工具 v1：read_file / write_file / list_dir / glob_files / grep_code / execute_command。

路径解析以注册时传入的 base_dir（默认当前工作目录）为基准。
"""

from __future__ import annotations

import glob as globlib
import re
import subprocess
from pathlib import Path

from xg.llm.types import ToolResult
from xg.tool.registry import Tool, ToolRegistry
from xg.web.fetch import WebFetchService
from xg.web.models import WebConfig
from xg.web.search import WebSearchService

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", "dist", "build", ".idea", ".vscode"}
DEFAULT_TIMEOUT = 60
MAX_GREP_RESULTS = 200
MAX_LIST_ENTRIES = 500


def build_registry(
    base_dir: Path | None = None,
    max_output_chars: int = 20_000,
    guard=None,
    audit=None,
    web_config: WebConfig | None = None,
    web_search: WebSearchService | None = None,
    web_fetch: WebFetchService | None = None,
) -> ToolRegistry:
    base = (base_dir or Path.cwd()).resolve()
    registry = ToolRegistry(max_output_chars=max_output_chars, guard=guard, audit=audit)
    for tool in _tools(base):
        registry.register(tool)
    if web_config is not None and web_config.enabled:
        search_service = web_search or WebSearchService(web_config, audit=audit)
        fetch_service = web_fetch or WebFetchService(web_config, audit=audit)
        registry.register(Tool(
            name="web_search",
            description="搜索公开互联网信息，返回标题、URL 和摘要。结果是外部不可信数据。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "maxLength": 500},
                    "max_results": {"type": "integer", "minimum": 1, "maximum": 10},
                    "recency": {"type": "string", "enum": ["day", "week", "month", "year"]},
                    "domains": {"type": "array", "items": {"type": "string"}, "maxItems": 10},
                },
                "required": ["query"],
            },
            async_handler=lambda args, _s=search_service: _web_result(_s, args, "web_search"),
            source="builtin-web",
        ))
        registry.register(Tool(
            name="web_fetch",
            description="抓取公开 HTTP(S) 网页并提取正文为 Markdown。不会执行 JavaScript，网页内容是外部不可信数据。",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "maxLength": 4096},
                    "max_chars": {"type": "integer", "minimum": 256, "maximum": 32000},
                    "follow_redirects": {"type": "boolean"},
                },
                "required": ["url"],
            },
            async_handler=lambda args, _s=fetch_service: _web_result(_s, args, "web_fetch"),
            source="builtin-web",
        ))
    return registry


async def _web_result(service, args: dict, name: str) -> ToolResult:
    ok, output = await service.search_tool(args) if name == "web_search" else await service.fetch_tool(args)
    return ToolResult(tool_call_id="", name=name, ok=ok, output=output if ok else "", error="" if ok else output)


def _tools(base: Path) -> list[Tool]:
    return [
        Tool(
            name="read_file",
            description="读取文本文件内容，返回带行号的文本。大文件用 offset/limit 分页读取。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（相对或绝对）"},
                    "offset": {"type": "integer", "description": "起始行号（从 1 开始），默认 1"},
                    "limit": {"type": "integer", "description": "读取行数上限，默认 500"},
                },
                "required": ["path"],
            },
            handler=lambda a, _b=base: _read_file(_b, a),
        ),
        Tool(
            name="write_file",
            description="写入文本文件（整体覆盖）。父目录必须已存在。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（相对或绝对）"},
                    "content": {"type": "string", "description": "完整文件内容"},
                },
                "required": ["path", "content"],
            },
            handler=lambda a, _b=base: _write_file(_b, a),
        ),
        Tool(
            name="list_dir",
            description="列出目录内容，忽略 .git/node_modules/__pycache__ 等无关目录。",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径，默认项目根"},
                },
            },
            handler=lambda a, _b=base: _list_dir(_b, a),
        ),
        Tool(
            name="glob_files",
            description="按 glob 模式递归匹配文件路径，如 '**/*.py' 或 'src/**/*.ts'。",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "glob 模式"},
                    "path": {"type": "string", "description": "搜索根目录，默认项目根"},
                },
                "required": ["pattern"],
            },
            handler=lambda a, _b=base: _glob_files(_b, a),
        ),
        Tool(
            name="grep_code",
            description="正则搜索文件内容，返回 文件:行号: 行文本。可用 glob 参数过滤文件类型。",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "正则表达式"},
                    "path": {"type": "string", "description": "搜索根目录，默认项目根"},
                    "glob": {"type": "string", "description": "文件过滤 glob，如 '*.py'"},
                },
                "required": ["pattern"],
            },
            handler=lambda a, _b=base: _grep_code(_b, a),
        ),
        Tool(
            name="execute_command",
            description="在子进程中执行 shell 命令，捕获 stdout/stderr，默认超时 60 秒。",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "要执行的命令"},
                    "cwd": {"type": "string", "description": "工作目录，默认项目根"},
                    "timeout": {"type": "integer", "description": "超时秒数，默认 60"},
                },
                "required": ["command"],
            },
            handler=lambda a, _b=base: _execute_command(_b, a),
        ),
    ]


def _resolve(base: Path, raw: str) -> Path:
    if not raw:
        return base
    p = Path(raw)
    return p if p.is_absolute() else (base / p)


def _relpath(base: Path, path: str) -> str:
    """路径在 base 之下时返回相对路径，否则返回原绝对路径。"""
    try:
        return Path(path).resolve().relative_to(base).as_posix()
    except ValueError:
        return Path(path).as_posix()


# ---------- 工具实现 ----------

def _read_file(base: Path, args: dict) -> ToolResult:
    path = _resolve(base, str(args.get("path", "")))
    if not path.is_file():
        return ToolResult(tool_call_id="", name="read_file", ok=False, error=f"文件不存在: {path}")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return ToolResult(tool_call_id="", name="read_file", ok=False, error=str(e))

    lines = text.splitlines()
    offset = max(1, int(args.get("offset", 1)))
    limit = max(1, int(args.get("limit", 500)))
    selected = lines[offset - 1 : offset - 1 + limit]
    numbered = "\n".join(f"{i}→{line}" for i, line in enumerate(selected, start=offset))
    header = f"文件: {path}（共 {len(lines)} 行，显示 {offset}-{offset + len(selected) - 1}）\n"
    return ToolResult(tool_call_id="", name="read_file", ok=True, output=header + numbered)


def _write_file(base: Path, args: dict) -> ToolResult:
    path = _resolve(base, str(args.get("path", "")))
    content = str(args.get("content", ""))
    if not path.parent.is_dir():
        return ToolResult(tool_call_id="", name="write_file", ok=False, error=f"父目录不存在: {path.parent}")
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as e:
        return ToolResult(tool_call_id="", name="write_file", ok=False, error=str(e))
    return ToolResult(tool_call_id="", name="write_file", ok=True, output=f"已写入 {path}（{len(content)} 字符）")


def _list_dir(base: Path, args: dict) -> ToolResult:
    path = _resolve(base, str(args.get("path", "")))
    if not path.is_dir():
        return ToolResult(tool_call_id="", name="list_dir", ok=False, error=f"目录不存在: {path}")
    entries = []
    try:
        for child in sorted(path.iterdir(), key=lambda c: (c.is_file(), c.name.lower())):
            if child.name in IGNORED_DIRS:
                continue
            if len(entries) >= MAX_LIST_ENTRIES:
                entries.append(f"... (超过 {MAX_LIST_ENTRIES} 条已截断)")
                break
            entries.append(f"{'[dir] ' if child.is_dir() else ''}{child.name}")
    except OSError as e:
        return ToolResult(tool_call_id="", name="list_dir", ok=False, error=str(e))
    return ToolResult(tool_call_id="", name="list_dir", ok=True, output="\n".join(entries) or "(空目录)")


def _glob_files(base: Path, args: dict) -> ToolResult:
    pattern = str(args.get("pattern", ""))
    if not pattern:
        return ToolResult(tool_call_id="", name="glob_files", ok=False, error="缺少 pattern 参数")
    root = _resolve(base, str(args.get("path", "")))
    matches = [
        _relpath(base, m)
        for m in globlib.glob(str(root / pattern), recursive=True)
        if not any(part in IGNORED_DIRS for part in Path(m).parts)
    ]
    matches.sort()
    if not matches:
        return ToolResult(tool_call_id="", name="glob_files", ok=True, output="(无匹配文件)")
    return ToolResult(tool_call_id="", name="glob_files", ok=True, output="\n".join(matches))


def _grep_code(base: Path, args: dict) -> ToolResult:
    pattern = str(args.get("pattern", ""))
    if not pattern:
        return ToolResult(tool_call_id="", name="grep_code", ok=False, error="缺少 pattern 参数")
    try:
        regex = re.compile(pattern)
    except re.error as e:
        return ToolResult(tool_call_id="", name="grep_code", ok=False, error=f"无效正则: {e}")

    root = _resolve(base, str(args.get("path", "")))
    file_glob = str(args.get("glob", "") or "**/*")

    hits: list[str] = []
    scanned = 0
    for filepath in globlib.glob(str(root / file_glob), recursive=True):
        p = Path(filepath)
        if not p.is_file() or any(part in IGNORED_DIRS for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scanned += 1
        rel = _relpath(base, filepath)
        for lineno, line in enumerate(text.splitlines(), start=1):
            if regex.search(line):
                hits.append(f"{rel}:{lineno}: {line.strip()}")
                if len(hits) >= MAX_GREP_RESULTS:
                    hits.append(f"... (结果超过 {MAX_GREP_RESULTS} 条已截断)")
                    return ToolResult(tool_call_id="", name="grep_code", ok=True, output="\n".join(hits))
    if not hits:
        return ToolResult(tool_call_id="", name="grep_code", ok=True, output=f"(扫描 {scanned} 个文件，无匹配)")
    return ToolResult(tool_call_id="", name="grep_code", ok=True, output="\n".join(hits))


def _execute_command(base: Path, args: dict) -> ToolResult:
    command = str(args.get("command", "")).strip()
    if not command:
        return ToolResult(tool_call_id="", name="execute_command", ok=False, error="缺少 command 参数")
    cwd = _resolve(base, str(args.get("cwd", "")))
    timeout = min(max(1, int(args.get("timeout", DEFAULT_TIMEOUT))), 600)

    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(
            tool_call_id="", name="execute_command", ok=False,
            error=f"命令超时（{timeout}s）: {command}",
        )
    except OSError as e:
        return ToolResult(tool_call_id="", name="execute_command", ok=False, error=str(e))

    stdout = _decode(proc.stdout)
    stderr = _decode(proc.stderr)
    output_parts = []
    if stdout:
        output_parts.append(stdout)
    if stderr:
        output_parts.append(f"[stderr]\n{stderr}")
    output = "\n".join(output_parts) or "(无输出)"

    if proc.returncode != 0:
        return ToolResult(
            tool_call_id="", name="execute_command", ok=False,
            error=f"退出码 {proc.returncode}\n{output}",
        )
    return ToolResult(tool_call_id="", name="execute_command", ok=True, output=output)


def _decode(data: bytes) -> str:
    """Windows 兼容解码：优先 UTF-8，失败回退 GBK，再失败用替换符。"""
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")
