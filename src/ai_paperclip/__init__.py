"""AI Paperclip — search and analyze 800K+ AI/ML papers."""

__version__ = "0.3.0"
__package_name__ = "ai-paperclip"

from pathlib import Path

DATA_DIR = Path.home() / ".ai-paperclip"
CACHE_DIR = DATA_DIR / "cache"
PAPERS_DIR = DATA_DIR / "papers"
