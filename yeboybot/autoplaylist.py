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
    from .bot import MusicBot

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
        """Ім'я базового файлу цього списку відтворення."""
        return self._file.name

    @property
    def loaded(self) -> bool:
        """
        Повертає стан завантаження цього списку відтворення.
        Якщо значення False, дані списку відтворення будуть недоступні.
        """
        return self._is_loaded

    @property
    def rmlog_file(self) -> pathlib.Path:
        """Повертає ім'я згенерованого файлу журналу видалення."""
        return self._removed_file

    def create_file(self) -> None:
        """Створює файл плейлиста, якщо його не існує."""
        if not self._file.is_file():
            self._file.touch(exist_ok=True)

    async def load(self, force: bool = False) -> None:
        """
        Завантажує файл плейлиста, якщо його не було завантажено.
        """
        # ignore loaded lists unless forced.
        if (self._is_loaded or self._file_lock.locked()) and not force:
            return

        # Load the actual playlist file.
        async with self._file_lock:
            try:
                self.data = self._read_playlist()
            except OSError:
                log.warning("Error loading auto playlist file:  %s", self._file)
                self.data = []
                self._is_loaded = False
                return
            self._is_loaded = True

    def _read_playlist(self) -> List[str]:
        """
        Прочитайте та проаналізуйте файл плейлиста на наявність записів.
        """
        # Коментарі в apl-файлах обробляються лише на основі початку рядка.
        # Вбудовані коментарі не підтримуються через підтримку записів не за URL-адресою.
        comment_char = "#"

        # Прочитати файл і додати до списку відтворення без коментарів.
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
        Обробляє видалення заданого пункту `пісня_суб'єкт` з черги автовідтворення,
        і, за бажанням, з налаштованого файлу автовідтворення.

        :param: ex: виняток, який буде вказано як причину видалення.
        :param: delete_from_ap: чи слід оновлювати налаштований файл списку?
        """
        if song_subject not in self.data:
            return

        async with self._update_lock:
            self.data.remove(song_subject)
            log.info(
                "Видалення%s пісні зі списку відтворення, %s: %s",
                " не відтворюється" if ex and not isinstance(ex, UserWarning) else "",
                self._file.name,
                song_subject,
            )

            if not self._removed_file.is_file():
                self._removed_file.touch(exist_ok=True)

            try:
                with open(self._removed_file, "a", encoding="utf8") as f:
                    ctime = time.ctime()
                    # додати 10 пропусків до рядка з # Reason:
                    e_str = str(ex).replace("\n", "\n#" + " " * 10)
                    sep = "#" * 32
                    f.write(
                        f"# Запис видалено {ctime}\n"
                        f"# Трек:  {song_subject}\n"
                        f"# Причина: {e_str}\n"
                        f"\n{sep}\n\n"
                    )
            except (OSError, PermissionError, FileNotFoundError, IsADirectoryError):
                log.exception(
                    "Не вдалося зберегти інформацію про видалення URL-адреси плейлиста."
                )

            if delete_from_ap:
                log.info("Updating playlist file...")

                def _filter_replace(line: str, url: str) -> str:
                    target = line.strip()
                    if target == url:
                        return f"# Removed # {url}"
                    return line

                # зчитує оригінальний файл і оновлює рядки з URL-адресою.
                # це робиться для збереження коментарів та форматування.
                try:
                    data = self._file.read_text(encoding="utf8").split("\n")
                    data = [_filter_replace(x, song_subject) for x in data]
                    text = "\n".join(data)
                    self._file.write_text(text, encoding="utf8")
                except (OSError, PermissionError, FileNotFoundError):
                    log.exception("Не вдалося зберегти файл плейлиста:  %s", self._file)
                self._bot.filecache.remove_autoplay_cachemap_entry_by_url(song_subject)

    async def add_track(self, song_subject: str) -> None:
        """
        Додайте задану пісню до файлу автоматичного відтворення та до списку в пам'яті
        до списку у пам'яті.  Не оновлює поточну чергу автовідтворення плеєра.
        """
        if song_subject in self.data:
            log.debug("URL-адреса вже є у списку відтворення %s, ігнорування", self._file.name)
            return

        async with self._update_lock:
            # Зауважте, що це не призведе до оновлення копії списку у програвачі.
            self.data.append(song_subject)
            log.info(
                "Додавання нової URL-адреси до списку відтворення, %s: %s",
                self._file.name,
                song_subject,
            )

            try:
                # переконайтеся, що файл існує.
                if not self._file.is_file():
                    self._file.touch(exist_ok=True)

                # додати до файлу, щоб зберегти його форматування.
                with open(self._file, "r+", encoding="utf8") as fh:
                    lines = fh.readlines()
                    if not lines:
                        lines.append("# Автосписок відтворення MusicBot\n")
                    if lines[-1].endswith("\n"):
                        lines.append(f"{song_subject}\n")
                    else:
                        lines.append(f"\n{song_subject}\n")
                    fh.seek(0)
                    fh.writelines(lines)
            except (OSError, PermissionError, FileNotFoundError):
                log.exception("Не вдалося зберегти файл плейлиста:  %s", self._file)


class AutoPlaylistManager:
    """Клас менеджера, який полегшує роботу з декількома плейлистами."""

    def __init__(self, bot: "MusicBot") -> None:
        """
        Ініціалізуйте менеджер, перевіривши файлову систему на наявність придатних для використання списків відтворення.
        """
        self._bot: "MusicBot" = bot
        self._apl_dir: pathlib.Path = bot.config.auto_playlist_dir
        self._apl_file_default = self._apl_dir.joinpath(APL_FILE_DEFAULT)
        self._apl_file_history = self._apl_dir.joinpath(APL_FILE_HISTORY)
        self._apl_file_usercopy = self._apl_dir.joinpath(APL_FILE_APLCOPY)

        self._playlists: Dict[str, AutoPlaylist] = {}

        self.setup_autoplaylist()

    def setup_autoplaylist(self) -> None:
        """
        Переконайтеся, що директорії для автоматичних списків відтворення доступні, і що історичні
        файли плейлистів скопійовано.
        """
        if not self._apl_dir.is_dir():
            self._apl_dir.mkdir(parents=True, exist_ok=True)

        # Файли з попередніх версій MusicBot
        old_usercopy = pathlib.Path(OLD_DEFAULT_AUTOPLAYLIST_FILE)
        old_bundle = pathlib.Path(OLD_BUNDLED_AUTOPLAYLIST_FILE)

        # Скопіюйте або перейменуйте старі файли списку автоматичного відтворення, якщо нові файли ще не існують.
        if old_usercopy.is_file() and not self._apl_file_usercopy.is_file():
            # перейменувати старий autoplaylist.txt у новий каталог плейлистів.
            old_usercopy.rename(self._apl_file_usercopy)
        if old_bundle.is_file() and not self._apl_file_default.is_file():
            # скопіювати збірний список відтворення до типового списку відтворення зі спільним доступом.
            shutil.copy(old_bundle, self._apl_file_default)

        if (
            not self._apl_file_history.is_file()
            and self._bot.config.enable_queue_history_global
        ):
            self._apl_file_history.touch(exist_ok=True)

        self.discover_playlists()

    @property
    def _default_pl(self) -> AutoPlaylist:
        """Повертає список відтворення за замовчуванням, навіть якщо файл видалено."""
        if self._apl_file_default.stem in self._playlists:
            return self._playlists[self._apl_file_default.stem]

        self._playlists[self._apl_file_default.stem] = AutoPlaylist(
            filename=self._apl_file_default,
            bot=self._bot,
        )
        return self._playlists[self._apl_file_default.stem]

    @property
    def _usercopy_pl(self) -> Optional[AutoPlaylist]:
        """Повертає скопійований плейлист autoplaylist.txt, якщо він існує."""
        # повернути відображену копію, якщо це можливо.
        if self._apl_file_usercopy.stem in self._playlists:
            return self._playlists[self._apl_file_usercopy.stem]

        # якщо копія не зіставлена, перевірити, чи існує файл, і зіставити його.
        if self._apl_file_usercopy.is_file():
            self._playlists[self._apl_file_usercopy.stem] = AutoPlaylist(
                filename=self._apl_file_usercopy,
                bot=self._bot,
            )

        return self._playlists.get(self._apl_file_usercopy.stem, None)

    @property
    def global_history(self) -> AutoPlaylist:
        """Повертає файл глобальної історії MusicBot."""
        if self._apl_file_history.stem in self._playlists:
            return self._playlists[self._apl_file_history.stem]

        self._playlists[self._apl_file_history.stem] = AutoPlaylist(
            filename=self._apl_file_history,
            bot=self._bot,
        )
        return self._playlists[self._apl_file_history.stem]

    @property
    def playlist_names(self) -> List[str]:
        """Повертає всі знайдені назви списків відтворення."""
        return list(self._playlists.keys())

    @property
    def loaded_playlists(self) -> List[AutoPlaylist]:
        """Повертає всі завантажені об'єкти автовідтворення."""
        return [pl for pl in self._playlists.values() if pl.loaded]

    @property
    def loaded_tracks(self) -> List[str]:
        """
        Містить список усіх унікальних записів плейлиста з кожного завантаженого плейлиста.
        """
        tracks: Set[str] = set()
        for pl in self._playlists.values():
            if pl.loaded:
                tracks = tracks.union(set(pl))
        return list(tracks)

    def discover_playlists(self) -> None:
        """
        Шукати доступні файли списків відтворення, але ще не завантажувати їх до пам'яті.
        Цей метод робить списки відтворення доступними для відображення або вибору.
        """
        for pfile in self._apl_dir.iterdir():
            # обробляти тільки .txt файли
            if pfile.suffix.lower() == ".txt":
                # ігнорувати вже знайдені плейлисти.
                if pfile.stem in self._playlists:
                    continue

                pl = AutoPlaylist(pfile, self._bot)
                self._playlists[pfile.stem] = pl

    def get_default(self) -> AutoPlaylist:
        """
        Отримує відповідний список відтворення за замовчуванням на основі наявних файлів.
        """
        # Якщо було скопійовано старий файл autoplaylist.txt, використовуйте його.
        if self._usercopy_pl is not None:
            return self._usercopy_pl
        return self._default_pl

    def get_playlist(self, filename: str) -> AutoPlaylist:
        """Отримати або створити список відтворення з вказаною назвою файлу."""
        # використання pathlib .name тут запобігає атаці обходу каталогів.
        pl_file = self._apl_dir.joinpath(pathlib.Path(filename).name)

        # Повернути існуючий екземпляр, якщо він є.
        if pl_file.stem in self._playlists:
            return self._playlists[pl_file.stem]

        # інакше створіть новий екземпляр з цим ім'ям файлу
        self._playlists[pl_file.stem] = AutoPlaylist(pl_file, self._bot)
        return self._playlists[pl_file.stem]

    def playlist_exists(self, filename: str) -> bool:
        """Перевірити існування заданого файлу списку відтворення."""
        # використання pathlib .name запобігає атаці обходу каталогів.
        return self._apl_dir.joinpath(pathlib.Path(filename).name).is_file()
