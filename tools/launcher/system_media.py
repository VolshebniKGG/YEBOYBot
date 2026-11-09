"""Read-only discovery of compatible *external* Windows media executables.

This module uses only the standard library so setup can run it before installing
the application's virtual environment. Paths and versions are displayed only
after probing an executable; no raw process output or credentials are logged.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

MINIMUM = {"ffmpeg": (9, 0, 2), "deno": (2, 9, 8)}
FLAGS = {"ffmpeg": "-version", "deno": "--version"}


def candidates(name, root):
    if name not in MINIMUM:
        raise ValueError("Unsupported media tool")
    filename = name + (".exe" if os.name == "nt" else "")
    found = shutil.which(name)
    paths = [Path(found)] if found else []
    local = os.getenv("LOCALAPPDATA")
    home = os.getenv("USERPROFILE")
    program_files = os.getenv("ProgramFiles")
    if local:
        base = Path(local)
        winget = base / "Microsoft" / "WinGet"
        paths.extend((winget / "Links" / filename,
                      base / "Programs" / "deno" / filename))
        # WinGet portable packages are usually below Packages/<id>_<source>/...
        # Winget may have updated the user's PATH on disk, but the launcher
        # inherits the OLD PATH from its already-running parent shell.
        # Discover exact package IDs without a recursive filesystem scan.
        packages = winget / "Packages"
        package_id = "Gyan.FFmpeg_" if name == "ffmpeg" else "DenoLand.Deno_"
        if packages.is_dir():
            try:
                for item in sorted(packages.iterdir()):
                    if not item.is_dir() or not item.name.casefold().startswith(package_id.casefold()):
                        continue
                    paths.extend((item / filename, item / "bin" / filename))
                    # Expected Gyan layout: ffmpeg-9.0.2-full_build/bin/ffmpeg.exe;
                    # Deno may also be under a version-named directory.
                    paths.extend(item.glob("*/" + filename))
                    paths.extend(item.glob("*/bin/" + filename))
            except OSError:
                pass
    if home:
        paths.extend((Path(home) / ".deno" / "bin" / filename,
                      Path(home) / "scoop" / "shims" / filename,
                      Path(home) / "scoop" / "apps" / name / "current" / "bin" / filename))
    if program_files:
        paths.extend((Path(program_files) / name / filename,
                      Path(program_files) / name / "bin" / filename))
    program_data = os.getenv("ProgramData")
    if program_data:
        paths.append(Path(program_data) / "chocolatey" / "bin" / filename)
    seen = set()
    root = Path(root).resolve()
    for candidate in paths:
        try:
            resolved = candidate.resolve(strict=True)
            if (not resolved.is_file() or resolved in seen
                    or resolved.is_relative_to(root)
                    or (os.name == "nt" and resolved.suffix.lower() != ".exe")):
                continue
            seen.add(resolved)
            yield resolved
        except (OSError, RuntimeError, ValueError):
            continue


def validate(name, path, root):
    """Return a safe diagnostic; never reproduce untrusted executable output."""
    if name not in MINIMUM:
        raise ValueError("Unsupported media tool")
    try:
        resolved = Path(path).resolve(strict=True)
        if (not resolved.is_file() or resolved.is_relative_to(Path(root).resolve())
                or (os.name == "nt" and resolved.suffix.lower() != ".exe")):
            raise ValueError("Not an external executable")
        result = subprocess.run([str(resolved), FLAGS[name]], check=False,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=10, cwd=str(Path(root).resolve()))
        first = (result.stdout or "")[:4096].splitlines()
        prefix = r"ffmpeg version " if name == "ffmpeg" else r"deno "
        match = re.match(prefix + r"(\d+)\.(\d+)(?:\.(\d+))?(?=$|[-+\s])",
                         first[0], re.IGNORECASE) if first else None
        if result.returncode != 0 or match is None:
            raise ValueError("Unexpected version response")
        digits = tuple(int(group or "0") for group in match.groups())
        version = ".".join(str(part) for part in digits)
        good = digits >= MINIMUM[name]
        return {"found": True, "compatible": good, "path": str(resolved),
                "version": version, "reason": "" if good else "too-old"}
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return {"found": True, "compatible": False, "path": str(path),
                "version": "", "reason": "probe-failed"}


def discover(name, root):
    first_invalid = None
    for path in candidates(name, root):
        checked = validate(name, path, root)
        if checked["compatible"]:
            return checked
        if first_invalid is None:
            first_invalid = checked
    return first_invalid or {"found": False, "compatible": False, "path": "",
                             "version": "", "reason": "not-found"}


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args != ["--json"]:
        print("Use --json to inspect external FFmpeg and Deno.", file=sys.stderr)
        return 2
    root = Path(__file__).resolve().parents[2]
    print(json.dumps({name: discover(name, root) for name in MINIMUM}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
