"""SQLite online backup; source remains untouched, existing targets are refused."""
import argparse
import os
from contextlib import closing
from pathlib import Path
import sqlite3


def backup(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError("Source database does not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental overwrite, including source == target.
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    try:
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            with closing(sqlite3.connect(destination)) as copied:
                original.backup(copied)
                # Publish a standalone snapshot: WAL/SHM are never part of the artifact.
                copied.execute("PRAGMA journal_mode=DELETE")
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

