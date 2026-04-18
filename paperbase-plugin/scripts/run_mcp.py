#!/usr/bin/env python3
"""Launch the paperbase MCP server from the plugin root without installation."""

from __future__ import annotations

import sys
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = PLUGIN_ROOT / "src"

sys.path.insert(0, str(SRC_DIR))

from paperbase_plugin.mcp_server import main


if __name__ == "__main__":
    main()
