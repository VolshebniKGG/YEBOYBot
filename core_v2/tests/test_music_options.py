from pathlib import Path

import pytest

from core_v2.music_options import MusicError, load_autoplaylist, load_music_options


def configuration(tmp_path, body):
    (tmp_path / "config").mkdir(exist_ok=True)
    path = tmp_path / "config/options.ini"
    path.write_text(body, encoding="utf-8")
    return path


def test_real_example_and_playlist_are_read_only():
    root = Path(__file__).resolve().parents[2]
    before = (root / "config/example_options.ini").read_bytes()
    options = load_music_options(root)
    assert options.default_volume == .25
    assert options.use_autoplay and options.autoplay_auto_skip
    assert len(load_autoplaylist(root)) > 1
    assert (root / "config/example_options.ini").read_bytes() == before


def test_runtime_options_wins_and_has_no_credential_fields(tmp_path):
    configuration(tmp_path, "[MusicBot]\nDefaultVolume=.5\nUseAutoPlaylist=no\nAutoPause=no\nSelfDeafen=no\n"
                  "AutoPlaylistRandom=no\nAutoPlaylistAutoSkip=no\n[Chat]\nAutojoinChannels=123 456\n"
                  "[Credentials]\nToken=placeholder%value\nSpotify_ClientSecret=not-a-real-secret\n")
    options = load_music_options(tmp_path)
    assert options.default_volume == .5
    assert not options.use_autoplay
    assert not options.auto_pause and not options.self_deafen
    assert not options.autoplay_random and not options.autoplay_auto_skip
    assert options.autojoin_channels == (123, 456)
    assert "placeholder" not in repr(options)
    assert "not-a-real-secret" not in repr(options)


@pytest.mark.parametrize("body", [
    "[MusicBot]\nDefaultVolume=nan", "[MusicBot]\nDefaultVolume=1.01",
    "[MusicBot]\nDefaultVolume=0", "[MusicBot]\nUseAutoPlaylist=maybe",
    "[MusicBot]\nAutoPause=maybe", "[Chat]\nAutojoinChannels=-1",
    "[Chat]\nAutojoinChannels=0", "[Chat]\nAutojoinChannels=１２３",
    "[Chat]\nAutojoinChannels=123 123", "[Chat]\nAutojoinChannels=9223372036854775808",
    "[Chat]\nAutojoinChannels=" + " ".join(str(x) for x in range(1, 22)),
    "[MusicBot]\nDefaultVolume=.5\nDefaultVolume=.6", "not an ini file"
])
def test_invalid_options_fail_with_safe_error(tmp_path, body):
    path = configuration(tmp_path, body)
    original = path.read_bytes()
    with pytest.raises(MusicError, match="Некоректні"):
        load_music_options(tmp_path)
    assert path.read_bytes() == original


def test_example_fallback_missing_error_and_bom(tmp_path):
    with pytest.raises(MusicError):
        load_music_options(tmp_path)
    path = configuration(tmp_path, "\ufeff[MusicBot]\nDefaultVolume=.7")
    path.rename(path.with_name("example_options.ini"))
    assert load_music_options(tmp_path).default_volume == .7
    assert not path.exists()


def test_missing_playlist_is_empty_and_creates_nothing(tmp_path):
    assert load_autoplaylist(tmp_path) == []
    assert list(tmp_path.iterdir()) == []


def test_playlist_comments_dedup_unicode_bom_and_no_rewrite(tmp_path):
    configuration(tmp_path, "[MusicBot]")
    path = tmp_path / "config/_autoplaylist.txt"
    original = "\ufeff# comment\n ; comment\n\n пісня \nпісня\nhttps://youtu.be/id\n".encode("utf-8")
    path.write_bytes(original)
    assert load_autoplaylist(tmp_path) == ["пісня", "https://youtu.be/id"]
    assert path.read_bytes() == original


@pytest.mark.parametrize("content", [b"\xff", b"x" * 2_000_001,
                                           b"x" * 501, b"\n".join([b"a"] * 10001)],
                         ids=["invalid-utf8", "over-2MB", "long-entry", "over-10000-lines"])
def test_playlist_bounds_and_invalid_utf8_are_safe(tmp_path, content):
    configuration(tmp_path, "[MusicBot]")
    path = tmp_path / "config/_autoplaylist.txt"
    path.write_bytes(content)
    with pytest.raises(MusicError, match="авто|_autoplaylist"):
        load_autoplaylist(tmp_path)
    assert path.read_bytes() == content
