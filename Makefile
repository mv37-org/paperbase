PYTHON := python3
VENV := .venv
VENV_PYTHON := $(VENV)/bin/python
PIP := $(VENV_PYTHON) -m pip
PYTEST := $(VENV_PYTHON) -m pytest

.PHONY: help venv install install-paperbase install-plugin test test-paperbase test-plugin run-api run-worker run-plugin-mcp clean

help:
	@echo "Available targets:"
	@echo "  make venv            Create .venv and bootstrap pip"
	@echo "  make install         Install both paperbase and paperbase-plugin with dev deps"
	@echo "  make install-paperbase Install only the paperbase backend"
	@echo "  make install-plugin  Install only the paperbase plugin"
	@echo "  make test            Run all tests"
	@echo "  make test-paperbase  Run backend tests only"
	@echo "  make test-plugin     Run plugin tests only"
	@echo "  make run-api         Run the paperbase FastAPI server"
	@echo "  make run-worker      Run the paperbase background worker"
	@echo "  make run-plugin-mcp  Run the plugin MCP server locally"
	@echo "  make clean           Remove local caches and test artifacts"

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m ensurepip --upgrade

venv: $(VENV_PYTHON)

install: venv
	$(PIP) install -e './paperbase[dev]' -e './paperbase-plugin[dev]'

install-paperbase: venv
	$(PIP) install -e './paperbase[dev]'

install-plugin: venv
	$(PIP) install -e './paperbase-plugin[dev]'

test: test-paperbase test-plugin

test-paperbase: venv
	$(PYTEST) paperbase/tests -q

test-plugin: venv
	$(PYTEST) paperbase-plugin/tests -q

run-api: venv
	cd paperbase && ../$(VENV_PYTHON) -m paperbase.api

run-worker: venv
	cd paperbase && ../$(VENV_PYTHON) -m paperbase.worker

run-plugin-mcp: venv
	cd paperbase-plugin && ../$(VENV_PYTHON) scripts/run_mcp.py

clean:
	rm -rf .pytest_cache
	rm -rf paperbase/.pytest_cache paperbase-plugin/.pytest_cache
	rm -rf paperbase/tests/__pycache__ paperbase-plugin/tests/__pycache__
	find paperbase paperbase-plugin -type d -name "__pycache__" -prune -exec rm -rf {} +
