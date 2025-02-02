



import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
import os
import json
import asyncio
import logging

# Налаштування логування: повідомлення будуть виводитись у консоль.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("bot")

# -------------------------------------------------------------------
# ФУНКЦІЇ ДЛЯ МАЛЮВАННЯ МІКРОФОНА ТА "БУБЛЯШКИ" ПОВІДОМЛЕННЯ
# -------------------------------------------------------------------
def draw_microphone(draw: ImageDraw.ImageDraw, x, y, scale=1.0, color=(255, 255, 255)):
    """
    Малює умовний мікрофон (голівка-еліпс, ніжка-прямокутник, основа).
    x, y  – верхня ліва точка
    scale – масштаб
    color – (R, G, B)
    """
    r = int(10 * scale)           # радіус головної частини
    h_body = int(25 * scale)      # висота "ніжки"
    w_body = int(6 * scale)       # ширина "ніжки"

    # Голівка
    head_box = [(x, y), (x + 2 * r, y + 2 * r)]
    # Ніжка
    body_box = [
        (x + r - w_body // 2, y + 2 * r),
        (x + r + w_body // 2, y + 2 * r + h_body)
    ]
    # Основа
    base_box = [
        (x + r - w_body, y + 2 * r + h_body),
        (x + r + w_body, y + 2 * r + h_body + int(4 * scale))
    ]
    draw.ellipse(head_box, fill=color)
    draw.rectangle(body_box, fill=color)
    draw.rectangle(base_box, fill=color)

def draw_bubble(draw: ImageDraw.ImageDraw, x, y, width, height, color=(255, 255, 255)):
    """
    Малює "хмаринку повідомлення": заокруглений прямокутник + маленький трикутник унизу.
    (R, G, B)
    """
    corner_radius = min(width, height) // 5
    left, top = x + corner_radius, y
    right, bottom = x + width - corner_radius, y + height

    # Центральний прямокутник
    draw.rectangle([left, top, right, bottom], fill=color)
    # Кути (еліпси)
    draw.pieslice([x, y, x + 2 * corner_radius, y + 2 * corner_radius], 180, 270, fill=color)
    draw.pieslice([x + width - 2 * corner_radius, y, x + width, y + 2 * corner_radius], 270, 360, fill=color)
    draw.pieslice([x, y + height - 2 * corner_radius, x + 2 * corner_radius, y + height],
                  90, 180, fill=color)
    draw.pieslice([x + width - 2 * corner_radius, y + height - 2 * corner_radius, x + width, y + height],
                  0, 90, fill=color)

    # Трикутник-"хвостик"
    triangle_height = 10
    triangle_width_half = 6
    triangle_x_center = x + width // 2
    triangle_top = y + height
    triangle_points = [
        (triangle_x_center, triangle_top + triangle_height),  # нижня вершина
        (triangle_x_center - triangle_width_half, triangle_top),
        (triangle_x_center + triangle_width_half, triangle_top)
    ]
    draw.polygon(triangle_points, fill=color)

def create_microphone_icon(size=24, scale=1.0, color=(255, 255, 255)):
    """
    Створює Image (RGBA) з намальованим мікрофоном на прозорому тлі.
    size – розмір зображення (квадрат)
    scale – масштаб мікрофона
    color – колір
    """
    icon = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    # Центруємо мікрофон
    margin = (size - 2 * int(10 * scale)) // 2
    draw_microphone(d, x=margin, y=margin // 2, scale=scale, color=color)
    return icon

def create_bubble_icon(width=24, height=24, color=(255, 255, 255)):
    """
    Створює Image (RGBA) з намальованою "хмаринкою" (пухир чату) на прозорому тлі.
    width, height – розмір картинки
    color – колір
    """
    icon = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(icon)
    pad = 2
    draw_bubble(d, x=pad, y=pad, width=width - 2 * pad, height=height - 2 * pad, color=color)
    return icon

# -------------------------------------------------------------------
# COG ДЛЯ РАНГІВ
# -------------------------------------------------------------------
class RankCog(commands.Cog):
    """
    Cog для:
    1) Нарахування текстового XP (on_message)
    2) Нарахування голосового XP (фоновий цикл)
    3) Команди !rank з окремими ранками та рівнями для тексту й голосу
       (автоматична міграція старих полів 'xp', 'level' -> 'xp_text', 'level_text')
       + намальовані іконки (мікрофон та "булька чату") замість PNG.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("RankCog ініціалізовано.")
        # Запуск фонового циклу для нарахування голосового XP
        self.voice_xp_task = self.bot.loop.create_task(self.give_voice_xp_loop())

    def cog_unload(self):
        """При відключенні Cog-а зупиняємо фоновий цикл."""
        if self.voice_xp_task:
            self.voice_xp_task.cancel()
            logger.info("Voice XP loop зупинено (cog_unload).")

    # --------------------------------------------------------------------------
    # Допоміжні методи завантаження/збереження з міграцією
    # --------------------------------------------------------------------------
    def get_guild_levels(self, guild: discord.Guild) -> dict:
        base_data_path = r"E:\Discord Bot\Bot\data"
        guild_folder = os.path.join(base_data_path, "rank", str(guild.id))
        os.makedirs(guild_folder, exist_ok=True)
        levels_file = os.path.join(guild_folder, "levels.json")

        try:
            with open(levels_file, "r", encoding="utf-8") as f:
                guild_levels = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            logger.warning("Файл рівнів не знайдено або пошкоджено для гільдії %s, створюємо новий.", guild.id)
            guild_levels = {}

        changed = False

        # Міграція старих полів
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
            with open(levels_file, "w", encoding="utf-8") as f:
                json.dump(guild_levels, f, ensure_ascii=False, indent=4)
            logger.info("Дані рівнів для гільдії %s оновлено (міграція).", guild.id)

        return guild_levels

    def save_guild_levels(self, guild: discord.Guild, guild_levels: dict):
        base_data_path = r"E:\Discord Bot\Bot\data"
        guild_folder = os.path.join(base_data_path, "rank", str(guild.id))
        os.makedirs(guild_folder, exist_ok=True)
        levels_file = os.path.join(guild_folder, "levels.json")

        with open(levels_file, "w", encoding="utf-8") as f:
            json.dump(guild_levels, f, ensure_ascii=False, indent=4)

    # --------------------------------------------------------------------------
    # 1. Нарахування TEXT XP за повідомлення
    # --------------------------------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        guild_levels = self.get_guild_levels(message.guild)
        user_id = str(message.author.id)

        if user_id not in guild_levels:
            guild_levels[user_id] = {
                "xp_text": 0,
                "level_text": 1,
                "xp_voice": 0,
                "level_voice": 1
            }

        xp_for_message = 5
        guild_levels[user_id]["xp_text"] += xp_for_message

        # Перевірка підвищення рівня для тексту
        current_text_xp = guild_levels[user_id]["xp_text"]
        current_text_level = guild_levels[user_id]["level_text"]
        xp_needed_text = current_text_level * 100

        if current_text_xp >= xp_needed_text:
            guild_levels[user_id]["level_text"] += 1
            # Тут можна додати повідомлення про підвищення рівня

        self.save_guild_levels(message.guild, guild_levels)

    # --------------------------------------------------------------------------
    # 2. Фонове нарахування VOICE XP
    # --------------------------------------------------------------------------
    async def give_voice_xp_loop(self):
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            try:
                for guild in self.bot.guilds:
                    guild_levels = self.get_guild_levels(guild)

                    # Знаходимо користувачів у голосових каналах
                    members_in_voice = []
                    for channel in guild.voice_channels:
                        for member in channel.members:
                            if not member.bot:
                                members_in_voice.append(member)

                    xp_per_minute_voice = 2
                    for member in members_in_voice:
                        user_id = str(member.id)
                        if user_id not in guild_levels:
                            guild_levels[user_id] = {
                                "xp_text": 0,
                                "level_text": 1,
                                "xp_voice": 0,
                                "level_voice": 1
                            }

                        guild_levels[user_id]["xp_voice"] += xp_per_minute_voice

                        # Перевірка підвищення рівня для голосу
                        current_voice_xp = guild_levels[user_id]["xp_voice"]
                        current_voice_level = guild_levels[user_id]["level_voice"]
                        xp_needed_voice = current_voice_level * 50

                        if current_voice_xp >= xp_needed_voice:
                            guild_levels[user_id]["level_voice"] += 1
                            # Тут можна додати повідомлення про підвищення рівня

                    self.save_guild_levels(guild, guild_levels)

                await asyncio.sleep(60)
            except asyncio.CancelledError:
                logger.info("Voice XP loop скасовано.")
                break
            except Exception as e:
                logger.error("[Voice XP loop error]: %s", e)
                await asyncio.sleep(60)

    # --------------------------------------------------------------------------
    # 3. Команда !rank
    # --------------------------------------------------------------------------
    @commands.command(name="rank")
    async def rank_command(self, ctx: commands.Context):
        logger.info("Команда !rank викликана користувачем %s у гільдії %s", ctx.author.id, ctx.guild.id)
        guild_levels = self.get_guild_levels(ctx.guild)
        user_id = str(ctx.author.id)

        if user_id not in guild_levels:
            guild_levels[user_id] = {
                "xp_text": 0,
                "level_text": 1,
                "xp_voice": 0,
                "level_voice": 1
            }

        user_data = guild_levels[user_id]
        xp_text = user_data["xp_text"]
        level_text = user_data["level_text"]
        xp_voice = user_data["xp_voice"]
        level_voice = user_data["level_voice"]

        # Ліміти для підвищення рівня
        text_xp_to_next = level_text * 100
        voice_xp_to_next = level_voice * 50

        text_progress = min(xp_text / text_xp_to_next, 1.0) if text_xp_to_next else 0
        voice_progress = min(xp_voice / voice_xp_to_next, 1.0) if voice_xp_to_next else 0

        # Ранжування: текстовий
        text_ranking = sorted(guild_levels.items(), key=lambda x: x[1]["xp_text"], reverse=True)
        text_rank = 1
        for index, (mid, data) in enumerate(text_ranking):
            if mid == user_id:
                text_rank = index + 1
                break

        # Ранжування: голосовий
        voice_ranking = sorted(guild_levels.items(), key=lambda x: x[1]["xp_voice"], reverse=True)
        voice_rank = 1
        for index, (mid, data) in enumerate(voice_ranking):
            if mid == user_id:
                voice_rank = index + 1
                break

        # ----------------- Завантаження фонового зображення -----------------
        dlc_folder = r"E:\Discord Bot\Bot\DLC"
        font_path = os.path.join(dlc_folder, "font.ttf")
        base_image_path = os.path.join(dlc_folder, "rank_background.png")

        try:
            background = Image.open(base_image_path).convert("RGBA")
        except FileNotFoundError:
            logger.error("Не знайдено файл rank_background.png за шляхом: %s", base_image_path)
            await ctx.send("Не знайдено rank_background.png.")
            return

        try:
            font_big = ImageFont.truetype(font_path, 18)
            font_med = ImageFont.truetype(font_path, 14)
            font_small = ImageFont.truetype(font_path, 12)
        except OSError:
            logger.error("Не знайдено шрифт (font.ttf) за шляхом: %s", font_path)
            await ctx.send("Не знайдено шрифт (font.ttf).")
            return

        # Завантаження аватара користувача та іконки сервера
        avatar_url = ctx.author.avatar.url if ctx.author.avatar else ctx.author.default_avatar.url
        response = requests.get(avatar_url)
        avatar_img = Image.open(BytesIO(response.content)).convert("RGBA").resize((65, 65))

        server_icon_img = None
        if ctx.guild.icon:
            response = requests.get(ctx.guild.icon.url)
            server_icon_img = Image.open(BytesIO(response.content)).convert("RGBA").resize((40, 40))

        # Створення іконок за допомогою функцій
        text_icon_img = create_bubble_icon(24, 24, color=(255, 255, 255))
        mic_icon_img = create_microphone_icon(size=24, scale=1.0, color=(255, 255, 255))

        def make_circle(image, size_with_border=70, border_color=(255, 255, 255, 255)):
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

        avatar_with_border = make_circle(avatar_img, 75)
        server_icon_with_border = make_circle(server_icon_img, 50) if server_icon_img else None

        draw = ImageDraw.Draw(background)

        # Розміщення елементів на фоні (координати для зображення 500×188)
        if avatar_with_border:
            background.paste(avatar_with_border, (15, 15), avatar_with_border)
        if server_icon_with_border:
            background.paste(server_icon_with_border, (440, 10), server_icon_with_border)

        # Вивід імені користувача
        draw.text((100, 20), f"{ctx.author.name}", font=font_big, fill="white")

        # 1) Блок текстового рівня
        background.paste(text_icon_img, (100, 50), text_icon_img)
        draw.text((125, 50), f"LVL {level_text}", font=font_med, fill="white")
        draw.text((125, 66), f"Rank: #{text_rank}", font=font_small, fill="white")
        draw.text((220, 66), f"Total: {xp_text}", font=font_small, fill="white")

        draw.text((125, 82), f"{xp_text} / {text_xp_to_next}", font=font_small, fill="white")
        bar_tx1, bar_ty1, bar_tx2, bar_ty2 = 125, 95, 320, 108
        draw.rounded_rectangle((bar_tx1, bar_ty1, bar_tx2, bar_ty2), radius=5, fill="#505050")
        fill_twidth = bar_tx1 + int((bar_tx2 - bar_tx1) * text_progress)
        draw.rounded_rectangle((bar_tx1, bar_ty1, fill_twidth, bar_ty2), radius=5, fill="#4CAF50")

        # 2) Блок голосового рівня
        background.paste(mic_icon_img, (100, 115), mic_icon_img)
        draw.text((125, 115), f"LVL {level_voice}", font=font_med, fill="white")
        draw.text((125, 131), f"Rank: #{voice_rank}", font=font_small, fill="white")
        draw.text((220, 131), f"Total: {xp_voice}", font=font_small, fill="white")

        draw.text((125, 147), f"{xp_voice} / {voice_xp_to_next}", font=font_small, fill="white")
        bar_vx1, bar_vy1, bar_vx2, bar_vy2 = 125, 160, 320, 173
        draw.rounded_rectangle((bar_vx1, bar_vy1, bar_vx2, bar_vy2), radius=5, fill="#505050")
        fill_vwidth = bar_vx1 + int((bar_vx2 - bar_vx1) * voice_progress)
        draw.rounded_rectangle((bar_vx1, bar_vy1, fill_vwidth, bar_vy2), radius=5, fill="#2196F3")

        # Збереження зображення у BytesIO та відправлення у чат
        with BytesIO() as image_binary:
            background.save(image_binary, "PNG")
            image_binary.seek(0)
            await ctx.send(file=discord.File(fp=image_binary, filename="rank.png"))

        # Збереження оновлених даних рівнів
        self.save_guild_levels(ctx.guild, guild_levels)

# Функція для підключення Cog до бота (py-cord)
def setup(bot: commands.Bot):
    bot.add_cog(RankCog(bot))
    logger.info("RankCog успішно завантажено.")








