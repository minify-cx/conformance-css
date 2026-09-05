# CSS WPT triage - 2026-09-06

This note records the audit of the first full structured-CSSOM Minify++ run so the
classification decisions remain reviewable after downloaded WPT/results are cleaned.

Baseline snapshot:

- 31,137 extracted WPT CSS cases
- 30,579 pass
- 558 `cssom-difference`
- 0 minify errors
- 0 browser/source rejection
- 0 unverified

The 558 differences were inspected using Chromium's retained before/after CSSOM.
PostCSS was used only to enumerate selectors, at-rule parameters and declarations
from that saved evidence; it is not used as the correctness oracle.

## Findings

The audit found 17 cases where Minify++ had changed browser-relevant structure:

- native CSS nesting descendant selectors on either side of `&` lost their
  descendant-combinator whitespace (`& .child`, `& h3`, `.ancestor &`);
- whitespace around an attribute-selector namespace separator could turn an
  invalid selector into a valid one;
- escaped whitespace in `::part()` identifiers was removed;
- invalid declaration values containing `{}` could be joined into a different,
  browser-accepted construct;
- a final bad-string newline was trimmed, changing browser error recovery for
  malformed `var()`/`url()` declarations.

All of those families now have permanent Minify++ smoke regressions.

Two additional selector differences came from UTF-16 CSS2 encoding fixtures that
had been extracted as NUL-interleaved JavaScript strings. That does not model
loading the encoded stylesheet, so it was a harness defect rather than a Minify++
defect. The extractor now detects BOM-marked and unambiguous BOM-less UTF-16LE/BE
CSS and decodes it before constructing a CSSOM string test.

The remaining 539 differences were lexical-trivia differences in browser-parsed
declaration values or conditional-rule preludes. The revised oracle does not simply
ignore whitespace. It canonicalizes those fields while preserving CSS token
boundaries, quoted/escaped content, comment-as-separator behavior and binary `+`/`-`
math whitespace. Selector text is still compared exactly after Chromium parses it.
Raw Chromium serialization remains attached to every non-pass.

As a cross-check on the saved snapshot:

- 338 cases had at-rule parameter serialization differences; all 338 reduce to the
  same conservative lexical signature;
- 205 cases had declaration serialization differences; 199 reduce to the same
  lexical signature and the other 6 overlap the structural Minify++ bugs above;
- selector-tree comparison exposed 16 cases: 14 Minify++ structural bugs and the
  2 UTF-16 extraction artifacts;
- 9 deliberately malformed cases could not be parsed by the PostCSS triage parser;
  Chromium evidence identified 3 Minify++ recovery/boundary bugs among them and
  6 lexical-only differences.

This note does **not** claim a 31,137/31,137 result. The corrected Minify++ and
revised Chromium oracle must be run over a freshly extracted corpus to establish
the next conformance result.
