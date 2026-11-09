"""Pinned, local Windows music tools; no PATH changes or administrator installer."""
import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tempfile
import time
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from core_v2.runtime import InstanceLock

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = '.yeboy-media.json'
MAX_UNPACKED = 1_500_000_000
RESERVED_NAMES = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'} | {
    prefix + digit for prefix in ('COM', 'LPT') for digit in '123456789¹²³'
}


class MediaInstallError(RuntimeError):
    """Safe, user-facing diagnostic without downloaded output or credentials."""


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    url: str
    sha256: str
    size: int
    argument: str

    @property
    def executable(self):
        return self.name + '.exe'


PACKAGES = (
    Package('ffmpeg', '9.0.2',
            'https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip',
            '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba', 114768076, '-version'),
    Package('deno', '2.9.8',
            'https://github.com/denoland/deno/releases/download/v2.9.8/deno-x86_64-pc-windows-msvc.zip',
            'cba84b26e0df5ae7f74fc66b86a95b866872a86292ebd84fa1e959927edd93bf', 42917472, '--version'),
)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def reject_link(path):
    if path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction()):
        raise MediaInstallError('Папка музичних програм не може бути посиланням. Файли не змінено.')


def download(package, destination):
    request = urllib.request.Request(package.url, headers={'User-Agent': 'YEBOYBot-portable-setup'})
    deadline = time.monotonic() + 600
    count = 0
    progress = -1
    checksum = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=30) as response, destination.open('xb') as output:
        if not response.geturl().startswith('https://'):
            raise MediaInstallError('Джерело завантаження має використовувати HTTPS.')
        while True:
            if time.monotonic() > deadline:
                raise MediaInstallError('Завантаження перевищило 10 хвилин. Перевірте мережу й повторіть.')
            # read1 performs at most one underlying read, allowing the wall-time
            # check to run even when a server sends a slow trickle of data.
            chunk = response.read1(1024 * 1024)
            if not chunk:
                break
            count += len(chunk)
            if count > package.size:
                raise MediaInstallError('Розмір архіву не відповідає зафіксованій версії.')
            checksum.update(chunk)
            output.write(chunk)
            completed = count * 10 // package.size
            if completed > progress:
                progress = completed
                print(f'      {package.name}: {count // 1_000_000}/{package.size // 1_000_000} МБ', flush=True)
    if count != package.size or checksum.hexdigest() != package.sha256:
        raise MediaInstallError('SHA-256 або розмір архіву не збігається. Завантажений файл не запускався.')
    print(f'      {package.name}: SHA-256 підтверджено; розпакування…', flush=True)


def invalid_component(part):
    return (any(ord(char) < 32 or char in '<>:"|?*' for char in part)
            or part.endswith((' ', '.'))
            or part.split('.', 1)[0].upper() in RESERVED_NAMES)


def unpack(archive, destination, package):
    """Extract all upstream files, including license notices, within a private stage."""
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > 20000 or sum(item.file_size for item in members) > MAX_UNPACKED:
            raise MediaInstallError('Архів перевищує дозволений розмір розпакування.')
        seen = set()
        executables = []
        for item in members:
            # ZipInfo normalizes backslashes on Windows and truncates NULs.
            # Validate the original archive name before using its normalized form.
            if item.orig_filename != item.filename:
                raise MediaInstallError('Архів містить неприпустимий шлях або посилання.')
            path = PurePosixPath(item.filename)
            parts = path.parts
            kind = stat.S_IFMT(item.external_attr >> 16)
            if (not parts or path.is_absolute() or '..' in parts or '\\' in item.filename
                    or any(invalid_component(part) for part in parts)
                    or kind not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise MediaInstallError('Архів містить неприпустимий шлях або посилання.')
            key = str(path).casefold()
            if key in seen:
                raise MediaInstallError('Архів містить дублікати шляхів.')
            seen.add(key)
            if not item.is_dir() and path.name.casefold() == package.executable:
                executables.append(path)
        if len(executables) != 1:
            raise MediaInstallError('У перевіреному архіві не знайдено єдиний потрібний executable.')
        for item in members:
            output = destination.joinpath(*PurePosixPath(item.filename).parts)
            if item.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as incoming, output.open('xb') as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
        executable = destination.joinpath(*executables[0].parts)
        stable = destination / package.executable
        if executable != stable:
            executable.rename(stable)
    return stable


def verify(executable, package):
    reject_link(executable)
    result = subprocess.run([str(executable), package.argument], check=False, timeout=20,
                            capture_output=True, text=True, encoding='utf-8', errors='replace',
                            cwd=executable.parent)
    prefix = ('ffmpeg version ' if package.name == 'ffmpeg' else 'deno ') + package.version
    first = (result.stdout or '').splitlines()
    if result.returncode or not first or not re.match(re.escape(prefix) + r'(?:$|\s|[-+])', first[0]):
        raise MediaInstallError(f'{package.name}: програма не підтвердила очікувану версію.')


def read_manifest(target, package):
    marker = target / MANIFEST
    reject_link(marker)
    if not marker.is_file():
        return None
    try:
        if marker.stat().st_size > 65536:
            return None
        data = json.loads(marker.read_text(encoding='utf-8'))
    except (ValueError, OSError):
        return None
    return data if isinstance(data, dict) and data.get('owner') == 'YEBOYBot' and data.get('name') == package.name else None


def install_package(root, package):
    root = Path(root).resolve()
    folder = root / 'bin'
    reject_link(folder)
    folder.mkdir(exist_ok=True)
    target = folder / package.name
    reject_link(target)
    executable = target / package.executable
    manifest = read_manifest(target, package)
    if target.exists() and not manifest:
        raise MediaInstallError(f'bin/{package.name} вже існує без маркера YEBOYBot. Збережіть папку перейменуванням.')
    if manifest and manifest.get('archive_sha256') == package.sha256:
        reject_link(executable)
        if executable.is_file() and digest(executable) == manifest.get('executable_sha256'):
            try:
                verify(executable, package)
            except (OSError, subprocess.SubprocessError, MediaInstallError):
                pass
            else:
                print(f'  [OK] {package.name} {package.version} — готово, повторне завантаження не потрібне.')
                return executable
    with tempfile.TemporaryDirectory(prefix='.media-stage-', dir=folder) as temporary:
        stage = Path(temporary)
        archive = stage / 'download.zip'
        extracted = stage / 'ready'
        extracted.mkdir()
        print(f'  […] {package.name} {package.version} — завантаження {package.size // 1_000_000} МБ, перевірка SHA-256…', flush=True)
        download(package, archive)
        candidate = unpack(archive, extracted, package)
        verify(candidate, package)
        (extracted / MANIFEST).write_text(json.dumps({
            'owner': 'YEBOYBot', 'name': package.name, 'version': package.version,
            'archive_sha256': package.sha256, 'executable_sha256': digest(candidate),
        }, indent=2), encoding='utf-8')
        saved = None
        if target.exists():
            saved = folder / (package.name + '.previous-' + uuid.uuid4().hex)
            target.rename(saved)
        try:
            extracted.rename(target)
        except OSError:
            if saved is not None:
                saved.rename(target)
            raise
    print(f'  [OK] {package.name} — перевірено й підготовлено в bin/{package.name}.')
    return executable


def ensure_tools(root=ROOT, *, names=None):
    """Prepare only explicitly selected local programs, never touch system tools."""
    if os.name != 'nt' or platform.machine().lower() not in {'amd64', 'x86_64'}:
        raise MediaInstallError('Автоматичний комплект призначено для Windows x64. На іншій ОС встановіть FFmpeg і Deno штатним менеджером.')
    selected = {package.name for package in PACKAGES} if names is None else set(names)
    if not selected or not selected <= {package.name for package in PACKAGES}:
        raise MediaInstallError('Невідомий або порожній перелік музичних програм.')
    root = Path(root).resolve()
    with InstanceLock(root / 'data/v2/media-install.lock'), InstanceLock(root / 'data/v2/bot.lock'):
        return {package.name: install_package(root, package)
                for package in PACKAGES if package.name in selected}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Локальні FFmpeg і Deno для YEBOYBot')
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        ensure_tools(args.root)
    except MediaInstallError as error:
        print('[ПОМИЛКА] ' + str(error))
        return 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, zipfile.BadZipFile):
        print('[ПОМИЛКА] Музичні програми не підготовлено. Перевірте мережу, вільне місце та доступ до папки. Повторіть запуск; токен і дані збережено.')
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
