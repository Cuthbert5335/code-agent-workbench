"""Safe file processing and deterministic demo analysis."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from fastapi import UploadFile
from pydantic import TypeAdapter, ValidationError

from app.config import Settings
from app.providers.openai_compatible import (
    is_model_configured,
    request_chat_completion,
)
from app.schemas.analysis import (
    AnalysisResponse,
    AnalysisStats,
    ConversationMessage,
    FileReference,
)
from app.services.usage import UsageContext, usage_service

READ_CHUNK_SIZE = 65_536
MAX_QUESTION_CHARS = 8_000
MAX_CONVERSATION_MESSAGES = 10
REFERENCE_PATTERN = re.compile(
    r"\[引用\s*:\s*(?P<file>[^:\]\r\n]+?)\s*:\s*"
    r"(?P<start>\d+)\s*-\s*(?P<end>\d+)\s*\]",
)

LANGUAGE_BY_EXTENSION = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript JSX",
    ".ts": "TypeScript",
    ".tsx": "TypeScript JSX",
    ".java": "Java",
    ".c": "C",
    ".h": "C Header",
    ".cpp": "C++",
    ".cc": "C++",
    ".hpp": "C++ Header",
    ".cs": "C#",
    ".go": "Go",
    ".rs": "Rust",
    ".php": "PHP",
    ".rb": "Ruby",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".kts": "Kotlin Script",
    ".scala": "Scala",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".html": "HTML",
    ".htm": "HTML",
    ".css": "CSS",
    ".scss": "SCSS",
    ".sass": "Sass",
    ".less": "Less",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".xml": "XML",
    ".ini": "INI",
    ".cfg": "Config",
    ".conf": "Config",
    ".md": "Markdown",
    ".txt": "Text",
    ".sql": "SQL",
    ".sh": "Shell",
    ".ps1": "PowerShell",
    ".bat": "Batch",
    ".cmd": "Command Script",
    ".lock": "Lockfile",
}

LANGUAGE_BY_FILENAME = {
    "dockerfile": "Dockerfile",
    "makefile": "Makefile",
    "procfile": "Procfile",
    ".gitignore": "Git Ignore",
    ".dockerignore": "Docker Ignore",
    ".editorconfig": "EditorConfig",
}

IGNORED_PATH_PARTS = {
    ".git",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "coverage",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
}

BLOCKED_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".keystore"}
BLOCKED_FILE_PATTERNS = (
    re.compile(r"^\.env(?:\..+)?$", re.IGNORECASE),
    re.compile(r"^id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?$", re.IGNORECASE),
    re.compile(r"^(?:credentials?|secrets?)(?:\.[^.]+)?$", re.IGNORECASE),
)


class AnalysisInputError(Exception):
    """An expected client input error with a suitable HTTP status code."""

    def __init__(self, detail: str, status_code: int = 422) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class AcceptedFile:
    """Decoded source file kept only for the duration of one request."""

    path: str
    language: str
    content: str


@dataclass(frozen=True)
class ContextResult:
    """Bounded context plus the references and warnings it produced."""

    text: str
    references: list[FileReference]
    warnings: list[str]


def validate_question(question: str) -> str:
    """Trim and validate the user's natural-language question."""

    cleaned_question = question.strip()
    if not cleaned_question:
        raise AnalysisInputError("问题不能为空，请输入需要分析的代码问题。")
    if len(cleaned_question) > MAX_QUESTION_CHARS:
        raise AnalysisInputError(
            f"问题长度不能超过 {MAX_QUESTION_CHARS} 个字符。",
        )
    return cleaned_question


def parse_conversation(raw_conversation: str | None) -> tuple[list[ConversationMessage], list[str]]:
    """Parse optional JSON conversation history and keep only recent messages."""

    if raw_conversation is None or not raw_conversation.strip():
        return [], []

    try:
        decoded_conversation = json.loads(raw_conversation)
        messages = TypeAdapter(list[ConversationMessage]).validate_python(
            decoded_conversation,
        )
    except (json.JSONDecodeError, ValidationError) as error:
        raise AnalysisInputError(
            "conversation 必须是由 role 和 content 组成的 JSON 消息数组。",
        ) from error

    if len(messages) <= MAX_CONVERSATION_MESSAGES:
        return messages, []

    return (
        messages[-MAX_CONVERSATION_MESSAGES:],
        [f"对话历史超过 {MAX_CONVERSATION_MESSAGES} 条，仅使用最近的消息。"],
    )


def normalize_upload_path(filename: str | None) -> str:
    """Normalize a browser filename and reject absolute or traversing paths."""

    if filename is None or not filename.strip():
        raise AnalysisInputError("检测到没有文件名的上传内容。")

    normalized_filename = filename.replace("\\", "/").strip()
    if "\x00" in normalized_filename:
        raise AnalysisInputError("文件名包含无效字符。")
    if normalized_filename.startswith("/") or re.match(
        r"^[A-Za-z]:/",
        normalized_filename,
    ):
        raise AnalysisInputError("不允许使用绝对文件路径。")

    path = PurePosixPath(normalized_filename)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise AnalysisInputError("文件路径不合法，不能包含路径穿越片段。")

    return path.as_posix()


def get_language(path: str) -> str | None:
    """Return a display language for a supported source file."""

    normalized_name = PurePosixPath(path).name.casefold()
    if normalized_name in LANGUAGE_BY_FILENAME:
        return LANGUAGE_BY_FILENAME[normalized_name]
    return LANGUAGE_BY_EXTENSION.get(PurePosixPath(normalized_name).suffix)


def get_skip_reason(path: str) -> str | None:
    """Return a safe skip reason for irrelevant or sensitive files."""

    path_parts = PurePosixPath(path).parts
    normalized_parts = {part.casefold() for part in path_parts[:-1]}
    if normalized_parts & IGNORED_PATH_PARTS:
        return "位于默认忽略的目录中"

    file_name = PurePosixPath(path).name
    if any(pattern.match(file_name) for pattern in BLOCKED_FILE_PATTERNS):
        return "疑似包含密钥或凭据"
    if PurePosixPath(file_name.casefold()).suffix in BLOCKED_EXTENSIONS:
        return "密钥或证书文件不允许载入"
    if get_language(path) is None:
        return "不支持的文件类型"
    return None


async def read_limited_upload(upload: UploadFile, max_bytes: int) -> bytes:
    """Read one upload in chunks without exceeding the configured file limit."""

    content = bytearray()
    while chunk := await upload.read(READ_CHUNK_SIZE):
        content.extend(chunk)
        if len(content) > max_bytes:
            raise AnalysisInputError(
                f"文件 {upload.filename or '(未命名)'} 超过单文件大小限制。",
                status_code=413,
            )
    return bytes(content)


async def process_uploads(
    uploads: list[UploadFile],
    settings: Settings,
) -> tuple[list[AcceptedFile], list[str]]:
    """Validate, decode and retain safe files only in request memory."""

    if not uploads:
        raise AnalysisInputError("请至少选择一个代码文件。")
    if len(uploads) > settings.max_file_count:
        raise AnalysisInputError(
            f"一次最多可以分析 {settings.max_file_count} 个文件。",
        )

    known_total_size = sum(upload.size or 0 for upload in uploads)
    if known_total_size > settings.max_total_file_size_bytes:
        raise AnalysisInputError("上传文件总大小超过限制。", status_code=413)

    accepted_files: list[AcceptedFile] = []
    warnings: list[str] = []
    actual_total_size = 0

    for upload in uploads:
        path = normalize_upload_path(upload.filename)
        skip_reason = get_skip_reason(path)
        if skip_reason:
            warnings.append(f"已跳过 {path}：{skip_reason}。")
            await upload.close()
            continue

        try:
            if upload.size is not None and upload.size > settings.max_file_size_bytes:
                raise AnalysisInputError(
                    f"文件 {path} 超过单文件大小限制。",
                    status_code=413,
                )

            raw_content = await read_limited_upload(upload, settings.max_file_size_bytes)
            actual_total_size += len(raw_content)
            if actual_total_size > settings.max_total_file_size_bytes:
                raise AnalysisInputError("上传文件总大小超过限制。", status_code=413)

            if b"\x00" in raw_content:
                warnings.append(f"已跳过 {path}：检测到二进制内容。")
                continue

            try:
                decoded_content = raw_content.decode("utf-8-sig")
            except UnicodeDecodeError:
                warnings.append(f"已跳过 {path}：当前仅支持 UTF-8 文本编码。")
                continue

            accepted_files.append(
                AcceptedFile(
                    path=path,
                    language=get_language(path) or "Text",
                    content=decoded_content,
                ),
            )
        finally:
            await upload.close()

    if not accepted_files:
        detail = "没有可用于分析的代码文件。"
        if warnings:
            detail = f"{detail} {warnings[0]}"
        raise AnalysisInputError(detail)

    return accepted_files, warnings


def build_context(
    question: str,
    files: list[AcceptedFile],
    conversation: list[ConversationMessage],
    max_chars: int,
) -> ContextResult:
    """Build line-numbered context without exceeding the character budget."""

    question_block = f"用户问题：\n{question}\n"
    context_parts = [question_block[:max_chars]]
    references: list[FileReference] = []
    warnings: list[str] = []
    used_chars = len(context_parts[0])
    if len(question_block) > max_chars:
        warnings.append("用户问题因上下文长度限制而被截断。")

    for accepted_file in files:
        header = f"\n文件：{accepted_file.path}（{accepted_file.language}）\n"
        if used_chars + len(header) > max_chars:
            warnings.append(f"未加入 {accepted_file.path}：上下文长度已达到限制。")
            continue

        context_parts.append(header)
        used_chars += len(header)
        source_lines = accepted_file.content.splitlines() or [""]
        included_lines = 0

        for line_number, line in enumerate(source_lines, start=1):
            numbered_line = f"{line_number:>5} | {line}\n"
            remaining_chars = max_chars - used_chars
            if remaining_chars <= 0:
                break
            if len(numbered_line) > remaining_chars:
                context_parts.append(numbered_line[:remaining_chars])
                used_chars = max_chars
                included_lines = line_number
                break
            context_parts.append(numbered_line)
            used_chars += len(numbered_line)
            included_lines = line_number

        if included_lines == 0:
            warnings.append(f"未加入 {accepted_file.path}：没有剩余上下文空间。")
            continue

        was_truncated = included_lines < len(source_lines)
        references.append(
            FileReference(
                file=accepted_file.path,
                language=accepted_file.language,
                start_line=1,
                end_line=included_lines,
                truncated=was_truncated,
            ),
        )
        if was_truncated:
            warnings.append(f"{accepted_file.path} 因上下文长度限制被截断。")

    if conversation:
        conversation_header = "\n最近对话：\n"
        if used_chars + len(conversation_header) > max_chars:
            warnings.append("对话历史未加入：上下文空间优先保留给代码文件。")
        else:
            context_parts.append(conversation_header)
            used_chars += len(conversation_header)

            for message in conversation:
                message_text = f"{message.role}: {message.content}\n"
                remaining_chars = max_chars - used_chars
                if remaining_chars <= 0:
                    warnings.append("对话历史因上下文长度限制而被截断。")
                    break
                if len(message_text) > remaining_chars:
                    context_parts.append(message_text[:remaining_chars])
                    used_chars = max_chars
                    warnings.append("对话历史因上下文长度限制而被截断。")
                    break
                context_parts.append(message_text)
                used_chars += len(message_text)

    return ContextResult(
        text="".join(context_parts),
        references=references,
        warnings=warnings,
    )


def create_demo_answer(
    question: str,
    accepted_files: list[AcceptedFile],
    references: list[FileReference],
) -> str:
    """Return an honest deterministic response while no model is configured."""

    referenced_file_names = "、".join(reference.file for reference in references)
    return (
        "当前请求已在演示模式下完成。后端已经校验并读取了 "
        f"{len(accepted_files)} 个代码文件，并围绕问题“{question}”构建了受限长度的上下文。\n\n"
        f"本次上下文引用：{referenced_file_names or '无'}。\n\n"
        "演示模式不会假装进行了真实的代码推理，也不会执行或修改这些文件。"
        "配置模型服务后，同一接口会使用已经构建好的代码上下文生成真实回答。"
    )


def parse_model_references(
    answer: str,
    available_references: list[FileReference],
) -> tuple[list[FileReference], list[str]]:
    """Validate model citations against files and line ranges in the context."""

    available_by_path = {reference.file: reference for reference in available_references}
    parsed_references: list[FileReference] = []
    warnings: list[str] = []
    invalid_citation_found = False

    for match in REFERENCE_PATTERN.finditer(answer):
        file_path = match.group("file").strip()
        start_line = int(match.group("start"))
        end_line = int(match.group("end"))
        available = available_by_path.get(file_path)
        if (
            available is None
            or start_line > end_line
            or start_line < available.start_line
            or end_line > available.end_line
        ):
            invalid_citation_found = True
            continue

        reference = FileReference(
            file=file_path,
            language=available.language,
            start_line=start_line,
            end_line=end_line,
            truncated=available.truncated,
        )
        if reference not in parsed_references:
            parsed_references.append(reference)

    if not parsed_references:
        warnings.append("模型回答中没有可验证的文件行号引用。")
    if invalid_citation_found:
        warnings.append("模型回答包含无法用当前上下文验证的引用，已忽略这些引用。")

    return parsed_references, warnings


async def analyze_code_request(
    question: str,
    uploads: list[UploadFile],
    raw_conversation: str | None,
    settings: Settings,
    usage_context: UsageContext | None = None,
    request_id: str | None = None,
) -> AnalysisResponse:
    """Run the safe pipeline in real mode when model configuration is complete."""

    cleaned_question = validate_question(question)
    conversation, conversation_warnings = parse_conversation(raw_conversation)
    accepted_files, file_warnings = await process_uploads(uploads, settings)
    context = build_context(
        question=cleaned_question,
        files=accepted_files,
        conversation=conversation,
        max_chars=settings.max_context_chars,
    )

    shared_warnings = [
        *conversation_warnings,
        *file_warnings,
        *context.warnings,
    ]

    model_configured = is_model_configured(settings)
    if usage_context is not None:
        usages = {"files": len(accepted_files)}
        if model_configured:
            usages["model_calls"] = 1
        usage_service.consume_many(
            context=usage_context,
            usages=usages,
            settings=settings,
            request_id=request_id,
        )

    if model_configured:
        answer = await request_chat_completion(context=context.text, settings=settings)
        references, reference_warnings = parse_model_references(
            answer,
            context.references,
        )
        mode = "real"
        warnings = [*shared_warnings, *reference_warnings]
    else:
        answer = create_demo_answer(
            question=cleaned_question,
            accepted_files=accepted_files,
            references=context.references,
        )
        references = context.references
        mode = "demo"
        warnings = [
            "当前未调用真实模型，返回的是用于验证请求链路的演示回答（模型配置不完整）。",
            *shared_warnings,
        ]

    return AnalysisResponse(
        answer=answer,
        references=references,
        mode=mode,
        warnings=warnings,
        stats=AnalysisStats(
            received_files=len(uploads),
            accepted_files=len(accepted_files),
            skipped_files=len(uploads) - len(accepted_files),
            context_chars=len(context.text),
            conversation_messages=len(conversation),
        ),
    )


async def analyze_in_demo_mode(
    question: str,
    uploads: list[UploadFile],
    raw_conversation: str | None,
    settings: Settings,
) -> AnalysisResponse:
    """Backward-compatible helper that forces the deterministic demo mode."""

    demo_settings = settings.model_copy(
        update={"model_api_key": "", "model_base_url": "", "model_name": ""},
    )
    return await analyze_code_request(
        question=question,
        uploads=uploads,
        raw_conversation=raw_conversation,
        settings=demo_settings,
    )
