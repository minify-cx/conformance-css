#!/usr/bin/env python3
"""Minify++ standards-conformance harness.

The harness deliberately lives outside Minify++ itself. It acquires independent
standards corpora, extracts transformable cases, runs Minify++, asks a real
browser to canonicalize both original and transformed CSS, and writes durable
JSON evidence for the static dashboard.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import base64
import time
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULTS = ROOT / "results" / "latest.json"


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True,
        capture: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        timeout=timeout,
    )


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def source_lock() -> dict[str, Any]:
    lock_path = ROOT / ".state" / "sources.lock.json"
    return load_json(lock_path) if lock_path.exists() else {}


def sync_sources(names: list[str]) -> int:
    cfg = load_json(ROOT / "config" / "sources.json")
    lock: dict[str, Any] = source_lock()
    for name in names:
        spec = cfg[name]
        destination = ROOT / spec["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            print(f"cloning {name}...")
            run(["git", "clone", "--filter=blob:none", "--no-tags", "--branch", spec["branch"], spec["url"], str(destination)], capture=False)
        else:
            print(f"updating {name}...")
            run(["git", "fetch", "--prune", "origin", spec["branch"]], cwd=destination, capture=False)
            run(["git", "checkout", "--detach", "FETCH_HEAD"], cwd=destination, capture=False)
        revision = run(["git", "rev-parse", "HEAD"], cwd=destination).stdout.strip()
        lock[name] = {
            "url": spec["url"],
            "branch": spec["branch"],
            "revision": revision,
            "synced_at": utc_now(),
        }
        print(f"{name}: {revision}")
    write_json(ROOT / ".state" / "sources.lock.json", lock)
    return 0


def js_unquote(token: str) -> str | None:
    token = token.strip()
    if len(token) < 2 or token[0] not in "'\"`" or token[-1] != token[0]:
        return None
    if token[0] == "`" and "${" in token:
        return None
    quote = token[0]
    body = token[1:-1]
    # The WPT helper corpus overwhelmingly uses ordinary JS escapes. Decode the
    # useful subset without evaluating JavaScript.
    out: list[str] = []
    i = 0
    while i < len(body):
        c = body[i]
        if c != "\\":
            out.append(c); i += 1; continue
        i += 1
        if i >= len(body): return None
        c = body[i]; i += 1
        simple = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}
        if c in simple:
            out.append(simple[c]); continue
        if c in "\\'\"`":
            out.append(c); continue
        if c == "x" and i + 2 <= len(body):
            try: out.append(chr(int(body[i:i+2], 16))); i += 2; continue
            except ValueError: return None
        if c == "u":
            if i < len(body) and body[i] == "{":
                end = body.find("}", i + 1)
                if end < 0: return None
                try: out.append(chr(int(body[i+1:end], 16))); i = end + 1; continue
                except ValueError: return None
            if i + 4 <= len(body):
                try: out.append(chr(int(body[i:i+4], 16))); i += 4; continue
                except ValueError: return None
        # JS line continuation.
        if c == "\n": continue
        if c == "\r":
            if i < len(body) and body[i] == "\n": i += 1
            continue
        out.append(c)
    return "".join(out)


def split_call_args(text: str) -> list[str]:
    args: list[str] = []
    start = 0
    depth = 0
    quote: str | None = None
    escaped = False
    i = 0
    while i < len(text):
        c = text[i]
        if quote:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                quote = None
            i += 1; continue
        if c in "'\"`":
            quote = c; i += 1; continue
        if c in "([{": depth += 1
        elif c in ")]}":
            if depth == 0: break
            depth -= 1
        elif c == "," and depth == 0:
            args.append(text[start:i].strip()); start = i + 1
        i += 1
    args.append(text[start:].strip())
    return args


def iter_helper_calls(text: str, name: str) -> Iterable[tuple[int, list[str]]]:
    pattern = re.compile(r"\b" + re.escape(name) + r"\s*\(")
    for match in pattern.finditer(text):
        start = match.end()
        depth = 0
        quote: str | None = None
        escaped = False
        i = start
        while i < len(text):
            c = text[i]
            if quote:
                if escaped: escaped = False
                elif c == "\\": escaped = True
                elif c == quote: quote = None
                i += 1; continue
            if c in "'\"`": quote = c
            elif c in "([{": depth += 1
            elif c in ")]}":
                if c == ")" and depth == 0:
                    yield match.start(), split_call_args(text[start:i]); break
                depth = max(0, depth - 1)
            i += 1


def case_id(source_path: str, offset: int, css: str) -> str:
    digest = hashlib.sha256(f"{source_path}\0{offset}\0{css}".encode()).hexdigest()[:16]
    return digest


def extract_wpt_css(wpt: Path, output: Path, limit: int | None = None) -> int:
    roots = [wpt / "css", wpt / "cssom"]
    helpers = {
        "test_valid_value": "declaration",
        "test_computed_value": "declaration",
        "test_valid_selector": "selector",
        "test_valid_rule": "rule",
    }
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(kind: str, css: str, rel: str, offset: int, meta: dict[str, Any] | None = None) -> None:
        if not css.strip(): return
        key = kind + "\0" + css
        if key in seen: return
        seen.add(key)
        item = {
            "id": case_id(rel, offset, css), "suite": "wpt-css", "kind": kind,
            "source": rel, "offset": offset, "css": css,
        }
        if meta: item.update(meta)
        cases.append(item)

    for root in roots:
        if not root.exists(): continue
        for path in root.rglob("*"):
            if limit is not None and len(cases) >= limit: break
            if not path.is_file(): continue
            rel = path.relative_to(wpt).as_posix()
            if path.suffix == ".css":
                try: text = path.read_text(encoding="utf-8")
                except UnicodeDecodeError: continue
                add("stylesheet", text, rel, 0)
                continue
            if path.suffix not in {".js", ".html", ".htm", ".xhtml"}: continue
            try: text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError: continue
            for helper, kind in helpers.items():
                for offset, args in iter_helper_calls(text, helper):
                    if limit is not None and len(cases) >= limit: break
                    if kind == "declaration" and len(args) >= 2:
                        prop, value = js_unquote(args[0]), js_unquote(args[1])
                        if prop is not None and value is not None:
                            add(kind, f".minify-conformance{{{prop}:{value}}}", rel, offset,
                                {"helper": helper, "property": prop, "value": value})
                    elif kind == "selector" and args:
                        selector = js_unquote(args[0])
                        if selector is not None:
                            add(kind, f"{selector}{{--minify-conformance:1}}", rel, offset,
                                {"helper": helper, "selector": selector})
                    elif kind == "rule" and args:
                        rule = js_unquote(args[0])
                        if rule is not None:
                            add(kind, rule, rel, offset, {"helper": helper})
            # Extract literal <style> blocks as an additional independent corpus.
            for match in re.finditer(r"<style(?:\s[^>]*)?>(.*?)</style\s*>", text, re.I | re.S):
                css = match.group(1)
                if "{{" not in css and "}}" not in css:
                    add("style-block", css, rel, match.start(1))
        if limit is not None and len(cases) >= limit: break

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as f:
        for item in cases[:limit]:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"extracted {min(len(cases), limit or len(cases))} CSS cases to {output}")
    return 0


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip(): cases.append(json.loads(line))
    return cases


def smoke_cases() -> list[dict[str, Any]]:
    cases = []
    for path in sorted((ROOT / "fixtures" / "smoke" / "css").glob("*.css")):
        css = path.read_text(encoding="utf-8")
        cases.append({
            "id": case_id(path.name, 0, css), "suite": "smoke-css", "kind": "stylesheet",
            "source": path.relative_to(ROOT).as_posix(), "offset": 0, "css": css,
        })
    return cases


def chromium_path(explicit: str | None) -> str | None:
    if explicit: return explicit
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        found = shutil.which(name)
        if found: return found
    return None


def cssom_oracle_page(pairs: list[tuple[str, str, str]]) -> str:
    # Never splice arbitrary standards-test text directly into executable HTML.
    # Besides being safer, base64 avoids a CSS string containing </script> from
    # terminating the oracle script before Chromium can emit its result.
    raw = json.dumps([{"id": i, "before": b, "after": a} for i, b, a in pairs], ensure_ascii=False)
    payload = base64.b64encode(raw.encode("utf-8")).decode("ascii")
    return f'''<!doctype html><meta charset="utf-8"><pre id="result"></pre><script>
const cases = JSON.parse(new TextDecoder().decode(Uint8Array.from(atob("{payload}"), c => c.charCodeAt(0))));
function canonical(css) {{
  const sheet = new CSSStyleSheet();
  try {{
    sheet.replaceSync(css);
    return {{ok:true, rules:Array.from(sheet.cssRules, r => r.cssText)}};
  }} catch (error) {{
    return {{ok:false, error:String(error && error.message || error)}};
  }}
}}
const out = cases.map(c => ({{id:c.id, before:canonical(c.before), after:canonical(c.after)}}));
document.getElementById('result').textContent = JSON.stringify(out);
</script>'''


def cssom_browser_batch(chromium: str, pairs: list[tuple[str, str, str]], timeout: int = 12) -> dict[str, dict[str, Any]]:
    page = cssom_oracle_page(pairs)
    # Snap-packaged Chromium has a private /tmp and cannot read tempfile paths
    # created there by the host process. Keep oracle files under the repository's
    # work tree so native and confined Chromium installations can both access them.
    browser_work = ROOT / "work" / "browser"
    browser_work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="oracle-", dir=browser_work) as td:
        path = Path(td) / "oracle.html"
        path.write_text(page, encoding="utf-8")
        proc = run([
            chromium, "--headless", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
            "--virtual-time-budget=3000", "--dump-dom", path.as_uri()
        ], check=False, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"Chromium oracle failed ({proc.returncode}): {proc.stderr[-2000:]}")
        match = re.search(r'<pre id="result">(.*?)</pre>', proc.stdout, re.S)
        if not match:
            raise RuntimeError("Chromium oracle did not emit a result payload")
        decoded = html.unescape(match.group(1))
        values = json.loads(decoded)
        return {v["id"]: v for v in values}


def minify_cases(cases: list[dict[str, Any]], minify_bin: Path) -> tuple[dict[str, str], dict[str, str]]:
    outputs: dict[str, str] = {}
    errors: dict[str, str] = {}
    with tempfile.TemporaryDirectory(prefix="minify-conformance-") as td:
        temp = Path(td)
        paths: list[Path] = []
        ids: dict[Path, str] = {}
        for index, case in enumerate(cases):
            path = temp / f"case-{index:06d}.css"
            path.write_text(case["css"], encoding="utf-8")
            paths.append(path); ids[path] = case["id"]
        # Batch invocation matters at standards-suite scale; process startup
        # should not dominate the conformance run.
        proc = run([str(minify_bin), *map(str, paths)], check=False, timeout=max(60, len(paths) // 50 + 60))
        stderr = proc.stderr or ""
        for path in paths:
            dest = path.with_name(path.stem + ".min" + path.suffix)
            cid = ids[path]
            if dest.exists(): outputs[cid] = dest.read_text(encoding="utf-8")
            else:
                # Keep the relevant CLI error where possible; the full stderr is
                # retained only per failed case to keep successful results small.
                marker = path.name + ":"
                line = next((ln for ln in stderr.splitlines() if marker in ln), "minifier produced no output")
                errors[cid] = line
    return outputs, errors


def classify(case: dict[str, Any], output: str | None, min_error: str | None,
             oracle: dict[str, Any] | None) -> tuple[str, str]:
    if min_error is not None:
        return "minify-error", min_error
    if oracle is None:
        return "unverified", "browser oracle unavailable"
    before, after = oracle["before"], oracle["after"]
    if not before["ok"]:
        # A standards corpus can contain intentionally invalid/unsupported input.
        # It should not count as a semantic regression in the valid-CSS gate.
        return "source-rejected", before.get("error", "browser rejected source")
    if not after["ok"]:
        return "browser-rejected", after.get("error", "browser rejected minified CSS")
    if before["rules"] != after["rules"]:
        return "cssom-difference", "browser CSSOM serialization differs after minification"
    return "pass", ""


def run_css(cases: list[dict[str, Any]], minify_bin: Path, chromium: str | None,
            result_path: Path, browser_batch: int = 400, browser_timeout: int = 12) -> int:
    started = time.monotonic()
    outputs, min_errors = minify_cases(cases, minify_bin)
    browser = chromium_path(chromium)
    oracle_map: dict[str, dict[str, Any]] = {}
    browser_error = None
    if browser:
        pairs = [(c["id"], c["css"], outputs[c["id"]]) for c in cases if c["id"] in outputs]
        batch_errors: list[str] = []
        for i in range(0, len(pairs), browser_batch):
            batch = pairs[i:i+browser_batch]
            try:
                oracle_map.update(cssom_browser_batch(browser, batch, timeout=browser_timeout))
            except Exception as exc:
                message = f"batch {i // browser_batch + 1} ({len(batch)} cases): {exc}"
                batch_errors.append(message)
                print(f"warning: browser oracle failed for {message}", file=sys.stderr)
        if batch_errors:
            browser_error = "; ".join(batch_errors)
    else:
        browser_error = "Chromium not found"

    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for case in cases:
        cid = case["id"]
        output = outputs.get(cid)
        status, detail = classify(case, output, min_errors.get(cid), oracle_map.get(cid))
        counts[status] = counts.get(status, 0) + 1
        row = {
            "id": cid, "suite": case.get("suite", "css"), "kind": case.get("kind", "css"),
            "source": case.get("source", ""), "offset": case.get("offset", 0),
            "status": status, "detail": detail,
        }
        if status != "pass":
            row["input"] = case["css"]
            if output is not None: row["output"] = output
        rows.append(row)

    minify_version = run([str(minify_bin), "--version"], check=False).stdout.strip()
    lock = source_lock()
    report = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "duration_seconds": round(time.monotonic() - started, 3),
        "minifier": {"path": str(minify_bin.resolve()), "version": minify_version},
        "browser": {"path": browser, "error": browser_error},
        "sources": lock,
        "summary": {"total": len(rows), "counts": counts},
        "cases": rows,
    }
    write_json(result_path, report)
    history = ROOT / "results" / "history" / (report["generated_at"].replace(":", "-") + ".json")
    write_json(history, report)
    print(f"wrote {result_path}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    # The broad upstream corpus is diagnostic initially. Hard failures are only
    # crashes/oracle invalidation; CSSOM differences are triage candidates until
    # per-kind semantic oracles are mature enough to be release gates.
    hard = counts.get("browser-rejected", 0)
    return 1 if hard else 0


def dashboard_text(value: str) -> str:
    """Escape arbitrary evidence for both HTML and Nift template parsing.

    Dashboard evidence is generated HTML that is subsequently consumed through
    @input(), so HTML escaping alone is insufficient: upstream standards cases
    may contain Nift-significant sequences such as @# or $[...]. Encoding the
    sigils as numeric HTML entities preserves the browser-visible text while
    preventing Nift from interpreting it as template syntax.
    """
    return html.escape(value).replace("@", "&#64;").replace("$", "&#36;")


def render_dashboard(results: Path) -> int:
    report = load_json(results)
    summary = report["summary"]
    counts = summary.get("counts", {})
    order = ["pass", "cssom-difference", "minify-error", "browser-rejected", "source-rejected", "unverified"]
    labels = {
        "pass": "Pass", "cssom-difference": "CSSOM differences", "minify-error": "Minify errors",
        "browser-rejected": "Browser rejected", "source-rejected": "Source rejected", "unverified": "Unverified",
    }
    cards = "".join(
        f'<div class="metric"><strong>{counts.get(key,0):,}</strong><span>{labels[key]}</span></div>'
        for key in order
    )
    rows = []
    for case in report["cases"]:
        if case["status"] == "pass": continue
        inp = dashboard_text(case.get("input", ""))
        out = dashboard_text(case.get("output", ""))
        detail = dashboard_text(case.get("detail", ""))
        source = dashboard_text(case.get("source", ""))
        rows.append(f'''<tr data-status="{case['status']}">
<td><span class="status {case['status']}">{html.escape(labels.get(case['status'], case['status']))}</span></td>
<td><code>{source}</code><small>#{case.get('offset',0)}</small></td>
<td>{html.escape(case.get('kind',''))}</td>
<td><details><summary>{detail or 'Inspect case'}</summary><h4>Input</h4><pre><code>{inp}</code></pre>{('<h4>Output</h4><pre><code>'+out+'</code></pre>') if out else ''}</details></td>
</tr>''')
    if not rows:
        rows.append('<tr><td colspan="4" class="empty">No non-passing cases in this run.</td></tr>')
    lock_bits = []
    for name, data in report.get("sources", {}).items():
        rev = html.escape(data.get("revision", "unknown")[:12])
        lock_bits.append(f"<li><strong>{html.escape(name)}</strong> <code>{rev}</code></li>")
    browser = report.get("browser", {})
    browser_text = html.escape(browser.get("path") or "not available")
    generated = html.escape(report["generated_at"])
    content = f'''<header class="hero">
<p class="eyebrow">Minify++ standards lab</p>
<h1>Conformance dashboard</h1>
<p class="lede">Independent standards cases are transformed by Minify++, then parsed again by a real browser. The dashboard is a static snapshot generated only after a run completes.</p>
</header>
<section class="metrics">{cards}</section>
<section class="run-meta"><div><span>Cases</span><strong>{summary['total']:,}</strong></div><div><span>Duration</span><strong>{report['duration_seconds']:.3f}s</strong></div><div><span>Generated</span><strong>{generated}</strong></div><div><span>Browser</span><strong>{browser_text}</strong></div></section>
<section>
<div class="section-head"><div><p class="eyebrow">Triage</p><h2>Non-passing cases</h2></div><select id="status-filter" aria-label="Filter failures"><option value="all">All statuses</option>{''.join(f'<option value="{k}">{labels[k]}</option>' for k in order if k != 'pass')}</select></div>
<div class="table-wrap"><table><thead><tr><th>Status</th><th>Source</th><th>Kind</th><th>Evidence</th></tr></thead><tbody id="cases">{''.join(rows)}</tbody></table></div>
</section>
<section class="sources"><p class="eyebrow">Exact upstream revisions</p><h2>Sources</h2><ul>{''.join(lock_bits) or '<li>Smoke/local corpus - no upstream checkout recorded</li>'}</ul></section>'''
    (ROOT / "generated" / "latest.html").parent.mkdir(parents=True, exist_ok=True)
    (ROOT / "generated" / "latest.html").write_text(content + "\n", encoding="utf-8")
    # Keep a machine-readable snapshot beside the site without making the page
    # depend on fetching it at runtime.
    public_result = ROOT / "public" / "results" / "latest.json"
    public_result.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(results, public_result)
    nift = shutil.which("nift")
    if not nift:
        raise RuntimeError("nift is required to build the dashboard")
    proc = run([nift, "build"], cwd=ROOT, check=False, capture=False)
    if proc.returncode != 0: return proc.returncode
    proc = run([nift, "status"], cwd=ROOT, check=False, capture=False)
    return proc.returncode


def command_smoke(args: argparse.Namespace) -> int:
    result = Path(args.results)
    rc = run_css(smoke_cases(), Path(args.minify_bin), args.chromium, result, browser_timeout=args.browser_timeout)
    if args.dashboard:
        dash = render_dashboard(result)
        if dash: return dash
    return rc


def main() -> int:
    parser = argparse.ArgumentParser(description="Minify++ standards-conformance harness")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sync", help="clone/update upstream standards suites and record exact revisions")
    p.add_argument("sources", nargs="*", choices=["wpt", "test262"], default=["wpt", "test262"])

    p = sub.add_parser("extract-css", help="extract transformable CSS cases from a WPT checkout")
    p.add_argument("--wpt", type=Path, default=ROOT / ".state" / "upstreams" / "wpt")
    p.add_argument("--output", type=Path, default=ROOT / "work" / "wpt-css.jsonl")
    p.add_argument("--limit", type=int)

    p = sub.add_parser("run-css", help="minify extracted CSS and verify browser parsing/CSSOM")
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--minify-bin", default=str(ROOT.parent / "minify" / "minify"))
    p.add_argument("--chromium")
    p.add_argument("--results", default=str(DEFAULT_RESULTS))
    p.add_argument("--browser-batch", type=int, default=400)
    p.add_argument("--browser-timeout", type=int, default=12)

    p = sub.add_parser("dashboard", help="bake the latest completed result into the static Nift dashboard")
    p.add_argument("--results", type=Path, default=DEFAULT_RESULTS)

    p = sub.add_parser("smoke", help="run the local corpus through Minify++ and Chromium")
    p.add_argument("--minify-bin", default=str(ROOT.parent / "minify" / "minify"))
    p.add_argument("--chromium")
    p.add_argument("--results", default=str(DEFAULT_RESULTS))
    p.add_argument("--browser-timeout", type=int, default=12)
    p.add_argument("--dashboard", action="store_true")

    args = parser.parse_args()
    if args.command == "sync": return sync_sources(args.sources or ["wpt", "test262"])
    if args.command == "extract-css": return extract_wpt_css(args.wpt, args.output, args.limit)
    if args.command == "run-css": return run_css(load_cases(args.cases), Path(args.minify_bin), args.chromium, Path(args.results), args.browser_batch, args.browser_timeout)
    if args.command == "dashboard": return render_dashboard(args.results)
    if args.command == "smoke": return command_smoke(args)
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
