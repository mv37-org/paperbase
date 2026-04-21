SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

VENV := .venv
VENV_BIN := $(VENV)/bin
PYTHON := $(VENV_BIN)/python
PIP := $(PYTHON) -m pip
PYTEST := $(VENV_BIN)/pytest
RUFF := $(VENV_BIN)/ruff
PAPERBASE_API := $(VENV_BIN)/paperbase-api
PAPERBASE_DEV := $(VENV_BIN)/paperbase-dev
PAPERBASE_WORKER := $(VENV_BIN)/paperbase-worker
RUN_DIR := .run
CHAT_DIR := chat
STATIC_DIR := paperbase/src/paperbase/static
BENCHMARK_OUT_DIR := benchmarks/out
BACKEND_JUNIT := $(BENCHMARK_OUT_DIR)/backend.junit.xml
PLUGIN_JUNIT := $(BENCHMARK_OUT_DIR)/plugin.junit.xml
CHAT_TEST_JSON := $(BENCHMARK_OUT_DIR)/chat.vitest.json
VP_INSTALL_CMD := curl -fsSL https://vite.plus | bash
BASELINE_FLAG :=

ifneq ($(UPDATE_BASELINE),)
BASELINE_FLAG := --update-baseline
endif

.PHONY: help setup doctor dev test profile clean _require_vp _require_setup _stop_stack

help:
	@printf "paper-ai local workflow\n\n"
	@printf "  make setup   Install Python packages, chat dependencies, the repo .env, and Git hooks.\n"
	@printf "  make doctor  Verify vp, Python, Postgres, pgvector, ports, and the repo .env.\n"
	@printf "  make dev     Initialize local storage/database and run API, worker, MCP bridge, and chat.\n"
	@printf "  make test    Run doctor, init-local, run-prereq verification, lint, chat checks/tests/build, pytest, and timing comparisons.\n"
	@printf "  make profile Run doctor, init-local, run-prereq verification, deterministic hotpath profiling, and baseline comparisons.\n"
	@printf "  make clean   Stop local services and remove generated logs, caches, and frontend output.\n"

setup:
	@$(MAKE) --no-print-directory _require_vp
	@if [ ! -d "$(VENV)" ]; then \
		python3 -m venv "$(VENV)"; \
	fi
	@$(PIP) install --upgrade pip
	@$(PIP) install -e "./paperbase[dev]" -e "./paperbase-plugin[dev]"
	@if [ ! -f .env ]; then \
		cp .env.example .env; \
	fi
	@(cd "$(CHAT_DIR)" && vp install)
	@mkdir -p "$(RUN_DIR)"
	@chmod +x .githooks/pre-push
	@git config --local core.hooksPath .githooks
	@printf "Setup complete. Review .env before running make dev.\n"

doctor:
	@$(MAKE) --no-print-directory _require_vp
	@$(MAKE) --no-print-directory _require_setup
	@"$(PAPERBASE_DEV)" doctor --mode dev

dev:
	@$(MAKE) --no-print-directory _require_vp
	@$(MAKE) --no-print-directory _require_setup
	@"$(PAPERBASE_DEV)" init-local
	@$(MAKE) --no-print-directory _stop_stack
	@rm -rf "$(RUN_DIR)"
	@mkdir -p "$(RUN_DIR)"
	@set -a; \
	source ./.env; \
	set +a; \
	PYTHONUNBUFFERED=1 "$(PAPERBASE_API)" >"$(RUN_DIR)/api.log" 2>&1 & \
	echo $$! >"$(RUN_DIR)/api.pid"; \
	PYTHONUNBUFFERED=1 "$(PAPERBASE_WORKER)" >"$(RUN_DIR)/worker.log" 2>&1 & \
	echo $$! >"$(RUN_DIR)/worker.pid"; \
	PYTHONUNBUFFERED=1 \
	PAPERBASE_PLUGIN_TRANSPORT="$${PAPERBASE_PLUGIN_TRANSPORT:-streamable-http}" \
	PAPERBASE_PLUGIN_MOUNT_PATH="$${PAPERBASE_PLUGIN_MOUNT_PATH:-/mcp}" \
	FASTMCP_HOST="$${FASTMCP_HOST:-127.0.0.1}" \
	FASTMCP_PORT="$${FASTMCP_PORT:-8090}" \
	"$(PYTHON)" paperbase-plugin/scripts/run_mcp.py >"$(RUN_DIR)/plugin.log" 2>&1 & \
	echo $$! >"$(RUN_DIR)/plugin.pid"; \
	cleanup() { \
		$(MAKE) -C "$(CURDIR)" --no-print-directory _stop_stack; \
	}; \
	trap cleanup EXIT INT TERM; \
	printf "API log:    %s/api.log\n" "$(RUN_DIR)"; \
	printf "Worker log: %s/worker.log\n" "$(RUN_DIR)"; \
	printf "Plugin log: %s/plugin.log\n" "$(RUN_DIR)"; \
	printf "Stopping the local stack when the chat dev server exits.\n"; \
	cd "$(CHAT_DIR)" && VITE_PAPERBASE_BASE_URL="$${VITE_PAPERBASE_BASE_URL:-http://127.0.0.1:8080}" vp dev

test:
	@$(MAKE) --no-print-directory _require_vp
	@$(MAKE) --no-print-directory _require_setup
	@mkdir -p "$(BENCHMARK_OUT_DIR)"
	@"$(PAPERBASE_DEV)" doctor --mode test
	@"$(PAPERBASE_DEV)" init-local
	@"$(PAPERBASE_DEV)" verify-run --mode test
	@"$(RUFF)" check paperbase/src paperbase/tests paperbase-plugin/src paperbase-plugin/tests paperbase-plugin/scripts
	@(cd "$(CHAT_DIR)" && set -a && source ../.env && set +a && vp check)
	@(cd "$(CHAT_DIR)" && set -a && source ../.env && set +a && ./node_modules/.bin/vitest run --reporter=json --outputFile="../$(CHAT_TEST_JSON)")
	@(cd "$(CHAT_DIR)" && set -a && source ../.env && set +a && vp build)
	@"$(PYTEST)" --junitxml="$(BACKEND_JUNIT)" paperbase/tests
	@"$(PYTEST)" --junitxml="$(PLUGIN_JUNIT)" paperbase-plugin/tests
	@"$(PAPERBASE_DEV)" record-test-times --backend-junit "$(BACKEND_JUNIT)" --plugin-junit "$(PLUGIN_JUNIT)" --chat-json "$(CHAT_TEST_JSON)" $(BASELINE_FLAG)

profile:
	@$(MAKE) --no-print-directory _require_vp
	@$(MAKE) --no-print-directory _require_setup
	@"$(PAPERBASE_DEV)" doctor --mode profile
	@"$(PAPERBASE_DEV)" init-local
	@"$(PAPERBASE_DEV)" verify-run --mode profile
	@"$(PAPERBASE_DEV)" profile $(BASELINE_FLAG)

clean:
	@$(MAKE) --no-print-directory _stop_stack
	@rm -rf "$(RUN_DIR)" "$(BENCHMARK_OUT_DIR)" .pytest_cache .ruff_cache "$(STATIC_DIR)" chat/.vite chat/dist paperbase/.venv paperbase-plugin/.venv
	@find . -name "__pycache__" -type d -prune -exec rm -rf {} +
	@find . -name ".DS_Store" -type f -delete
	@printf "Removed generated logs, caches, and frontend output.\n"

_require_vp:
	@if ! command -v vp >/dev/null 2>&1; then \
		printf "vp is required for this repo. Install it with:\n  %s\n" "$(VP_INSTALL_CMD)"; \
		exit 1; \
	fi

_require_setup:
	@test -x "$(PAPERBASE_DEV)" || { \
		printf "Run make setup first.\n"; \
		exit 1; \
	}
	@test -f .env || { \
		printf "Missing .env. Run make setup or copy .env.example to .env.\n"; \
		exit 1; \
	}

_stop_stack:
	@if [ ! -d "$(RUN_DIR)" ]; then \
		exit 0; \
	fi; \
	shopt -s nullglob; \
	for pid_file in "$(RUN_DIR)"/*.pid; do \
		pid="$$(cat "$$pid_file" 2>/dev/null || true)"; \
		if [ -n "$$pid" ] && kill -0 "$$pid" 2>/dev/null; then \
			kill "$$pid" 2>/dev/null || true; \
		fi; \
		rm -f "$$pid_file"; \
	done
