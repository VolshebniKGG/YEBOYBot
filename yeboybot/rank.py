import asyncio
import json
import logging
import math
import random  # Додано для генерації випадкових чисел
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, Tuple

import aiohttp
import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

# Налаштування логування
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

TEXT_XP_MULTIPLIER: int = 100
VOICE_XP_MULTIPLIER: int = 50

# ----------------------- Допоміжні функції -----------------------
def circle_crop(image: Image.Image, size: int) -> Image.Image:
    """
    Обрізає зображення до круга з заданим розміром.
    """
    image = image.resize((size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)
    return result

def draw_centered_text(draw: ImageDraw.Draw, text: str, box: Tuple[int, int, int, int],
                         font: ImageFont.FreeTypeFont, fill: str) -> None:
    """
    Малює текст, центрований у заданому прямокутнику.
    
    :param draw: Об'єкт ImageDraw.
    :param text: Текст для відображення.
    :param box: Координати (left, top, right, bottom) області.
    :param font: Шрифт.
    :param fill: Колір тексту.
    """
    left, top, right, bottom = box
    bbox = font.getbbox(text)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = left + ((right - left) - text_width) // 2
    y = top + ((bottom - top) - text_height) // 2
    draw.text((x, y), text, font=font, fill=fill)

def draw_progress_bar(draw: ImageDraw.Draw, x1: int, y1: int, x2: int, y2: int,
                      progress: float, bg_color: str, fill_color: str,
                      font: ImageFont.FreeTypeFont, radius: int = 10,
                      inner_text: str = "") -> None:
    """
    Малює прогрес-бар із заданими координатами:
      - Малює фон бару.
      - Обчислює ширину заповнення за значенням progress (від 0 до 1).
      - Малює внутрішній текст по центру бару, якщо він заданий.
    
    :param draw: Об'єкт ImageDraw.
    :param x1, y1, x2, y2: Координати прямокутника прогрес-бару.
    :param progress: Значення прогресу від 0 до 1.
    :param bg_color: Колір фону.
    :param fill_color: Колір заповнення.
    :param font: Шрифт для внутрішнього тексту.
    :param radius: Максимальний радіус округлення.
    :param inner_text: Текст, який буде відображено по центру.
    """
    # Малюємо фон прогрес-бару
    draw.rounded_rectangle((x1, y1, x2, y2), radius=radius, fill=bg_color)
    # Обчислюємо ширину заповненої частини
    filled_width = x1 + round((x2 - x1) * progress)
    fill_radius = min(radius, (filled_width - x1) // 2) if filled_width > x1 else 0
    draw.rounded_rectangle((x1, y1, filled_width, y2), radius=fill_radius, fill=fill_color)
    # Виводимо текст по центру, якщо задано
    if inner_text:
        draw_centered_text(draw, inner_text, (x1, y1, x2, y2), font, fill="white")

# ----------------------- Cog для ранжування -----------------------
class RankCog(commands.Cog):
    """
    Cog для нарахування XP та генерації зображення з інформацією про рівень користувача.
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
            logger.error("Не знайдено шрифт (font.ttf) за шляхом: %s. Використовується стандартний шрифт.",
                         self.dlc_path / "font.ttf")
            self.font_big = ImageFont.load_default()
            self.font_med = ImageFont.load_default()
            self.font_small = ImageFont.load_default()

        # Завантаження фонового зображення
        try:
            self.background_template = Image.open(self.dlc_path / "rank_background.png").convert("RGBA")
        except FileNotFoundError:
            logger.error("Не знайдено файл rank_background.png за шляхом: %s. Використовується резервний фон.",
                         self.dlc_path / "rank_background.png")
            self.background_template = Image.new("RGBA", (500, 200), (30, 30, 30, 255))

    def cog_unload(self) -> None:
        if self.voice_xp_task:
            self.voice_xp_task.cancel()
            logger.info("Voice XP loop скасовано (cog_unload).")

    # ------------- Файлові операції -------------
    def get_guild_folder(self, guild: discord.Guild) -> Path:
        folder = self.data_path / "rank" / str(guild.id)
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def get_levels_file(self, guild: discord.Guild) -> Path:
        return self.get_guild_folder(guild) / "levels.json"

    def _load_guild_levels(self, guild: discord.Guild) -> Dict[str, Any]:
        file_path = self.get_levels_file(guild)
        try:
            with file_path.open("r", encoding="utf-8") as f:
                levels = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Файл рівнів для гільдії %s не знайдено/пошкоджено. Створюємо новий.", guild.id)
            levels = {}
        # Міграція даних, якщо потрібно
        changed = False
        for user_id, data in levels.items():
            if "xp" in data or "level" in data:
                if "xp" in data and "level" in data:
                    data["xp_text"] = data.pop("xp")
                    data["level_text"] = data.pop("level")
                else:
                    data.setdefault("xp_text", 0)
                    data.setdefault("level_text", 1)
                data.setdefault("xp_voice", 0)
                data.setdefault("level_voice", 1)
                changed = True
        if changed:
            with file_path.open("w", encoding="utf-8") as f:
                json.dump(levels, f, ensure_ascii=False, indent=4)
            logger.info("Дані рівнів для гільдії %s оновлено.", guild.id)
        return levels

    def _save_guild_levels(self, guild: discord.Guild, levels: Dict[str, Any]) -> None:
        with self.get_levels_file(guild).open("w", encoding="utf-8") as f:
            json.dump(levels, f, ensure_ascii=False, indent=4)

    async def get_guild_levels(self, guild: discord.Guild) -> Dict[str, Any]:
        async with self.levels_lock:
            return await asyncio.to_thread(self._load_guild_levels, guild)

    async def save_guild_levels(self, guild: discord.Guild, levels: Dict[str, Any]) -> None:
        async with self.levels_lock:
            await asyncio.to_thread(self._save_guild_levels, guild, levels)

    def get_or_create_user_data(self, levels: Dict[str, Any], user_id: str) -> Dict[str, Any]:
        if user_id not in levels:
            levels[user_id] = {
                "xp_text": 0,
                "level_text": 1,
                "xp_voice": 0,
                "level_voice": 1
            }
        return levels[user_id]

    # ------------- Асинхронне завантаження зображень -------------
    async def fetch_image(self, url: str) -> Image.Image:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP error: {resp.status} while fetching {url}")
                data = await resp.read()
                return Image.open(BytesIO(data)).convert("RGBA")

    # ------------- Функції розрахунку рівня -------------
    def calculate_level(self, xp: int, multiplier: int) -> int:
        return int(math.sqrt(xp / multiplier)) + 1

    def get_level_thresholds(self, level: int, multiplier: int) -> Tuple[int, int]:
        prev_threshold = ((level - 1) ** 2) * multiplier
        next_threshold = (level ** 2) * multiplier
        return prev_threshold, next_threshold

    # ------------- Обробка повідомлень та нарахування XP -------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or not message.guild:
            return
        levels = await self.get_guild_levels(message.guild)
        user_id = str(message.author.id)
        self.get_or_create_user_data(levels, user_id)
        # Нарахування випадкового XP від 1 до 5 за повідомлення
        xp_gain = random.randint(1, 5)
        levels[user_id]["xp_text"] += xp_gain
        new_level = self.calculate_level(levels[user_id]["xp_text"], TEXT_XP_MULTIPLIER)
        if new_level > levels[user_id]["level_text"]:
            levels[user_id]["level_text"] = new_level
        await self.save_guild_levels(message.guild, levels)

    async def give_voice_xp_loop(self) -> None:
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            try:
                for guild in self.bot.guilds:
                    levels = await self.get_guild_levels(guild)
                    # Отримуємо всіх учасників голосових каналів, крім ботів
                    members = [member for channel in guild.voice_channels for member in channel.members if not member.bot]
                    for member in members:
                        user_id = str(member.id)
                        self.get_or_create_user_data(levels, user_id)
                        # Нарахування випадкового XP від 1 до 10 за хвилину
                        xp_gain = random.randint(1, 10)
                        levels[user_id]["xp_voice"] += xp_gain
                        new_level_voice = self.calculate_level(levels[user_id]["xp_voice"], VOICE_XP_MULTIPLIER)
                        if new_level_voice > levels[user_id]["level_voice"]:
                            levels[user_id]["level_voice"] = new_level_voice
                    await self.save_guild_levels(guild, levels)
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                logger.info("Voice XP loop скасовано.")
                break
            except Exception as e:
                logger.error("[Voice XP loop error]: %s", e, exc_info=True)
                await asyncio.sleep(60)

    # ------------- Команда !rank -------------
    @commands.command(name="rank")
    async def rank_command(self, ctx: commands.Context) -> None:
        logger.info("Команда !rank викликана користувачем %s у гільдії %s", ctx.author.id, ctx.guild.id)
        levels = await self.get_guild_levels(ctx.guild)
        user_id = str(ctx.author.id)
        self.get_or_create_user_data(levels, user_id)
        data = levels[user_id]

        xp_text = data["xp_text"]
        level_text = data["level_text"]
        xp_voice = data["xp_voice"]
        level_voice = data["level_voice"]

        # Розрахунок порогів та прогресу для текстового рівня
        prev_text, next_text = self.get_level_thresholds(level_text, TEXT_XP_MULTIPLIER)
        text_progress = (xp_text - prev_text) / (next_text - prev_text) if next_text > prev_text else 0
        text_progress = max(0, min(text_progress, 1))
        text_rank = next((i + 1 for i, (uid, _) in enumerate(sorted(levels.items(), key=lambda x: x[1]["xp_text"], reverse=True)) if uid == user_id), 1)

        # Розрахунок порогів та прогресу для голосового рівня
        prev_voice, next_voice = self.get_level_thresholds(level_voice, VOICE_XP_MULTIPLIER)
        voice_progress = (xp_voice - prev_voice) / (next_voice - prev_voice) if next_voice > prev_voice else 0
        voice_progress = max(0, min(voice_progress, 1))
        voice_rank = next((i + 1 for i, (uid, _) in enumerate(sorted(levels.items(), key=lambda x: x[1]["xp_voice"], reverse=True)) if uid == user_id), 1)

        # Створення копії фонового зображення
        background = self.background_template.copy()

        # Завантаження аватара користувача
        try:
            avatar_url = ctx.author.avatar.url if ctx.author.avatar else ctx.author.default_avatar.url
            avatar_img = (await self.fetch_image(avatar_url)).resize((126, 126))
        except Exception as e:
            logger.error("Помилка завантаження аватара: %s", e, exc_info=True)
            await ctx.send("Помилка завантаження аватара.")
            return

        # Завантаження іконки сервера
        server_icon_img = None
        if ctx.guild.icon:
            try:
                server_icon_img = (await self.fetch_image(ctx.guild.icon.url)).resize((34, 34))
            except Exception as e:
                logger.error("Помилка завантаження іконки сервера: %s", e, exc_info=True)
                server_icon_img = None

        # Створення кругових зображень
        avatar_cropped = circle_crop(avatar_img, 126)
        server_cropped = circle_crop(server_icon_img, 34) if server_icon_img else None

        draw = ImageDraw.Draw(background)
        # Пастимо аватар і іконку сервера
        background.paste(avatar_cropped, (15, 1), avatar_cropped)
        if server_cropped:
            background.paste(server_cropped, (183, 1), server_cropped)
        # Вивід імені користувача
        draw.text((260, 10), ctx.author.name, font=self.font_big, fill="white")

        # ----- Прогрес-бар для текстового XP -----
        xp_bar_text = f"{xp_text} / {next_text}"
        draw_progress_bar(draw, 260, 100, 483, 120, text_progress, "#505050", "#4CAF50", font=self.font_small, radius=10)
        # Додаємо текст над баром
        draw.text((260, 83), f"Rank: #{text_rank}", font=self.font_small, fill="white")
        draw.text((423, 83), f"Total: {xp_text}", font=self.font_small, fill="white")
        # Додаємо рядок всередині бару
        draw_centered_text(draw, xp_bar_text, (260, 100, 483, 120), self.font_small, "white")
        # Відображення рівня (окремо)
        text_level_box = (193, 90, 213, 107)
        draw_centered_text(draw, str(level_text), text_level_box, self.font_med, "white")

        # ----- Прогрес-бар для голосового XP -----
        voice_bar_text = f"{xp_voice} / {next_voice}"
        draw_progress_bar(draw, 260, 143, 483, 163, voice_progress, "#505050", "#2196F3", font=self.font_small, radius=10)
        draw.text((260, 126), f"Rank: #{voice_rank}", font=self.font_small, fill="white")
        draw.text((423, 126), f"Total: {xp_voice}", font=self.font_small, fill="white")
        draw_centered_text(draw, voice_bar_text, (260, 143, 483, 163), self.font_small, "white")
        voice_level_box = (193, 140, 213, 157)
        draw_centered_text(draw, str(level_voice), voice_level_box, self.font_med, "white")

        with BytesIO() as output:
            background.save(output, "PNG")
            output.seek(0)
            await ctx.send(file=discord.File(fp=output, filename="rank.png"))

        await self.save_guild_levels(ctx.guild, levels)

def setup(bot: commands.Bot) -> None:
    bot.add_cog(RankCog(bot))
    logger.info("RankCog успішно завантажено.")
