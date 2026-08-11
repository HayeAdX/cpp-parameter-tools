#!/usr/bin/env python3
"""Copy function and parameter comments from a reference C++ file."""

from __future__ import annotations

import argparse
import difflib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import align_cpp_parameters as aligner
import cpp_signature_tools as cpp


TARGET_FUNCTION_PREFIX = "FIC_"


@dataclass(frozen=True)
class SyncResult:
    text: str
    matched_functions: int
    unmatched_functions: tuple[str, ...]
    ambiguous_functions: tuple[str, ...]
    changed_comments: int


def _match_reference_function(
    target: cpp.FunctionInfo,
    references: Sequence[cpp.FunctionInfo],
) -> tuple[cpp.FunctionInfo | None, bool]:
    """Match by the FIC_-normalized name and parameter count only."""

    target_name = target.base_name
    if target_name.startswith(TARGET_FUNCTION_PREFIX):
        target_name = target_name[len(TARGET_FUNCTION_PREFIX) :]

    candidates = [
        reference
        for reference in references
        if reference.base_name == target_name and reference.arity == target.arity
    ]
    if not candidates:
        return None, False
    if len(candidates) != 1:
        return None, True
    return candidates[0], False


def _replace_inline_comment(
    line: str,
    lexical: aligner.LexedLine,
    new_comment: str,
) -> str:
    body, newline = cpp.split_line_ending(line)
    code, _old_comment = aligner._split_trailing_comment(body, lexical)
    return code.rstrip() + "\t" + new_comment.rstrip() + newline


def _comment_is_syncable(
    line: str,
    lexical: aligner.LexedLine,
) -> bool:
    """Allow inline comments; require a tab for comment-only lines."""

    body, _newline = cpp.split_line_ending(line)
    code, comment = aligner._split_trailing_comment(body, lexical)
    if comment is not None:
        if code.strip():
            return True
        comment_index = len(code)
        return any(
            body[index] == "\t" and lexical.kinds[index] == "C"
            for index in range(comment_index)
        )

    if "M" not in lexical.kinds[: len(body)]:
        return False
    leading_whitespace = body[: len(body) - len(body.lstrip(" \t"))]
    return "\t" in leading_whitespace


def _parameter_pairs(
    reference: cpp.FunctionInfo,
    target: cpp.FunctionInfo,
) -> list[tuple[cpp.ParameterInfo, cpp.ParameterInfo]]:
    reference_by_name: dict[str, list[cpp.ParameterInfo]] = {}
    for parameter in reference.parameters:
        if parameter.name:
            reference_by_name.setdefault(parameter.name.casefold(), []).append(parameter)

    pairs: list[tuple[cpp.ParameterInfo, cpp.ParameterInfo]] = []
    used_reference_lines: set[int] = set()

    for position, target_parameter in enumerate(target.parameters):
        reference_parameter: cpp.ParameterInfo | None = None
        if target_parameter.name:
            same_name = reference_by_name.get(target_parameter.name.casefold(), [])
            unused_same_name = [
                candidate
                for candidate in same_name
                if candidate.line_index not in used_reference_lines
            ]
            if len(unused_same_name) == 1:
                reference_parameter = unused_same_name[0]

        if reference_parameter is None and position < len(reference.parameters):
            positional = reference.parameters[position]
            if positional.line_index not in used_reference_lines:
                reference_parameter = positional

        if reference_parameter is not None:
            pairs.append((reference_parameter, target_parameter))
            used_reference_lines.add(reference_parameter.line_index)

    return pairs


def _adapt_comment_block(
    reference_lines: Sequence[str],
    target_indent: str,
    target_newline: str,
) -> list[str]:
    bodies = [cpp.split_line_ending(line)[0] for line in reference_lines]
    indents = [
        len(body) - len(body.lstrip(" \t"))
        for body in bodies
        if body.strip()
    ]
    common_indent = min(indents, default=0)
    return [target_indent + body[common_indent:] + target_newline for body in bodies]


def sync_comments(reference_text: str, target_text: str) -> SyncResult:
    reference = cpp.parse_source(reference_text)
    target = cpp.parse_source(target_text)
    lines = list(target.lines)
    line_comment_updates: dict[int, str] = {}
    leading_replacements: list[tuple[int, int, list[str]]] = []
    unmatched: list[str] = []
    ambiguous: list[str] = []
    matched_functions = 0

    for target_function in target.functions:
        reference_function, is_ambiguous = _match_reference_function(
            target_function,
            reference.functions,
        )
        display_name = f"{target_function.full_name}/{target_function.arity}"
        if is_ambiguous:
            ambiguous.append(display_name)
            continue
        if reference_function is None:
            unmatched.append(display_name)
            continue

        matched_functions += 1
        if reference_function.opener_comment is not None and _comment_is_syncable(
            reference.lines[reference_function.opener_line],
            reference.lexed[reference_function.opener_line],
        ):
            line_comment_updates[target_function.opener_line] = (
                reference_function.opener_comment
            )
        if reference_function.closing_comment is not None and _comment_is_syncable(
            reference.lines[reference_function.close_line],
            reference.lexed[reference_function.close_line],
        ):
            line_comment_updates[target_function.close_line] = (
                reference_function.closing_comment
            )

        for reference_parameter, target_parameter in _parameter_pairs(
            reference_function,
            target_function,
        ):
            if reference_parameter.comment is not None and _comment_is_syncable(
                reference.lines[reference_parameter.line_index],
                reference.lexed[reference_parameter.line_index],
            ):
                line_comment_updates[target_parameter.line_index] = (
                    reference_parameter.comment
                )

        leading_comment_has_tabs = bool(reference_function.leading_comment_lines) and all(
            _comment_is_syncable(reference.lines[line_index], reference.lexed[line_index])
            for line_index in range(
                reference_function.leading_comment_start,
                reference_function.opener_line,
            )
        )
        if leading_comment_has_tabs:
            opener_body, opener_newline = cpp.split_line_ending(
                lines[target_function.opener_line]
            )
            target_indent = opener_body[: len(opener_body) - len(opener_body.lstrip(" \t"))]
            replacement = _adapt_comment_block(
                reference_function.leading_comment_lines,
                target_indent,
                opener_newline or cpp.newline_for(lines),
            )
            leading_replacements.append(
                (
                    target_function.leading_comment_start,
                    target_function.opener_line,
                    replacement,
                )
            )

    changed_comments = 0
    for line_index, new_comment in line_comment_updates.items():
        updated = _replace_inline_comment(
            lines[line_index],
            target.lexed[line_index],
            new_comment,
        )
        if updated != lines[line_index]:
            changed_comments += 1
            lines[line_index] = updated

    for start, end, replacement in sorted(leading_replacements, reverse=True):
        if lines[start:end] != replacement:
            changed_comments += max(len(replacement), end - start)
            lines[start:end] = replacement

    return SyncResult(
        text="".join(lines),
        matched_functions=matched_functions,
        unmatched_functions=tuple(unmatched),
        ambiguous_functions=tuple(ambiguous),
        changed_comments=changed_comments,
    )


def _supported_file(parser: argparse.ArgumentParser, path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        parser.error(f"{label} introuvable ou non fichier: {resolved}")
    if resolved.suffix.lower() not in set(cpp.SUPPORTED_EXTENSIONS):
        parser.error(f"extension {label} non prise en charge: {resolved.suffix}")
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Copie les commentaires des fonctions et paramètres d'un fichier C++ "
            "de référence vers un fichier cible."
        )
    )
    parser.add_argument("reference", type=Path, help="fichier .cpp/.h de référence")
    parser.add_argument("target", type=Path, help="fichier .cpp/.h à modifier")
    parser.add_argument(
        "--check",
        action="store_true",
        help="ne rien écrire et retourner 1 si la cible doit changer",
    )
    parser.add_argument(
        "--diff",
        action="store_true",
        help="afficher les différences sans modifier la cible",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="retourner 2 si une fonction cible est absente ou ambiguë",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="afficher les fonctions absentes ou ambiguës",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    reference_path = _supported_file(parser, args.reference, "référence")
    target_path = _supported_file(parser, args.target, "cible")

    try:
        reference_text, _reference_bom = cpp.read_utf8(reference_path)
        target_text, target_bom = cpp.read_utf8(target_path)
        result = sync_comments(reference_text, target_text)
    except (OSError, UnicodeError) as error:
        print(f"ERREUR: {error}", file=sys.stderr)
        return 2

    changed = result.text != target_text
    preview_only = args.check or args.diff
    if changed:
        print(f"{'À_MODIFIER' if preview_only else 'MODIFIÉ'} {target_path}")
        if args.diff:
            sys.stdout.writelines(
                difflib.unified_diff(
                    target_text.splitlines(keepends=True),
                    result.text.splitlines(keepends=True),
                    fromfile=str(target_path),
                    tofile=str(target_path),
                )
            )
        if not preview_only:
            try:
                cpp.write_utf8_atomic(target_path, result.text, target_bom)
            except OSError as error:
                print(f"ERREUR {target_path}: {error}", file=sys.stderr)
                return 2

    if args.verbose:
        for name in result.unmatched_functions:
            print(f"ABSENTE {name}")
        for name in result.ambiguous_functions:
            print(f"AMBIGUË {name}")

    print(
        "Résumé: "
        f"{result.matched_functions} fonction(s) associée(s), "
        f"{result.changed_comments} commentaire(s) modifié(s), "
        f"{len(result.unmatched_functions)} absente(s), "
        f"{len(result.ambiguous_functions)} ambiguë(s)."
    )

    if args.strict and (result.unmatched_functions or result.ambiguous_functions):
        return 2
    if args.check and changed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
