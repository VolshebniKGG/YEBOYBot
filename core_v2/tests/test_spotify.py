"""Official metadata boundary with chunked HTTP doubles, never Spotify audio."""
import asyncio
import json

import pytest

from core_v2.music_engine import MusicError, YTDLPResolver
from core_v2.spotify import SpotifyMetadata, _json

LINK = "https://open.spotify.com/track/1234567890123456789012"


class Content:
    def __init__(self, data, *, chunk_size=7):
        self.data, self.chunk_size = data, chunk_size

    async def iter_chunked(self, maximum):
        for start in range(0, len(self.data), min(self.chunk_size, maximum)):
            await asyncio.sleep(0)
            yield self.data[start:start + min(self.chunk_size, maximum)]


class Response:
    def __init__(self, payload, status=200):
        self.status = status
        self.content = Content(json.dumps(payload).encode())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def fake_http(monkeypatch, token=None, track=None, *, status=200):
    from core_v2 import spotify
    calls = []

    class Session:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def post(self, url, **kwargs):
            calls.append(("post", url, kwargs))
            return Response(token if token is not None else {"access_token": "synthetic-access"}, status)

        def get(self, url, **kwargs):
            calls.append(("get", url, kwargs))
            return Response(track if track is not None else {
                "name": "Моя: пісня", "artists": [{"name": "Виконавець"}]})

    monkeypatch.setattr(spotify.aiohttp, "ClientSession", Session)
    return calls


@pytest.mark.asyncio
async def test_metadata_uses_only_fixed_official_endpoints_auth_and_no_redirects(monkeypatch):
    calls = fake_http(monkeypatch)
    metadata = SpotifyMetadata("synthetic-id", "synthetic-secret")
    assert await metadata.search_query(LINK) == "Виконавець Моя пісня"
    assert len(calls) == 2
    assert calls[0][1] == "https://accounts.spotify.com/api/token"
    import base64
    auth = calls[0][2]["headers"]["Authorization"]
    assert base64.b64decode(auth.removeprefix("Basic ")) == b"synthetic-id:synthetic-secret"
    assert calls[1][1] == "https://api.spotify.com/v1/tracks/1234567890123456789012"
    assert calls[1][2]["headers"]["Authorization"] == "Bearer synthetic-access"
    assert all(call[2]["allow_redirects"] is False for call in calls)
    assert "synthetic-secret" not in repr(metadata)


@pytest.mark.asyncio
async def test_missing_credentials_is_actionable_without_network(monkeypatch):
    calls = fake_http(monkeypatch)
    with pytest.raises(MusicError, match="Spotify_ClientID"):
        await SpotifyMetadata("", "").search_query(LINK)
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("token,track,status", [({}, None, 200), ({"access_token": 123}, None, 200),
    ({"access_token": "x"}, {}, 200), ({"access_token": "x"}, {"name": "n", "artists": []}, 200),
    ({"access_token": "x"}, {"name": "n", "artists": [123]}, 200), (None, None, 401), (None, None, 429)])
async def test_rejected_or_malformed_provider_response_never_exposes_credentials(monkeypatch, token, track, status):
    fake_http(monkeypatch, token, track, status=status)
    with pytest.raises(MusicError) as caught:
        await SpotifyMetadata("synthetic-private-id", "synthetic-private-secret").search_query(LINK)
    assert "synthetic-private" not in str(caught.value)


@pytest.mark.asyncio
async def test_json_collector_handles_many_small_chunks_and_limits_total_bytes():
    assert await _json(Response({"name": "a" * 1000})) == {"name": "a" * 1000}
    with pytest.raises(ValueError, match="large"):
        await _json(Response({"name": "a" * 65536}))


@pytest.mark.asyncio
async def test_spotify_query_is_not_allowed_to_inject_media_url(monkeypatch):
    class Metadata:
        async def search_query(self, source):
            return "https://www.youtube.com/watch?v=untrusted"
    resolver = YTDLPResolver(spotify=Metadata())
    with pytest.raises(MusicError, match="метаданими"):
        await resolver.resolve(LINK)


def test_credentials_loaded_from_runtime_ini_without_interpolation(tmp_path):
    (tmp_path / "config").mkdir()
    path = tmp_path / "config/options.ini"
    assert not SpotifyMetadata.from_root(tmp_path).client_id
    path.write_text("[Credentials]\nSpotify_ClientID=synthetic-id\nSpotify_ClientSecret=synthetic%secret\n", encoding="utf-8")
    metadata = SpotifyMetadata.from_root(tmp_path)
    assert metadata.client_id == "synthetic-id" and metadata.client_secret == "synthetic%secret"
    path.write_text("invalid synthetic-secret", encoding="utf-8")
    with pytest.raises(ValueError) as caught:
        SpotifyMetadata.from_root(tmp_path)
    assert "synthetic-secret" not in str(caught.value)
