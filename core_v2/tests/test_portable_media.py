"""Pinned portable tools: bounded download, safe extraction, reuse and rollback."""
import errno
import hashlib
import io
import json
import os
import stat
import subprocess
import zipfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core_v2.runtime import InstanceLock
from tools import portable_media as media


def archive_bytes(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, data in members:
            if isinstance(name, str):
                # Preserve raw names in the fixture on every OS. ZipInfo's
                # constructor would sanitize backslashes on Windows and NULs.
                item = zipfile.ZipInfo('placeholder')
                item.filename = item.orig_filename = name
            else:
                item = name
            archive.writestr(item, data)
    return buffer.getvalue()


def package_for(payload, name='deno'):
    return media.Package(name, '2.9.8', 'https://example.invalid/pinned.zip',
                         hashlib.sha256(payload).hexdigest(), len(payload), '--version')


class Response(io.BytesIO):
    def geturl(self):
        return 'https://release-assets.githubusercontent.com/pinned.zip'


def stub_download(monkeypatch, payload):
    fetch = Mock(side_effect=lambda request, timeout: Response(payload))
    monkeypatch.setattr(media.urllib.request, 'urlopen', fetch)
    return fetch


@pytest.mark.parametrize('change', ['none', 'digest', 'truncated', 'oversize'])
def test_download_requires_exact_size_and_digest(tmp_path, monkeypatch, change):
    payload = b'known archive content'
    package = package_for(payload)
    if change == 'digest':
        package = replace(package, sha256='0' * 64)
    actual = payload[:-1] if change == 'truncated' else payload + b'x' if change == 'oversize' else payload
    fetch = stub_download(monkeypatch, actual)
    path = tmp_path / 'download.zip'
    if change == 'none':
        media.download(package, path)
        assert path.read_bytes() == payload
    else:
        with pytest.raises(media.MediaInstallError):
            media.download(package, path)
    assert fetch.call_args.kwargs['timeout'] == 30


def test_download_checks_deadline_before_read(tmp_path, monkeypatch):
    payload = b'archive'
    stub_download(monkeypatch, payload)
    clock = iter([0, 601])
    monkeypatch.setattr(media.time, 'monotonic', lambda: next(clock))
    with pytest.raises(media.MediaInstallError, match='10 хвилин'):
        media.download(package_for(payload), tmp_path / 'download.zip')


def test_download_rejects_non_https_destination(tmp_path, monkeypatch):
    response = Response(b'archive')
    response.geturl = lambda: 'http://example.invalid/archive'
    monkeypatch.setattr(media.urllib.request, 'urlopen', lambda *a, **kw: response)
    with pytest.raises(media.MediaInstallError, match='HTTPS'):
        media.download(package_for(b'archive'), tmp_path / 'download.zip')


def write_archive(tmp_path, members):
    payload = archive_bytes(members)
    path = tmp_path / 'archive.zip'
    path.write_bytes(payload)
    destination = tmp_path / 'stage'
    destination.mkdir()
    return path, destination, package_for(payload)


def test_unpack_keeps_notices_and_has_stable_executable(tmp_path):
    path, destination, package = write_archive(tmp_path, [
        ('version/bin/deno.exe', b'binary'), ('version/LICENSE', b'license'),
        ('version/README.txt', b'readme'),
    ])
    assert media.unpack(path, destination, package) == destination / 'deno.exe'
    assert (destination / 'deno.exe').read_bytes() == b'binary'
    assert (destination / 'LICENSE').read_bytes() == b'license'
    assert (destination / 'README.txt').read_bytes() == b'readme'


@pytest.mark.parametrize('name', [
    '../outside', '/outside', 'dir/../../outside', r'dir\outside', 'C:/outside',
    'file:stream', 'dir/NUL.txt', 'dir/COM1', 'dir/LPT¹.txt', 'dir/bad.',
    'dir/bad ', 'dir/what?.txt', 'dir/pipe|name', 'dir/control\x01.txt', 'dir/nul\x00hidden',
])
def test_unpack_rejects_windows_unsafe_paths_before_writing(tmp_path, name):
    path, destination, package = write_archive(tmp_path, [('deno.exe', b'binary'), (name, b'bad')])
    with pytest.raises(media.MediaInstallError):
        media.unpack(path, destination, package)
    assert list(destination.iterdir()) == []
    assert not (tmp_path / 'outside').exists()


def test_unpack_rejects_symlink_member(tmp_path):
    item = zipfile.ZipInfo('link')
    item.create_system = 3
    item.external_attr = (stat.S_IFLNK | 0o777) << 16
    path, destination, package = write_archive(tmp_path, [('deno.exe', b'binary'), (item, b'outside')])
    with pytest.raises(media.MediaInstallError):
        media.unpack(path, destination, package)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize('members', [
    [('DENO.exe', b'one'), ('deno.exe', b'two')],
    [('first/deno.exe', b'one'), ('second/deno.exe', b'two')],
    [('README', b'no executable')],
])
def test_unpack_requires_one_unambiguous_executable(tmp_path, members):
    path, destination, package = write_archive(tmp_path, members)
    with pytest.raises(media.MediaInstallError):
        media.unpack(path, destination, package)
    assert list(destination.iterdir()) == []


def test_unpack_enforces_total_expansion_limit(tmp_path, monkeypatch):
    path, destination, package = write_archive(tmp_path, [('deno.exe', b'123456')])
    monkeypatch.setattr(media, 'MAX_UNPACKED', 5)
    with pytest.raises(media.MediaInstallError, match='розмір'):
        media.unpack(path, destination, package)
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize(('name', 'version', 'output', 'ok'), [
    ('deno', '2.9.8', 'deno 2.9.8 (stable, release)\nv8 1\n', True),
    ('deno', '2.9.8', 'deno 2.9.80 (stable, release)\n', False),
    ('ffmpeg', '9.0.2', 'ffmpeg version 9.0.2-essentials_build-www.gyan.dev\n', True),
    ('ffmpeg', '9.0.2', 'ffmpeg version 9.0.20\n', False),
    ('ffmpeg', '9.0.2', 'ffmpeg version 8.0.0\n', False),
])
def test_version_verification_is_exact(tmp_path, monkeypatch, name, version, output, ok):
    package = replace(package_for(b'', name), version=version)
    executable = tmp_path / package.executable
    executable.touch()
    run = Mock(return_value=subprocess.CompletedProcess([], 0, output, ''))
    monkeypatch.setattr(media.subprocess, 'run', run)
    if ok:
        media.verify(executable, package)
    else:
        with pytest.raises(media.MediaInstallError):
            media.verify(executable, package)
    assert run.call_args.kwargs['timeout'] == 20
    assert run.call_args.args[0] == [str(executable), package.argument]


@pytest.fixture
def installed(tmp_path, monkeypatch):
    payload = archive_bytes([('deno.exe', b'binary'), ('LICENSE', b'license')])
    package = package_for(payload)
    fetch = stub_download(monkeypatch, payload)
    probe = Mock()
    monkeypatch.setattr(media, 'verify', probe)
    executable = media.install_package(tmp_path, package)
    return tmp_path, package, fetch, probe, executable


def test_verified_archive_is_cached_after_failed_unpack_and_reused(tmp_path, monkeypatch):
    payload = archive_bytes([('folder/bin/deno.exe', b'binary'),
                             ('folder/LICENSE', b'license')])
    package = package_for(payload)
    fetch = stub_download(monkeypatch, payload)
    original_unpack = media.unpack
    calls = []

    def fail_once(archive, destination, selected):
        calls.append(archive)
        if len(calls) == 1:
            raise PermissionError(errno.EACCES, 'synthetic access denied')
        return original_unpack(archive, destination, selected)

    monkeypatch.setattr(media, 'unpack', fail_once)
    monkeypatch.setattr(media, 'verify', Mock())
    with pytest.raises(media.MediaInstallError, match='розпакування ZIP'):
        media.install_package(tmp_path, package)
    cache = list((tmp_path / 'bin').glob('.media-verified-*.zip'))
    assert len(cache) == 1 and cache[0].read_bytes() == payload
    executable = media.install_package(tmp_path, package)
    assert executable.read_bytes() == b'binary'
    assert (executable.parent / 'LICENSE').read_bytes() == b'license'
    assert fetch.call_count == 1
    assert not list((tmp_path / 'bin').glob('.media-verified-*.zip'))


@pytest.mark.parametrize('error, hint', [
    (PermissionError(errno.EACCES, 'https://secret@private.example'), 'доступ'),
    (OSError(errno.ENAMETOOLONG, 'https://secret@private.example'), 'довгий шлях'),
    (OSError(errno.ENOSPC, 'https://secret@private.example'), 'місця на диску'),
    (zipfile.BadZipFile('https://secret@private.example'), 'ZIP'),
])
def test_safe_failure_identifies_stage_without_sensitive_source(error, hint):
    message = str(media.explain_install_failure('ffmpeg', 'розпакування ZIP', error))
    assert 'ffmpeg' in message and 'розпакування ZIP' in message
    assert hint in message and 'secret' not in message


def test_unpack_shared_root_cannot_hide_colliding_files(tmp_path):
    path, destination, package = write_archive(tmp_path, [
        ('root/bin/deno.exe', b'one'), ('root/BIN/DENO.EXE', b'two'),
    ])
    with pytest.raises(media.MediaInstallError, match='дублікати'):
        media.unpack(path, destination, package)
    assert list(destination.iterdir()) == []


def test_healthy_install_reuses_files_without_network(installed):
    root, package, fetch, probe, executable = installed
    fetch.side_effect = AssertionError('Healthy installation must work offline')
    assert media.install_package(root, package) == executable
    assert fetch.call_count == 1
    assert probe.call_count == 2
    assert (executable.parent / 'LICENSE').read_bytes() == b'license'
    assert not list((root / 'bin').glob('.m-*'))


def test_corrupt_owned_install_is_preserved_and_replaced(installed):
    root, package, fetch, probe, executable = installed
    executable.write_bytes(b'corrupted')
    (executable.parent / 'user-file.txt').write_text('keep')
    assert media.install_package(root, package) == executable
    assert executable.read_bytes() == b'binary'
    saved = list((root / 'bin').glob('deno.previous-*'))
    assert len(saved) == 1
    assert (saved[0] / 'deno.exe').read_bytes() == b'corrupted'
    assert (saved[0] / 'user-file.txt').read_text() == 'keep'
    assert fetch.call_count == 2


def test_failed_replacement_preserves_original_and_removes_stage(installed):
    root, package, fetch, probe, executable = installed
    executable.write_bytes(b'original-corrupt')
    probe.side_effect = media.MediaInstallError('candidate failed')
    with pytest.raises(media.MediaInstallError, match='candidate failed'):
        media.install_package(root, package)
    assert executable.read_bytes() == b'original-corrupt'
    assert not list((root / 'bin').glob('deno.previous-*'))
    assert not list((root / 'bin').glob('.m-*'))


def test_failed_atomic_publish_restores_previous_install(installed, monkeypatch):
    root, package, fetch, probe, executable = installed
    executable.write_bytes(b'previous')
    original = Path.rename
    def fail_publish(path, target):
        if path.name == 'ready':
            raise PermissionError('blocked publish')
        return original(path, target)
    monkeypatch.setattr(Path, 'rename', fail_publish)
    with pytest.raises(media.MediaInstallError, match='публікація програми'):
        media.install_package(root, package)
    assert executable.read_bytes() == b'previous'
    assert not list((root / 'bin').glob('deno.previous-*'))
    assert not list((root / 'bin').glob('.m-*'))


def test_unowned_folder_is_not_changed_or_downloaded(tmp_path, monkeypatch):
    target = tmp_path / 'bin/deno'
    target.mkdir(parents=True)
    (target / 'custom').write_text('keep')
    download = Mock(side_effect=AssertionError('No download for an unowned target'))
    monkeypatch.setattr(media, 'download', download)
    with pytest.raises(media.MediaInstallError, match='без маркера'):
        media.install_package(tmp_path, package_for(b'archive'))
    assert (target / 'custom').read_text() == 'keep'
    download.assert_not_called()


@pytest.mark.skipif(os.name == 'nt', reason='Creating symlinks requires optional Windows privileges')
@pytest.mark.parametrize('linked', ['bin', 'target', 'manifest', 'executable'])
def test_symlink_targets_are_not_followed(tmp_path, monkeypatch, linked):
    root = tmp_path / 'root'
    root.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    payload = archive_bytes([('deno.exe', b'binary')])
    package = package_for(payload)
    if linked == 'bin':
        (root / 'bin').symlink_to(outside, target_is_directory=True)
    else:
        (root / 'bin').mkdir()
        target = root / 'bin/deno'
        if linked == 'target':
            target.symlink_to(outside, target_is_directory=True)
        else:
            target.mkdir()
            if linked == 'manifest':
                (target / media.MANIFEST).symlink_to(outside / 'marker')
            else:
                (target / media.MANIFEST).write_text(json.dumps({
                    'owner': 'YEBOYBot', 'name': 'deno', 'archive_sha256': package.sha256,
                }))
                (target / 'deno.exe').symlink_to(outside / 'deno.exe')
    with pytest.raises(media.MediaInstallError, match='посиланням'):
        media.install_package(root, package)
    assert list(outside.iterdir()) == []


def test_running_bot_blocks_download(tmp_path, monkeypatch):
    # Substitute only this module's platform object: pathlib and OS locks keep
    # their real native platform while the Windows guard is exercised offline.
    monkeypatch.setattr(media, 'os', SimpleNamespace(name='nt'))
    monkeypatch.setattr(media.platform, 'machine', lambda: 'AMD64')
    install = Mock()
    monkeypatch.setattr(media, 'install_package', install)
    with InstanceLock(tmp_path / 'data/v2/bot.lock'):
        with pytest.raises(RuntimeError):
            media.ensure_tools(tmp_path)
    install.assert_not_called()


def test_unsupported_platform_is_clear_and_does_not_download(tmp_path, monkeypatch):
    monkeypatch.setattr(media, 'os', SimpleNamespace(name='posix'))
    install = Mock()
    monkeypatch.setattr(media, 'install_package', install)
    with pytest.raises(media.MediaInstallError, match='Windows x64'):
        media.ensure_tools(tmp_path)
    install.assert_not_called()


def test_cli_failure_returns_nonzero_without_echoing_remote_secrets(tmp_path, monkeypatch, capsys):
    failure = OSError('https://secret-user:secret-password@private.example')
    monkeypatch.setattr(media, 'ensure_tools', Mock(side_effect=failure))
    assert media.main(['--root', str(tmp_path)]) == 1
    assert 'secret-password' not in capsys.readouterr().out
