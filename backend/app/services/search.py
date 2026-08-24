"""Safe in-memory literal search across explicitly uploaded source files."""

from __future__ import annotations

import re

from fastapi import UploadFile

from app.config import Settings
from app.schemas.search import SearchMatch, SearchResponse, SearchStats
from app.services.analysis import AnalysisInputError, process_uploads

MAX_SEARCH_QUERY_CHARS = 500
MAX_SEARCH_RESULTS = 100
MAX_DISPLAY_LINE_CHARS = 500
CONTEXT_LINE_COUNT = 1


def validate_search_query(query: str) -> str:
    """Trim and validate a literal text-search query."""

    cleaned_query = query.strip()
    if not cleaned_query:
        raise AnalysisInputError("搜索关键词不能为空。")
    if len(cleaned_query) > MAX_SEARCH_QUERY_CHARS:
        raise AnalysisInputError(
            f"搜索关键词不能超过 {MAX_SEARCH_QUERY_CHARS} 个字符。",
        )
    return cleaned_query


def clip_line(line: str, match_start: int | None = None) -> tuple[str, bool]:
    """Bound a displayed line while keeping the first match visible."""

    if len(line) <= MAX_DISPLAY_LINE_CHARS:
        return line, False

    if match_start is None:
        return f"{line[:MAX_DISPLAY_LINE_CHARS]}…", True

    window_start = max(0, match_start - MAX_DISPLAY_LINE_CHARS // 2)
    window_end = min(len(line), window_start + MAX_DISPLAY_LINE_CHARS)
    window_start = max(0, window_end - MAX_DISPLAY_LINE_CHARS)
    prefix = "…" if window_start > 0 else ""
    suffix = "…" if window_end < len(line) else ""
    return f"{prefix}{line[window_start:window_end]}{suffix}", True


async def search_code_files(
    *,
    query: str,
    uploads: list[UploadFile],
    settings: Settings,
) -> SearchResponse:
    """Search safe uploaded UTF-8 files without executing or persisting them."""

    cleaned_query = validate_search_query(query)
    accepted_files, file_warnings = await process_uploads(uploads, settings)
    pattern = re.compile(re.escape(cleaned_query), re.IGNORECASE)
    results: list[SearchMatch] = []
    matched_files: set[str] = set()
    truncated = False

    for accepted_file in accepted_files:
        source_lines = accepted_file.content.splitlines() or [""]
        for line_index, source_line in enumerate(source_lines):
            matches = list(pattern.finditer(source_line))
            if not matches:
                continue

            if len(results) >= MAX_SEARCH_RESULTS:
                truncated = True
                break

            first_match = matches[0]
            display_line, line_truncated = clip_line(source_line, first_match.start())
            before = [
                clip_line(source_lines[index])[0]
                for index in range(
                    max(0, line_index - CONTEXT_LINE_COUNT),
                    line_index,
                )
            ]
            after = [
                clip_line(source_lines[index])[0]
                for index in range(
                    line_index + 1,
                    min(len(source_lines), line_index + CONTEXT_LINE_COUNT + 1),
                )
            ]
            results.append(
                SearchMatch(
                    file=accepted_file.path,
                    language=accepted_file.language,
                    line_number=line_index + 1,
                    column=first_match.start() + 1,
                    match_count=len(matches),
                    line=display_line,
                    before=before,
                    after=after,
                    line_truncated=line_truncated,
                ),
            )
            matched_files.add(accepted_file.path)

        if truncated:
            break

    warnings = [*file_warnings]
    if truncated:
        warnings.append(f"搜索结果超过 {MAX_SEARCH_RESULTS} 条，仅返回前 {MAX_SEARCH_RESULTS} 条。")

    return SearchResponse(
        query=cleaned_query,
        results=results,
        warnings=warnings,
        truncated=truncated,
        stats=SearchStats(
            received_files=len(uploads),
            accepted_files=len(accepted_files),
            skipped_files=len(uploads) - len(accepted_files),
            matched_files=len(matched_files),
            matched_lines=len(results),
        ),
    )

