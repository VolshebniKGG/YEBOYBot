import asyncio
import json
import logging
import math
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Optional

import aiohttp
import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

# Налаштування логування: повідомлення виводяться у консоль.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("bot")

# ----------------------- Константи та налаштування -----------------------
# Поточний файл знаходиться в каталозі yeboybot, тому BASE_DIR.parent вказує на корінь проекту.
BASE_DIR: Path = Path(__file__).resolve().parent
BASE_DATA_PATH: Path = BASE_DIR.parent / "data"
DLC_PATH: Path = BASE_DIR.parent / "DLC"

XP_FOR_MESSAGE: int = 5
# Використовуються як базові множники для обчислення cumulative XP.
# Формула рівня: level = int(sqrt(xp / multiplier)) + 1
TEXT_XP_MULTIPLIER: int = 100
VOICE_XP_MULTIPLIER: int = 50
VOICE_XP_PER_MINUTE: int = 2

# ----------------------- Функції для малювання -----------------------

def draw_microphone(
    draw: ImageDraw.Draw,
    x: int,
    y: int,
    scale: float = 1.0,
    color: tuple = (255, 255, 255)
) -> None:
    r = int(10 * scale)
    h_body = int(25 * scale)
    w_body = int(6 * scale)
    head_box = [(x, y), (x + 2 * r, y + 2 * r)]
    body_box = [(x + r - w_body // 2, y + 2 * r), (x + r + w_body // 2, y + 2 * r + h_body)]
    base_box = [(x + r - w_body, y + 2 * r + h_body), (x + r + w_body, y + 2 * r + h_body + int(4 * scale))]
    draw.ellipse(head_box, fill=color)
    draw.rectangle(body_box, fill=color)
    draw.rectangle(base_box, fill=color)

def draw_bubble(
    draw: ImageDraw.Draw,
    x: int,
    y: int,
    width: int,
    height: int,
    color: tuple = (255, 255, 255)
) -> None:
    corner_radius = min(width, height) // 5
    left, top = x + corner_radius, y
    right, bottom = x + width - corner_radius, y + height
    draw.rectangle([left, top, right, bottom], fill=color)
    draw.pieslice([x, y, x + 2 * corner_radius, y + 2 * corner_radius], 180, 270, fill=color)
    draw.pieslice([x + width - 2 * corner_radius, y, x + width, y + 2 * corner_radius], 270, 360, fill=color)
    draw.pieslice([x, y + height - 2 * corner_radius, x + 2 * corner_radius, y + height], 90, 180, fill=color)
    draw.pieslice([x + width - 2 * corner_radius, y + height - 2 * corner_radius, x + width, y + height], 0, 90, fill=color)
    triangle_height = 10
    triangle_width_half = 6
    triangle_x_center = x + width // 2
    triangle_top = y + height
    triangle_points = [
        (triangle_x_center, triangle_top + triangle_height),
        (triangle_x_center - triangle_width_half, triangle_top),
        (triangle_x_center + triangle_width_half, triangle_top)
    ]
    draw.polygon(triangle_points, fill=color)

def create_microphone_icon(
    size: int = 24,
    scale: float = 1.0,
    color: tuple = (255, 255, 255)
) -> Image.Image:
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    margin = (size - 2 * int(10 * scale)) // 2
    draw_microphone(d, x=margin, y=margin // 2, scale=scale, color=color)
    return icon

def create_bubble_icon(
    width: int = 24,
    height: int = 24,
    color: tuple = (255, 255, 255)
) -> Image.Image:
    icon = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    pad = 2
    draw_bubble(d, x=pad, y=pad, width=width - 2 * pad, height=height - 2 * pad, color=color)
    return icon

def make_circle(
    image: Image.Image,
    size_with_border: int = 70,
    border_color: tuple = (255, 255, 255, 255)
) -> Optional[Image.Image]:
    if not image:
        return None
    mask = Image.new("L", image.size, 0)
    d = ImageDraw.Draw(mask)
    d.ellipse((0, 0) + image.size, fill=255)
    circle_im = Image.new("RGBA", (size_with_border, size_with_border), (0, 0, 0, 0))
    draw_border = ImageDraw.Draw(circle_im)
    draw_border.ellipse((0, 0, size_with_border, size_with_border), fill=border_color)
    offset_x = (size_with_border - image.size[0]) // 2
    offset_y = (size_with_border - image.size[1]) // 2
    circle_im.paste(image, (offset_x, offset_y), mask)
    return circle_im

# ----------------------- Cog для ранжування -----------------------

class RankCog(commands.Cog):
    """
    Cog для:
      1) Нарахування текстового XP (on_message)
      2) Нарахування голосового XP (фоновий цикл)
      3) Команди !rank із візуальним відображенням рангу користувача.
    """
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        logger.info("RankCog ініціалізовано.")
        self.voice_xp_task = self.bot.loop.create_task(self.give_voice_xp_loop())
        self.levels_lock = asyncio.Lock()
        self.data_path: Path = BASE_DATA_PATH
        self.dlc_path: Path = DLC_PATH

        # Завантаження шрифтів
        try:
            self.font_big = ImageFont.truetype(str(self.dlc_path / "font.ttf"), 18)
            self.font_med = ImageFont.truetype(str(self.dlc_path / "font.ttf"), 14)
            self.font_small = ImageFont.truetype(str(self.dlc_path / "font.ttf"), 12)
        except OSError:
            logger.error("Не знайдено шрифт (font.ttf) за шляхом: %s. Використовується стандартний шрифт.", self.dlc_path / "font.ttf")
            self.font_big = ImageFont.load_default()
            self.font_med = ImageFont.load_default()
            self.font_small = ImageFont.load_default()

        # Завантаження фонового зображення
        try:
            self.background_template = Image.open(self.dlc_path / "rank_background.png").convert("RGBA")
        except FileNotFoundError:
            logger.error("Не знайдено файл rank_background.png за шляхом: %s. Використовується резервний фон.", self.dlc_path / "rank_background.png")
            self.background_template = Image.new("RGBA", (500, 200), (30, 30, 30, 255))

    def cog_unload(self) -> None:
        if self.voice_xp_task:
            self.voice_xp_task.cancel()
            logger.info("Voice XP loop скасовано (cog_unload).")

    # ------------------ Файлові операції ------------------

    def get_guild_folder(self, guild: discord.Guild) -> Path:
        guild_folder = self.data_path / "rank" / str(guild.id)
        guild_folder.mkdir(parents=True, exist_ok=True)
        return guild_folder

    def get_levels_file(self, guild: discord.Guild) -> Path:
        return self.get_guild_folder(guild) / "levels.json"

    def _load_guild_levels(self, guild: discord.Guild) -> Dict[str, Any]:
        levels_file = self.get_levels_file(guild)
        try:
            with levels_file.open("r", encoding="utf-8") as f:
                guild_levels = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Файл рівнів не знайдено або пошкоджено для гільдії %s, створюємо новий.", guild.id)
            guild_levels = {}

        changed = False
        for user_id, data in guild_levels.items():
            if "xp" in data or "level" in data:
                if "xp" in data and "level" in data:
                    data["xp_text"] = data["xp"]
                    data["level_text"] = data["level"]
                    del data["xp"]
                    del data["level"]
                else:
                    data.setdefault("xp_text", 0)
                    data.setdefault("level_text", 1)
                changed = True
            if "xp_voice" not in data:
                data["xp_voice"] = 0
                changed = True
            if "level_voice" not in data:
                data["level_voice"] = 1
                changed = True

        if changed:
            with levels_file.open("w", encoding="utf-8") as f:
                json.dump(guild_levels, f, ensure_ascii=False, indent=4)
            logger.info("Дані рівнів для гільдії %s оновлено (міграція).", guild.id)

        return guild_levels

    def _save_guild_levels(self, guild: discord.Guild, guild_levels: Dict[str, Any]) -> None:
        levels_file = self.get_levels_file(guild)
        with levels_file.open("w", encoding="utf-8") as f:
            json.dump(guild_levels, f, ensure_ascii=False, indent=4)

    async def get_guild_levels(self, guild: discord.Guild) -> Dict[str, Any]:
        async with self.levels_lock:
            return await asyncio.to_thread(self._load_guild_levels, guild)

    async def save_guild_levels(self, guild: discord.Guild, guild_levels: Dict[str, Any]) -> None:
        async with self.levels_lock:
            await asyncio.to_thread(self._save_guild_levels, guild, guild_levels)

    def get_or_create_user_data(self, guild_levels: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        if user_id not in guild_levels:
            guild_levels[user_id] = {
                "xp_text": 0,
                "level_text": 1,
                "xp_voice": 0,
                "level_voice": 1
            }
        return guild_levels[user_id]

    # ------------------ Асинхронне завантаження зображень ------------------

    async def fetch_image(self, url: str) -> Image.Image:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP error: {resp.status} while fetching {url}")
                data = await resp.read()
                return Image.open(BytesIO(data)).convert("RGBA")

    # ------------------ Допоміжні функції для розрахунку рівня ------------------

    def calculate_level(self, xp: int, multiplier: int) -> int:
        return int(math.sqrt(xp / multiplier)) + 1

    def get_level_thresholds(self, level: int, multiplier: int) -> tuple[int, int]:
        prev_threshold = ((level - 1) ** 2) * multiplier
        next_threshold = (level ** 2) * multiplier
        return prev_threshold, next_threshold

    # ------------------ Обробка повідомлень та нарахування XP ------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return

        guild_levels = await self.get_guild_levels(message.guild)
        user_id = str(message.author.id)
        self.get_or_create_user_data(guild_levels, user_id)
        guild_levels[user_id]["xp_text"] += XP_FOR_MESSAGE
        new_level = self.calculate_level(guild_levels[user_id]["xp_text"], TEXT_XP_MULTIPLIER)
        if new_level > guild_levels[user_id]["level_text"]:
            guild_levels[user_id]["level_text"] = new_level
        await self.save_guild_levels(message.guild, guild_levels)

    async def give_voice_xp_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for guild in self.bot.guilds:
                    guild_levels = await self.get_guild_levels(guild)
                    members_in_voice = []
                    for channel in guild.voice_channels:
                        for member in channel.members:
                            if not member.bot:
                                members_in_voice.append(member)
                    for member in members_in_voice:
                        user_id = str(member.id)
                        self.get_or_create_user_data(guild_levels, user_id)
                        guild_levels[user_id]["xp_voice"] += VOICE_XP_PER_MINUTE
                        new_level_voice = self.calculate_level(guild_levels[user_id]["xp_voice"], VOICE_XP_MULTIPLIER)
                        if new_level_voice > guild_levels[user_id]["level_voice"]:
                            guild_levels[user_id]["level_voice"] = new_level_voice
                    await self.save_guild_levels(guild, guild_levels)
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                logger.info("Voice XP loop скасовано.")
                break
            except Exception as e:
                logger.error("[Voice XP loop error]: %s", e, exc_info=True)
                await asyncio.sleep(60)

    # ------------------ Команда !rank ------------------

    @commands.command(name="rank")
    async def rank_command(self, ctx: commands.Context) -> None:
        logger.info("Команда !rank викликана користувачем %s у гільдії %s", ctx.author.id, ctx.guild.id)
        guild_levels = await self.get_guild_levels(ctx.guild)
        user_id = str(ctx.author.id)
        self.get_or_create_user_data(guild_levels, user_id)
        user_data = guild_levels[user_id]

        xp_text = user_data["xp_text"]
        level_text = user_data["level_text"]
        xp_voice = user_data["xp_voice"]
        level_voice = user_data["level_voice"]

        # Обчислюємо пороги для текстового рівня
        prev_text_threshold, next_text_threshold = self.get_level_thresholds(level_text, TEXT_XP_MULTIPLIER)
        text_progress = (xp_text - prev_text_threshold) / (next_text_threshold - prev_text_threshold) if next_text_threshold > prev_text_threshold else 0
        # <--- Обмежуємо значення в діапазоні [0, 1]
        text_progress = max(0, min(text_progress, 1))

        text_ranking = sorted(guild_levels.items(), key=lambda x: x[1]["xp_text"], reverse=True)
        text_rank = next((index + 1 for index, (mid, _) in enumerate(text_ranking) if mid == user_id), 1)

        # Обчислюємо пороги для голосового рівня
        prev_voice_threshold, next_voice_threshold = self.get_level_thresholds(level_voice, VOICE_XP_MULTIPLIER)
        voice_progress = (xp_voice - prev_voice_threshold) / (next_voice_threshold - prev_voice_threshold) if next_voice_threshold > prev_voice_threshold else 0
        voice_progress = max(0, min(voice_progress, 1))  # <--- Обмежуємо значення в діапазоні [0, 1]

        voice_ranking = sorted(guild_levels.items(), key=lambda x: x[1]["xp_voice"], reverse=True)
        voice_rank = next((index + 1 for index, (mid, _) in enumerate(voice_ranking) if mid == user_id), 1)

        background = self.background_template.copy()

        try:
            avatar_url = ctx.author.avatar.url if ctx.author.avatar else ctx.author.default_avatar.url
            avatar_img = await self.fetch_image(avatar_url)
            avatar_img = avatar_img.resize((65, 65))
        except Exception as e:
            logger.error("Помилка завантаження аватара: %s", e, exc_info=True)
            await ctx.send("Помилка завантаження аватара.")
            return

        server_icon_img = None
        if ctx.guild.icon:
            try:
                server_icon_img = await self.fetch_image(ctx.guild.icon.url)
                server_icon_img = server_icon_img.resize((40, 40))
            except Exception as e:
                logger.error("Помилка завантаження іконки сервера: %s", e, exc_info=True)
                server_icon_img = None

        text_icon_img = create_bubble_icon(24, 24, color=(255, 255, 255))
        mic_icon_img = create_microphone_icon(size=24, scale=1.0, color=(255, 255, 255))
        avatar_with_border = make_circle(avatar_img, 75)
        server_icon_with_border = make_circle(server_icon_img, 50) if server_icon_img else None

        draw = ImageDraw.Draw(background)
        if avatar_with_border:
            background.paste(avatar_with_border, (15, 15), avatar_with_border)
        if server_icon_with_border:
            background.paste(server_icon_with_border, (440, 10), server_icon_with_border)
        draw.text((100, 20), f"{ctx.author.name}", font=self.font_big, fill="white")

        # 1) Блок текстового рівня
        background.paste(text_icon_img, (100, 50), text_icon_img)
        draw.text((125, 50), f"LVL {level_text}", font=self.font_med, fill="white")
        draw.text((125, 66), f"Rank: #{text_rank}", font=self.font_small, fill="white")
        draw.text((220, 66), f"Total: {xp_text}", font=self.font_small, fill="white")
        draw.text((125, 82), f"{xp_text} / {next_text_threshold}", font=self.font_small, fill="white")

        bar_tx1, bar_ty1, bar_tx2, bar_ty2 = 125, 95, 320, 108
        draw.rounded_rectangle((bar_tx1, bar_ty1, bar_tx2, bar_ty2), radius=5, fill="#505050")
        fill_twidth = bar_tx1 + int((bar_tx2 - bar_tx1) * text_progress)
        draw.rounded_rectangle((bar_tx1, bar_ty1, fill_twidth, bar_ty2), radius=5, fill="#4CAF50")

        # 2) Блок голосового рівня
        background.paste(mic_icon_img, (100, 115), mic_icon_img)
        draw.text((125, 115), f"LVL {level_voice}", font=self.font_med, fill="white")
        draw.text((125, 131), f"Rank: #{voice_rank}", font=self.font_small, fill="white")
        draw.text((220, 131), f"Total: {xp_voice}", font=self.font_small, fill="white")
        draw.text((125, 147), f"{xp_voice} / {next_voice_threshold}", font=self.font_small, fill="white")

        bar_vx1, bar_vy1, bar_vx2, bar_vy2 = 125, 160, 320, 173
        draw.rounded_rectangle((bar_vx1, bar_vy1, bar_vx2, bar_vy2), radius=5, fill="#505050")
        fill_vwidth = bar_vx1 + int((bar_vx2 - bar_vx1) * voice_progress)
        draw.rounded_rectangle((bar_vx1, bar_vy1, fill_vwidth, bar_vy2), radius=5, fill="#2196F3")

        with BytesIO() as image_binary:
            background.save(image_binary, "PNG")
            image_binary.seek(0)
            await ctx.send(file=discord.File(fp=image_binary, filename="rank.png"))

        await self.save_guild_levels(ctx.guild, guild_levels)

# Функція для підключення Cog до бота
def setup(bot: commands.Bot) -> None:
    bot.add_cog(RankCog(bot))
    logger.info("RankCog успішно завантажено.")
