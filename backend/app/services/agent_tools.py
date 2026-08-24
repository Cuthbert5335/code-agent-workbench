"""Registered, read-only tools used by the phase-four single Agent."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from app.schemas.agents import ToolEvidence, ToolParameter, ToolResultSummary, ToolSpec
from app.services.analysis import AcceptedFile, AnalysisInputError
from app.services.chunks import chunk_accepted_file
from app.services.symbols import extract_symbols_from_file

MAX_TOOL_OUTPUT_CHARS = 4_000
MAX_EVIDENCE_ITEMS = 30
MAX_PREVIEW_CHARS = 240
MAX_TOOL_QUERY_CHARS = 500
MAX_TOOL_SEARCH_RESULTS = 30
MAX_TOOL_SYMBOL_RESULTS = 30
MAX_TOOL_CHUNK_RESULTS = 12


@dataclass(frozen=True)
class ToolContext:
    """Validated request-local files available to a controlled tool."""

    files: tuple[AcceptedFile, ...]


ToolHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolResultSummary]]


@dataclass(frozen=True)
class RegisteredTool:
    """Internal tool definition pairing public metadata with its handler."""

    spec: ToolSpec
    handler: ToolHandler


def clip_text(value: str, max_chars: int = MAX_PREVIEW_CHARS) -> str:
    """Clip user-controlled text before retaining it in a task trace."""

    cleaned = value.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return f"{cleaned[:max_chars]}…"


def validate_arguments(spec: ToolSpec, arguments: dict[str, Any]) -> dict[str, Any]:
    """Reject unknown, missing, or oversized tool arguments."""

    known_parameters = {parameter.name: parameter for parameter in spec.parameters}
    unknown_parameters = set(arguments) - set(known_parameters)
    if unknown_parameters:
        unknown = ", ".join(sorted(unknown_parameters))
        raise AnalysisInputError(f"工具 {spec.name} 收到未知参数：{unknown}。")

    validated: dict[str, Any] = {}
    for name, parameter in known_parameters.items():
        value = arguments.get(name)
        if value is None or (isinstance(value, str) and not value.strip()):
            if parameter.required:
                raise AnalysisInputError(f"工具 {spec.name} 缺少必填参数 {name}。")
            continue
        if not isinstance(value, str):
            raise AnalysisInputError(f"工具 {spec.name} 的参数 {name} 必须是字符串。")
        cleaned = value.strip()
        if parameter.max_length and len(cleaned) > parameter.max_length:
            raise AnalysisInputError(
                f"工具 {spec.name} 的参数 {name} 不能超过 {parameter.max_length} 个字符。",
            )
        validated[name] = cleaned
    return validated


def bound_result(result: ToolResultSummary, max_chars: int) -> ToolResultSummary:
    """Enforce a deterministic character and evidence budget on tool output."""

    summary = clip_text(result.summary, min(max_chars, 800))
    evidence: list[ToolEvidence] = []
    used_chars = len(summary)
    truncated = result.truncated

    for item in result.evidence[:MAX_EVIDENCE_ITEMS]:
        bounded_item = item.model_copy(
            update={
                "label": clip_text(item.label, 160),
                "preview": clip_text(item.preview, MAX_PREVIEW_CHARS)
                if item.preview
                else None,
            },
        )
        item_cost = len(bounded_item.label) + len(bounded_item.preview or "")
        if used_chars + item_cost > max_chars:
            truncated = True
            break
        evidence.append(bounded_item)
        used_chars += item_cost

    if len(result.evidence) > len(evidence):
        truncated = True
    return ToolResultSummary(
        summary=summary,
        item_count=result.item_count,
        truncated=truncated,
        evidence=evidence,
    )


async def list_files_tool(
    context: ToolContext,
    arguments: dict[str, Any],
) -> ToolResultSummary:
    """List the safe files already attached to the task."""

    del arguments
    await asyncio.sleep(0)
    evidence = [
        ToolEvidence(
            file=file.path,
            label=f"{file.language} · {len(file.content)} 字符",
        )
        for file in context.files
    ]
    return ToolResultSummary(
        summary=f"已列出任务快照中的 {len(context.files)} 个安全文件。",
        item_count=len(context.files),
        evidence=evidence,
    )


async def project_summary_tool(
    context: ToolContext,
    arguments: dict[str, Any],
) -> ToolResultSummary:
    """Summarize file, language, line, chunk, and symbol counts."""

    del arguments
    await asyncio.sleep(0)
    total_lines = 0
    total_chunks = 0
    total_symbols = 0
    language_counts: dict[str, int] = {}
    evidence: list[ToolEvidence] = []

    for file in context.files:
        lines = len(file.content.splitlines()) if file.content else 0
        chunks = len(chunk_accepted_file(file).chunks)
        symbols = len(extract_symbols_from_file(file)[0])
        total_lines += lines
        total_chunks += chunks
        total_symbols += symbols
        language_counts[file.language] = language_counts.get(file.language, 0) + 1
        evidence.append(
            ToolEvidence(
                file=file.path,
                label=f"{file.language} · {lines} 行 · {chunks} 切片 · {symbols} 符号",
            ),
        )

    languages = "、".join(
        f"{language} {count} 个" for language, count in sorted(language_counts.items())
    )
    return ToolResultSummary(
        summary=(
            f"项目快照包含 {len(context.files)} 个文件、{total_lines} 行、"
            f"{total_chunks} 个切片和 {total_symbols} 个基础符号。"
            f"语言分布：{languages or '无'}。"
        ),
        item_count=len(context.files),
        evidence=evidence,
    )


async def search_text_tool(
    context: ToolContext,
    arguments: dict[str, Any],
) -> ToolResultSummary:
    """Perform a bounded case-insensitive literal text search."""

    query = arguments["query"]
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    evidence: list[ToolEvidence] = []
    total_matches = 0
    await asyncio.sleep(0)

    for file in context.files:
        for line_number, line in enumerate(file.content.splitlines(), start=1):
            matches = list(pattern.finditer(line))
            if not matches:
                continue
            total_matches += len(matches)
            if len(evidence) < MAX_TOOL_SEARCH_RESULTS:
                evidence.append(
                    ToolEvidence(
                        file=file.path,
                        start_line=line_number,
                        end_line=line_number,
                        label=f"第 {line_number} 行 · {len(matches)} 处匹配",
                        preview=clip_text(line),
                    ),
                )

    return ToolResultSummary(
        summary=f"文本关键词“{clip_text(query, 80)}”共找到 {total_matches} 处匹配。",
        item_count=total_matches,
        truncated=total_matches > len(evidence),
        evidence=evidence,
    )


async def search_symbols_tool(
    context: ToolContext,
    arguments: dict[str, Any],
) -> ToolResultSummary:
    """Find bounded syntax-light symbols, optionally filtered by name."""

    query = arguments.get("query")
    evidence: list[ToolEvidence] = []
    total_symbols = 0
    truncated = False
    await asyncio.sleep(0)

    for file in context.files:
        symbols, file_truncated = extract_symbols_from_file(file, query=query)
        total_symbols += len(symbols)
        truncated = truncated or file_truncated
        for symbol in symbols:
            if len(evidence) >= MAX_TOOL_SYMBOL_RESULTS:
                truncated = True
                continue
            evidence.append(
                ToolEvidence(
                    file=symbol.file,
                    start_line=symbol.line_number,
                    end_line=symbol.line_number,
                    label=f"{symbol.kind} · {symbol.name}",
                    preview=symbol.declaration,
                ),
            )

    query_label = f"名称包含“{clip_text(query, 80)}”的" if query else "全部"
    return ToolResultSummary(
        summary=f"已识别 {query_label}基础符号 {total_symbols} 个。",
        item_count=total_symbols,
        truncated=truncated or total_symbols > len(evidence),
        evidence=evidence,
    )


async def inspect_chunks_tool(
    context: ToolContext,
    arguments: dict[str, Any],
) -> ToolResultSummary:
    """Inspect bounded code chunks most relevant to an optional literal query."""

    query = arguments.get("query")
    normalized_query = query.casefold() if query else None
    candidates = []
    await asyncio.sleep(0)

    for file in context.files:
        for chunk in chunk_accepted_file(file).chunks:
            if normalized_query and normalized_query not in chunk.content.casefold():
                continue
            candidates.append(chunk)

    if normalized_query and not candidates:
        for file in context.files:
            candidates.extend(chunk_accepted_file(file).chunks[:1])

    selected = candidates[:MAX_TOOL_CHUNK_RESULTS]
    evidence = [
        ToolEvidence(
            file=chunk.file,
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            label=f"{chunk.language} · 第 {chunk.start_line}-{chunk.end_line} 行",
            preview=clip_text(chunk.content),
        )
        for chunk in selected
    ]
    return ToolResultSummary(
        summary=(
            f"已检查 {len(selected)} 个代码切片"
            + (f"，优先包含“{clip_text(query, 80)}”的片段。" if query else "。")
        ),
        item_count=len(candidates),
        truncated=len(candidates) > len(selected),
        evidence=evidence,
    )


def build_registry() -> dict[str, RegisteredTool]:
    """Return the fixed phase-four allowlist of read-only tools."""

    query_parameter = ToolParameter(
        name="query",
        type="string",
        required=True,
        description="忽略大小写的字面量搜索关键词。",
        max_length=MAX_TOOL_QUERY_CHARS,
    )
    optional_query_parameter = query_parameter.model_copy(
        update={"required": False, "description": "可选的字面量名称或内容筛选词。"},
    )
    definitions = (
        RegisteredTool(
            spec=ToolSpec(
                name="list_files",
                title="列出项目文件",
                description="列出任务创建时已通过安全校验的文件和语言。",
                timeout_seconds=3,
                max_output_chars=MAX_TOOL_OUTPUT_CHARS,
                parameters=[],
            ),
            handler=list_files_tool,
        ),
        RegisteredTool(
            spec=ToolSpec(
                name="project_summary",
                title="生成项目摘要",
                description="汇总文件、语言、行数、切片和基础符号数量。",
                timeout_seconds=5,
                max_output_chars=MAX_TOOL_OUTPUT_CHARS,
                parameters=[],
            ),
            handler=project_summary_tool,
        ),
        RegisteredTool(
            spec=ToolSpec(
                name="search_text",
                title="搜索项目文本",
                description="在任务快照中执行受限的忽略大小写字面量搜索。",
                timeout_seconds=5,
                max_output_chars=MAX_TOOL_OUTPUT_CHARS,
                parameters=[query_parameter],
            ),
            handler=search_text_tool,
        ),
        RegisteredTool(
            spec=ToolSpec(
                name="search_symbols",
                title="搜索基础符号",
                description="识别常见函数、类、接口、类型等声明。",
                timeout_seconds=5,
                max_output_chars=MAX_TOOL_OUTPUT_CHARS,
                parameters=[optional_query_parameter],
            ),
            handler=search_symbols_tool,
        ),
        RegisteredTool(
            spec=ToolSpec(
                name="inspect_chunks",
                title="检查相关代码切片",
                description="读取受限代码切片并保留路径和行号证据。",
                timeout_seconds=5,
                max_output_chars=MAX_TOOL_OUTPUT_CHARS,
                parameters=[optional_query_parameter],
            ),
            handler=inspect_chunks_tool,
        ),
    )
    return {definition.spec.name: definition for definition in definitions}


TOOL_REGISTRY = build_registry()


def list_tool_specs() -> list[ToolSpec]:
    """Expose the stable public registry without handler internals."""

    return [definition.spec for definition in TOOL_REGISTRY.values()]


async def execute_registered_tool(
    *,
    name: str,
    arguments: dict[str, Any],
    context: ToolContext,
) -> ToolResultSummary:
    """Validate, time-limit, execute, and bound an allowlisted tool."""

    definition = TOOL_REGISTRY.get(name)
    if definition is None:
        raise AnalysisInputError(f"工具 {name} 未注册或不允许执行。")
    validated_arguments = validate_arguments(definition.spec, arguments)
    result = await asyncio.wait_for(
        definition.handler(context, validated_arguments),
        timeout=definition.spec.timeout_seconds,
    )
    return bound_result(result, definition.spec.max_output_chars)
