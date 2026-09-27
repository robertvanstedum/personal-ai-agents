"""Make the standalone prototype importable; quiet the dev server log."""
import logging
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parents[2]
sys.path.insert(0, str(PROJECT))
logging.getLogger("werkzeug").setLevel(logging.ERROR)
