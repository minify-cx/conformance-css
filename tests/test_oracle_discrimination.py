import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("conformance", ROOT / "tools" / "conformance.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def _oracle_available() -> bool:
    return mod.chromium_path(None) is not None


@unittest.skipUnless(_oracle_available(), "Chromium is required to exercise the CSSOM oracle")
class OracleDiscriminationTests(unittest.TestCase):
    """Prove the strengthened oracle treats descriptor and @import/@charset
    mutations as semantic differences, while preserving conservative lexical
    equivalence for genuine serialization trivia."""

    def setUp(self):
        self.chromium = mod.chromium_path(None)

    def classify(self, before: str, after: str) -> str:
        oracle = mod.cssom_browser_batch(self.chromium, [("x", before, after)])
        return mod.classify({"id": "x"}, after, None, oracle["x"])[0]

    def assert_differs(self, before, after, label):
        self.assertEqual(self.classify(before, after), "cssom-difference", label)

    def assert_equivalent(self, before, after, label):
        self.assertEqual(self.classify(before, after), "pass", label)

    def test_counter_style_escape_whitespace_is_a_difference(self):
        # The defect found by the adversarial review: collapsing the whitespace
        # run after a CSS hex escape merges two @counter-style symbols into one
        # identifier. The strengthened oracle must not treat these as equal.
        self.assert_differs(
            '@counter-style a{symbols:\\2020  \\2021;suffix:"";}',
            '@counter-style a{symbols:\\2020 \\2021;suffix:"";}',
            "hex-escape terminator+separator collapse must be a difference")

    def test_counter_style_system_mutation_is_a_difference(self):
        self.assert_differs(
            '@counter-style a{system:cyclic;symbols:x;suffix:"";}',
            '@counter-style a{system:fixed;symbols:x;suffix:"";}',
            "counter-style system descriptor mutation must be a difference")

    def test_counter_style_pad_mutation_is_a_difference(self):
        self.assert_differs(
            '@counter-style a{system:cyclic;symbols:x;pad:3 "*";}',
            '@counter-style a{system:cyclic;symbols:x;pad:4 "*";}',
            "counter-style pad descriptor mutation must be a difference")

    def test_property_initial_value_mutation_is_a_difference(self):
        self.assert_differs(
            '@property --x{syntax:"<color>";inherits:false;initial-value:red;}',
            '@property --x{syntax:"<color>";inherits:false;initial-value:blue;}',
            "@property initialValue descriptor mutation must be a difference")

    def test_property_syntax_mutation_is_a_difference(self):
        self.assert_differs(
            '@property --x{syntax:"<color>";inherits:false;initial-value:red;}',
            '@property --x{syntax:"<length>";inherits:false;initial-value:red;}',
            "@property syntax descriptor mutation must be a difference")

    def test_font_palette_values_base_palette_mutation_is_a_difference(self):
        self.assert_differs(
            '@font-palette-values --p{font-family:x;base-palette:1;}',
            '@font-palette-values --p{font-family:x;base-palette:2;}',
            "@font-palette-values basePalette mutation must be a difference")

    def test_import_layer_mutation_is_a_difference(self):
        self.assert_differs(
            '@import url(a.css) layer(foo);',
            '@import url(a.css) layer(bar);',
            "@import layerName mutation must be a difference")

    def test_import_media_mutation_is_a_difference(self):
        self.assert_differs(
            '@import url(a.css) screen;',
            '@import url(a.css) print;',
            "@import media mutation must be a difference")

    def test_charset_encoding_mutation_is_a_difference(self):
        self.assert_differs(
            '@charset "utf-8";a{color:red}',
            '@charset "utf-16";a{color:red}',
            "@charset prologue mutation must be a difference")

    def test_descriptor_comma_trivia_remains_equivalent(self):
        # Non-semantic serialization trivia must stay pass: a comma in a
        # registered-property initial value does not need surrounding spaces.
        self.assert_equivalent(
            '@property --x{syntax:"<color>#";inherits:false;initial-value:red, blue;}',
            '@property --x{syntax:"<color>#";inherits:false;initial-value:red,blue;}',
            "trivial comma whitespace must remain equivalent")

    def test_counter_style_identical_symbols_remain_equivalent(self):
        self.assert_equivalent(
            '@counter-style a{symbols:\\2020  \\2021;suffix:"";}',
            '@counter-style a{symbols:\\2020  \\2021;suffix:"";}',
            "identical escape-separated symbol lists must remain equivalent")

    def test_selector_whitespace_remains_sensitive(self):
        self.assert_differs(
            '.a .b{color:red}',
            '.a.b{color:red}',
            "selector descendant/compound whitespace sensitivity must be preserved")


if __name__ == "__main__":
    unittest.main()