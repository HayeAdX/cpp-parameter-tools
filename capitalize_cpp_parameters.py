#!/usr/bin/env python3
"""Uppercase the first character of C++ parameter names."""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import cpp_signature_tools as cpp


@dataclass(frozen=True)
class CapitalizeResult:
    text: str
    renamed_parameters: int
    renamed_references: int
    conflicting_functions: tuple[str, ...]


@dataclass
class RunStats:
    scanned_files: int = 0
    changed_files: int = 0
    renamed_parameters: int = 0
    renamed_references: int = 0
    conflicts: int = 0
    errors: int = 0


def _capitalized(name: str) -> str:
    if not name:
        return name
    return name[0].upper() + name[1:]


def _qualified_or_member_access(mask: str, start: int) -> bool:
    previous = cpp.previous_code_character(mask, start)
    if previous is None:
        return False
    if mask[previous] == ".":
        return True
    before_previous = cpp.previous_code_character(mask, previous)
    if before_previous is None:
        return False
    return (mask[before_previous : previous + 1] in {"->", "::"})


def _initializer_designator(
    mask: str,
    start: int,
    end: int,
    body_open: int,
) -> bool:
    if start >= body_open:
        return False
    previous = cpp.previous_code_character(mask, start)
    following = cpp.next_code_character(mask, end)
    if previous is None or following is None:
        return False
    return mask[previous] in {":", ","} and mask[following] in {"(", "{"}


def _body_replacements(
    parsed: cpp.ParsedSource,
    function: cpp.FunctionInfo,
    renames: dict[str, str],
) -> list[tuple[int, int, str]]:
    body_span = cpp.find_body_span(parsed, function)
    if body_span is None:
        return []

    body_open, body_close = body_span
    close_offset = parsed.absolute_offset(function.close_line, function.close_column)
    scan_start = close_offset + 1
    mask = parsed.code_mask
    replacements: list[tuple[int, int, str]] = []

    for old_name, new_name in renames.items():
        pattern = re.compile(rf"\b{re.escape(old_name)}\b")
        for match in pattern.finditer(mask, scan_start, body_close + 1):
            start, end = match.span()
            if _qualified_or_member_access(mask, start):
                continue
            if _initializer_designator(mask, start, end, body_open):
                continue
            replacements.append((start, end, new_name))

    return replacements


def capitalize_parameters(text: str) -> CapitalizeResult:
    parsed = cpp.parse_source(text)
    replacements: dict[tuple[int, int], str] = {}
    conflicting: list[str] = []
    renamed_parameters = 0
    renamed_references = 0

    for function in parsed.functions:
        named_parameters = [
            parameter for parameter in function.parameters if parameter.name is not None
        ]
        renames = {
            parameter.name: _capitalized(parameter.name)
            for parameter in named_parameters
            if parameter.name is not None and _capitalized(parameter.name) != parameter.name
        }
        if not renames:
            continue

        final_names = [
            renames.get(parameter.name, parameter.name)
            for parameter in named_parameters
        ]
        if len(final_names) != len(set(final_names)):
            conflicting.append(f"{function.full_name}/{function.arity}")
            continue

        for parameter in named_parameters:
            if parameter.name not in renames:
                continue
            if parameter.name_start_column is None or parameter.name_end_column is None:
                continue
            start = parsed.absolute_offset(
                parameter.line_index,
                parameter.name_start_column,
            )
            end = parsed.absolute_offset(
                parameter.line_index,
                parameter.name_end_column,
            )
            replacements[(start, end)] = renames[parameter.name]
            renamed_parameters += 1

        for start, end, new_name in _body_replacements(parsed, function, renames):
            replacements[(start, end)] = new_name
            renamed_references += 1

    updated = text
    for (start, end), replacement in sorted(replacements.items(), reverse=True):
        updated = updated[:start] + replacement + updated[end:]

    return CapitalizeResult(
        text=updated,
        renamed_parameters=renamed_parameters,
        renamed_references=renamed_references,
        conflicting_functions=tuple(conflicting),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Met en majuscule le premier caractère des noms de paramètres dans "
            "les fichiers C++ d'un dossier."
        )
    )
    parser.add_argument("path", type=Path, help="fichier ou dossier C/C++ à traiter")
    parser.add_argument(
        "--check",
        action="store_true",
        help="ne rien écrire et retourner 1 si un fichier doit changer",
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
        "--strict",
        action="store_true",
        help="retourner 2 lorsqu'un renommage créerait un doublon",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="afficher les fichiers inchangés et les fonctions en conflit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    target = args.path.expanduser().resolve()
    if not target.exists():
        parser.error(f"chemin introuvable: {target}")

    files = list(cpp.iter_cpp_files(target, recursive=not args.no_recursive))
    if target.is_file() and not files:
        parser.error(f"extension non prise en charge: {target.suffix or '(aucune)'}")

    stats = RunStats()
    preview_only = args.check or args.diff
    for path in files:
        stats.scanned_files += 1
        try:
            original, has_bom = cpp.read_utf8(path)
            result = capitalize_parameters(original)
        except (OSError, UnicodeError) as error:
            stats.errors += 1
            print(f"ERREUR {path}: {error}", file=sys.stderr)
            continue

        stats.renamed_parameters += result.renamed_parameters
        stats.renamed_references += result.renamed_references
        stats.conflicts += len(result.conflicting_functions)
        changed = result.text != original

        if changed:
            stats.changed_files += 1
            print(f"{'À_MODIFIER' if preview_only else 'MODIFIÉ'} {path}")
            if args.diff:
                sys.stdout.writelines(
                    difflib.unified_diff(
                        original.splitlines(keepends=True),
                        result.text.splitlines(keepends=True),
                        fromfile=str(path),
                        tofile=str(path),
                    )
                )
            if not preview_only:
                try:
                    cpp.write_utf8_atomic(path, result.text, has_bom)
                except OSError as error:
                    stats.errors += 1
                    print(f"ERREUR {path}: {error}", file=sys.stderr)
                    continue
        elif args.verbose:
            print(f"OK {path}")

        if args.verbose:
            for function_name in result.conflicting_functions:
                print(f"CONFLIT {path}: {function_name}")

    print(
        "Résumé: "
        f"{stats.scanned_files} fichier(s), "
        f"{stats.changed_files} à modifier/modifié(s), "
        f"{stats.renamed_parameters} paramètre(s) renommé(s), "
        f"{stats.renamed_references} usage(s) renommé(s), "
        f"{stats.conflicts} conflit(s)."
    )

    if stats.errors or (args.strict and stats.conflicts):
        return 2
    if args.check and stats.changed_files:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
