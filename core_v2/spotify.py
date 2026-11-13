"""Spotify track metadata only; audio is a separately found public-source match."""
import asyncio
import configparser
from dataclasses import dataclass, field

import aiohttp

from .config import ROOT


@dataclass(repr=False)
class SpotifyMetadata:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)

    @classmethod
    def from_root(cls, root=ROOT):
        path = root / "config/options.ini"
        try:
            parser = configparser.ConfigParser(interpolation=None)
            if path.exists():
                if path.stat().st_size > 1024 * 1024:
                    raise ValueError("Oversized options")
                parser.read_string(path.read_text(encoding="utf-8-sig"))
            return cls(parser.get("Credentials", "Spotify_ClientID", fallback="").strip(),
                       parser.get("Credentials", "Spotify_ClientSecret", fallback="").strip())
        except (OSError, UnicodeError, configparser.Error, ValueError):
            raise ValueError("Перевірте формат options.ini; дані Spotify приховано.") from None

    async def search_query(self, source):
        from .music_engine import MusicError, spotify_track_id
        track_id = spotify_track_id(source)
        if not track_id:
            raise MusicError("Вкажіть посилання на один трек Spotify.")
        if not self.client_id or not self.client_secret:
            raise MusicError("Для метаданих Spotify заповніть Spotify_ClientID/Spotify_ClientSecret у options.ini. Аудіо шукатиметься на YouTube.")
        try:
            async with asyncio.timeout(15):
                async with aiohttp.ClientSession(trust_env=True, timeout=aiohttp.ClientTimeout(total=8)) as session:
                    async with session.post("https://accounts.spotify.com/api/token",
                        headers={"Authorization": aiohttp.encode_basic_auth(self.client_id, self.client_secret)},
                        data={"grant_type": "client_credentials"}, allow_redirects=False) as response:
                        token = await _json(response)
                    access = token.get("access_token")
                    if not isinstance(access, str) or not 1 <= len(access) <= 4096:
                        raise ValueError("Invalid access token")
                    async with session.get(f"https://api.spotify.com/v1/tracks/{track_id}",
                        headers={"Authorization": f"Bearer {access}"}, allow_redirects=False) as response:
                        track = await _json(response)
            name, artists = track.get("name"), track.get("artists")
            if (not isinstance(name, str) or not name.strip() or not isinstance(artists, list)
                    or not artists or len(artists) > 50):
                raise ValueError("Invalid track metadata")
            names = [artist["name"] for artist in artists[:5]
                     if isinstance(artist, dict) and isinstance(artist.get("name"), str)]
            if not names:
                raise ValueError("Missing artists")
            query = " ".join([*names, name])
            # Source validation treats colon-like input as a potential protocol.
            query = " ".join("".join(c if c.isprintable() and c not in ":/\\" else " "
                                      for c in query).split())[:250]
            if not query:
                raise ValueError("Empty query")
            return query
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError, UnicodeError):
            raise MusicError("Не вдалося отримати метадані Spotify. Перевірте credentials і доступ API; можна замовити трек за назвою.") from None


async def _json(response):
    if response.status != 200:
        raise ValueError("Provider request rejected")
    data = bytearray()
    async for chunk in response.content.iter_chunked(8192):
        data.extend(chunk)
        if len(data) > 65536:
            raise ValueError("Provider response too large")
    import json
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("Provider response is not an object")
    return value
