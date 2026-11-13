"""Real artwork, Unicode layout and bounded untrusted image decoding."""
from io import BytesIO
import struct
from zlib import crc32

from PIL import Image, ImageDraw
import pytest

from core_v2 import rank_card as module


def png(colour, size=(160, 100)):
    with BytesIO() as output:
        Image.new("RGBA", size, colour).save(output, "PNG")
        return output.getvalue()


def render(**kwargs):
    arguments = dict(display_name="Волшебнік", text_xp=1339, voice_xp=86380,
                     text_rank=1, voice_rank=2)
    arguments.update(kwargs)
    return Image.open(BytesIO(module.render_rank_card(**arguments)))


@pytest.mark.parametrize("xp,multiplier,level,previous,following", [
    (0, 100, 1, 0, 100), (99, 100, 1, 0, 100), (100, 100, 2, 100, 400),
    (399, 100, 2, 100, 400), (400, 100, 3, 400, 900),
    (1339, 100, 4, 900, 1600), (86380, 50, 42, 84050, 88200),
    (50, 50, 2, 50, 200), (2**63 - 1, 100, 303700050, 9223371976260240100, 9223372037000250000),
])
def test_integer_level_boundaries(xp, multiplier, level, previous, following):
    result = module.level_progress(xp, multiplier)
    assert result[:3] == (level, previous, following)
    assert 0 <= result[3] < 1


@pytest.mark.parametrize("value", [-1, True, False, 1.5, "5", None, 2**63, 10**200])
def test_reject_invalid_xp(value):
    with pytest.raises(ValueError):
        render(text_xp=value)
    with pytest.raises(ValueError):
        render(voice_xp=value)


@pytest.mark.parametrize("value", [-1, 0, True, 1.5, "1", 2**63])
def test_reject_invalid_rank(value):
    with pytest.raises(ValueError):
        render(text_rank=value)


def test_real_template_and_gg_circle_slots_are_used_without_mutating_assets(tmp_path, monkeypatch):
    background_before = (module.DLC / "rank_background.png").read_bytes()
    guide_before = (module.DLC / "gg.png").read_bytes()
    monkeypatch.chdir(tmp_path)
    result = render(avatar_bytes=png("#ff0000"), guild_icon_bytes=png("#00ff00"))
    assert result.format == "PNG"
    assert result.size == (500, 188)
    assert result.getpixel((70, 60)) == (255, 0, 0, 255)
    assert result.getpixel((199, 16)) == (0, 255, 0, 255)
    with Image.open(BytesIO(background_before)) as original:
        # The avatar corners and unused background remain the shipped image.
        for coordinate in [(15, 1), (140, 126), (0, 180), (100, 170), (490, 180)]:
            assert result.getpixel(coordinate) == original.getpixel(coordinate)
    assert (module.DLC / "rank_background.png").read_bytes() == background_before
    assert (module.DLC / "gg.png").read_bytes() == guide_before


def test_transparent_avatar_preserves_template():
    result = render(avatar_bytes=png((255, 0, 0, 0)), guild_icon_bytes=png((0, 255, 0, 0)))
    with Image.open(module.DLC / "rank_background.png") as original:
        assert result.getpixel((70, 60)) == original.getpixel((70, 60))
        assert result.getpixel((199, 16)) == original.getpixel((199, 16))


@pytest.mark.parametrize("data", [None, b"", b"not a PNG", b"\x89PNG\r\n\x1a\n", bytearray(b"no"),
                                  b"x" * (module.MAX_IMAGE_BYTES + 1)],
                         ids=["missing", "empty", "invalid-png", "truncated-png", "bytearray", "over-2MiB"])
def test_invalid_or_oversized_image_has_neutral_placeholder(data):
    result = render(avatar_bytes=data, guild_icon_bytes=data)
    assert result.getpixel((70, 60)) == (57, 65, 90, 255)
    assert result.getpixel((199, 16)) == (83, 96, 116, 255)


def test_image_dimensions_checked_before_decode():
    # A tiny compressed PNG claiming huge dimensions must never be loaded.
    header = struct.pack(">IIBBBBB", 100000, 100000, 8, 6, 0, 0, 0)
    chunk = b"IHDR" + header
    oversized_header = (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", len(header))
                        + chunk + struct.pack(">I", crc32(chunk)))
    result = render(avatar_bytes=oversized_header)
    assert result.getpixel((70, 60)) == (57, 65, 90, 255)
    assert module._decode(png("red", (2049, 1))) is None


def test_animated_image_uses_first_frame_only():
    with BytesIO() as output:
        Image.new("RGB", (30, 30), "red").save(output, "GIF", save_all=True,
            append_images=[Image.new("RGB", (30, 30), "blue")], duration=20, loop=0)
        data = output.getvalue()
    assert render(avatar_bytes=data).getpixel((70, 60)) == (255, 0, 0, 255)


def test_long_unicode_and_maximum_totals_still_fit_card():
    result = render(display_name="Україна 😀 👨‍👩‍👧‍👦" * 10000, text_xp=2**63 - 1,
                    voice_xp=2**63 - 1, text_rank=2**63 - 1, voice_rank=None)
    assert result.size == (500, 188)
    assert result.getbbox() is not None
    draw = ImageDraw.Draw(result)
    fitted = module._fit(draw, "Привіт😀" * 1000, module._font(18), 253)
    assert draw.textlength(fitted, font=module._font(18)) <= 253
    assert fitted.endswith("…")
    assert module._clean("Ім'я\n\t\x00\u202e\ud800") == "Ім'я"


def test_zero_and_unranked_member_generates_card():
    result = render(text_xp=0, voice_xp=0, text_rank=None, voice_rank=None)
    assert result.getpixel((267, 105))[:3] == (55, 57, 61)


def test_bar_uses_progress_within_current_level():
    # Level 2 starts at100; 250 is halfway to400 (not250/400).
    result = render(text_xp=250)
    assert result.getpixel((280, 94))[:3] == (135, 117, 189)
    assert result.getpixel((460, 94))[:3] == (55, 57, 61)


def test_missing_artwork_is_deployment_error(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "DLC", tmp_path)
    with pytest.raises(FileNotFoundError):
        render()
