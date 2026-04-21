PYTHON := python3
VP ?= vp
VENV := .venv
VENV_PYTHON := $(VENV)/bin/python
VENV_BIN := $(VENV)/bin
PIP := $(VENV_PYTHON) -m pip
PYTEST := $(VENV_PYTHON) -m pytest
CHAT_FRONTEND_DIR := $(CURDIR)/chat
API_HOST ?= 127.0.0.1
API_PORT ?= 8080
PLUGIN_MCP_HOST ?= 127.0.0.1
PLUGIN_MCP_PORT ?= 8090
PAPERBASE_BASE_URL ?= http://$(API_HOST):$(API_PORT)
PLUGIN_MCP_URL := http://$(PLUGIN_MCP_HOST):$(PLUGIN_MCP_PORT)/mcp
LOCAL_DATABASE_URL ?= postgresql+psycopg:///paperbase
LOCAL_STORAGE_ROOT ?= $(CURDIR)/.paperbase-data
LOCAL_API_KEY ?= development-token
LOCAL_LOG_LEVEL ?= DEBUG
LOCAL_LOG_FORMAT ?= json
LOCAL_STREAM_LOGS ?= 1
LOCAL_AUTO_SYNC_LATEST ?= 0
LOCAL_INITIAL_SYNC_LATEST ?= 0
LOCAL_STACK_ENV = env PAPERBASE_DATABASE_URL="$(LOCAL_DATABASE_URL)" PAPERBASE_STORAGE_BACKEND="local" PAPERBASE_LOCAL_STORAGE_ROOT="$(LOCAL_STORAGE_ROOT)" PAPERBASE_API_KEY="$(LOCAL_API_KEY)" PAPERBASE_BASE_URL="$(PAPERBASE_BASE_URL)" PAPERBASE_LOG_LEVEL="$(LOCAL_LOG_LEVEL)" PAPERBASE_LOG_FORMAT="$(LOCAL_LOG_FORMAT)" PAPERBASE_WORKER_AUTO_SYNC_LATEST="$(LOCAL_AUTO_SYNC_LATEST)" PAPERBASE_WORKER_INITIAL_SYNC_LATEST="$(LOCAL_INITIAL_SYNC_LATEST)"
RUN_DIR := .run
API_PID := $(RUN_DIR)/paperbase-api.pid
WORKER_PID := $(RUN_DIR)/paperbase-worker.pid
PLUGIN_PID := $(RUN_DIR)/paperbase-plugin-mcp.pid
API_LOG := $(RUN_DIR)/paperbase-api.log
WORKER_LOG := $(RUN_DIR)/paperbase-worker.log
PLUGIN_LOG := $(RUN_DIR)/paperbase-plugin-mcp.log

.PHONY: help venv install install-paperbase install-plugin install-chat build-chat-frontend test test-paperbase test-plugin test-chat run-api run-worker run-plugin-mcp run-chat start-all start-local flush kill-all clean

help:
	@echo "Available targets:"
	@echo "  make venv            Create .venv and bootstrap pip"
	@echo "  make install         Install paperbase, paperbase-plugin, and chat frontend deps"
	@echo "  make install-paperbase Install only the paperbase backend"
	@echo "  make install-plugin  Install only the paperbase plugin"
	@echo "  make install-chat    Install chat frontend dependencies"
	@echo "  make build-chat-frontend Install frontend deps and build assets into paperbase/static"
	@echo "  make test            Run all tests"
	@echo "  make test-paperbase  Run backend tests only"
	@echo "  make test-plugin     Run plugin tests only"
	@echo "  make test-chat       Run chat frontend checks only"
	@echo "  make run-api         Run the paperbase FastAPI server"
	@echo "  make run-worker      Run the paperbase background worker"
	@echo "  make run-plugin-mcp  Run the plugin MCP server locally"
	@echo "  make run-chat        Run the chat VitePlus dev server"
	@echo "  make start-all       Start API, worker, and plugin MCP HTTP bridge in the background"
	@echo "  make start-local     Start local Postgres/filesystem stack with JSON logs attached"
	@echo "                       Options: LOCAL_STREAM_LOGS=0 LOCAL_AUTO_SYNC_LATEST=1 LOCAL_INITIAL_SYNC_LATEST=1"
	@echo "  make flush           Reset the local Postgres schema and clear local artifact storage"
	@echo "                       Options: LOCAL_DATABASE_URL=... LOCAL_STORAGE_ROOT=..."
	@echo "  make kill-all        Stop all paperbase API, worker, and plugin MCP processes"
	@echo "  make clean           Remove local caches and test artifacts"

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV)
	$(VENV_PYTHON) -m ensurepip --upgrade

venv: $(VENV_PYTHON)

install: install-paperbase install-plugin install-chat

install-paperbase: venv
	$(PIP) install -e './paperbase[dev]'

install-plugin: venv
	$(PIP) install -e './paperbase-plugin[dev]'

install-chat:
	cd $(CHAT_FRONTEND_DIR) && $(VP) install

build-chat-frontend: install-chat
	cd $(CHAT_FRONTEND_DIR) && $(VP) build

test: test-paperbase test-plugin test-chat

test-paperbase: venv build-chat-frontend
	$(PYTEST) paperbase/tests -q

test-plugin: venv
	$(PYTEST) paperbase-plugin/tests -q

test-chat: build-chat-frontend
	cd $(CHAT_FRONTEND_DIR) && $(VP) run typecheck

run-api: install-paperbase build-chat-frontend
	$(VENV_BIN)/paperbase-api

run-worker: install-paperbase
	$(VENV_BIN)/paperbase-worker

run-plugin-mcp: install-plugin
	$(VENV_BIN)/paperbase-plugin-mcp

run-chat: install-chat
	cd $(CHAT_FRONTEND_DIR) && $(VP) dev

start-all: install build-chat-frontend
	@mkdir -p $(RUN_DIR)
	@$(MAKE) kill-all >/dev/null
	@nohup env PAPERBASE_API_HOST=$(API_HOST) PAPERBASE_API_PORT=$(API_PORT) $(VENV_BIN)/paperbase-api > $(API_LOG) 2>&1 & echo $$! > $(API_PID)
	@nohup $(VENV_BIN)/paperbase-worker > $(WORKER_LOG) 2>&1 & echo $$! > $(WORKER_PID)
	@nohup env PAPERBASE_BASE_URL=$(PAPERBASE_BASE_URL) PAPERBASE_PLUGIN_TRANSPORT=streamable-http FASTMCP_HOST=$(PLUGIN_MCP_HOST) FASTMCP_PORT=$(PLUGIN_MCP_PORT) $(VENV_BIN)/paperbase-plugin-mcp > $(PLUGIN_LOG) 2>&1 & echo $$! > $(PLUGIN_PID)
	@sleep 2
	@if ! kill -0 "$$(cat $(API_PID))" 2>/dev/null; then \
		echo "paperbase API failed to stay up"; \
		sed -n '1,120p' $(API_LOG); \
		$(MAKE) kill-all >/dev/null; \
		exit 1; \
	fi
	@if ! kill -0 "$$(cat $(WORKER_PID))" 2>/dev/null; then \
		echo "paperbase worker failed to stay up"; \
		sed -n '1,120p' $(WORKER_LOG); \
		$(MAKE) kill-all >/dev/null; \
		exit 1; \
	fi
	@if ! kill -0 "$$(cat $(PLUGIN_PID))" 2>/dev/null; then \
		echo "paperbase plugin MCP bridge failed to stay up"; \
		sed -n '1,120p' $(PLUGIN_LOG); \
		$(MAKE) kill-all >/dev/null; \
		exit 1; \
	fi
	@if ! curl -fsS $(PAPERBASE_BASE_URL)/health >/dev/null; then \
		echo "paperbase API failed health check"; \
		sed -n '1,120p' $(API_LOG); \
		$(MAKE) kill-all >/dev/null; \
		exit 1; \
	fi
	@echo "Started paperbase API    pid=$$(cat $(API_PID)) log=$(API_LOG)"
	@echo "Chat URL                 $(PAPERBASE_BASE_URL)"
	@echo "Started paperbase worker pid=$$(cat $(WORKER_PID)) log=$(WORKER_LOG)"
	@echo "Started plugin MCP       pid=$$(cat $(PLUGIN_PID)) url=$(PLUGIN_MCP_URL) log=$(PLUGIN_LOG)"

start-local: install build-chat-frontend
	@mkdir -p $(RUN_DIR) "$(LOCAL_STORAGE_ROOT)"
	@$(MAKE) kill-all >/dev/null
	@nohup $(LOCAL_STACK_ENV) PAPERBASE_API_HOST=$(API_HOST) PAPERBASE_API_PORT=$(API_PORT) $(VENV_BIN)/paperbase-api > $(API_LOG) 2>&1 & echo $$! > $(API_PID)
	@nohup $(LOCAL_STACK_ENV) $(VENV_BIN)/paperbase-worker > $(WORKER_LOG) 2>&1 & echo $$! > $(WORKER_PID)
	@nohup $(LOCAL_STACK_ENV) PAPERBASE_PLUGIN_TRANSPORT=streamable-http FASTMCP_HOST=$(PLUGIN_MCP_HOST) FASTMCP_PORT=$(PLUGIN_MCP_PORT) $(VENV_BIN)/paperbase-plugin-mcp > $(PLUGIN_LOG) 2>&1 & echo $$! > $(PLUGIN_PID)
	@sleep 2
	@if ! kill -0 "$$(cat $(API_PID))" 2>/dev/null; then \
		echo "paperbase API failed to stay up"; \
		sed -n '1,120p' $(API_LOG); \
		$(MAKE) kill-all >/dev/null; \
		exit 1; \
	fi
	@if ! kill -0 "$$(cat $(WORKER_PID))" 2>/dev/null; then \
		echo "paperbase worker failed to stay up"; \
		sed -n '1,120p' $(WORKER_LOG); \
		$(MAKE) kill-all >/dev/null; \
		exit 1; \
	fi
	@if ! kill -0 "$$(cat $(PLUGIN_PID))" 2>/dev/null; then \
		echo "paperbase plugin MCP bridge failed to stay up"; \
		sed -n '1,120p' $(PLUGIN_LOG); \
		$(MAKE) kill-all >/dev/null; \
		exit 1; \
	fi
	@if ! curl -fsS $(PAPERBASE_BASE_URL)/health >/dev/null; then \
		echo "paperbase API failed health check"; \
		sed -n '1,120p' $(API_LOG); \
		$(MAKE) kill-all >/dev/null; \
		exit 1; \
	fi
	@echo "Started local API       pid=$$(cat $(API_PID)) url=$(PAPERBASE_BASE_URL) log=$(API_LOG)"
	@echo "Chat URL                $(PAPERBASE_BASE_URL)"
	@echo "Started local worker    pid=$$(cat $(WORKER_PID)) log=$(WORKER_LOG)"
	@echo "Started local plugin    pid=$$(cat $(PLUGIN_PID)) url=$(PLUGIN_MCP_URL) log=$(PLUGIN_LOG)"
	@echo "Local Postgres URL      $(LOCAL_DATABASE_URL)"
	@echo "Local storage root      $(LOCAL_STORAGE_ROOT)"
	@echo "Auto sync latest        $(LOCAL_AUTO_SYNC_LATEST)"
	@echo "Initial sync latest     $(LOCAL_INITIAL_SYNC_LATEST)"
	@echo "Log format/level        $(LOCAL_LOG_FORMAT)/$(LOCAL_LOG_LEVEL)"
	@if [ "$(LOCAL_STREAM_LOGS)" = "1" ]; then \
		echo "Streaming logs. Press Ctrl-C to detach; use 'make kill-all' to stop the stack."; \
		tail -n +1 -F $(API_LOG) $(WORKER_LOG) $(PLUGIN_LOG); \
	fi

flush: install-paperbase
	@$(MAKE) kill-all >/dev/null
	@$(VENV_PYTHON) $(CURDIR)/scripts/flush_local.py \
		--database-url "$(LOCAL_DATABASE_URL)" \
		--storage-root "$(LOCAL_STORAGE_ROOT)" \
		--repo-root "$(CURDIR)"
	@echo "Flushed local paperbase state"
	@echo "Local Postgres URL      $(LOCAL_DATABASE_URL)"
	@echo "Local storage root      $(LOCAL_STORAGE_ROOT)"

kill-all:
	@mkdir -p $(RUN_DIR)
	@for pid_file in $(API_PID) $(WORKER_PID) $(PLUGIN_PID); do \
		if [ -f "$$pid_file" ]; then \
			pid=$$(cat "$$pid_file"); \
			if kill -0 "$$pid" 2>/dev/null; then \
				kill "$$pid" 2>/dev/null || true; \
				for _ in 1 2 3 4 5; do \
					if kill -0 "$$pid" 2>/dev/null; then \
						sleep 1; \
					else \
						break; \
					fi; \
				done; \
				if kill -0 "$$pid" 2>/dev/null; then \
					kill -9 "$$pid" 2>/dev/null || true; \
				fi; \
				echo "Stopped pid $$pid"; \
			fi; \
			rm -f "$$pid_file"; \
		fi; \
	done
	@for pattern in paperbase-api paperbase-worker paperbase-plugin-mcp; do \
		pids=$$(pgrep -f "$$pattern" 2>/dev/null || true); \
		if [ -n "$$pids" ]; then \
			kill $$pids 2>/dev/null || true; \
		fi; \
	done
	@if command -v lsof >/dev/null 2>&1; then \
		for port in $(API_PORT) $(PLUGIN_MCP_PORT); do \
			pids=$$(lsof -ti tcp:$$port 2>/dev/null || true); \
			if [ -n "$$pids" ]; then \
				kill $$pids 2>/dev/null || true; \
			fi; \
		done; \
	fi
	@sleep 1
	@for pattern in paperbase-api paperbase-worker paperbase-plugin-mcp; do \
		pids=$$(pgrep -f "$$pattern" 2>/dev/null || true); \
		if [ -n "$$pids" ]; then \
			kill -9 $$pids 2>/dev/null || true; \
		fi; \
	done
	@if command -v lsof >/dev/null 2>&1; then \
		for port in $(API_PORT) $(PLUGIN_MCP_PORT); do \
			pids=$$(lsof -ti tcp:$$port 2>/dev/null || true); \
			if [ -n "$$pids" ]; then \
				kill -9 $$pids 2>/dev/null || true; \
			fi; \
		done; \
	fi
	@rm -f $(API_PID) $(WORKER_PID) $(PLUGIN_PID)

clean:
	rm -rf .pytest_cache
	rm -rf paperbase/.pytest_cache paperbase-plugin/.pytest_cache
	rm -rf paperbase/tests/__pycache__ paperbase-plugin/tests/__pycache__
	find paperbase paperbase-plugin -type d -name "__pycache__" -prune -exec rm -rf {} +
	rm -rf $(RUN_DIR)
