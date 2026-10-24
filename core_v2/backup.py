"""SQLite online backup; source remains untouched, existing targets are refused."""
import argparse
from pathlib import Path
import sqlite3


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError("Source database does not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental overwrite, including source == target.
    with destination.open("xb"):
        pass
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
            with sqlite3.connect(destination) as copied:
                original.backup(copied)
                if copied.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Backup integrity check failed")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination")
    parser.add_argument("--source", default="data/v2/yeboy.db")
    args = parser.parse_args()
    backup(args.source, args.destination)
    print("Backup created and verified.")
