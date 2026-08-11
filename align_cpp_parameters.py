#!/usr/bin/env python3
"""Align C++ multiline function parameters with tab characters.

The formatter is intentionally conservative: it only rewrites function parameter
lists whose opening and closing parentheses are on their own logical lines and
whose parameters each fit on one physical line.
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_EXTENSIONS = (
    ".c++",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".h++",
    ".hh",
    ".hpp",
    ".hxx",
)
HEADER_EXTENSIONS = {".h", ".h++", ".hh", ".hpp", ".hxx"}

CONTROL_WORDS = {
    "alignof",
    "catch",
    "decltype",
    "for",
    "if",
    "noexcept",
    "requires",
    "sizeof",
    "static_assert",
    "switch",
    "typeid",
    "while",
}

CXX_KEYWORDS = {
    "alignas",
    "alignof",
    "and",
    "and_eq",
    "asm",
    "auto",
    "bitand",
    "bitor",
    "bool",
    "break",
    "case",
    "catch",
    "char",
    "char8_t",
    "char16_t",
    "char32_t",
    "class",
    "compl",
    "concept",
    "const",
    "consteval",
    "constexpr",
    "constinit",
    "const_cast",
    "continue",
    "co_await",
    "co_return",
    "co_yield",
    "decltype",
    "default",
    "delete",
    "do",
    "double",
    "dynamic_cast",
    "else",
    "enum",
    "explicit",
    "export",
    "extern",
    "false",
    "float",
    "for",
    "friend",
    "goto",
    "if",
    "inline",
    "int",
    "long",
    "mutable",
    "namespace",
    "new",
    "noexcept",
    "not",
    "not_eq",
    "nullptr",
    "operator",
    "or",
    "or_eq",
    "private",
    "protected",
    "public",
    "register",
    "reinterpret_cast",
    "requires",
    "return",
    "short",
    "signed",
    "sizeof",
    "static",
    "static_assert",
    "static_cast",
    "struct",
    "switch",
    "template",
    "this",
    "thread_local",
    "throw",
    "true",
    "try",
    "typedef",
    "typeid",
    "typename",
    "union",
    "unsigned",
    "using",
    "virtual",
    "void",
    "volatile",
    "wchar_t",
    "while",
    "xor",
    "xor_eq",
}

TYPE_PREFIX_ONLY_WORDS = {
    "class",
    "const",
    "enum",
    "extern",
    "friend",
    "inline",
    "mutable",
    "register",
    "static",
    "struct",
    "thread_local",
    "typename",
    "union",
    "virtual",
    "volatile",
}

FUNCTION_POINTER_RE = re.compile(
    r"^(?P<type>.*\(\s*(?:(?:[A-Za-z_]\w*::)+)?[*&]{1,2}\s*"
    r"(?:(?:const|volatile)\s+)*)"
    r"(?P<name>[A-Za-z_]\w*)"
    r"(?P<suffix>\s*\).*)$"
)

RAW_STRING_START_RE = re.compile(r'(?:u8|u|U|L)?R"([^\s()\\]{0,16})\(')


@dataclass
class LexedLine:
    mask: str
    kinds: str


@dataclass
class ParameterRow:
    line_index: int
    newline: str
    declaration: str
    comment: str | None
    leading_comma: bool
    trailing_comma: bool
    type_part: str | None
    name_part: str | None
    default_value: str | None


@dataclass
class FormatResult:
    text: str
    formatted_blocks: int
    skipped_blocks: int


@dataclass
class RunStats:
    scanned_files: int = 0
    changed_files: int = 0
    formatted_blocks: int = 0
    skipped_blocks: int = 0
    errors: int = 0


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1], line[-1]
    return line, ""


def _lex_lines(lines: Sequence[str]) -> list[LexedLine]:
    """Mask comments and literals while retaining exact character positions."""

    result: list[LexedLine] = []
    state = "code"
    quote = ""
    raw_end = ""
    escaped = False

    for line in lines:
        mask = list(line)
        kinds = ["C"] * len(line)
        index = 0

        while index < len(line):
            char = line[index]

            if state == "block_comment":
                kinds[index] = "M"
                if char not in "\r\n":
                    mask[index] = " "
                if line.startswith("*/", index):
                    if index + 1 < len(line):
                        kinds[index + 1] = "M"
                        mask[index + 1] = " "
                    index += 2
                    state = "code"
                    continue
                index += 1
                continue

            if state == "raw_string":
                kinds[index] = "S"
                if char not in "\r\n":
                    mask[index] = " "
                if raw_end and line.startswith(raw_end, index):
                    for offset in range(len(raw_end)):
                        if index + offset < len(line):
                            kinds[index + offset] = "S"
                            if line[index + offset] not in "\r\n":
                                mask[index + offset] = " "
                    index += len(raw_end)
                    state = "code"
                    raw_end = ""
                    continue
                index += 1
                continue

            if state == "quoted":
                kinds[index] = "S"
                if char not in "\r\n":
                    mask[index] = " "
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    state = "code"
                    quote = ""
                elif char in "\r\n":
                    state = "code"
                    quote = ""
                index += 1
                continue

            if line.startswith("//", index):
                for offset in range(index, len(line)):
                    kinds[offset] = "M"
                    if line[offset] not in "\r\n":
                        mask[offset] = " "
                break

            if line.startswith("/*", index):
                kinds[index] = "M"
                mask[index] = " "
                if index + 1 < len(line):
                    kinds[index + 1] = "M"
                    mask[index + 1] = " "
                index += 2
                state = "block_comment"
                continue

            raw_match = RAW_STRING_START_RE.match(line, index)
            if raw_match:
                raw_end = ")" + raw_match.group(1) + '"'
                for offset in range(index, raw_match.end()):
                    kinds[offset] = "S"
                    mask[offset] = " "
                index = raw_match.end()
                state = "raw_string"
                continue

            if char in {'"', "'"}:
                kinds[index] = "S"
                mask[index] = " "
                state = "quoted"
                quote = char
                escaped = False
                index += 1
                continue

            index += 1

        result.append(LexedLine("".join(mask), "".join(kinds)))

    return result


def _scope_is_executable(lines: Sequence[str], lexed: Sequence[LexedLine]) -> list[bool]:
    """Return whether each line starts inside a function/block scope."""

    scope_stack: list[str] = []
    executable: list[bool] = []
    statement = ""

    for line, lexical in zip(lines, lexed):
        executable.append("block" in scope_stack)
        code = lexical.mask

        for char in code:
            if char in "\r\n":
                continue
            if char == "{":
                context = statement.strip()
                declarative = bool(
                    re.search(r"\b(?:class|enum|namespace|struct|union)\b[^;{}]*$", context)
                    or re.search(r"\bextern\s*$", context)
                )
                scope_stack.append("declarative" if declarative else "block")
                statement = ""
            elif char == "}":
                if scope_stack:
                    scope_stack.pop()
                statement = ""
            elif char == ";":
                statement = ""
            else:
                statement += char

        if len(statement) > 2048:
            statement = statement[-2048:]
        if line.endswith(("\n", "\r")):
            statement += " "

    return executable


def _split_trailing_comment(body: str, lexical: LexedLine) -> tuple[str, str | None]:
    index = 0
    while index < len(body):
        if lexical.kinds[index] == "M" and body.startswith("//", index):
            return body[:index], body[index:].rstrip()
        if lexical.kinds[index] == "M" and body.startswith("/*", index):
            end = body.find("*/", index + 2)
            if end == -1 or not body[end + 2 :].strip():
                return body[:index], body[index:].rstrip()
            index = end + 2
            continue
        index += 1
    return body, None


def _angle_opener(text: str, index: int) -> bool:
    previous = index - 1
    while previous >= 0 and text[previous].isspace():
        previous -= 1
    if previous < 0:
        return False
    return text[previous].isalnum() or text[previous] in "_:>)"


def _split_default_value(declaration: str) -> tuple[str, str | None]:
    lexical = _lex_lines([declaration])[0]
    paren = bracket = brace = angle = 0

    for index, char in enumerate(declaration):
        if lexical.kinds[index] != "C":
            continue
        if char == "(":
            paren += 1
        elif char == ")" and paren:
            paren -= 1
        elif char == "[":
            bracket += 1
        elif char == "]" and bracket:
            bracket -= 1
        elif char == "{":
            brace += 1
        elif char == "}" and brace:
            brace -= 1
        elif char == "<" and not (paren or bracket or brace) and _angle_opener(declaration, index):
            angle += 1
        elif char == ">" and angle and not (paren or bracket or brace):
            angle -= 1
        elif char == "=" and not (paren or bracket or brace or angle):
            previous = declaration[index - 1] if index else ""
            following = declaration[index + 1] if index + 1 < len(declaration) else ""
            if previous not in "=!<>+-*/%&|^" and following != "=":
                return declaration[:index].rstrip(), declaration[index + 1 :].strip()

    return declaration.rstrip(), None


def _top_level_identifiers(declaration: str) -> list[tuple[int, int, str]]:
    lexical = _lex_lines([declaration])[0]
    identifiers: list[tuple[int, int, str]] = []
    paren = bracket = brace = angle = 0
    index = 0

    while index < len(declaration):
        if lexical.kinds[index] != "C":
            index += 1
            continue

        char = declaration[index]
        if char == "(":
            paren += 1
            index += 1
            continue
        if char == ")":
            paren = max(0, paren - 1)
            index += 1
            continue
        if char == "[":
            bracket += 1
            index += 1
            continue
        if char == "]":
            bracket = max(0, bracket - 1)
            index += 1
            continue
        if char == "{":
            brace += 1
            index += 1
            continue
        if char == "}":
            brace = max(0, brace - 1)
            index += 1
            continue
        if char == "<" and not (paren or bracket or brace) and _angle_opener(declaration, index):
            angle += 1
            index += 1
            continue
        if char == ">" and angle and not (paren or bracket or brace):
            angle -= 1
            index += 1
            continue

        if (char.isalpha() or char == "_") and not (paren or bracket or brace or angle):
            end = index + 1
            while end < len(declaration) and (
                declaration[end].isalnum() or declaration[end] == "_"
            ):
                end += 1
            identifiers.append((index, end, declaration[index:end]))
            index = end
            continue

        index += 1

    return identifiers


def _contains_invalid_type_operator(type_part: str) -> bool:
    lexical = _lex_lines([type_part])[0]
    paren = bracket = brace = angle = 0
    index = 0
    while index < len(type_part):
        if lexical.kinds[index] != "C":
            index += 1
            continue
        char = type_part[index]
        if char == "(":
            paren += 1
        elif char == ")" and paren:
            paren -= 1
        elif char == "[":
            bracket += 1
        elif char == "]" and bracket:
            bracket -= 1
        elif char == "{":
            brace += 1
        elif char == "}" and brace:
            brace -= 1
        elif char == "<" and not (paren or bracket or brace) and _angle_opener(type_part, index):
            angle += 1
        elif char == ">" and angle and not (paren or bracket or brace):
            angle -= 1
        elif not (paren or bracket or brace or angle):
            if char in "+/%?!;=|":
                return True
            if char == "-" and index + 1 < len(type_part) and type_part[index + 1] == ">":
                return True
        index += 1
    return False


def _split_type_and_name(declaration: str) -> tuple[str, str] | None:
    pointer_match = FUNCTION_POINTER_RE.match(declaration)
    if pointer_match:
        type_part = pointer_match.group("type").rstrip()
        name_part = (pointer_match.group("name") + pointer_match.group("suffix")).strip()
        if type_part and not _contains_invalid_type_operator(type_part):
            return type_part, name_part

    identifiers = _top_level_identifiers(declaration)
    if not identifiers:
        return None

    start, _end, name = identifiers[-1]
    if name in CXX_KEYWORDS:
        return None

    type_part = declaration[:start].rstrip()
    name_part = declaration[start:].strip()
    if not type_part or type_part.endswith("::"):
        return None
    if _contains_invalid_type_operator(type_part):
        return None

    type_words = re.findall(r"[A-Za-z_]\w*", type_part)
    if type_words and all(word in TYPE_PREFIX_ONLY_WORDS for word in type_words):
        return None

    return type_part, name_part


def _parse_parameter_row(
    line_index: int,
    line: str,
    lexical: LexedLine,
) -> ParameterRow | None:
    body, newline = _split_line_ending(line)
    code, comment = _split_trailing_comment(body, lexical)
    declaration = code.strip()

    if not declaration:
        return None
    if declaration.startswith("#") or declaration.endswith("\\"):
        raise ValueError("preprocessor line")

    leading_comma = declaration.startswith(",")
    if leading_comma:
        declaration = declaration[1:].lstrip()

    trailing_comma = declaration.endswith(",")
    if trailing_comma:
        declaration = declaration[:-1].rstrip()

    if not declaration:
        raise ValueError("empty parameter")

    declaration, default_value = _split_default_value(declaration)
    split = _split_type_and_name(declaration)
    type_part = split[0] if split else None
    name_part = split[1] if split else None

    if split is None and declaration not in {"...", "void"}:
        raise ValueError("unsupported declarator")

    return ParameterRow(
        line_index=line_index,
        newline=newline,
        declaration=declaration,
        comment=comment,
        leading_comma=leading_comma,
        trailing_comma=trailing_comma,
        type_part=type_part,
        name_part=name_part,
        default_value=default_value,
    )


def _display_width(text: str, tab_width: int) -> int:
    return len(text.expandtabs(tab_width))


def _next_tab_stop(column: int, tab_width: int) -> int:
    return column + (tab_width - column % tab_width)


def _tabs_to_column(column: int, target: int, tab_width: int) -> str:
    tabs: list[str] = []
    while column < target:
        tabs.append("\t")
        column += tab_width - column % tab_width
    return "".join(tabs)


def _looks_like_function_opener(code_before_paren: str) -> bool:
    stripped = code_before_paren.strip()
    if not stripped or stripped.startswith("#"):
        return False

    final_word_match = re.search(r"([A-Za-z_]\w*)\s*$", stripped)
    if final_word_match and final_word_match.group(1) in CONTROL_WORDS:
        return False

    name_match = re.search(
        r"(?P<name>(?:(?:[A-Za-z_]\w*::)*)~?[A-Za-z_]\w*|"
        r"operator\s*(?:\(\)|\[\]|[^\s]+))\s*$",
        stripped,
    )
    if not name_match:
        return False

    prefix = stripped[: name_match.start()].rstrip()
    if prefix.endswith(".") or prefix.endswith("->"):
        return False
    if prefix in {"co_return", "delete", "new", "return", "throw"}:
        return False
    return True


def _valid_closing_suffix(mask: str, close_column: int) -> bool:
    suffix = mask[close_column + 1 :].strip()
    if not suffix:
        return True
    if suffix.startswith((",", ".", "->", ")", "]")):
        return False
    return True


def _find_closing_paren(
    start_line: int,
    lexed: Sequence[LexedLine],
) -> tuple[int, int] | None:
    depth = 1
    for line_index in range(start_line + 1, len(lexed)):
        line_depth_start = depth
        for column, char in enumerate(lexed[line_index].mask):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    if line_depth_start != 1:
                        return None
                    return line_index, column
        if depth != 1:
            return None
    return None


def _format_block(
    lines: list[str],
    lexed: Sequence[LexedLine],
    start_line: int,
    end_line: int,
    *,
    align_defaults: bool,
    tab_width: int,
) -> bool:
    rows: list[ParameterRow] = []

    try:
        for line_index in range(start_line + 1, end_line):
            row = _parse_parameter_row(line_index, lines[line_index], lexed[line_index])
            if row is not None:
                rows.append(row)
    except ValueError:
        return False

    if not rows or not any(row.name_part is not None for row in rows):
        return False
    if rows[0].leading_comma:
        return False

    for index in range(1, len(rows)):
        current = rows[index]
        previous = rows[index - 1]
        if current.leading_comma:
            previous.trailing_comma = True
            current.leading_comma = False
        elif not previous.trailing_comma:
            return False

    opener_body, _ = _split_line_ending(lines[start_line])
    opener_indent = opener_body[: len(opener_body) - len(opener_body.lstrip(" \t"))]
    indent = opener_indent + "\t"

    named_rows = [row for row in rows if row.type_part is not None]
    type_end_columns = {
        row.line_index: _display_width(indent + (row.type_part or ""), tab_width)
        for row in named_rows
    }
    name_column = _next_tab_stop(max(type_end_columns.values()), tab_width)

    code_by_line: dict[int, str] = {}
    name_end_columns: dict[int, int] = {}
    for row in rows:
        if row.type_part is None or row.name_part is None:
            code = indent + row.declaration
        else:
            type_end = type_end_columns[row.line_index]
            code = (
                indent
                + row.type_part
                + _tabs_to_column(type_end, name_column, tab_width)
                + row.name_part
            )
            name_end_columns[row.line_index] = _display_width(code, tab_width)
        code_by_line[row.line_index] = code

    default_rows = [row for row in named_rows if row.default_value is not None]
    equals_column: int | None = None
    if align_defaults and default_rows:
        equals_column = _next_tab_stop(max(name_end_columns.values()), tab_width)

    for row in rows:
        code = code_by_line[row.line_index]
        if row.default_value is not None:
            current_column = _display_width(code, tab_width)
            if equals_column is not None:
                code += _tabs_to_column(current_column, equals_column, tab_width)
            else:
                code += "\t"
            code += "= " + row.default_value
        if row.trailing_comma:
            code += ","
        code_by_line[row.line_index] = code

    comment_rows = [row for row in rows if row.comment is not None]
    comment_column: int | None = None
    if comment_rows:
        longest_code = max(
            _display_width(code_by_line[row.line_index], tab_width) for row in rows
        )
        comment_column = _next_tab_stop(longest_code, tab_width)

    for row in rows:
        code = code_by_line[row.line_index]
        if row.comment is not None and comment_column is not None:
            current_column = _display_width(code, tab_width)
            code += _tabs_to_column(current_column, comment_column, tab_width) + row.comment
        lines[row.line_index] = code + row.newline

    return True


def format_source(text: str, *, is_header: bool, tab_width: int = 4) -> FormatResult:
    if tab_width < 1:
        raise ValueError("tab_width must be at least 1")

    lines = text.splitlines(keepends=True)
    if not lines and text:
        lines = [text]
    lexed = _lex_lines(lines)
    executable_scope = _scope_is_executable(lines, lexed)
    formatted_blocks = 0
    skipped_blocks = 0
    line_index = 0

    while line_index < len(lines):
        code = lexed[line_index].mask.rstrip("\r\n").rstrip()
        if not code.endswith("("):
            line_index += 1
            continue

        open_column = len(code) - 1
        if executable_scope[line_index] or not _looks_like_function_opener(code[:open_column]):
            line_index += 1
            continue

        closing = _find_closing_paren(line_index, lexed)
        if closing is None:
            skipped_blocks += 1
            line_index += 1
            continue

        end_line, close_column = closing
        if lexed[end_line].mask[:close_column].strip() or not _valid_closing_suffix(
            lexed[end_line].mask, close_column
        ):
            skipped_blocks += 1
            line_index = end_line + 1
            continue

        if _format_block(
            lines,
            lexed,
            line_index,
            end_line,
            align_defaults=is_header,
            tab_width=tab_width,
        ):
            formatted_blocks += 1
        else:
            skipped_blocks += 1
        line_index = end_line + 1

    return FormatResult("".join(lines), formatted_blocks, skipped_blocks)


def _normalise_extensions(raw_extensions: str) -> tuple[str, ...]:
    extensions: list[str] = []
    for raw in raw_extensions.split(","):
        extension = raw.strip().lower()
        if not extension:
            continue
        if not extension.startswith("."):
            extension = "." + extension
        extensions.append(extension)
    if not extensions:
        raise argparse.ArgumentTypeError("at least one extension is required")
    return tuple(dict.fromkeys(extensions))


def _iter_files(path: Path, extensions: Sequence[str], recursive: bool) -> Iterable[Path]:
    extension_set = {extension.lower() for extension in extensions}
    if path.is_file():
        if path.suffix.lower() in extension_set:
            yield path
        return

    iterator = path.rglob("*") if recursive else path.glob("*")
    for candidate in sorted(iterator):
        if candidate.is_file() and not candidate.is_symlink():
            if candidate.suffix.lower() in extension_set:
                yield candidate


def _atomic_write(path: Path, payload: bytes) -> None:
    original_mode = stat.S_IMODE(path.stat().st_mode)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, original_mode)
        os.replace(temporary_path, path)
    except BaseException:
        try:
            temporary_path.unlink(missing_ok=True)
        finally:
            raise


def _decode_utf8(payload: bytes) -> tuple[str, bool]:
    has_bom = payload.startswith(b"\xef\xbb\xbf")
    if has_bom:
        payload = payload[3:]
    return payload.decode("utf-8"), has_bom


def _encode_utf8(text: str, has_bom: bool) -> bytes:
    encoded = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + encoded) if has_bom else encoded


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Aligne avec des tabulations les noms, initialisations et commentaires "
            "des paramètres de fonctions C++ multilignes."
        )
    )
    parser.add_argument("path", type=Path, help="fichier ou dossier C/C++ à traiter")
    parser.add_argument(
        "--check",
        action="store_true",
        help="ne rien écrire et retourner 1 si un fichier doit être reformatté",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="afficher les différences sans modifier les fichiers",
    )
    parser.add_argument(
        "--no-recursive",
        action="store_true",
        help="ne pas parcourir les sous-dossiers",
    )
    parser.add_argument(
        "--extensions",
        type=_normalise_extensions,
        default=DEFAULT_EXTENSIONS,
        metavar="LISTE",
        help="extensions séparées par des virgules (défaut: extensions C++ usuelles)",
    )
    parser.add_argument(
        "--tab-width",
        type=int,
        default=4,
        metavar="N",
        help="largeur visuelle d'une tabulation pour calculer les colonnes (défaut: 4)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="afficher aussi les fichiers qui ne changent pas",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    target = args.path.expanduser().resolve()
    if not target.exists():
        parser.error(f"chemin introuvable: {target}")
    if args.tab_width < 1:
        parser.error("--tab-width doit être supérieur ou égal à 1")

    files = list(_iter_files(target, args.extensions, not args.no_recursive))
    if target.is_file() and not files:
        parser.error(f"extension non prise en charge: {target.suffix or '(aucune)'}")

    stats = RunStats()
    preview_only = args.check or args.diff

    for path in files:
        stats.scanned_files += 1
        try:
            original_bytes = path.read_bytes()
            original, has_bom = _decode_utf8(original_bytes)
            result = format_source(
                original,
                is_header=path.suffix.lower() in HEADER_EXTENSIONS,
                tab_width=args.tab_width,
            )
        except (OSError, UnicodeError) as error:
            stats.errors += 1
            print(f"ERREUR {path}: {error}", file=sys.stderr)
            continue

        stats.formatted_blocks += result.formatted_blocks
        stats.skipped_blocks += result.skipped_blocks
        changed = result.text != original
        if changed:
            stats.changed_files += 1
            if preview_only:
                print(f"À_MODIFIER {path}")
            if args.diff:
                diff = difflib.unified_diff(
                    original.splitlines(keepends=True),
                    result.text.splitlines(keepends=True),
                    fromfile=str(path),
                    tofile=str(path),
                )
                sys.stdout.writelines(diff)
            if not preview_only:
                try:
                    _atomic_write(path, _encode_utf8(result.text, has_bom))
                except OSError as error:
                    stats.errors += 1
                    print(f"ERREUR {path}: {error}", file=sys.stderr)
                    continue
                print(f"MODIFIÉ {path}")
        elif args.verbose:
            print(f"OK {path}")

    print(
        "Résumé: "
        f"{stats.scanned_files} fichier(s), "
        f"{stats.changed_files} à modifier/modifié(s), "
        f"{stats.formatted_blocks} bloc(s) formaté(s), "
        f"{stats.skipped_blocks} bloc(s) ignoré(s)."
    )

    if stats.errors:
        return 2
    if args.check and stats.changed_files:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
