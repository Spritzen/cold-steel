# Day-to-day commands. `make check` must pass before merging.

PY      := python3
VERSION := $(shell PYTHONPATH=src $(PY) -c 'import cold_steel; print(cold_steel.__version__)')
export PYTHONPATH := src

.PHONY: run check lint format test bench docs package screenshots

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

screenshots:    ## Take the README's screenshots from your install (best run on the host)
	$(PY) tools/screenshots.py

package:        ## Build the Arch package from the last commit, into build/package/
	rm -rf build/package
	mkdir -p build/package
	git archive --prefix=cold-steel-$(VERSION)/ -o build/package/cold-steel-$(VERSION).tar.gz HEAD
	cp packaging/PKGBUILD build/package/
	cd build/package && makepkg --force --cleanbuild
	namcap build/package/PKGBUILD build/package/cold-steel-$(VERSION)-*.pkg.tar.zst
