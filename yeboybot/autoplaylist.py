import asyncio
import logging
import pathlib
import shutil
import time
from collections import UserList
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from .constants import (
    APL_FILE_APLCOPY,
    APL_FILE_DEFAULT,
    APL_FILE_HISTORY,
    OLD_BUNDLED_AUTOPLAYLIST_FILE,
    OLD_DEFAULT_AUTOPLAYLIST_FILE,
)

if TYPE_CHECKING:
    # Припускаємо, що ваш MusicBot успадковує discord.ext.commands.Bot (py‑cord)
    from discord.ext.commands import Bot as MusicBot  
    StrUserList = UserList[str]
else:
    StrUserList = UserList

log = logging.getLogger(__name__)


class AutoPlaylist(StrUserList):
    def __init__(self, filename: pathlib.Path, bot: "MusicBot") -> None:
        super().__init__()
        self._bot: "MusicBot" = bot
        self._file: pathlib.Path = filename
        self._removed_file = filename.with_name(f"{filename.stem}.removed.log")
        self._update_lock: asyncio.Lock = asyncio.Lock()
        self._file_lock: asyncio.Lock = asyncio.Lock()
        self._is_loaded: bool = False

    @property
    def filename(self) -> str:
        """Повертає базове ім'я файлу автосписку."""
        return self._file.name

    @property
    def loaded(self) -> bool:
        """Повертає стан завантаження автосписку. Якщо False – дані недоступні."""
        return self._is_loaded

    @property
    def rmlog_file(self) -> pathlib.Path:
        """Повертає ім'я файлу для логування видалених записів."""
        return self._removed_file

    def create_file(self) -> None:
        """Створює файл автосписку, якщо його ще немає."""
        if not self._file.is_file():
            self._file.touch(exist_ok=True)

    async def load(self, force: bool = False) -> None:
        """
        Завантажує автосписок з файлу, якщо ще не завантажено.
        """
        if (self._is_loaded or self._file_lock.locked()) and not force:
            return
        async with self._file_lock:
            try:
                self.data = self._read_playlist()
            except OSError:
                log.warning("Помилка завантаження автосписку: %s", self._file)
                self.data = []
                self._is_loaded = False
                return
            self._is_loaded = True

    def _read_playlist(self) -> List[str]:
        """
        Зчитує та розбирає файл автосписку, повертаючи список URL/записів.
        """
        comment_char = "#"
        playlist: List[str] = []
        with open(self._file, "r", encoding="utf8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith(comment_char):
                    continue
                playlist.append(line)
        return playlist

    async def remove_track(
        self,
        song_subject: str,
        *,
        ex: Optional[Exception] = None,
        delete_from_ap: bool = False,
    ) -> None:
        """
        Видаляє запис (song_subject) з автосписку (і, опційно, оновлює файл).
        """
        if song_subject not in self.data:
            return
        async with self._update_lock:
            self.data.remove(song_subject)
            log.info("Видаляємо%s пісню з автосписку %s: %s",
                     " непроигравану" if ex and not isinstance(ex, UserWarning) else "",
                     self._file.name, song_subject)
            if not self._removed_file.is_file():
                self._removed_file.touch(exist_ok=True)
            try:
                with open(self._removed_file, "a", encoding="utf8") as f:
                    ctime = time.ctime()
                    e_str = str(ex).replace("\n", "\n#" + " " * 10)
                    sep = "#" * 32
                    f.write(f"# Entry removed {ctime}\n# Track:  {song_subject}\n# Reason: {e_str}\n\n{sep}\n\n")
            except (OSError, PermissionError, FileNotFoundError, IsADirectoryError):
                log.exception("Не вдалося записати лог видалення для: %s", self._file)
            if delete_from_ap:
                log.info("Оновлення файлу автосписку...")
                def _filter_replace(line: str, url: str) -> str:
                    target = line.strip()
                    return f"# Removed # {url}" if target == url else line
                try:
                    data = self._file.read_text(encoding="utf8").split("\n")
                    data = [_filter_replace(x, song_subject) for x in data]
                    text = "\n".join(data)
                    self._file.write_text(text, encoding="utf8")
                except (OSError, PermissionError, FileNotFoundError):
                    log.exception("Не вдалося оновити файл автосписку: %s", self._file)
                self._bot.filecache.remove_autoplay_cachemap_entry_by_url(song_subject)

    async def add_track(self, song_subject: str) -> None:
        """
        Додає новий запис до автосписку (як у файлі, так і в пам'яті).
        """
        if song_subject in self.data:
            log.debug("Запис уже існує у автосписку %s, пропускаємо", self._file.name)
            return
        async with self._update_lock:
            self.data.append(song_subject)
            log.info("Додаємо новий запис до автосписку %s: %s", self._file.name, song_subject)
            try:
                if not self._file.is_file():
                    self._file.touch(exist_ok=True)
                with open(self._file, "r+", encoding="utf8") as fh:
                    lines = fh.readlines()
                    if not lines:
                        lines.append("# MusicBot Auto Playlist\n")
                    if lines[-1].endswith("\n"):
                        lines.append(f"{song_subject}\n")
                    else:
                        lines.append(f"\n{song_subject}\n")
                    fh.seek(0)
                    fh.writelines(lines)
            except (OSError, PermissionError, FileNotFoundError):
                log.exception("Не вдалося зберегти файл автосписку: %s", self._file)


class AutoPlaylistManager:
    """
    Менеджер автосписків, що полегшує роботу з кількома автосписками.
    """
    def __init__(self, bot: "MusicBot") -> None:
        self._bot: "MusicBot" = bot
        self._apl_dir: pathlib.Path = bot.config.auto_playlist_dir
        self._apl_file_default = self._apl_dir.joinpath(APL_FILE_DEFAULT)
        self._apl_file_history = self._apl_dir.joinpath(APL_FILE_HISTORY)
        self._apl_file_usercopy = self._apl_dir.joinpath(APL_FILE_APLCOPY)
        self._playlists: Dict[str, AutoPlaylist] = {}
        self.setup_autoplaylist()

    def setup_autoplaylist(self) -> None:
        """
        Переконується, що каталог автосписків існує, та копіює/перейменовує старі файли автосписків.
        """
        if not self._apl_dir.is_dir():
            self._apl_dir.mkdir(parents=True, exist_ok=True)
        old_usercopy = pathlib.Path(OLD_DEFAULT_AUTOPLAYLIST_FILE)
        old_bundle = pathlib.Path(OLD_BUNDLED_AUTOPLAYLIST_FILE)
        if old_usercopy.is_file() and not self._apl_file_usercopy.is_file():
            old_usercopy.rename(self._apl_file_usercopy)
        if old_bundle.is_file() and not self._apl_file_default.is_file():
            shutil.copy(old_bundle, self._apl_file_default)
        if not self._apl_file_history.is_file() and self._bot.config.enable_queue_history_global:
            self._apl_file_history.touch(exist_ok=True)
        self.discover_playlists()

    @property
    def _default_pl(self) -> AutoPlaylist:
        if self._apl_file_default.stem in self._playlists:
            return self._playlists[self._apl_file_default.stem]
        self._playlists[self._apl_file_default.stem] = AutoPlaylist(filename=self._apl_file_default, bot=self._bot)
        return self._playlists[self._apl_file_default.stem]

    @property
    def _usercopy_pl(self) -> Optional[AutoPlaylist]:
        if self._apl_file_usercopy.stem in self._playlists:
            return self._playlists[self._apl_file_usercopy.stem]
        if self._apl_file_usercopy.is_file():
            self._playlists[self._apl_file_usercopy.stem] = AutoPlaylist(filename=self._apl_file_usercopy, bot=self._bot)
        return self._playlists.get(self._apl_file_usercopy.stem, None)

    @property
    def global_history(self) -> AutoPlaylist:
        if self._apl_file_history.stem in self._playlists:
            return self._playlists[self._apl_file_history.stem]
        self._playlists[self._apl_file_history.stem] = AutoPlaylist(filename=self._apl_file_history, bot=self._bot)
        return self._playlists[self._apl_file_history.stem]

    @property
    def playlist_names(self) -> List[str]:
        return list(self._playlists.keys())

    @property
    def loaded_playlists(self) -> List[AutoPlaylist]:
        return [pl for pl in self._playlists.values() if pl.loaded]

    @property
    def loaded_tracks(self) -> List[str]:
        tracks: Set[str] = set()
        for pl in self._playlists.values():
            if pl.loaded:
                tracks = tracks.union(set(pl))
        return list(tracks)

    def discover_playlists(self) -> None:
        for pfile in self._apl_dir.iterdir():
            if pfile.suffix.lower() == ".txt":
                if pfile.stem in self._playlists:
                    continue
                pl = AutoPlaylist(pfile, self._bot)
                self._playlists[pfile.stem] = pl

    def get_default(self) -> AutoPlaylist:
        if self._usercopy_pl is not None:
            return self._usercopy_pl
        return self._default_pl

    def get_playlist(self, filename: str) -> AutoPlaylist:
        pl_file = self._apl_dir.joinpath(pathlib.Path(filename).name)
        if pl_file.stem in self._playlists:
            return self._playlists[pl_file.stem]
        self._playlists[pl_file.stem] = AutoPlaylist(pl_file, self._bot)
        return self._playlists[pl_file.stem]

    def playlist_exists(self, filename: str) -> bool:
        return self._apl_dir.joinpath(pathlib.Path(filename).name).is_file()
