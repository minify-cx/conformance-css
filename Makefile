PYTHON ?= python3
MINIFY_BIN ?= ../minify/minify
LIGHTNINGCSS_BIN ?= lightningcss

.PHONY: test smoke smoke-lightningcss sync-wpt extract-css css css-lightningcss dashboard

test:
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) -m py_compile tools/conformance.py

smoke:
	$(PYTHON) tools/conformance.py smoke --minifier minifypp --minify-bin "$(MINIFY_BIN)" --dashboard

smoke-lightningcss:
	$(PYTHON) tools/conformance.py smoke --minifier lightningcss --minify-bin "$(LIGHTNINGCSS_BIN)" --dashboard

sync-wpt:
	$(PYTHON) tools/conformance.py sync wpt

extract-css:
	$(PYTHON) tools/conformance.py extract-css

css:
	$(PYTHON) tools/conformance.py run-css --cases work/wpt-css.jsonl --minifier minifypp --minify-bin "$(MINIFY_BIN)"

css-lightningcss:
	$(PYTHON) tools/conformance.py run-css --cases work/wpt-css.jsonl --minifier lightningcss --minify-bin "$(LIGHTNINGCSS_BIN)"

dashboard:
	$(PYTHON) tools/conformance.py dashboard
