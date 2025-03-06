import shutil
import textwrap
from enum import Enum


class MusicbotException(Exception):
    def __init__(self, message: str, *, expire_in: int = 0) -> None:
        super().__init__(message)
        self._message = message
        self.expire_in = expire_in

    @property
    def message(self) -> str:
        """Повертає форматоване повідомлення з додатковим форматуванням."""
        return self._message

    @property
    def message_no_format(self) -> str:
        """Повертає повідомлення без форматування."""
        return self._message


class CommandError(MusicbotException):
    """Помилка під час обробки команди."""
    pass


class ExtractionError(MusicbotException):
    """Помилка під час обробки пісні або роботи з ytdl."""
    pass


class InvalidDataError(MusicbotException):
    """Невірні дані."""
    pass


class WrongEntryTypeError(ExtractionError):
    def __init__(self, message: str, is_playlist: bool, use_url: str) -> None:
        super().__init__(message)
        self.is_playlist = is_playlist
        self.use_url = use_url


class FFmpegError(MusicbotException):
    """Помилка від FFmpeg."""
    pass


class FFmpegWarning(MusicbotException):
    """Попередження від FFmpeg (не критичне)."""
    pass


class SpotifyError(ExtractionError):
    """Помилка при роботі з API Spotify."""
    pass


class PermissionsError(CommandError):
    def __init__(self, msg: str, expire_in: int = 0) -> None:
        super().__init__(msg, expire_in=expire_in)

    @property
    def message(self) -> str:
        return "У вас немає прав для використання цієї команди.\nПричина: " + self._message


class HelpfulError(MusicbotException):
    def __init__(
        self,
        issue: str,
        solution: str,
        *,
        preface: str = "Виникла помилка:",
        footnote: str = "",
        expire_in: int = 0,
    ) -> None:
        self.issue = issue
        self.solution = solution
        self.preface = preface
        self.footnote = footnote
        self._message_fmt = "\n{preface}\n{problem}\n\n{solution}\n\n{footnote}"
        super().__init__(self.message_no_format, expire_in=expire_in)

    @property
    def message(self) -> str:
        return self._message_fmt.format(
            preface=self.preface,
            problem=self._pretty_wrap(self.issue, "  Проблема:"),
            solution=self._pretty_wrap(self.solution, "  Рішення:"),
            footnote=self.footnote,
        )

    @property
    def message_no_format(self) -> str:
        return self._message_fmt.format(
            preface=self.preface,
            problem=self._pretty_wrap(self.issue, "  Проблема:", width=-1),
            solution=self._pretty_wrap(self.solution, "  Рішення:", width=-1),
            footnote=self.footnote,
        )

    @staticmethod
    def _pretty_wrap(text: str, pretext: str, *, width: int = -1) -> str:
        if width == -1:
            pretext = pretext.rstrip() + "\n"
            width = shutil.get_terminal_size().columns
        lines = []
        for line in text.split("\n"):
            lines += textwrap.wrap(line, width=width - 5)
        lines = [("    " + line).rstrip() for line in lines]
        return pretext + "\n".join(lines)

class HelpfulWarning(HelpfulError):
    pass


class RestartCode(Enum):
    RESTART_SOFT = 0
    RESTART_FULL = 1
    RESTART_UPGRADE_ALL = 2
    RESTART_UPGRADE_PIP = 3
    RESTART_UPGRADE_GIT = 4


class Signal(Exception):
    pass


class RestartSignal(Signal):
    def __init__(self, code: RestartCode = RestartCode.RESTART_SOFT):
        self.restart_code = code

    def get_code(self) -> int:
        return self.restart_code.value

    def get_name(self) -> str:
        return self.restart_code.name


class TerminateSignal(Signal):
    def __init__(self, exit_code: int = 0):
        self.exit_code: int = exit_code
