"""Shared, conservative C++ signature parsing helpers.

This is not a complete C++ parser.  It deliberately recognises the multiline
signature layout handled by the scripts in this directory and skips constructs
that cannot be identified safely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import align_cpp_parameters as aligner


SUPPORTED_EXTENSIONS = aligner.DEFAULT_EXTENSIONS
HEADER_EXTENSIONS = aligner.HEADER_EXTENSIONS

FUNCTION_NAME_RE = re.compile(
    r"(?P<name>(?:(?:[A-Za-z_]\w*::)*)~?[A-Za-z_]\w*|"
    r"operator\s*(?:\(\)|\[\]|[^\s]+))\s*$"
)
IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")


@dataclass(frozen=True)
class ParameterInfo:
    line_index: int
    name: str | None
    type_fingerprint: str
    name_start_column: int | None
    name_end_column: int | None
    comment: str | None


@dataclass(frozen=True)
class FunctionInfo:
    opener_line: int
    close_line: int
    open_column: int
    close_column: int
    base_name: str
    full_name: str
    parameters: tuple[ParameterInfo, ...]
    leading_comment_start: int
    leading_comment_lines: tuple[str, ...]
    opener_comment: str | None
    closing_comment: str | None
    closing_suffix_fingerprint: str

    @property
    def arity(self) -> int:
        return len(self.parameters)

    @property
    def type_fingerprint(self) -> tuple[str, ...]:
        return tuple(parameter.type_fingerprint for parameter in self.parameters)

    @property
    def parameter_names(self) -> tuple[str | None, ...]:
        return tuple(parameter.name for parameter in self.parameters)


@dataclass(frozen=True)
class ParsedSource:
    text: str
    lines: tuple[str, ...]
    lexed: tuple[aligner.LexedLine, ...]
    line_offsets: tuple[int, ...]
    code_mask: str
    functions: tuple[FunctionInfo, ...]

    def absolute_offset(self, line_index: int, column: int) -> int:
        return self.line_offsets[line_index] + column


def split_line_ending(line: str) -> tuple[str, str]:
    return aligner._split_line_ending(line)


def newline_for(lines: Sequence[str]) -> str:
    for line in lines:
        _body, newline = split_line_ending(line)
        if newline:
            return newline
    return "\n"


def read_utf8(path: Path) -> tuple[str, bool]:
    return aligner._decode_utf8(path.read_bytes())


def write_utf8_atomic(path: Path, text: str, has_bom: bool) -> None:
    aligner._atomic_write(path, aligner._encode_utf8(text, has_bom))


def iter_cpp_files(path: Path, recursive: bool = True):
    return aligner._iter_files(path, SUPPORTED_EXTENSIONS, recursive)


def is_header(path: Path) -> bool:
    return path.suffix.lower() in HEADER_EXTENSIONS


def _line_offsets(lines: Sequence[str]) -> tuple[int, ...]:
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)
    return tuple(offsets)


def _declarative_scopes(
    lines: Sequence[str],
    lexed: Sequence[aligner.LexedLine],
) -> list[tuple[str, ...]]:
    stack: list[tuple[str, ...] | None] = []
    scopes: list[tuple[str, ...]] = []
    statement = ""

    for line, lexical in zip(lines, lexed):
        names: list[str] = []
        for entry in stack:
            if entry:
                names.extend(entry)
        scopes.append(tuple(names))

        for char in lexical.mask:
            if char in "\r\n":
                continue
            if char == "{":
                context = statement.strip()
                namespace_matches = list(
                    re.finditer(r"\bnamespace(?:\s+([A-Za-z_]\w*(?:::[A-Za-z_]\w*)*))?", context)
                )
                type_matches = list(
                    re.finditer(
                        r"\b(?:class|struct|union)\s+([A-Za-z_]\w*)",
                        context,
                    )
                )
                if type_matches:
                    stack.append((type_matches[-1].group(1),))
                elif namespace_matches:
                    namespace_name = namespace_matches[-1].group(1)
                    stack.append(tuple(namespace_name.split("::")) if namespace_name else ())
                elif re.search(r"\bextern\s*$", context):
                    stack.append(())
                else:
                    stack.append(None)
                statement = ""
            elif char == "}":
                if stack:
                    stack.pop()
                statement = ""
            elif char == ";":
                statement = ""
            else:
                statement += char

        if len(statement) > 4096:
            statement = statement[-4096:]
        if line.endswith(("\n", "\r")):
            statement += " "

    return scopes


def _extract_function_name(code_before_paren: str, scope: Sequence[str]) -> tuple[str, str] | None:
    stripped = code_before_paren.strip()
    match = FUNCTION_NAME_RE.search(stripped)
    if match is None:
        return None

    raw_name = re.sub(r"\s+", "", match.group("name"))
    base_name = raw_name.rsplit("::", 1)[-1]
    if raw_name.startswith("operator"):
        base_name = raw_name

    if "::" in raw_name:
        raw_parts = tuple(part for part in raw_name.split("::") if part)
        if scope and raw_parts[: len(scope)] != tuple(scope):
            full_parts = tuple(scope) + raw_parts
        else:
            full_parts = raw_parts
    else:
        full_parts = tuple(scope) + (raw_name,)

    return base_name, "::".join(full_parts)


def _comment_only(line: str, lexical: aligner.LexedLine) -> bool:
    body, _newline = split_line_ending(line)
    if not body.strip():
        return False
    return not lexical.mask.rstrip("\r\n").strip() and "M" in lexical.kinds


def _leading_comment_block(
    lines: Sequence[str],
    lexed: Sequence[aligner.LexedLine],
    opener_line: int,
) -> tuple[int, tuple[str, ...]]:
    start = opener_line
    while start > 0 and _comment_only(lines[start - 1], lexed[start - 1]):
        start -= 1
    return start, tuple(lines[start:opener_line])


def _inline_comment(line: str, lexical: aligner.LexedLine) -> str | None:
    body, _newline = split_line_ending(line)
    _code, comment = aligner._split_trailing_comment(body, lexical)
    return comment


def _parameter_info(
    line_index: int,
    line: str,
    lexical: aligner.LexedLine,
) -> ParameterInfo | None:
    row = aligner._parse_parameter_row(line_index, line, lexical)
    if row is None:
        return None
    if row.declaration == "void" and row.name_part is None:
        return None

    if row.name_part is None or row.type_part is None:
        return ParameterInfo(
            line_index=line_index,
            name=None,
            type_fingerprint=re.sub(r"\s+", "", row.declaration),
            name_start_column=None,
            name_end_column=None,
            comment=row.comment,
        )

    name_match = IDENTIFIER_RE.match(row.name_part)
    if name_match is None:
        raise ValueError("parameter name is not an identifier")
    name = name_match.group(0)

    body, _newline = split_line_ending(line)
    code, _comment = aligner._split_trailing_comment(body, lexical)
    cursor = len(code) - len(code.lstrip(" \t"))
    if cursor < len(code) and code[cursor] == ",":
        cursor += 1
        while cursor < len(code) and code[cursor] in " \t":
            cursor += 1

    search_start = min(len(code), cursor + len(row.type_part))
    absolute_match = re.search(rf"\b{re.escape(name)}\b", code[search_start:])
    if absolute_match is None:
        raise ValueError("parameter name position not found")
    name_start = search_start + absolute_match.start()

    type_fingerprint = re.sub(r"\s+", "", row.type_part)
    return ParameterInfo(
        line_index=line_index,
        name=name,
        type_fingerprint=type_fingerprint,
        name_start_column=name_start,
        name_end_column=name_start + len(name),
        comment=row.comment,
    )


def _closing_suffix_fingerprint(mask: str, close_column: int) -> str:
    suffix = mask[close_column + 1 :]
    suffix = re.sub(r"[;{}]", "", suffix)
    suffix = re.sub(r"\b(?:final|override)\b", "", suffix)
    return re.sub(r"\s+", "", suffix)


def parse_source(text: str) -> ParsedSource:
    lines = text.splitlines(keepends=True)
    if not lines and text:
        lines = [text]
    lexed = aligner._lex_lines(lines)
    executable_scope = aligner._scope_is_executable(lines, lexed)
    scopes = _declarative_scopes(lines, lexed)
    functions: list[FunctionInfo] = []
    line_index = 0

    while line_index < len(lines):
        code = lexed[line_index].mask.rstrip("\r\n").rstrip()
        if not code.endswith("("):
            line_index += 1
            continue

        open_column = len(code) - 1
        if executable_scope[line_index] or not aligner._looks_like_function_opener(
            code[:open_column]
        ):
            line_index += 1
            continue

        name = _extract_function_name(code[:open_column], scopes[line_index])
        closing = aligner._find_closing_paren(line_index, lexed)
        if name is None or closing is None:
            line_index += 1
            continue

        close_line, close_column = closing
        if lexed[close_line].mask[:close_column].strip() or not aligner._valid_closing_suffix(
            lexed[close_line].mask, close_column
        ):
            line_index = close_line + 1
            continue

        parameters: list[ParameterInfo] = []
        try:
            for parameter_line in range(line_index + 1, close_line):
                parameter = _parameter_info(
                    parameter_line,
                    lines[parameter_line],
                    lexed[parameter_line],
                )
                if parameter is not None:
                    parameters.append(parameter)
        except ValueError:
            line_index = close_line + 1
            continue

        leading_start, leading_lines = _leading_comment_block(lines, lexed, line_index)
        base_name, full_name = name
        functions.append(
            FunctionInfo(
                opener_line=line_index,
                close_line=close_line,
                open_column=open_column,
                close_column=close_column,
                base_name=base_name,
                full_name=full_name,
                parameters=tuple(parameters),
                leading_comment_start=leading_start,
                leading_comment_lines=leading_lines,
                opener_comment=_inline_comment(lines[line_index], lexed[line_index]),
                closing_comment=_inline_comment(lines[close_line], lexed[close_line]),
                closing_suffix_fingerprint=_closing_suffix_fingerprint(
                    lexed[close_line].mask,
                    close_column,
                ),
            )
        )
        line_index = close_line + 1

    offsets = _line_offsets(lines)
    return ParsedSource(
        text=text,
        lines=tuple(lines),
        lexed=tuple(lexed),
        line_offsets=offsets,
        code_mask="".join(line.mask for line in lexed),
        functions=tuple(functions),
    )


def find_body_span(parsed: ParsedSource, function: FunctionInfo) -> tuple[int, int] | None:
    close_offset = parsed.absolute_offset(function.close_line, function.close_column)
    mask = parsed.code_mask
    position = close_offset + 1
    paren_depth = 0
    bracket_depth = 0

    while position < len(mask):
        char = mask[position]
        if char == "(":
            paren_depth += 1
            position += 1
            continue
        if char == ")" and paren_depth:
            paren_depth -= 1
            position += 1
            continue
        if char == "[":
            bracket_depth += 1
            position += 1
            continue
        if char == "]" and bracket_depth:
            bracket_depth -= 1
            position += 1
            continue
        if char == ";" and not (paren_depth or bracket_depth):
            return None
        if char != "{":
            position += 1
            continue

        closing = _matching_brace(mask, position)
        if closing is None:
            return None

        if paren_depth or bracket_depth:
            position = closing + 1
            continue

        following = _next_code_character(mask, closing + 1)
        if following is not None and mask[following] in {",", "{"}:
            position = closing + 1
            continue
        return position, closing

    return None


def _matching_brace(mask: str, opening: int) -> int | None:
    depth = 1
    for position in range(opening + 1, len(mask)):
        if mask[position] == "{":
            depth += 1
        elif mask[position] == "}":
            depth -= 1
            if depth == 0:
                return position
    return None


def _next_code_character(mask: str, position: int) -> int | None:
    while position < len(mask):
        if not mask[position].isspace():
            return position
        position += 1
    return None


def previous_code_character(mask: str, position: int) -> int | None:
    position -= 1
    while position >= 0:
        if not mask[position].isspace():
            return position
        position -= 1
    return None


def next_code_character(mask: str, position: int) -> int | None:
    return _next_code_character(mask, position)
