"""Build an in-memory metadata, chunk, and symbol index for selected files."""

from __future__ import annotations

from fastapi import UploadFile

from app.config import Settings
from app.schemas.indexing import IndexedFile, IndexResponse, IndexStats
from app.services.analysis import process_uploads
from app.services.chunks import chunk_accepted_file
from app.services.symbols import extract_symbols_from_file


async def build_project_index(
    *,
    uploads: list[UploadFile],
    settings: Settings,
) -> IndexResponse:
    """Validate files once and derive bounded metadata, chunks, and symbols."""

    accepted_files, file_warnings = await process_uploads(uploads, settings)
    indexed_files: list[IndexedFile] = []
    warnings = [*file_warnings]
    total_chunks = 0
    total_symbols = 0
    total_content_chars = 0
    partial = bool(file_warnings)

    for accepted_file in accepted_files:
        chunk_result = chunk_accepted_file(accepted_file)
        symbols, symbols_truncated = extract_symbols_from_file(accepted_file)
        warnings.extend(chunk_result.warnings)
        if chunk_result.warnings or symbols_truncated:
            partial = True
        if symbols_truncated:
            warnings.append(
                f"{accepted_file.path} 的符号数量超过单文件限制，仅索引前面的声明。",
            )

        line_count = len(accepted_file.content.splitlines()) if accepted_file.content else 0
        chunk_count = len(chunk_result.chunks)
        symbol_count = len(symbols)
        content_chars = len(accepted_file.content)
        indexed_files.append(
            IndexedFile(
                file=accepted_file.path,
                language=accepted_file.language,
                size_chars=content_chars,
                lines=line_count,
                chunks=chunk_count,
                symbols=symbol_count,
            ),
        )
        total_chunks += chunk_count
        total_symbols += symbol_count
        total_content_chars += content_chars

    return IndexResponse(
        status="partial" if partial else "completed",
        files=indexed_files,
        warnings=warnings,
        stats=IndexStats(
            received_files=len(uploads),
            accepted_files=len(accepted_files),
            skipped_files=len(uploads) - len(accepted_files),
            indexed_files=len(indexed_files),
            chunks=total_chunks,
            symbols=total_symbols,
            content_chars=total_content_chars,
        ),
    )

