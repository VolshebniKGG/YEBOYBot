"""Small isolated Windows entry point for Core V2 installation.

Invoke this *file* with the verified Python executable. Avoid passing a
Python executable filename as source input if PowerShell/native argument
parsing changes. No downloads, PATH edits, or package writes happen here.
"""
import runpy
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    runpy.run_module("tools.launcher.bootstrap", run_name="__main__", alter_sys=True)


if __name__ == "__main__":
    main()
