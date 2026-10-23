# Python dependencies come from uv.lock (ADR-0109): `make lock` after editing pyproject.toml, `make upgrade` to move
# every pin to the newest allowed version (then `make check`).
.PHONY: install lock upgrade lint format typecheck test test-js e2e eval check run docker docs docs-bundle docs-serve diagrams vendor vendor-icons

install:
	uv sync --locked --extra dev --group docs
	cd tests/e2e && npm ci && npx playwright install chromium

lock:
	uv lock

upgrade:
	uv lock --upgrade

lint:
	.venv/bin/ruff check . && .venv/bin/ruff format --check .

format:
	.venv/bin/ruff check --fix . && .venv/bin/ruff format .

typecheck:
	.venv/bin/mypy

test:
	.venv/bin/pytest --cov=arcviz --cov-report=term-missing

test-js:
	node --test tests/js/*.test.mjs

e2e:
	cd tests/e2e && npm test

# Prompt/tool eval set against the real provider (needs an API key; never part of `check` or CI).
eval:
	.venv/bin/python -m tests.evals.run $(EVAL_ARGS)

check: lint typecheck test test-js e2e

# /docs needs the bundled site: built on the first run (ADR-0116).
run:
	@test -f arcviz/docs_site/index.html || $(MAKE) docs-bundle
	.venv/bin/arcviz

docker:
	docker build -t arcviz:local .

vendor:
	.venv/bin/python scripts/vendor_mermaid.py

vendor-icons:
	.venv/bin/python scripts/vendor_icons.py

docs:
	.venv/bin/zensical build --clean --strict

# The documentation site the app serves under /docs (arcviz/docs_site/, offline variant of `docs`).
docs-bundle:
	.venv/bin/python scripts/build_docs_bundle.py

# Render the architecture diagrams (docs/architecture/diagrams/*.arcviz.json) with Arcviz's own renderers (.mmd/.svg).
diagrams:
	.venv/bin/python scripts/render_architecture_diagrams.py

docs-serve:
	.venv/bin/zensical serve --dev-addr 127.0.0.1:8001
