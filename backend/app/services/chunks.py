"""Deterministic, syntax-light source chunking for selected safe files."""

from __future__ import annotations

import re
from dataclasses import dataclass

from fastapi import UploadFile

from app.config import Settings
from app.schemas.chunks import ChunkResponse, ChunkStats, CodeChunk
from app.services.analysis import AcceptedFile, process_uploads

MAX_CHUNK_LINES = 80
MAX_CHUNK_CHARS = 4_000
MAX_CHUNKS = 200
MAX_TOTAL_CHUNK_CHARS = 60_000

DECLARATION_PATTERN = re.compile(
    r"^\s*(?:"
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:def|function|class|interface|enum|type)\s+"
    r"|(?:pub\s+)?(?:async\s+)?(?:fn|struct|trait|impl|mod)\s+"
    r"|func\s+(?:\([^)]*\)\s*)?"
    r")",
)


@dataclass(frozen=True)
class FileChunkResult:
    """Chunks and warnings produced for one already accepted file."""

    chunks: list[CodeChunk]
    warnings: list[str]


def is_declaration_line(line: str) -> bool:
    """Return whether a line is a useful syntax-light chunk boundary."""

    return bool(DECLARATION_PATTERN.match(line))


def semantic_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """Split line indexes before top-level-looking declarations."""

    ranges: list[tuple[int, int]] = []
    range_start = 0

    for line_index, line in enumerate(lines):
        if line_index > range_start and is_declaration_line(line):
            ranges.append((range_start, line_index))
            range_start = line_index

    ranges.append((range_start, len(lines)))
    return ranges


def bounded_range_end(
    lines: list[str],
    start: int,
    end: int,
    max_lines: int,
    max_chars: int,
) -> int:
    """Find the largest end index that fits both line and character limits."""

    candidate_end = start
    used_chars = 0

    while candidate_end < end and candidate_end - start < max_lines:
        line_cost = len(lines[candidate_end]) + (1 if candidate_end > start else 0)
        if used_chars + line_cost > max_chars:
            break
        used_chars += line_cost
        candidate_end += 1

    return candidate_end


def prefer_blank_boundary(lines: list[str], start: int, candidate_end: int) -> int:
    """Move a hard boundary back to the nearest non-leading blank line."""

    for line_index in range(candidate_end - 1, start, -1):
        if not lines[line_index].strip():
            return line_index + 1
    return candidate_end


def chunk_accepted_file(
    accepted_file: AcceptedFile,
    *,
    max_lines: int = MAX_CHUNK_LINES,
    max_chars: int = MAX_CHUNK_CHARS,
) -> FileChunkResult:
    """Create ordered, bounded chunks for one decoded source file."""

    if not accepted_file.content:
        return FileChunkResult(
            chunks=[],
            warnings=[f"已跳过 {accepted_file.path} 的切片：文件内容为空。"],
        )

    lines = accepted_file.content.splitlines()
    if not lines:
        return FileChunkResult(
            chunks=[],
            warnings=[f"已跳过 {accepted_file.path} 的切片：文件内容为空。"],
        )

    chunks: list[CodeChunk] = []
    warnings: list[str] = []

    for range_start, range_end in semantic_ranges(lines):
        cursor = range_start
        while cursor < range_end:
            candidate_end = bounded_range_end(
                lines,
                cursor,
                range_end,
                max_lines,
                max_chars,
            )

            if candidate_end == cursor:
                clipped_content = lines[cursor][:max_chars]
                chunks.append(
                    CodeChunk(
                        file=accepted_file.path,
                        language=accepted_file.language,
                        start_line=cursor + 1,
                        end_line=cursor + 1,
                        content=clipped_content,
                        truncated=True,
                    ),
                )
                warnings.append(
                    f"{accepted_file.path} 第 {cursor + 1} 行超过单片字符限制，已截断展示。",
                )
                cursor += 1
                continue

            if candidate_end < range_end:
                candidate_end = prefer_blank_boundary(lines, cursor, candidate_end)

            content = "\n".join(lines[cursor:candidate_end])
            chunks.append(
                CodeChunk(
                    file=accepted_file.path,
                    language=accepted_file.language,
                    start_line=cursor + 1,
                    end_line=candidate_end,
                    content=content,
                ),
            )
            cursor = candidate_end

    return FileChunkResult(chunks=chunks, warnings=warnings)


async def build_code_chunks(
    *,
    uploads: list[UploadFile],
    settings: Settings,
    max_chunks: int = MAX_CHUNKS,
    max_total_chars: int = MAX_TOTAL_CHUNK_CHARS,
) -> ChunkResponse:
    """Validate uploads once and return a globally bounded chunk collection."""

    accepted_files, file_warnings = await process_uploads(uploads, settings)
    chunks: list[CodeChunk] = []
    warnings = [*file_warnings]
    chunked_files: set[str] = set()
    content_chars = 0
    truncated = False

    for accepted_file in accepted_files:
        file_result = chunk_accepted_file(accepted_file)
        warnings.extend(file_result.warnings)

        for chunk in file_result.chunks:
            if len(chunks) >= max_chunks or content_chars + len(chunk.content) > max_total_chars:
                truncated = True
                break
            chunks.append(chunk)
            chunked_files.add(chunk.file)
            content_chars += len(chunk.content)

        if truncated:
            break

    if truncated:
        warnings.append("代码切片达到总输出限制，仅返回前面的片段。")

    return ChunkResponse(
        chunks=chunks,
        warnings=warnings,
        truncated=truncated,
        stats=ChunkStats(
            received_files=len(uploads),
            accepted_files=len(accepted_files),
            skipped_files=len(uploads) - len(accepted_files),
            chunked_files=len(chunked_files),
            chunks=len(chunks),
            content_chars=content_chars,
        ),
    )

