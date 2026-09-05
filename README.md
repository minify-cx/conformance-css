# Minify++ Conformance

Independent standards-conformance infrastructure for Minify++.

This repository applies the central idea behind Lightning CSS's Web Platform Tests harness to Minify++ without putting heavyweight standards suites into the Minify++ product repository. It acquires external corpora, extracts transformable cases, minifies them in batches, verifies the transformed CSS in a real Chromium CSS parser, records exact upstream revisions, retains machine-readable failure evidence, and bakes the latest completed run into a static Nift dashboard.

The standards suites remain upstream. They are not vendored here.

## Architecture

```text
WPT / Test262
      |
      v
extractors/adapters
      |
      v
independent cases ----> Minify++
      |                    |
      |                    v
      +-------------> transformed case
                           |
                           v
                    browser/runtime oracle
                           |
                           v
                      results JSON
                           |
                           v
                    Nift static dashboard
```

The dashboard is **not live while tests execute**. A completed result is a durable snapshot. `dashboard` then bakes that snapshot into generated HTML; JavaScript is used only for local filtering of already-rendered rows.

## Quick start

From a workspace where `minify-conformance/` is next to `minify/`:

```sh
python3 tools/conformance.py smoke --dashboard
```

This runs the built-in local corpus through the Minify++ CLI and Chromium, writes `results/latest.json`, stores a timestamped history result, then builds the static Nift site.

## Full CSS/WPT run

```sh
python3 tools/conformance.py sync wpt
python3 tools/conformance.py extract-css
python3 tools/conformance.py run-css --cases work/wpt-css.jsonl
python3 tools/conformance.py dashboard
```

`sync` records the exact checked-out upstream commit in `.state/sources.lock.json`; every result carries that revision. This gives reproducible evidence without committing a giant WPT checkout.

For iteration, cap extraction:

```sh
python3 tools/conformance.py extract-css --limit 1000
```

## What the first CSS adapter extracts

The initial WPT adapter deliberately favors cases that can be transformed independently:

- `.css` stylesheets under WPT's CSS/CSSOM trees;
- literal `<style>` blocks;
- literal `test_valid_value(...)` and `test_computed_value(...)` helper cases, wrapped as declarations;
- literal `test_valid_selector(...)` cases, wrapped as rules;
- literal `test_valid_rule(...)` cases.

It does not evaluate JavaScript or template expressions to manufacture cases. Unsupported dynamic helpers are skipped rather than guessed.

## Verification model

Minify++ is invoked once per batch, not once per test case. Chromium then parses the original and minified CSS with `CSSStyleSheet.replaceSync()` and produces canonical CSSOM serialization for both.

Statuses are intentionally diagnostic:

- `pass`: browser accepted both and their CSSOM serialization agrees;
- `cssom-difference`: both parse, but canonical rule serialization differs;
- `minify-error`: Minify++ did not produce output;
- `browser-rejected`: source parses but transformed output does not;
- `source-rejected`: browser rejects the extracted source, so it is not a valid transformation oracle;
- `unverified`: no browser oracle was available.

Only `browser-rejected` is a hard failure in this first iteration. A CSSOM difference is **not automatically a Minify++ bug**: two CSS programs can be semantically equivalent while serializing differently. Those cases are triage targets for stronger property/selector/rendering oracles in later iterations.

This avoids making a false conformance claim while the harness is still becoming more semantic.

## Planned adapters

The repository boundary is intentionally broader than WPT/CSS:

- CSS: WPT + browser semantic checks;
- HTML: WPT DOM/parsing cases;
- SVG: WPT DOM/rendering cases;
- JavaScript: Test262 plus runtime differential execution;
- JSON: Test262/independent structural corpora;
- JSX: parser-oriented independent corpora where licensing and semantics are suitable;
- XML: conservative parser/round-trip corpora.

Every real defect found upstream should also become a small permanent Minify++ regression fixture in the main repository.

## Source ownership

`stage` owns the harness and dashboard source. The nested `public/` checkout tracks `main`, and the generated website lives directly at that checkout's root so GitHub Pages can serve `main` as the published site.
