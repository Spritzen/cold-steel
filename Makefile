# Day-to-day commands. `make check` runs everything CI would.

PY      := python3
export PYTHONPATH := src

.PHONY: run check lint format test bench docs

run:            ## Open the app
	$(PY) -m cold_steel

check: lint test docs  ## Lint, type-check, test and check doc links

lint:           ## Ruff (lint + format check) and mypy
	ruff check .
	ruff format --check .
	mypy

format:         ## Fix formatting and safe lint issues
	ruff format .
	ruff check --fix .

test:           ## Run the tests (skips timing benchmarks)
	$(PY) -m pytest --benchmark-skip

bench:          ## Run only the timing benchmarks
	$(PY) -m pytest --benchmark-only

docs:           ## Check that every link in the docs resolves
	$(PY) tools/check_links.py
