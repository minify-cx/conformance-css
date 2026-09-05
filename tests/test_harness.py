import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("conformance", ROOT / "tools" / "conformance.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class HarnessTests(unittest.TestCase):
    def test_js_unquote(self):
        self.assertEqual(mod.js_unquote('"a\\n\\u0062"'), 'a\nb')
        self.assertEqual(mod.js_unquote("'x\\'y'"), "x'y")
        self.assertIsNone(mod.js_unquote('`x${y}`'))

    def test_extracts_wpt_helpers_and_style_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / 'css').mkdir()
            (root / 'css' / 'sample.html').write_text('''
<script>
test_valid_value("color", "red");
test_computed_value('margin-left', '1px');
test_valid_selector('.x\\:y');
test_valid_rule('@media (width > 1px) { .x { color: red } }');
</script>
<style>.from-style { display: grid; }</style>
''')
            out = root / 'cases.jsonl'
            self.assertEqual(mod.extract_wpt_css(root, out), 0)
            cases = [json.loads(x) for x in out.read_text().splitlines()]
            kinds = {c['kind'] for c in cases}
            self.assertTrue({'declaration','selector','rule','style-block'} <= kinds)
            self.assertTrue(any('color:red' in c['css'] for c in cases))

    def test_utf16_css_is_decoded_before_extraction(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            le = root / "le.css"
            be = root / "be.css"
            le.write_bytes("body { color: red; }".encode("utf-16-le"))
            be.write_bytes("body { color: green; }".encode("utf-16-be"))
            self.assertEqual(mod.read_wpt_text(le), "body { color: red; }")
            self.assertEqual(mod.read_wpt_text(be), "body { color: green; }")

    def test_oracle_payload_is_not_embedded_as_raw_script_text(self):
        hostile = 'a{content:"</script><script>boom()</script>"}'
        page = mod.cssom_oracle_page([('x', hostile, hostile)])
        self.assertNotIn(hostile, page)
        self.assertEqual(page.count('</script>'), 1)
        self.assertIn("function lexicalValue", page)
        self.assertIn("value.selectorText = String(rule[key])", page.replace("value[key]", "value.selectorText"))

    def test_dashboard_text_escapes_nift_template_sigils(self):
        hostile = 'unknown(!@#%{...}more()@stuff []) $[metadata] <tag> & value'
        escaped = mod.dashboard_text(hostile)
        self.assertNotIn('@', escaped)
        self.assertNotIn('$', escaped)
        self.assertIn('&#64;#%', escaped)
        self.assertIn('&#64;stuff', escaped)
        self.assertIn('&#36;[metadata]', escaped)
        self.assertIn('&lt;tag&gt;', escaped)
        self.assertIn('&amp; value', escaped)

    def test_classification_is_conservative(self):
        case = {'id':'x'}
        self.assertEqual(mod.classify(case, '', 'boom', None)[0], 'minify-error')
        self.assertEqual(mod.classify(case, 'x', None, {'before':{'ok':False,'error':'bad'},'after':{'ok':False}})[0], 'source-rejected')
        self.assertEqual(mod.classify(case, 'x', None, {'before':{'ok':True,'rules':['a{}']},'after':{'ok':False,'error':'bad'}})[0], 'browser-rejected')
        self.assertEqual(mod.classify(case, 'x', None, {'before':{'ok':True,'rules':['a{}'],'semantic':['a']},'after':{'ok':True,'rules':['b{}'],'semantic':['b']}})[0], 'cssom-difference')
        self.assertEqual(mod.classify(case, 'x', None, {'before':{'ok':True,'rules':['a { --x: 1; }'],'semantic':['same']},'after':{'ok':True,'rules':['a{--x:1}'],'semantic':['same']}})[0], 'pass')

if __name__ == '__main__':
    unittest.main()
