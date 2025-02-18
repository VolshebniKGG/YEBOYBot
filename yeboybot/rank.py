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
BASE_DIR: Path = Path(__file__).resolve().parent
BASE_DATA_PATH: Path = BASE_DIR.parent / "data"
DLC_PATH: Path = BASE_DIR.parent / "DLC"

XP_FOR_MESSAGE: int = 5
TEXT_XP_MULTIPLIER: int = 100
VOICE_XP_MULTIPLIER: int = 50
VOICE_XP_PER_MINUTE: int = 2

# ----------------------- Допоміжна функція для кругового кропу -----------------------
def circle_crop(image: Image.Image, size: int) -> Image.Image:
    """
    Функція обрізає передане зображення до круга.
    Параметри:
      image: вхідне зображення (буде змінено під заданий розмір).
      size: бажаний розмір (ширина = висота) для зображення.
    Повертає:
      Зображення з прозорим фоном у формі круга.
    """
    image = image.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, size, size), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)
    return result

# ----------------------- Функції для малювання (без змін) -----------------------
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

# ----------------------- Cog для ранжування -----------------------
class RankCog(commands.Cog):
    """
    Cog відповідає за:
      1) Нарахування текстового XP (on_message)
      2) Нарахування голосового XP (фоновий цикл)
      3) Команду !rank, яка генерує зображення з інформацією про рівень користувача.
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

        # Розрахунок порогів та прогресу для текстового рівня
        prev_text_threshold, next_text_threshold = self.get_level_thresholds(level_text, TEXT_XP_MULTIPLIER)
        text_progress = (xp_text - prev_text_threshold) / (next_text_threshold - prev_text_threshold) if next_text_threshold > prev_text_threshold else 0
        text_progress = max(0, min(text_progress, 1))
        text_ranking = sorted(guild_levels.items(), key=lambda x: x[1]["xp_text"], reverse=True)
        text_rank = next((index + 1 for index, (mid, _) in enumerate(text_ranking) if mid == user_id), 1)

        # Розрахунок порогів та прогресу для голосового рівня
        prev_voice_threshold, next_voice_threshold = self.get_level_thresholds(level_voice, VOICE_XP_MULTIPLIER)
        voice_progress = (xp_voice - prev_voice_threshold) / (next_voice_threshold - prev_voice_threshold) if next_voice_threshold > prev_voice_threshold else 0
        voice_progress = max(0, min(voice_progress, 1))
        voice_ranking = sorted(guild_levels.items(), key=lambda x: x[1]["xp_voice"], reverse=True)
        voice_rank = next((index + 1 for index, (mid, _) in enumerate(voice_ranking) if mid == user_id), 1)

        background = self.background_template.copy()

        # Завантаження та підготовка аватара користувача
        try:
            avatar_url = ctx.author.avatar.url if ctx.author.avatar else ctx.author.default_avatar.url
            avatar_img = await self.fetch_image(avatar_url)
            # Зміна розміру аватара до 126x126, щоб помістити у круг з координатами:
            # Ліво: 15, Верх: 1, Право: 141, Низ: 127 (діаметр 126px)
            avatar_img = avatar_img.resize((126, 126))
        except Exception as e:
            logger.error("Помилка завантаження аватара: %s", e, exc_info=True)
            await ctx.send("Помилка завантаження аватара.")
            return

        # Завантаження та підготовка іконки сервера
        server_icon_img = None
        if ctx.guild.icon:
            try:
                server_icon_img = await self.fetch_image(ctx.guild.icon.url)
                # Зміна розміру до 34x34, щоб помістити у круг з координатами:
                # Ліво: 183, Верх: 1, Право: 217, Низ: 35 (діаметр 34px, центр (200,18))
                server_icon_img = server_icon_img.resize((34, 34))
            except Exception as e:
                logger.error("Помилка завантаження іконки сервера: %s", e, exc_info=True)
                server_icon_img = None

        # Створення кругових зображень без додаткової рамки
        avatar_cropped = circle_crop(avatar_img, 126)
        server_cropped = circle_crop(server_icon_img, 34) if server_icon_img else None

        draw = ImageDraw.Draw(background)
        # Пастимо аватар користувача за новими координатами (бокова рамка визначається bounding box)
        background.paste(avatar_cropped, (15, 1), avatar_cropped)
        # Пастимо іконку сервера за новими координатами
        if server_cropped:
            background.paste(server_cropped, (183, 1), server_cropped)

        # Вивід імені користувача (залишається без змін)
        draw.text((100, 20), f"{ctx.author.name}", font=self.font_big, fill="white")

        # ----- Блок текстового рівня -----
        # Здвигаємо напис "LVL" на 45px вліво: з (125,50) -> (80,50)
        draw.text((80, 50), f"LVL {level_text}", font=self.font_med, fill="white")
        # Малюємо горизонтальний прогрес-бар для текстового XP:
        # Бар починається в (258,105) і закінчується в (483,125) (ширина 20px)
        draw.rounded_rectangle((258, 105, 483, 125), radius=5, fill="#505050")
        filled_width = 258 + int((483 - 258) * text_progress)
        draw.rounded_rectangle((258, 105, filled_width, 125), radius=5, fill="#4CAF50")
        # Додаємо написи над баром: "Rank" зліва та "Total" справа
        draw.text((258, 85), f"Rank: #{text_rank}", font=self.font_small, fill="white")
        draw.text((423, 85), f"Total: {xp_text}", font=self.font_small, fill="white")

        # ----- Блок голосового рівня -----
        # Здвигаємо напис "LVL" для голосу на 45px вліво: з (125,115) -> (80,115)
        draw.text((80, 115), f"LVL {level_voice}", font=self.font_med, fill="white")
        # Малюємо горизонтальний прогрес-бар для голосового XP:
        # Бар починається в (258,143) і закінчується в (483,163) (ширина 20px)
        draw.rounded_rectangle((258, 143, 483, 163), radius=5, fill="#505050")
        filled_width_voice = 258 + int((483 - 258) * voice_progress)
        draw.rounded_rectangle((258, 143, filled_width_voice, 163), radius=5, fill="#2196F3")
        # Додаємо написи над баром: "Rank" зліва та "Total" справа
        draw.text((258, 133), f"Rank: #{voice_rank}", font=self.font_small, fill="white")
        draw.text((423, 133), f"Total: {xp_voice}", font=self.font_small, fill="white")

        with BytesIO() as image_binary:
            background.save(image_binary, "PNG")
            image_binary.seek(0)
            await ctx.send(file=discord.File(fp=image_binary, filename="rank.png"))

        await self.save_guild_levels(ctx.guild, guild_levels)

# Функція для підключення Cog до бота
def setup(bot: commands.Bot) -> None:
    bot.add_cog(RankCog(bot))
    logger.info("RankCog успішно завантажено.")
