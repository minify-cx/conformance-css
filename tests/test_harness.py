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


    def test_lightningcss_adapter_uses_output_dir_and_maps_outputs(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cli = root / "lightningcss"
            cli.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "if '--version' in sys.argv:\n"
                "    print('lightningcss 9.9.9-test')\n"
                "    raise SystemExit(0)\n"
                "args = sys.argv[1:]\n"
                "idx = args.index('--output-dir')\n"
                "out = pathlib.Path(args[idx + 1])\n"
                "inputs = [pathlib.Path(x) for x in args[idx + 2:]]\n"
                "out.mkdir(parents=True, exist_ok=True)\n"
                "for src in inputs:\n"
                "    text = src.read_text()\n"
                "    (out / src.name).write_text(text.replace(' ', ''))\n"
            )
            cli.chmod(0o755)
            cases = [
                {"id": "a", "css": "a { color: red; }"},
                {"id": "b", "css": "b { margin: 0; }"},
            ]
            outputs, errors = mod.minify_cases(cases, "lightningcss", cli)
            self.assertEqual(errors, {})
            self.assertEqual(outputs["a"], "a{color:red;}")
            self.assertEqual(outputs["b"], "b{margin:0;}")


    def test_lightningcss_adapter_isolates_a_failed_case(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cli = root / "lightningcss"
            cli.write_text(
                "#!/usr/bin/env python3\n"
                "import pathlib, sys\n"
                "args = sys.argv[1:]\n"
                "idx = args.index('--output-dir')\n"
                "out = pathlib.Path(args[idx + 1])\n"
                "inputs = [pathlib.Path(x) for x in args[idx + 2:]]\n"
                "if any('BAD' in p.read_text() for p in inputs):\n"
                "    print('parse error in ' + next(p.name for p in inputs if 'BAD' in p.read_text()), file=sys.stderr)\n"
                "    raise SystemExit(1)\n"
                "out.mkdir(parents=True, exist_ok=True)\n"
                "for src in inputs:\n"
                "    (out / src.name).write_text(src.read_text().replace(' ', ''))\n"
            )
            cli.chmod(0o755)
            cases = [
                {"id": "good-a", "css": "a { color: red; }"},
                {"id": "bad", "css": "BAD"},
                {"id": "good-b", "css": "b { margin: 0; }"},
            ]
            outputs, errors = mod.minify_cases(cases, "lightningcss", cli)
            self.assertEqual(set(outputs), {"good-a", "good-b"})
            self.assertEqual(set(errors), {"bad"})
            self.assertIn("case-000001.css", errors["bad"])

    def test_resolve_executable_finds_path_command(self):
        import os
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            exe = root / "fake-minifier"
            exe.write_text("#!/bin/sh\nexit 0\n")
            exe.chmod(0o755)
            old = os.environ.get("PATH", "")
            os.environ["PATH"] = str(root) + os.pathsep + old
            try:
                self.assertEqual(mod.resolve_executable("fake-minifier"), exe)
            finally:
                os.environ["PATH"] = old

    def test_classification_is_conservative(self):
        case = {'id':'x'}
        self.assertEqual(mod.classify(case, '', 'boom', None)[0], 'minify-error')
        self.assertEqual(mod.classify(case, 'x', None, {'before':{'ok':False,'error':'bad'},'after':{'ok':False}})[0], 'source-rejected')
        self.assertEqual(mod.classify(case, 'x', None, {'before':{'ok':True,'rules':['a{}']},'after':{'ok':False,'error':'bad'}})[0], 'browser-rejected')
        self.assertEqual(mod.classify(case, 'x', None, {'before':{'ok':True,'rules':['a{}'],'semantic':['a']},'after':{'ok':True,'rules':['b{}'],'semantic':['b']}})[0], 'cssom-difference')
        self.assertEqual(mod.classify(case, 'x', None, {'before':{'ok':True,'rules':['a { --x: 1; }'],'semantic':['same']},'after':{'ok':True,'rules':['a{--x:1}'],'semantic':['same']}})[0], 'pass')



class IdentityValidationTests(unittest.TestCase):
    """Explicit identity validation for CSS release-candidate evidence.

    A Minify++ result must carry the normalized schema (name Minify++,
    semantic version, non-empty version_string, well-formed 40-hex commit) and
    a browser oracle with non-empty name and version; Lightning CSS is its own
    separate identity. verify_dashboard must reject matching-but-empty
    identities.
    """

    def _valid_minify_payload(self):
        return {
            "minifier": {"name": "Minify++", "version": "1.1.2",
                         "version_string": "Minify++ 1.1.2", "commit": "a" * 40},
            "oracle": {"name": "chromium", "version": "Chromium 152.0.7977.0"},
        }

    def test_valid_normalized_minify_identity(self):
        mod.validate_identity(self._valid_minify_payload())
        mod.validate_identity(self._valid_minify_payload(), expected_commit="a" * 40)

    def test_missing_minifier_rejected(self):
        with self.assertRaises(SystemExit):
            mod.validate_identity({"oracle": {"name": "chromium", "version": "1"}})

    def test_empty_minifier_rejected(self):
        bad = self._valid_minify_payload(); bad["minifier"] = {}
        with self.assertRaises(SystemExit):
            mod.validate_identity(bad)

    def test_missing_or_malformed_semantic_version_rejected(self):
        for v in ("", "1.1", "v1.1.2", "1.1.2-rc1", "one"):
            bad = self._valid_minify_payload(); bad["minifier"]["version"] = v
            with self.assertRaises(SystemExit):
                mod.validate_identity(bad)

    def test_missing_or_empty_version_string_rejected(self):
        for v in (None, ""):
            bad = self._valid_minify_payload(); bad["minifier"]["version_string"] = v
            with self.assertRaises(SystemExit):
                mod.validate_identity(bad)

    def test_missing_short_or_nonhex_commit_rejected(self):
        for c in (None, "", "a" * 39, "z" * 40, "4866db7" * 5, "GGGG" + "a" * 36):
            bad = self._valid_minify_payload(); bad["minifier"]["commit"] = c
            with self.assertRaises(SystemExit):
                mod.validate_identity(bad)

    def test_expected_commit_mismatch_rejected(self):
        bad = self._valid_minify_payload()
        with self.assertRaises(SystemExit):
            mod.validate_identity(bad, expected_commit="f" * 40)

    def test_missing_or_empty_oracle_rejected(self):
        for o in (None, {}, {"name": "chromium"}, {"version": "1"}):
            bad = self._valid_minify_payload(); bad["oracle"] = o
            with self.assertRaises(SystemExit):
                mod.validate_identity(bad)

    def test_missing_oracle_name_rejected(self):
        bad = self._valid_minify_payload(); bad["oracle"] = {"version": "1"}
        with self.assertRaises(SystemExit):
            mod.validate_identity(bad)

    def test_missing_oracle_version_rejected(self):
        bad = self._valid_minify_payload(); bad["oracle"] = {"name": "chromium"}
        with self.assertRaises(SystemExit):
            mod.validate_identity(bad)

    def test_matching_empty_identities_rejected_by_verify_dashboard(self):
        empty = {"summary": {"total": 1, "counts": {"pass": 1}}, "sources": {},
                 "minifier": {}, "oracle": {}, "generated_at": "x"}
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            (td / "res.json").write_text(json.dumps(empty))
            (td / "pub.json").write_text(json.dumps(empty))
            (td / "index.html").write_text("<h1>ok</h1>")
            with self.assertRaises(SystemExit):
                mod.verify_dashboard(td / "res.json", td / "index.html", td / "pub.json")

    def test_lightningcss_is_valid_separate_identity(self):
        payload = {"minifier": {"name": "lightningcss", "version": "3.4.0"},
                   "oracle": {"name": "chromium", "version": "Chromium 152"}}
        mod.validate_identity(payload)

    def test_minifier_identity_minifypp_normalized_schema(self):
        ident = mod._minifier_identity("minifypp", "Minify++ 1.1.2", "a" * 40, Path("/tmp/x/minify"))
        self.assertEqual(ident["name"], "Minify++")
        self.assertEqual(ident["version"], "1.1.2")
        self.assertEqual(ident["version_string"], "Minify++ 1.1.2")
        self.assertEqual(ident["commit"], "a" * 40)

    def test_minifier_identity_lightningcss_separate(self):
        ident = mod._minifier_identity("lightningcss", "3.4.0", None, Path("/tmp/x/lc"))
        self.assertEqual(ident["name"], "lightningcss")
        self.assertEqual(ident["version"], "3.4.0")

if __name__ == '__main__':
    unittest.main()
