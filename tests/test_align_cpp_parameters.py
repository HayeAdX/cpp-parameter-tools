from __future__ import annotations

import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path

import align_cpp_parameters as aligner


def comment_column(line: str, tab_width: int = 4) -> int:
    return line.expandtabs(tab_width).rindex("//")


def token_column(line: str, token: str, tab_width: int = 4) -> int:
    return line.expandtabs(tab_width).index(token)


class FormatSourceTests(unittest.TestCase):
    def test_header_aligns_names_defaults_comments_and_leading_commas(self) -> None:
        source = (
            "Result\n"
            "// Documentation conservée.\n"
            "make_order(\n"
            "    int id = 0 // identifiant\n"
            "    , const std::vector<std::string>& symbols = {} // symboles\n"
            "    , bool enabled = true // état\n"
            ");\n"
        )

        result = aligner.format_source(source, is_header=True)
        lines = result.text.splitlines()
        parameter_lines = lines[3:6]

        self.assertEqual(result.formatted_blocks, 1)
        self.assertEqual(lines[0], "Result")
        self.assertEqual(lines[1], "// Documentation conservée.")
        self.assertEqual(lines[2], "make_order(")
        self.assertEqual(lines[6], ");")
        self.assertTrue(all(line.startswith("\t") for line in parameter_lines))
        self.assertTrue(all(not line.startswith("    ") for line in parameter_lines))
        self.assertEqual(
            [
                token_column(line, token)
                for line, token in zip(parameter_lines, ("id", "symbols", "enabled"))
            ],
            [36, 36, 36],
        )
        self.assertEqual([token_column(line, "=") for line in parameter_lines], [44, 44, 44])
        self.assertEqual([comment_column(line) for line in parameter_lines], [52, 52, 52])
        self.assertIn("id\t\t= 0,", parameter_lines[0])
        self.assertIn("symbols\t= {},", parameter_lines[1])
        self.assertNotIn(", const", result.text)
        self.assertRegex(parameter_lines[0], r"0,\t+// identifiant$")
        self.assertRegex(parameter_lines[1], r"\{},\t+// symboles$")
        self.assertRegex(parameter_lines[2], r"true\t+// état$")
        self.assertEqual(
            aligner.format_source(result.text, is_header=True).text,
            result.text,
        )

    def test_cpp_aligns_names_and_comments(self) -> None:
        source = (
            "Widget\n"
            "build_widget(\n"
            "  int id, // court\n"
            "  VeryLongCustomType value // long\n"
            ")\n"
            "{\n"
            "}\n"
        )

        result = aligner.format_source(source, is_header=False)
        first, second = result.text.splitlines()[2:4]

        self.assertEqual(token_column(first, "id"), token_column(second, "value"))
        self.assertEqual(comment_column(first), comment_column(second))
        self.assertTrue(first.startswith("\tint\t"))
        self.assertTrue(second.startswith("\tVeryLongCustomType\t"))

    def test_calls_inside_function_bodies_are_unchanged(self) -> None:
        source = (
            "void run()\n"
            "{\n"
            "    consume(\n"
            "        lhs * rhs,\n"
            "        left * right\n"
            "    );\n"
            "}\n"
        )

        result = aligner.format_source(source, is_header=False)

        self.assertEqual(result.text, source)
        self.assertEqual(result.formatted_blocks, 0)

    def test_strings_and_nested_parentheses_do_not_break_parsing(self) -> None:
        source = (
            "void\n"
            "configure(\n"
            "  std::string url = \"https://example.test/a//b\", // url\n"
            "  Callback callback = make_callback(1, 2) // callback\n"
            ");\n"
        )

        result = aligner.format_source(source, is_header=True)
        first, second = result.text.splitlines()[2:4]

        self.assertIn('= \"https://example.test/a//b\",', first)
        self.assertIn("= make_callback(1, 2)", second)
        self.assertEqual(comment_column(first), comment_column(second))

    def test_function_pointer_parameter_is_supported(self) -> None:
        source = (
            "void\n"
            "register_callback(\n"
            "  int event_id,\n"
            "  void (*callback)(int) // callback\n"
            ");\n"
        )

        result = aligner.format_source(source, is_header=True)

        self.assertIn("void (*\tcallback)(int)", result.text)

    def test_class_method_is_formatted_but_inline_call_is_unchanged(self) -> None:
        source = (
            "class Service\n"
            "{\n"
            "public:\n"
            "    void send(\n"
            "      int id,\n"
            "      LongRequest request\n"
            "    );\n"
            "\n"
            "    void execute()\n"
            "    {\n"
            "        consume(\n"
            "            lhs * rhs,\n"
            "            left * right\n"
            "        );\n"
            "    }\n"
            "};\n"
        )

        result = aligner.format_source(source, is_header=True)
        lines = result.text.splitlines()

        self.assertEqual(result.formatted_blocks, 1)
        self.assertTrue(lines[4].startswith("    \tint\t"))
        self.assertTrue(lines[5].startswith("    \tLongRequest\t"))
        self.assertEqual(lines[11], "            lhs * rhs,")
        self.assertEqual(lines[12], "            left * right")

    def test_alignment_separators_are_tabs_not_spaces(self) -> None:
        source = (
            "void f(\n"
            "    int short_name = 1, // premier\n"
            "    const LongType& longer_name = 200 // second\n"
            ");\n"
        )

        result = aligner.format_source(source, is_header=True)
        first, second = result.text.splitlines()[1:3]

        first_match = re.fullmatch(
            r"\tint(?P<type_gap>\t+)short_name(?P<equal_gap>\t+)= 1,(?P<comment_gap>\t+)// premier",
            first,
        )
        second_match = re.fullmatch(
            r"\tconst LongType&(?P<type_gap>\t+)longer_name"
            r"(?P<equal_gap>\t+)= 200(?P<comment_gap>\t+)// second",
            second,
        )
        self.assertIsNotNone(first_match)
        self.assertIsNotNone(second_match)

    def test_leading_comma_style_places_separator_after_indent(self) -> None:
        source = (
            "void configure(\n"
            "  int short_name = 1, // premier\n"
            "  const LongType& longer_name = 200, // second\n"
            "  bool enabled = true // dernier\n"
            ");\n"
        )

        result = aligner.format_source(
            source,
            is_header=True,
            comma_style="leading",
        )
        first, second, third = result.text.splitlines()[1:4]

        self.assertTrue(first.startswith("\tint"))
        self.assertTrue(second.startswith("\t, const LongType&"))
        self.assertTrue(third.startswith("\t, bool"))
        self.assertNotRegex(first, r",\t+// premier$")
        self.assertNotRegex(second, r"200,\t+// second$")
        self.assertEqual(
            [
                token_column(line, token)
                for line, token in zip(
                    (first, second, third),
                    ("short_name", "longer_name", "enabled"),
                )
            ],
            [24, 24, 24],
        )
        self.assertEqual(
            [token_column(line, "=") for line in (first, second, third)],
            [36, 36, 36],
        )
        self.assertEqual(
            [comment_column(line) for line in (first, second, third)],
            [44, 44, 44],
        )
        self.assertEqual(
            aligner.format_source(
                result.text,
                is_header=True,
                comma_style="leading",
            ).text,
            result.text,
        )

        trailing = aligner.format_source(
            result.text,
            is_header=True,
            comma_style="trailing",
        ).text
        trailing_lines = trailing.splitlines()[1:4]
        self.assertRegex(trailing_lines[0], r"1,\t+// premier$")
        self.assertRegex(trailing_lines[1], r"200,\t+// second$")
        self.assertNotIn("\n\t, ", trailing)

    def test_unknown_comma_style_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "comma_style"):
            aligner.format_source(
                "void f(\n  int value\n);\n",
                is_header=True,
                comma_style="middle",
            )

    def test_common_cpp_declarators_are_supported_and_idempotent(self) -> None:
        source = (
            "void visit(\n"
            " int* pointer,\n"
            " const std::array<int, 2>& values,\n"
            " char name[16],\n"
            " Args&&... args,\n"
            " int (&array)[4],\n"
            " ...\n"
            ");\n"
        )

        result = aligner.format_source(source, is_header=True)

        self.assertEqual(result.formatted_blocks, 1)
        self.assertIn("\tconst std::array<int, 2>&\tvalues,", result.text)
        self.assertIn("\tint (&\t", result.text)
        self.assertIn("array)[4],", result.text)
        self.assertEqual(
            aligner.format_source(result.text, is_header=True).text,
            result.text,
        )

    def test_unsupported_multiline_parameter_is_left_unchanged(self) -> None:
        source = (
            "void complex(\n"
            "  std::vector<\n"
            "      int> values\n"
            ");\n"
        )

        result = aligner.format_source(source, is_header=True)

        self.assertEqual(result.text, source)
        self.assertEqual(result.formatted_blocks, 0)


class CliTests(unittest.TestCase):
    def test_check_then_write_then_check_and_preserve_crlf_bom(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            header = root / "sample.h"
            nested = root / "nested"
            nested.mkdir()
            source_file = nested / "sample.cpp"
            ignored = root / "sample.txt"
            original = (
                "void f(\r\n"
                "  int x, // x\r\n"
                "  LongType value // value\r\n"
                ");\r\n"
            )
            header.write_bytes(b"\xef\xbb\xbf" + original.encode("utf-8"))
            source_file.write_text(
                "Widget\n"
                "build(\n"
                "  int id, // id\n"
                "  LongType value // value\n"
                ");\n",
                encoding="utf-8",
            )
            ignored.write_text(original, encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(aligner.main(["--check", str(root)]), 1)
                self.assertEqual(aligner.main([str(root)]), 0)
                self.assertEqual(aligner.main(["--check", str(root)]), 0)

            payload = header.read_bytes()
            self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))
            self.assertIn(b"\r\n", payload)
            self.assertNotIn(b"\n", payload.replace(b"\r\n", b""))
            self.assertIn("\tint\t", source_file.read_text(encoding="utf-8"))
            self.assertEqual(ignored.read_bytes(), original.encode("utf-8"))

    def test_cli_can_select_leading_comma_style(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_file = Path(temporary_directory) / "sample.cpp"
            source_file.write_text(
                "void process(\n"
                "  int first, // premier\n"
                "  LongType second // second\n"
                ");\n",
                encoding="utf-8",
            )

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    aligner.main(
                        ["--check", "--comma-style", "leading", str(source_file)]
                    ),
                    1,
                )
                self.assertEqual(
                    aligner.main(
                        ["--comma-style", "leading", str(source_file)]
                    ),
                    0,
                )
                self.assertEqual(
                    aligner.main(
                        ["--check", "--comma-style", "leading", str(source_file)]
                    ),
                    0,
                )

            formatted = source_file.read_text(encoding="utf-8")
            self.assertIn("\n\t, LongType", formatted)
            self.assertNotRegex(formatted, r"first,\t+// premier")


if __name__ == "__main__":
    unittest.main()
