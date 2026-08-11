from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import sync_cpp_comments as synchronizer


class SyncCommentsTests(unittest.TestCase):
    def test_copies_function_and_parameter_comments_from_header_to_cpp(self) -> None:
        reference = (
            "class Service\n"
            "{\n"
            "public:\n"
            "Result\n"
            "\t// Construit le résultat.\n"
            "\t// Conserve la documentation.\n"
            "build(\t// signature\n"
            "\tint id,\t// identifiant\n"
            "\tconst Name& name\t// nom\n"
            ") const;\t// fin de fonction\n"
            "};\n"
        )
        target = (
            "Result\n"
            "// Ancienne documentation.\n"
            "Service::build(\n"
            "    int id, // ancien commentaire\n"
            "    const Name& name\n"
            ") const\n"
            "{\n"
            "    return make_result(id, name); // commentaire du corps\n"
            "}\n"
        )

        result = synchronizer.sync_comments(reference, target)

        self.assertEqual(result.matched_functions, 1)
        self.assertFalse(result.unmatched_functions)
        self.assertFalse(result.ambiguous_functions)
        self.assertIn("Result\n// Construit le résultat.\n", result.text)
        self.assertIn("// Conserve la documentation.\nService::build(\t// signature", result.text)
        self.assertIn("int id,\t// identifiant", result.text)
        self.assertIn("const Name& name\t// nom", result.text)
        self.assertIn(") const\t// fin de fonction", result.text)
        self.assertIn("// commentaire du corps", result.text)
        self.assertNotIn("ancien commentaire", result.text)
        self.assertNotIn("Ancienne documentation", result.text)
        self.assertEqual(
            synchronizer.sync_comments(reference, result.text).text,
            result.text,
        )

    def test_match_uses_only_name_and_arity_and_strips_target_fic_prefix(self) -> None:
        reference = (
            "namespace ReferenceScope\n"
            "{\n"
            "Value\n"
            "convert(\n"
            "  int value // commentaire copié\n"
            ");\n"
            "}\n"
        )
        target = (
            "namespace TargetScope\n"
            "{\n"
            "Value\n"
            "FIC_convert(\n"
            "  const Text& value\n"
            ");\n"
            "}\n"
        )

        result = synchronizer.sync_comments(reference, target)

        self.assertEqual(result.matched_functions, 1)
        self.assertFalse(result.unmatched_functions)
        self.assertFalse(result.ambiguous_functions)
        self.assertIn("const Text& value\t// commentaire copié", result.text)

    def test_parameter_comments_fall_back_to_position_without_using_types(self) -> None:
        reference = (
            "void process(\n"
            "  LegacyHandle& source_item, // description de l'élément\n"
            "  int source_count // quantité\n"
            ");\n"
        )
        target = (
            "void FIC_process(\n"
            "  ModernPointer renamed_item, // ancien élément\n"
            "  long renamed_count // ancienne quantité\n"
            ");\n"
        )

        result = synchronizer.sync_comments(reference, target)

        self.assertEqual(result.matched_functions, 1)
        self.assertFalse(result.unmatched_functions)
        self.assertFalse(result.ambiguous_functions)
        self.assertIn("ModernPointer renamed_item,\t// description de l'élément", result.text)
        self.assertIn("long renamed_count\t// quantité", result.text)
        self.assertNotIn("ancien élément", result.text)
        self.assertNotIn("ancienne quantité", result.text)

    def test_flush_left_comment_is_ignored_but_inline_comments_are_copied(self) -> None:
        reference = (
            "// documentation collée à gauche\tavec tab après le marqueur\n"
            "void run( // commentaire avec espaces\n"
            "  int value // paramètre avec espaces\n"
            "); // fermeture avec espaces\n"
        )
        target = (
            "// documentation cible\n"
            "void run(\t// ouverture cible\n"
            "  int value\t// paramètre cible\n"
            ");\t// fermeture cible\n"
        )

        result = synchronizer.sync_comments(reference, target)

        self.assertEqual(result.matched_functions, 1)
        self.assertIn("// documentation cible\nvoid run(", result.text)
        self.assertIn("void run(\t// commentaire avec espaces", result.text)
        self.assertIn("int value\t// paramètre avec espaces", result.text)
        self.assertIn(");\t// fermeture avec espaces", result.text)
        self.assertNotIn("ouverture cible", result.text)
        self.assertNotIn("paramètre cible", result.text)
        self.assertNotIn("fermeture cible", result.text)
        self.assertEqual(result.changed_comments, 3)

    def test_leading_comment_block_is_ignored_if_one_line_has_no_tab(self) -> None:
        reference = (
            "\t// ligne avec tabulation\n"
            "// ligne collée à gauche\n"
            "void run(\n"
            "\tint value\n"
            ");\n"
        )
        target = "// documentation cible\nvoid run(\n\tint value\n);\n"

        result = synchronizer.sync_comments(reference, target)

        self.assertEqual(result.matched_functions, 1)
        self.assertEqual(result.text, target)
        self.assertEqual(result.changed_comments, 0)

    def test_indented_multiline_leading_comment_is_copied(self) -> None:
        reference = (
            "\t/* documentation\n"
            "\t * sur plusieurs lignes\n"
            "\t */\n"
            "void run(\n"
            "\tint value\n"
            ");\n"
        )
        target = "void run(\n\tint value\n);\n"

        result = synchronizer.sync_comments(reference, target)

        self.assertEqual(result.matched_functions, 1)
        self.assertIn(
            "/* documentation\n * sur plusieurs lignes\n */\nvoid run(",
            result.text,
        )

    def test_same_name_and_arity_is_ambiguous_even_when_types_differ(self) -> None:
        reference = (
            "Value\n"
            "convert(\n"
            "  int value // entier\n"
            ");\n"
            "\n"
            "Value\n"
            "convert(\n"
            "  const Text& value // texte\n"
            ");\n"
        )
        target = (
            "Value\n"
            "FIC_convert(\n"
            "  const Text& value\n"
            ")\n"
            "{\n"
            "}\n"
        )

        result = synchronizer.sync_comments(reference, target)

        self.assertEqual(result.text, target)
        self.assertEqual(result.matched_functions, 0)
        self.assertEqual(result.ambiguous_functions, ("FIC_convert/1",))

    def test_same_name_with_different_arity_is_not_matched(self) -> None:
        reference = (
            "void process(\n"
            "  int first, // premier\n"
            "  int second // second\n"
            ");\n"
        )
        target = "void FIC_process(\n  int first\n);\n"

        result = synchronizer.sync_comments(reference, target)

        self.assertEqual(result.text, target)
        self.assertEqual(result.matched_functions, 0)
        self.assertEqual(result.unmatched_functions, ("FIC_process/1",))
        self.assertFalse(result.ambiguous_functions)

    def test_ambiguous_reference_is_reported_without_modifying_target(self) -> None:
        reference = (
            "void run(\n"
            "  int value // premier\n"
            ");\n"
            "void run(\n"
            "  int value // second\n"
            ");\n"
        )
        target = "void run(\n  int value\n);\n"

        result = synchronizer.sync_comments(reference, target)

        self.assertEqual(result.text, target)
        self.assertEqual(result.ambiguous_functions, ("run/1",))

    def test_missing_reference_comment_does_not_delete_target_comment(self) -> None:
        reference = "void run(\n  int value\n);\n"
        target = "void run(\n  int value // à conserver\n);\n"

        result = synchronizer.sync_comments(reference, target)

        self.assertEqual(result.text, target)


class SyncCommentsCliTests(unittest.TestCase):
    def test_check_write_check_preserves_target_bom_and_crlf(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            reference = root / "reference.h"
            target = root / "target.cpp"
            reference_text = "void run(\r\n  int value // valeur\r\n);\r\n"
            target_text = "void run(\r\n  int value\r\n);\r\n"
            reference.write_bytes(reference_text.encode("utf-8"))
            target.write_bytes(b"\xef\xbb\xbf" + target_text.encode("utf-8"))

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    synchronizer.main(["--check", str(reference), str(target)]),
                    1,
                )
                self.assertEqual(synchronizer.main([str(reference), str(target)]), 0)
                self.assertEqual(
                    synchronizer.main(["--check", str(reference), str(target)]),
                    0,
                )

            payload = target.read_bytes()
            self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"int value\t// valeur\r\n", payload)
            self.assertNotIn(b"\n", payload.replace(b"\r\n", b""))
            self.assertEqual(reference.read_bytes(), reference_text.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
