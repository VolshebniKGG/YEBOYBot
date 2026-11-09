"""Pinned, local Windows music tools; no PATH changes or administrator installer."""
import argparse
import errno
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
    """Safely extract the signed ZIP while shortening its shared root folder."""
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > 20000 or sum(item.file_size for item in members) > MAX_UNPACKED:
            raise MediaInstallError('Архів перевищує дозволений розмір розпакування.')
        entries = []
        for item in members:
            if item.orig_filename != item.filename:
                raise MediaInstallError('Архів містить неприпустимий шлях або посилання.')
            path = PurePosixPath(item.filename)
            parts = path.parts
            kind = stat.S_IFMT(item.external_attr >> 16)
            if (not parts or path.is_absolute() or '..' in parts or '\\' in item.filename
                    or any(invalid_component(part) for part in parts)
                    or kind not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise MediaInstallError('Архів містить неприпустимий шлях або посилання.')
            entries.append((item, path))
        # FFmpeg ZIP is nested under a single ffmpeg-...-essentials_build
        # directory. Strip only a genuinely common top directory, retaining
        # every notice, license and other file, and reducing Windows path length.
        common = entries[0][1].parts[0] if entries else None
        strip_root = bool(common) and all(
            path.parts[0] == common and (len(path.parts) > 1 or item.is_dir())
            for item, path in entries
        )
        normalized = []
        seen = set()
        executables = []
        for item, path in entries:
            parts = path.parts[1:] if strip_root else path.parts
            if not parts:
                continue
            relative = PurePosixPath(*parts)
            key = str(relative).casefold()
            if key in seen:
                raise MediaInstallError('Архів містить дублікати шляхів.')
            seen.add(key)
            normalized.append((item, relative))
            if not item.is_dir() and relative.name.casefold() == package.executable:
                executables.append(relative)
        if len(executables) != 1:
            raise MediaInstallError('У перевіреному архіві не знайдено єдиний потрібний executable.')
        for item, relative in normalized:
            output = destination.joinpath(*relative.parts)
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


def explain_install_failure(name, stage, error):
    """Report the failure stage and OS category without leaking local paths."""
    if isinstance(error, zipfile.BadZipFile):
        hint = 'Не вдалося відкрити перевірений ZIP.'
    elif isinstance(error, OSError):
        code = getattr(error, 'winerror', None)
        number = code if code is not None else error.errno
        if stage == 'запуск програми' and (code == 5 or (code is None and error.errno in (errno.EACCES, errno.EPERM))):
            hint = ('Windows відмовив у запуску виконуваного файлу. '
                    'Перевірте Безпека у Windows → Журнал захисту; '
                    'у Перегляді подій: CodeIntegrity/Operational (подія 3077) '
                    'або AppLocker/EXE and DLL (подія 8004). '
                    'Це може бути антивірус, політика запуску або права доступу. '
                    'Не вимикайте захист і не додавайте всю папку до винятків.')
        elif code in (5, 32, 33) or error.errno in (errno.EACCES, errno.EPERM):
            hint = ('Windows заборонив доступ до файлу або він зайнятий іншою програмою. '
                    'Перевірте дозволи папки й історію захисту Windows.')
        elif code == 206 or error.errno == errno.ENAMETOOLONG:
            hint = ('Windows відхилив занадто довгий шлях. '
                    'Спробуйте розпакувати бот у коротку папку, наприклад C:\\YEBOYBot.')
        elif code in (112, 39) or error.errno == errno.ENOSPC:
            hint = 'Недостатньо місця на диску для розпакування.'
        else:
            hint = 'Файлова система або Windows не завершили операцію.'
        if number is not None:
            hint += f' Код ОС: {number}.'
    elif isinstance(error, subprocess.SubprocessError):
        hint = 'Не вдалося запустити програму для перевірки її версії.'
    else:
        hint = 'Відхилено некоректний формат даних або операцію.'
    return MediaInstallError(f'{name}: збій на етапі «{stage}». {hint}')


def install_package(root, package):
    root = Path(root).resolve()
    stage_name = 'перевірка папки'
    try:
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

        cache = folder / ('.media-verified-' + package.name + '-' + package.sha256[:16] + '.zip')
        reject_link(cache)
        stage_name = 'тимчасова папка'
        # A shorter stage name, plus flattening the ZIP's shared top folder,
        # lowers the risk of Windows legacy MAX_PATH failures.
        with tempfile.TemporaryDirectory(prefix='.m-', dir=folder) as temporary:
            stage = Path(temporary)
            archive = stage / 'd.zip'
            extracted = stage / 'ready'
            extracted.mkdir()
            valid_cache = (cache.is_file() and cache.stat().st_size == package.size
                           and digest(cache) == package.sha256)
            stage_name = 'завантаження'
            if valid_cache:
                archive = cache
                print(f'  […] {package.name}: перевірений ZIP з попередньої спроби; завантаження не потрібне.', flush=True)
            else:
                print(f'  […] {package.name} {package.version} — завантаження {package.size // 1_000_000} МБ, перевірка SHA-256…', flush=True)
                download(package, archive)
            try:
                stage_name = 'розпакування ZIP'
                candidate = unpack(archive, extracted, package)
                stage_name = 'запуск програми'
                verify(candidate, package)
                stage_name = 'запис маркера'
                (extracted / MANIFEST).write_text(json.dumps({
                    'owner': 'YEBOYBot', 'name': package.name, 'version': package.version,
                    'archive_sha256': package.sha256, 'executable_sha256': digest(candidate),
                }, indent=2), encoding='utf-8')
                saved = None
                stage_name = 'публікація програми'
                if target.exists():
                    saved = folder / (package.name + '.previous-' + uuid.uuid4().hex)
                    target.rename(saved)
                try:
                    extracted.rename(target)
                except OSError:
                    if saved is not None:
                        saved.rename(target)
                    raise
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError,
                    zipfile.BadZipFile):
                # Keep only a verified archive for retry, not a partial download.
                if (archive != cache and archive.is_file() and not cache.exists()
                        and archive.stat().st_size == package.size
                        and digest(archive) == package.sha256):
                    try:
                        archive.rename(cache)
                        print('  [i] ZIP зі справжнім SHA-256 збережено для наступної спроби.', flush=True)
                    except OSError:
                        pass
                raise
        if valid_cache:
            try:
                cache.unlink(missing_ok=True)
            except OSError:
                pass  # Successful installation is not invalidated by cache cleanup.
        print(f'  [OK] {package.name} — перевірено й підготовлено в bin/{package.name}.')
        return executable
    except MediaInstallError:
        raise
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError,
            zipfile.BadZipFile) as error:
        raise explain_install_failure(package.name, stage_name, error) from error


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
