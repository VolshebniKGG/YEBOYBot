"""Bounded rank-card rendering using the project's original artwork.

This module does no network or SQL I/O. Run ``render_rank_card`` in a worker
thread: Pillow decoding, font layout and PNG encoding must not block Discord's
event loop. Avatar bytes are optional and untrusted; bad images get a placeholder.
"""
from io import BytesIO
from math import isqrt
from pathlib import Path
import unicodedata
import warnings

from PIL import Image, ImageDraw, ImageFont, ImageOps


DLC = Path(__file__).resolve().parent.parent / "DLC"
SIZE = (500, 188)
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 2048 * 2048
MAX_XP = 2**63 - 1


def level_progress(xp, multiplier):
    """Return (level, previous threshold, next threshold, fractional progress)."""
    if type(xp) is not int or not 0 <= xp <= MAX_XP:
        raise ValueError("XP must be a non-negative signed 64-bit integer")
    if type(multiplier) is not int or multiplier <= 0:
        raise ValueError("The XP multiplier must be positive")
    level = isqrt(xp // multiplier) + 1
    previous = (level - 1)**2 * multiplier
    following = level**2 * multiplier
    return level, previous, following, (xp - previous) / (following - previous)


def _font(size):
    # The shipped DejaVu Sans includes Ukrainian glyphs. Resolve independently
    # of cwd, the Windows drive letter, and the OS font installation.
    return ImageFont.truetype(str(DLC / "font.ttf"), size)


def _clean(value):
    # Bound layout work and remove controls, bidi overrides and lone surrogates.
    return "".join(char for char in str(value)[:256]
                   if not unicodedata.category(char).startswith("C")).strip()


def _fit(draw, value, font, width):
    text = _clean(value)
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…" if text else ""


def _text(draw, xy, value, font, width, *, anchor=None):
    draw.text(xy, _fit(draw, value, font, width), font=font, fill="white", anchor=anchor)


def _decode(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as opened:
                if (opened.format not in {"PNG", "JPEG", "WEBP", "GIF"}
                        or opened.width <= 0 or opened.height <= 0
                        or opened.width > 2048 or opened.height > 2048
                        or opened.width * opened.height > MAX_IMAGE_PIXELS):
                    return None
                opened.seek(0)
                opened.load()
                return ImageOps.exif_transpose(opened).convert("RGBA")
    except (OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return None


def _portrait(background, guide, data, box, marker, placeholder):
    """The two coloured circles in gg.png define the avatar/server slots."""
    left, top, width = box
    image = _decode(data)
    if image is None:
        image = Image.new("RGBA", (width, width), placeholder)
    image = ImageOps.fit(image, (width, width), method=Image.Resampling.LANCZOS)
    slot = guide.crop((left, top, left + width, top + width))
    # Use gg.png only as a position/mask guide; never paste its purple/pink
    # placeholders over the real rank_background.png.
    mask = Image.new("L", (width, width), 0)
    mask.putdata([pixel[3] if sum(abs(pixel[i] - marker[i]) for i in range(3)) < 80 else 0
                  for pixel in slot.get_flattened_data()])
    alpha = image.getchannel("A")
    from PIL import ImageChops
    image.putalpha(ImageChops.multiply(alpha, mask))
    background.alpha_composite(image, (left, top))


def _bar(draw, y, xp, multiplier, rank):
    level, previous, following, progress = level_progress(xp, multiplier)
    small, medium = _font(11), _font(14)
    rank_text = "—" if rank is None else f"#{rank}"
    _text(draw, (260, y - 18), f"Ранг: {rank_text}", small, 106)
    _text(draw, (483, y - 18), f"Всього: {xp}", small, 111, anchor="ra")
    _text(draw, (203, y + 10), str(level), medium, 30, anchor="mm")
    draw.rounded_rectangle((260, y, 483, y + 20), radius=10, fill="#37393d")
    fill_width = round(223 * progress)
    if fill_width:
        draw.rounded_rectangle((260, y, 260 + fill_width, y + 20),
                               radius=min(10, fill_width / 2), fill="#8775bd")
    _text(draw, (371, y + 10), f"{xp} / {following}", small, 211, anchor="mm")


def render_rank_card(*, display_name, text_xp, voice_xp, text_rank, voice_rank,
                     avatar_bytes=None, guild_icon_bytes=None):
    """Return a 500×188 PNG with separate text and voice XP/levels/ranks.

    ``text_rank`` and ``voice_rank`` may be None for an unranked member. Totals
    must fit the database's signed 64-bit non-negative range. Invalid optional
    avatars use neutral placeholders; missing/corrupt shipped artwork is a
    deployment error and intentionally propagates to the command's safe handler.
    """
    level_progress(text_xp, 100)
    level_progress(voice_xp, 50)
    for rank in (text_rank, voice_rank):
        if rank is not None and (type(rank) is not int or not 1 <= rank <= MAX_XP):
            raise ValueError("Rank must be positive or None")
    with Image.open(DLC / "rank_background.png") as template:
        if template.size != SIZE:
            raise ValueError("Unsupported rank background size")
        background = template.convert("RGBA")
    with Image.open(DLC / "gg.png") as template:
        if template.size != SIZE:
            raise ValueError("Unsupported rank position guide size")
        guide = template.convert("RGBA")
    _portrait(background, guide, avatar_bytes, (15, 1, 126), (99, 28, 232), "#39415a")
    _portrait(background, guide, guild_icon_bytes, (183, 1, 34), (232, 28, 130), "#536074")
    draw = ImageDraw.Draw(background)
    _text(draw, (230, 10), display_name or "Учасник", _font(18), 253)
    _bar(draw, 90, text_xp, 100, text_rank)
    _bar(draw, 143, voice_xp, 50, voice_rank)
    with BytesIO() as output:
        background.save(output, "PNG")
        return output.getvalue()
