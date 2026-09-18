"""Make the standalone PoC importable regardless of pytest collection order."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
