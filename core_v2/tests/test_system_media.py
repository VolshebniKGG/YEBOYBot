"""Offline compatibility checks for external tools; no executable is launched here."""
import json
import os
import subprocess

import pytest

from tools.launcher import system_media
from tools import portable_media


def executable(tmp_path, name):
    target = tmp_path / "external" / (name + (".exe" if os.name == "nt" else ""))
    target.parent.mkdir(exist_ok=True)
    target.touch()
    return target


@pytest.mark.parametrize("name,version,accepted", [
    ("ffmpeg", "ffmpeg version 9.0.2-essentials_build", True),
    ("ffmpeg", "ffmpeg version 10.1 Copyright etc", True),
    ("ffmpeg", "ffmpeg version 8.0 Copyright etc", False),
    ("deno", "deno 2.9.8 (release, x86_64-pc-windows-msvc)", True),
    ("deno", "deno 2.10.0 (release)", True),
    ("deno", "deno 2.7.0 (release)", False),
])
def test_external_probe_checks_compatible_versions_without_installing(
        tmp_path, monkeypatch, name, version, accepted):
    target = executable(tmp_path, name)
    root = tmp_path / "bot"
    root.mkdir()
    called = []

    def fake_run(args, **kwargs):
        called.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, version + "\n", "")

    monkeypatch.setattr(system_media.subprocess, "run", fake_run)
    report = system_media.validate(name, target, root)
    assert report["compatible"] is accepted
    assert report["found"]
    assert report["path"] == str(target.resolve())
    assert len(called) == 1
    assert called[0][0] == [str(target.resolve()), system_media.FLAGS[name]]
    assert called[0][1]["timeout"] == 10
    assert called[0][1].get("shell") is None


def test_incompatible_external_does_not_echo_executable_output(tmp_path, monkeypatch):
    target = executable(tmp_path, "ffmpeg")
    monkeypatch.setattr(system_media.subprocess, "run", lambda *a, **k:
                        subprocess.CompletedProcess(a, 1, "secret-token https://private.invalid", ""))
    report = system_media.validate("ffmpeg", target, tmp_path / "bot")
    assert not report["compatible"]
    assert "secret" not in json.dumps(report)
    assert "private.invalid" not in json.dumps(report)


def test_external_scanner_ignores_executable_inside_bot(tmp_path, monkeypatch):
    root = tmp_path / "bot"
    exe = root / "bin" / "ffmpeg" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    exe.parent.mkdir(parents=True)
    exe.touch()
    monkeypatch.setattr(system_media.shutil, "which", lambda name: str(exe))
    for key in ("LOCALAPPDATA", "USERPROFILE", "ProgramFiles", "ProgramData"):
        monkeypatch.delenv(key, raising=False)
    assert list(system_media.candidates("ffmpeg", root)) == []


def test_discover_selects_compatible_candidate_not_first_old_one(tmp_path, monkeypatch):
    old = executable(tmp_path, "ffmpeg")
    newer = old.with_name("ffmpeg-new" + old.suffix)
    newer.touch()
    monkeypatch.setattr(system_media, "candidates", lambda name, root: iter((old, newer)))
    monkeypatch.setattr(system_media, "validate", lambda name, path, root: {
        "found": True, "compatible": path == newer, "path": str(path),
        "version": "9.0.2" if path == newer else "8.0.0",
        "reason": "" if path == newer else "too-old"})
    assert system_media.discover("ffmpeg", tmp_path / "bot")["path"] == str(newer)


def test_winget_package_discovered_without_current_shell_path(tmp_path, monkeypatch):
    root = tmp_path / "bot"
    root.mkdir()
    local = tmp_path / "Local AppData"
    ffmpeg = (local / "Microsoft" / "WinGet" / "Packages" /
              "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe" /
              "ffmpeg-9.0.2-full_build" / "bin" /
              ("ffmpeg.exe" if os.name == "nt" else "ffmpeg"))
    ffmpeg.parent.mkdir(parents=True)
    ffmpeg.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(system_media.shutil, "which", lambda name: None)
    for key in ("USERPROFILE", "ProgramFiles", "ProgramData"):
        monkeypatch.delenv(key, raising=False)
    listed = list(system_media.candidates("ffmpeg", root))
    assert ffmpeg.resolve() in listed
    assert len(listed) == 1


def test_winget_deno_package_discovered_without_path(tmp_path, monkeypatch):
    root = tmp_path / "bot"
    root.mkdir()
    local = tmp_path / "LocalAppData"
    deno = (local / "Microsoft" / "WinGet" / "Packages" /
            "DenoLand.Deno_Microsoft.Winget.Source_8wekyb3d8bbwe" /
            ("deno.exe" if os.name == "nt" else "deno"))
    deno.parent.mkdir(parents=True)
    deno.touch()
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(system_media.shutil, "which", lambda name: None)
    for key in ("USERPROFILE", "ProgramFiles", "ProgramData"):
        monkeypatch.delenv(key, raising=False)
    assert deno.resolve() in list(system_media.candidates("deno", root))


def test_minimum_versions_match_pinned_portable_releases():
    for package in portable_media.PACKAGES:
        assert system_media.MINIMUM[package.name] == tuple(map(int, package.version.split(".")))


def test_preflight_reports_only_structured_data_and_no_pip(tmp_path, monkeypatch, capsys):
    def fake_discover(name, root):
        return {"found": False, "compatible": False, "path": "", "version": "",
                "reason": "not-found"}

    monkeypatch.setattr(system_media, "discover", fake_discover)
    assert system_media.main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"ffmpeg", "deno"}
    assert all(not entry["compatible"] for entry in payload.values())
