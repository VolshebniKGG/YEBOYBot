import asyncio
import base64
import logging
import re
import time
from json import JSONDecodeError
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

from .exceptions import SpotifyError

log = logging.getLogger(__name__)


# Базовий клас для об'єктів Spotify API
class SpotifyObject:
    def __init__(self, data: Dict[str, Any], origin_url: Optional[str] = None) -> None:
        self.data: Dict[str, Any] = data
        self.origin_url: str = origin_url if origin_url else self.spotify_url

    @staticmethod
    def is_type(data: Dict[str, Any], spotify_type: str) -> bool:
        return data.get("type") == spotify_type

    @staticmethod
    def is_track_data(data: Dict[str, Any]) -> bool:
        return SpotifyObject.is_type(data, "track")

    @staticmethod
    def is_playlist_data(data: Dict[str, Any]) -> bool:
        return SpotifyObject.is_type(data, "playlist")

    @staticmethod
    def is_album_data(data: Dict[str, Any]) -> bool:
        return SpotifyObject.is_type(data, "album")

    @property
    def spotify_type(self) -> str:
        return str(self.data.get("type", ""))

    @property
    def spotify_id(self) -> str:
        return str(self.data.get("id", ""))

    @property
    def spotify_url(self) -> str:
        exurls = self.data.get("external_urls", {})
        return str(exurls.get("spotify", "")) if exurls else ""

    @property
    def spotify_uri(self) -> str:
        return str(self.data.get("uri", ""))

    @property
    def name(self) -> str:
        return str(self.data.get("name", ""))

    @property
    def ytdl_type(self) -> str:
        return "url" if self.spotify_type == "track" else "playlist"

    def to_ytdl_dict(self) -> Dict[str, Any]:
        return {
            "_type": self.ytdl_type,
            "id": self.spotify_uri,
            "original_url": self.origin_url,
            "webpage_url": self.spotify_url,
            "extractor": "spotify:musicbot",
            "extractor_key": "SpotifyMusicBot",
        }


class SpotifyTrack(SpotifyObject):
    def __init__(self, track_data: Dict[str, Any], origin_url: Optional[str] = None) -> None:
        if not SpotifyObject.is_track_data(track_data):
            raise SpotifyError(f"Invalid track_data, expected type 'track', got '{track_data.get('type')}'")
        super().__init__(track_data, origin_url)

    @property
    def artist_name(self) -> str:
        artists = self.data.get("artists", [])
        return str(artists[0].get("name", "")) if artists else ""

    @property
    def artist_names(self) -> List[str]:
        return [str(artist.get("name", "")) for artist in self.data.get("artists", []) if artist.get("name")]

    def get_joined_artist_names(self, join_with: str = " ") -> str:
        return join_with.join(self.artist_names)

    def get_track_search_string(self, format_str: str = "{0} {1}", join_artists_with: str = " ") -> str:
        return format_str.format(self.get_joined_artist_names(join_artists_with), self.name)

    @property
    def duration(self) -> float:
        return float(self.data.get("duration_ms", 0)) / 1000

    @property
    def thumbnail_url(self) -> str:
        album = self.data.get("album", {})
        imgs = album.get("images", [])
        return str(imgs[0].get("url", "")) if imgs else ""

    def to_ytdl_dict(self, as_single: bool = True) -> Dict[str, Any]:
        url = self.get_track_search_string("ytsearch:{0} {1}") if as_single else self.spotify_url
        return {
            **super().to_ytdl_dict(),
            "title": self.name,
            "artists": self.artist_names,
            "url": url,
            "search_terms": self.get_track_search_string(),
            "thumbnail": self.thumbnail_url,
            "duration": self.duration,
            "playlist_count": 1,
        }


class SpotifyAlbum(SpotifyObject):
    def __init__(self, album_data: Dict[str, Any], origin_url: Optional[str] = None) -> None:
        if not SpotifyObject.is_album_data(album_data):
            raise ValueError("Invalid album_data, must be of type 'album'")
        super().__init__(album_data, origin_url)
        self._track_objects: List[SpotifyTrack] = []
        self._create_track_objects()

    def _create_track_objects(self) -> None:
        tracks_data = self.data.get("tracks", {})
        items = tracks_data.get("items")
        if not items:
            raise ValueError("Invalid album_data, missing items in tracks")
        for item in items:
            self._track_objects.append(SpotifyTrack(item))
    
    @property
    def track_objects(self) -> List[SpotifyTrack]:
        return self._track_objects

    @property
    def track_urls(self) -> List[str]:
        return [x.spotify_url for x in self.track_objects]

    @property
    def track_count(self) -> int:
        return int(self.data.get("tracks", {}).get("total", 0))

    @property
    def thumbnail_url(self) -> str:
        imgs = self.data.get("images", [])
        return str(imgs[0].get("url", "")) if imgs else ""

    def to_ytdl_dict(self) -> Dict[str, Any]:
        return {
            **super().to_ytdl_dict(),
            "title": self.name,
            "url": "",
            "thumbnail": self.thumbnail_url,
            "playlist_count": self.track_count,
            "entries": [t.to_ytdl_dict(False) for t in self.track_objects],
        }


class SpotifyPlaylist(SpotifyObject):
    def __init__(self, playlist_data: Dict[str, Any], origin_url: Optional[str] = None) -> None:
        if not SpotifyObject.is_playlist_data(playlist_data):
            raise ValueError("Invalid playlist_data, must be of type 'playlist'")
        super().__init__(playlist_data, origin_url)
        self._track_objects: List[SpotifyTrack] = []
        self._create_track_objects()

    def _create_track_objects(self) -> None:
        tracks_data = self.data.get("tracks", {})
        items = tracks_data.get("items")
        if not items:
            raise ValueError("Invalid playlist_data, missing items in tracks")
        for item in items:
            if "track" not in item:
                continue
            track_data = item.get("track")
            if track_data and track_data.get("type") == "track":
                self._track_objects.append(SpotifyTrack(track_data))
    
    @property
    def track_objects(self) -> List[SpotifyTrack]:
        return self._track_objects

    @property
    def track_urls(self) -> List[str]:
        return [x.spotify_url for x in self.track_objects]

    @property
    def track_count(self) -> int:
        return int(self.data.get("tracks", {}).get("total", 0))

    @property
    def thumbnail_url(self) -> str:
        imgs = self.data.get("images", [])
        return str(imgs[0].get("url", "")) if imgs else ""

    def to_ytdl_dict(self) -> Dict[str, Any]:
        return {
            **super().to_ytdl_dict(),
            "title": self.name,
            "url": "",
            "thumbnail": self.thumbnail_url,
            "playlist_count": self.track_count,
            "entries": [t.to_ytdl_dict(False) for t in self.track_objects],
        }


class Spotify:
    WEB_TOKEN_URL = "https://open.spotify.com/get_access_token?reason=transport&productType=web_player"
    OAUTH_TOKEN_URL = "https://accounts.spotify.com/api/token"
    API_BASE = "https://api.spotify.com/v1/"
    URL_REGEX = re.compile(r"(?:https?://)?open\.spotify\.com/", re.I)

    def __init__(
        self,
        client_id: Optional[str],
        client_secret: Optional[str],
        aiosession: aiohttp.ClientSession,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ) -> None:
        self.client_id: str = client_id or ""
        self.client_secret: str = client_secret or ""
        self.guest_mode: bool = not (client_id and client_secret)
        self.aiosession = aiosession
        self.loop = loop or asyncio.get_event_loop()
        self._token: Optional[Dict[str, Any]] = None
        self.max_token_tries = 2

    @staticmethod
    def url_to_uri(url: str) -> str:
        url = urlparse(url)._replace(query="", fragment="").geturl()
        return Spotify.URL_REGEX.sub("spotify:", url).replace("/", ":")

    @staticmethod
    def url_to_parts(url: str) -> List[str]:
        uri = Spotify.url_to_uri(url)
        return uri.split(":") if uri.startswith("spotify:") else []

    @staticmethod
    def is_url_supported(url: str) -> bool:
        parts = Spotify.url_to_parts(url)
        return bool(parts and parts[0] == "spotify" and parts[1] in ["track", "album", "playlist"] and len(parts) >= 3)

    def api_safe_url(self, url: str) -> str:
        return url.replace(self.API_BASE, "")

    async def make_api_req(self, endpoint: str) -> Dict[str, Any]:
        url = self.API_BASE + endpoint
        token = await self._get_token()
        return await self._make_get(url, headers={"Authorization": f"Bearer {token}"})

    async def _make_get(self, url: str, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        try:
            async with self.aiosession.get(url, headers=headers) as r:
                if r.status != 200:
                    raise SpotifyError(f"Response status not OK: [{r.status}] {r.reason}")
                data = await r.json()
                if not isinstance(data, dict):
                    raise SpotifyError("Response JSON did not decode to dict")
                return data
        except (aiohttp.ClientError, aiohttp.ContentTypeError, JSONDecodeError, SpotifyError) as e:
            log.exception("Failed GET request to url: %s", url)
            raise SpotifyError(f"GET request failed for URL: {url}. Reason: {str(e)}") from e

    async def _make_post(self, url: str, payload: Dict[str, str], headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        try:
            async with self.aiosession.post(url, data=payload, headers=headers) as r:
                if r.status != 200:
                    raise SpotifyError(f"Response status not OK: [{r.status}] {r.reason}")
                data = await r.json()
                if not isinstance(data, dict):
                    raise SpotifyError("Response JSON did not decode to dict")
                return data
        except (aiohttp.ClientError, aiohttp.ContentTypeError, JSONDecodeError, SpotifyError) as e:
            log.exception("Failed POST request to url: %s", url)
            raise SpotifyError(f"POST request failed for URL: {url}. Reason: {str(e)}") from e

    def _make_token_auth(self, client_id: str, client_secret: str) -> Dict[str, str]:
        auth_header = base64.b64encode(f"{client_id}:{client_secret}".encode("ascii")).decode("ascii")
        return {"Authorization": f"Basic {auth_header}"}

    def _is_token_valid(self) -> bool:
        if not self._token:
            return False
        return int(self._token["expires_at"]) - int(time.time()) > 60

    async def _get_token(self) -> str:
        if self._is_token_valid() and self._token:
            return str(self._token["access_token"])

        if self.guest_mode:
            token = await self._request_guest_token()
            if not token:
                raise SpotifyError("Failed to obtain guest token; specify client id/secret")
            try:
                self._token = {
                    "access_token": token["accessToken"],
                    "expires_at": int(token["accessTokenExpirationTimestampMs"]) // 1000,
                }
                log.debug("Created new guest token.")
            except KeyError as e:
                self._token = None
                raise SpotifyError(f"Missing key in token response: {str(e)}") from e
        else:
            token = await self._request_token()
            if token is None:
                raise SpotifyError("Requested token from Spotify but did not receive one")
            token["expires_at"] = int(time.time()) + token["expires_in"]
            self._token = token
            log.debug("Created new client token.")
        return str(self._token["access_token"])

    async def _request_token(self) -> Dict[str, Any]:
        try:
            payload = {"grant_type": "client_credentials"}
            headers = self._make_token_auth(self.client_id, self.client_secret)
            return await self._make_post(self.OAUTH_TOKEN_URL, payload=payload, headers=headers)
        except asyncio.exceptions.CancelledError as e:
            if self.max_token_tries == 0:
                raise e
            self.max_token_tries -= 1
            return await self._request_token()

    async def _request_guest_token(self) -> Dict[str, Any]:
        try:
            async with self.aiosession.get(self.WEB_TOKEN_URL) as r:
                if r.status != 200:
                    raise SpotifyError(f"Guest token request failed: [{r.status}] {r.reason}")
                data = await r.json()
                if not isinstance(data, dict):
                    raise SpotifyError("Guest token response did not decode to dict")
                return data
        except (aiohttp.ClientError, aiohttp.ContentTypeError, JSONDecodeError, SpotifyError) as e:
            log.error("Failed to get guest token: %s", str(e))
            return {}

    # ---- Додаткові методи для сумісності з music.py ----

    async def track(self, query: str) -> Dict[str, Any]:
        """
        Асинхронно отримує дані про трек за URL Spotify.
        """
        parts = Spotify.url_to_parts(query)
        if not parts or parts[1] != "track":
            raise SpotifyError("Невірний Spotify URL для треку")
        track_id = parts[-1]
        data = await self.get_track(track_id)
        return data

    async def playlist_items(
        self, query: str, offset: int = 0, additional_types: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Асинхронно отримує дані плейлиста за URL Spotify, повертаючи словник із ключами 'items', 'next' та 'total'.
        """
        parts = Spotify.url_to_parts(query)
        if not parts or parts[1] != "playlist":
            raise SpotifyError("Невірний Spotify URL для плейлиста")
        playlist_id = parts[-1]
        data = await self.get_playlist(playlist_id)
        tracks_data = data.get("tracks", {})
        items = tracks_data.get("items", [])[offset:]
        total = tracks_data.get("total", len(items))
        next_url = tracks_data.get("next")
        return {"items": items, "next": next_url, "total": total}

    async def get_track(self, track_id: str) -> Dict[str, Any]:
        """Отримує інформацію про трек за його ID."""
        return await self.make_api_req(f"tracks/{track_id}")

    async def get_album(self, album_id: str) -> Dict[str, Any]:
        """Отримує інформацію про альбом за його ID."""
        return await self.make_api_req(f"albums/{album_id}")

    async def get_playlist(self, list_id: str) -> Dict[str, Any]:
        """Отримує інформацію про плейлист за його ID."""
        return await self.make_api_req(f"playlists/{list_id}")
