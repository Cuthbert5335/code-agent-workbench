"""Explainable regex-based symbol extraction for common source languages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern

from fastapi import UploadFile

from app.config import Settings
from app.schemas.symbols import CodeSymbol, SymbolKind, SymbolResponse, SymbolStats
from app.services.analysis import AcceptedFile, AnalysisInputError, process_uploads

MAX_SYMBOL_QUERY_CHARS = 200
MAX_SYMBOLS_PER_FILE = 200
MAX_SYMBOLS = 500
MAX_DECLARATION_CHARS = 300


@dataclass(frozen=True)
class SymbolRule:
    """One language-specific declaration pattern and its output kind."""

    kind: SymbolKind
    pattern: Pattern[str]


def compile_rule(kind: SymbolKind, pattern: str) -> SymbolRule:
    return SymbolRule(kind=kind, pattern=re.compile(pattern))


COMMON_SCRIPT_RULES = (
    compile_rule("class", r"^\s*(?:export\s+)?(?:default\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)"),
    compile_rule("interface", r"^\s*(?:export\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)"),
    compile_rule("enum", r"^\s*(?:export\s+)?(?:const\s+)?enum\s+(?P<name>[A-Za-z_$][\w$]*)"),
    compile_rule("type", r"^\s*(?:export\s+)?type\s+(?P<name>[A-Za-z_$][\w$]*)"),
    compile_rule(
        "function",
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<name>[A-Za-z_$][\w$]*)",
    ),
    compile_rule(
        "function",
        r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
    ),
)

RULES_BY_LANGUAGE: dict[str, tuple[SymbolRule, ...]] = {
    "Python": (
        compile_rule("class", r"^\s*class\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("function", r"^\s*(?:async\s+)?def\s+(?P<name>[A-Za-z_]\w*)"),
    ),
    "JavaScript": COMMON_SCRIPT_RULES,
    "JavaScript JSX": COMMON_SCRIPT_RULES,
    "TypeScript": COMMON_SCRIPT_RULES,
    "TypeScript JSX": COMMON_SCRIPT_RULES,
    "Vue": COMMON_SCRIPT_RULES,
    "Svelte": COMMON_SCRIPT_RULES,
    "Java": (
        compile_rule("class", r"^\s*(?:public|protected|private|abstract|final|static|sealed|non-sealed|\s)*class\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("interface", r"^\s*(?:public|protected|private|abstract|static|sealed|\s)*interface\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("enum", r"^\s*(?:public|protected|private|static|\s)*enum\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule(
            "function",
            r"^\s*(?:public|protected|private|static|final|abstract|synchronized|native|default|\s)+[\w<>\[\],.?]+\s+(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*(?:\{|throws\b)",
        ),
    ),
    "C": (
        compile_rule("struct", r"^\s*(?:typedef\s+)?struct\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("enum", r"^\s*(?:typedef\s+)?enum\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("function", r"^\s*(?:[A-Za-z_]\w*[\s*]+)+(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*\{"),
    ),
    "C Header": (),
    "C++": (
        compile_rule("class", r"^\s*class\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("struct", r"^\s*struct\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("enum", r"^\s*enum(?:\s+class)?\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("function", r"^\s*(?:[\w:<>,~*&]+\s+)+(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*(?:const\s*)?\{"),
    ),
    "C++ Header": (),
    "C#": (
        compile_rule("class", r"^\s*(?:public|private|protected|internal|abstract|sealed|static|partial|\s)*class\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("interface", r"^\s*(?:public|private|protected|internal|partial|\s)*interface\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("enum", r"^\s*(?:public|private|protected|internal|\s)*enum\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("struct", r"^\s*(?:public|private|protected|internal|readonly|partial|\s)*struct\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("function", r"^\s*(?:public|private|protected|internal|static|virtual|override|abstract|async|sealed|partial|\s)+[\w<>\[\],.?]+\s+(?P<name>[A-Za-z_]\w*)\s*\([^;]*\)\s*(?:\{|=>)"),
    ),
    "Go": (
        compile_rule("function", r"^\s*func\s+(?:\([^)]*\)\s*)?(?P<name>[A-Za-z_]\w*)"),
        compile_rule("struct", r"^\s*type\s+(?P<name>[A-Za-z_]\w*)\s+struct\b"),
        compile_rule("interface", r"^\s*type\s+(?P<name>[A-Za-z_]\w*)\s+interface\b"),
        compile_rule("type", r"^\s*type\s+(?P<name>[A-Za-z_]\w*)\b"),
    ),
    "Rust": (
        compile_rule("function", r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("struct", r"^\s*(?:pub(?:\([^)]*\))?\s+)?struct\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("enum", r"^\s*(?:pub(?:\([^)]*\))?\s+)?enum\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("trait", r"^\s*(?:pub(?:\([^)]*\))?\s+)?trait\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("module", r"^\s*(?:pub(?:\([^)]*\))?\s+)?mod\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("type", r"^\s*(?:pub(?:\([^)]*\))?\s+)?type\s+(?P<name>[A-Za-z_]\w*)"),
    ),
    "PHP": (
        compile_rule("class", r"^\s*(?:abstract\s+|final\s+)?class\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("interface", r"^\s*interface\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("trait", r"^\s*trait\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("function", r"^\s*(?:public|protected|private|static|final|abstract|\s)*function\s+&?\s*(?P<name>[A-Za-z_]\w*)"),
    ),
    "Ruby": (
        compile_rule("class", r"^\s*class\s+(?P<name>[A-Z]\w*(?:::\w+)*)"),
        compile_rule("module", r"^\s*module\s+(?P<name>[A-Z]\w*(?:::\w+)*)"),
        compile_rule("function", r"^\s*def\s+(?P<name>(?:self\.)?[A-Za-z_]\w*[!?=]?)"),
    ),
    "Swift": (
        compile_rule("class", r"^\s*(?:public|private|internal|open|final|\s)*class\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("struct", r"^\s*(?:public|private|internal|\s)*struct\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("interface", r"^\s*(?:public|private|internal|\s)*protocol\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("enum", r"^\s*(?:public|private|internal|\s)*enum\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("function", r"^\s*(?:public|private|internal|open|static|class|mutating|async|\s)*func\s+(?P<name>[A-Za-z_]\w*)"),
    ),
    "Kotlin": (
        compile_rule("class", r"^\s*(?:public|private|protected|internal|open|abstract|sealed|data|enum|annotation|value|\s)*class\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("interface", r"^\s*(?:public|private|protected|internal|sealed|fun|\s)*interface\s+(?P<name>[A-Za-z_]\w*)"),
        compile_rule("function", r"^\s*(?:public|private|protected|internal|open|abstract|override|suspend|inline|operator|infix|tailrec|external|\s)*fun\s+(?:<[^>]+>\s*)?(?P<name>[A-Za-z_]\w*)"),
        compile_rule("type", r"^\s*(?:public|private|protected|internal|\s)*typealias\s+(?P<name>[A-Za-z_]\w*)"),
    ),
    "Kotlin Script": (),
}

RULES_BY_LANGUAGE["C Header"] = RULES_BY_LANGUAGE["C"]
RULES_BY_LANGUAGE["C++ Header"] = RULES_BY_LANGUAGE["C++"]
RULES_BY_LANGUAGE["Kotlin Script"] = RULES_BY_LANGUAGE["Kotlin"]


def validate_symbol_query(query: str | None) -> str | None:
    """Normalize an optional literal name filter."""

    if query is None or not query.strip():
        return None
    cleaned_query = query.strip()
    if len(cleaned_query) > MAX_SYMBOL_QUERY_CHARS:
        raise AnalysisInputError(
            f"符号关键词不能超过 {MAX_SYMBOL_QUERY_CHARS} 个字符。",
        )
    return cleaned_query


def extract_symbols_from_file(
    accepted_file: AcceptedFile,
    *,
    query: str | None = None,
    max_symbols: int = MAX_SYMBOLS_PER_FILE,
) -> tuple[list[CodeSymbol], bool]:
    """Extract bounded declarations from one decoded source file."""

    rules = RULES_BY_LANGUAGE.get(accepted_file.language, ())
    normalized_query = query.casefold() if query else None
    symbols: list[CodeSymbol] = []
    truncated = False

    for line_number, line in enumerate(accepted_file.content.splitlines(), start=1):
        for rule in rules:
            match = rule.pattern.match(line)
            if not match:
                continue

            name = match.group("name")
            if normalized_query and normalized_query not in name.casefold():
                break
            if len(symbols) >= max_symbols:
                truncated = True
                return symbols, truncated

            declaration = line.strip()
            if len(declaration) > MAX_DECLARATION_CHARS:
                declaration = f"{declaration[:MAX_DECLARATION_CHARS]}…"
            symbols.append(
                CodeSymbol(
                    name=name,
                    kind=rule.kind,
                    file=accepted_file.path,
                    language=accepted_file.language,
                    line_number=line_number,
                    declaration=declaration,
                ),
            )
            break

    return symbols, truncated


async def find_code_symbols(
    *,
    uploads: list[UploadFile],
    settings: Settings,
    query: str | None = None,
    max_symbols: int = MAX_SYMBOLS,
) -> SymbolResponse:
    """Validate selected files and return filtered syntax-light declarations."""

    cleaned_query = validate_symbol_query(query)
    accepted_files, file_warnings = await process_uploads(uploads, settings)
    symbols: list[CodeSymbol] = []
    warnings = [*file_warnings]
    symbol_files: set[str] = set()
    truncated = False
    total_truncated = False

    for file_index, accepted_file in enumerate(accepted_files):
        remaining = max_symbols - len(symbols)
        if remaining <= 0:
            truncated = True
            total_truncated = True
            break
        file_limit = min(MAX_SYMBOLS_PER_FILE, remaining)
        file_symbols, file_truncated = extract_symbols_from_file(
            accepted_file,
            query=cleaned_query,
            max_symbols=file_limit,
        )
        symbols.extend(file_symbols)
        if file_symbols:
            symbol_files.add(accepted_file.path)
        if file_truncated:
            truncated = True
            if remaining <= MAX_SYMBOLS_PER_FILE:
                total_truncated = True
                break
            warnings.append(
                f"{accepted_file.path} 的符号数量超过单文件 "
                f"{MAX_SYMBOLS_PER_FILE} 条限制，仅返回前面的结果。",
            )
        if len(symbols) >= max_symbols and file_index < len(accepted_files) - 1:
            truncated = True
            total_truncated = True
            break

    if total_truncated:
        warnings.append(f"符号总结果达到 {max_symbols} 条限制，仅返回前面的结果。")

    return SymbolResponse(
        query=cleaned_query,
        symbols=symbols,
        warnings=warnings,
        truncated=truncated,
        stats=SymbolStats(
            received_files=len(uploads),
            accepted_files=len(accepted_files),
            skipped_files=len(uploads) - len(accepted_files),
            symbol_files=len(symbol_files),
            symbols=len(symbols),
        ),
    )
