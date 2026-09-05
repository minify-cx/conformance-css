PYTHON ?= python3
MINIFY_BIN ?= ../minify/minify

.PHONY: test smoke sync-wpt extract-css css dashboard

test:
	$(PYTHON) -m unittest discover -s tests -v
	$(PYTHON) -m py_compile tools/conformance.py

smoke:
	$(PYTHON) tools/conformance.py smoke --minify-bin "$(MINIFY_BIN)" --dashboard

sync-wpt:
	$(PYTHON) tools/conformance.py sync wpt

extract-css:
	$(PYTHON) tools/conformance.py extract-css

css:
	$(PYTHON) tools/conformance.py run-css --cases work/wpt-css.jsonl --minify-bin "$(MINIFY_BIN)"

dashboard:
	$(PYTHON) tools/conformance.py dashboard
