from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import capitalize_cpp_parameters as capitalizer


class CapitalizeParametersTests(unittest.TestCase):
    def test_header_parameter_names_are_capitalized(self) -> None:
        source = (
            "Result\n"
            "// Documentation inchangée.\n"
            "build(\n"
            "  int item_count = 1, // nombre item_count\n"
            "  const Name& name = {} // nom name\n"
            ");\n"
        )

        result = capitalizer.capitalize_parameters(source)

        self.assertEqual(result.renamed_parameters, 2)
        self.assertEqual(result.renamed_references, 0)
        self.assertIn("int Item_count = 1, // nombre item_count", result.text)
        self.assertIn("const Name& Name = {} // nom name", result.text)
        self.assertIn("// Documentation inchangée.", result.text)
        self.assertEqual(
            capitalizer.capitalize_parameters(result.text).text,
            result.text,
        )

    def test_cpp_definition_updates_initializers_and_body_references(self) -> None:
        source = (
            "Widget::Widget(\n"
            "    int value,\n"
            "    const std::string& name\n"
            ")\n"
            "    : value(value),\n"
            "      name_{name}\n"
            "{\n"
            "    this->value = value;\n"
            "    object.value = value;\n"
            "    log(\"value\", name); // value et name restent dans le commentaire\n"
            "}\n"
        )

        result = capitalizer.capitalize_parameters(source)

        self.assertEqual(result.renamed_parameters, 2)
        self.assertEqual(result.renamed_references, 5)
        self.assertIn("int Value,", result.text)
        self.assertIn("const std::string& Name", result.text)
        self.assertIn(": value(Value),", result.text)
        self.assertIn("name_{Name}", result.text)
        self.assertIn("this->value = Value;", result.text)
        self.assertIn("object.value = Value;", result.text)
        self.assertIn('log("value", Name); // value et name', result.text)
        self.assertEqual(
            capitalizer.capitalize_parameters(result.text).text,
            result.text,
        )

    def test_same_named_braced_member_initializer_keeps_member_name(self) -> None:
        source = (
            "Box::Box(\n"
            "  int value\n"
            ")\n"
            "  : value{value}\n"
            "{\n"
            "}\n"
        )

        result = capitalizer.capitalize_parameters(source)

        self.assertIn(": value{Value}", result.text)
        self.assertNotIn(": Value{Value}", result.text)

    def test_name_collision_skips_the_function(self) -> None:
        source = (
            "void run(\n"
            "  int value,\n"
            "  int Value\n"
            ");\n"
        )

        result = capitalizer.capitalize_parameters(source)

        self.assertEqual(result.text, source)
        self.assertEqual(result.conflicting_functions, ("run/2",))

    def test_function_pointer_and_array_parameter_names_are_capitalized(self) -> None:
        source = (
            "void register_all(\n"
            "  void (*callback)(int),\n"
            "  int values[4]\n"
            ");\n"
        )

        result = capitalizer.capitalize_parameters(source)

        self.assertIn("void (*Callback)(int),", result.text)
        self.assertIn("int Values[4]", result.text)

    def test_braces_in_noexcept_do_not_hide_the_real_function_body(self) -> None:
        source = (
            "void run(\n"
            "  int value\n"
            ") noexcept(noexcept(Token{value}))\n"
            "{\n"
            "  consume(value);\n"
            "}\n"
        )

        result = capitalizer.capitalize_parameters(source)

        self.assertEqual(result.renamed_references, 2)
        self.assertIn("Token{Value}", result.text)
        self.assertIn("consume(Value);", result.text)


class CapitalizeParametersCliTests(unittest.TestCase):
    def test_recursive_folder_check_write_check(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            nested = root / "nested"
            nested.mkdir()
            header = root / "sample.h"
            source_file = nested / "sample.cpp"
            ignored = root / "sample.txt"
            header.write_text("void run(\n  int value\n);\n", encoding="utf-8")
            source_file.write_text(
                "void run(\n"
                "  int value\n"
                ")\n"
                "{\n"
                "  consume(value);\n"
                "}\n",
                encoding="utf-8",
            )
            ignored.write_text("int value;\n", encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(capitalizer.main(["--check", str(root)]), 1)
                self.assertEqual(capitalizer.main([str(root)]), 0)
                self.assertEqual(capitalizer.main(["--check", str(root)]), 0)

            self.assertIn("int Value", header.read_text(encoding="utf-8"))
            cpp_text = source_file.read_text(encoding="utf-8")
            self.assertIn("int Value", cpp_text)
            self.assertIn("consume(Value);", cpp_text)
            self.assertEqual(ignored.read_text(encoding="utf-8"), "int value;\n")


if __name__ == "__main__":
    unittest.main()
