"""A deliberately bounded subset of legacy options, without reading credentials."""
from dataclasses import dataclass
import configparser
from pathlib import Path


class MusicError(ValueError):
    """An operator-safe music error; never include credentials or extractor output."""


@dataclass(frozen=True)
class MusicOptions:
    default_volume: float = .25
    use_autoplay: bool = True
    autoplay_random: bool = True
    autoplay_auto_skip: bool = True
    auto_pause: bool = True
    self_deafen: bool = True
    autojoin_channels: tuple[int, ...] = ()
    max_queue: int = 50
    max_players: int = 20


def load_music_options(root: Path) -> MusicOptions:
    """Runtime options.ini wins; the tracked example is only the fallback."""
    directory = Path(root) / "config"
    path = directory / "options.ini"
    if not path.exists():
        path = directory / "example_options.ini"
    if not path.is_file() or path.stat().st_size > 256_000:
        raise MusicError("Не знайдено коректний config/options.ini або його приклад.")
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open(encoding="utf-8-sig") as stream:
            parser.read_file(stream)
        volume = parser.getfloat("MusicBot", "DefaultVolume", fallback=.25)
        if not .01 <= volume <= 1:
            raise ValueError
        channels = parser.get("Chat", "AutojoinChannels", fallback="").split()
        if len(channels) > 20 or any(not x.isascii() or not x.isdecimal() or
                                      not 0 < int(x) < 2**63 for x in channels):
            raise ValueError
        if len(set(channels)) != len(channels):
            raise ValueError
        return MusicOptions(
            default_volume=volume,
            use_autoplay=parser.getboolean("MusicBot", "UseAutoPlaylist", fallback=True),
            autoplay_random=parser.getboolean("MusicBot", "AutoPlaylistRandom", fallback=True),
            autoplay_auto_skip=parser.getboolean("MusicBot", "AutoPlaylistAutoSkip", fallback=True),
            auto_pause=parser.getboolean("MusicBot", "AutoPause", fallback=True),
            self_deafen=parser.getboolean("MusicBot", "SelfDeafen", fallback=True),
            autojoin_channels=tuple(map(int, channels)))
    except (OSError, UnicodeError, configparser.Error, ValueError):
        raise MusicError("Некоректні музичні налаштування у config/options.ini.") from None


def load_autoplaylist(root: Path) -> list[str]:
    """Read without rewriting/deleting failed user entries or making directories."""
    path = Path(root) / "config" / "_autoplaylist.txt"
    if not path.exists():
        return []
    try:
        if path.stat().st_size > 2_000_000:
            raise ValueError
        lines = [line.strip() for line in path.read_text(encoding="utf-8-sig").splitlines()
                 if line.strip() and not line.lstrip().startswith(("#", ";"))]
        if len(lines) > 10000 or any(len(line) > 500 for line in lines):
            raise ValueError
        return list(dict.fromkeys(lines))
    except (OSError, UnicodeError, ValueError):
        raise MusicError("Не вдалося прочитати обмежений config/_autoplaylist.txt.") from None
