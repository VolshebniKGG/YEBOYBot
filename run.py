"""Guarded foreground entry point for the isolated legacy bot."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    from tools.legacy_launcher import bot_entry
    raise SystemExit(bot_entry(ROOT, music="--music" in sys.argv[1:]))
