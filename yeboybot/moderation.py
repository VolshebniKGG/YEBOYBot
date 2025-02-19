

import os
import json
import re
import logging
import asyncio
from datetime import datetime, timedelta

import discord
from discord.ext import commands

# Налаштування логування
logger = logging.getLogger("bot")
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_format = logging.Formatter("%(asctime)s %(levelname)s:%(name)s: %(message)s")
console_handler.setFormatter(console_format)
logger.addHandler(console_handler)
logger.propagate = False


class Moderation(commands.Cog):
    """
    Cog для адміністративних та модераційних команд.
    Зберігаються дані (бали, попередження, дані користувачів) у JSON-файлах.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Шляхи для зберігання даних
        self.user_data_path = os.path.join("data", "user")
        os.makedirs(self.user_data_path, exist_ok=True)
        self.warns_file = os.path.join("data", "warnings.json")
        self.points_file = os.path.join("data", "points.json")
        # Якщо файли не існують – створюємо пусті структури
        if not os.path.exists(self.warns_file):
            with open(self.warns_file, "w", encoding="utf-8") as f:
                json.dump([], f, indent=4)
        if not os.path.exists(self.points_file):
            with open(self.points_file, "w", encoding="utf-8") as f:
                json.dump({}, f, indent=4)

    # =====================
    # Допоміжні методи
    # =====================

    @staticmethod
    def parse_duration(duration_str: str) -> int:
        """
        Перетворює рядок типу "1m", "1h", "1d", "1w", "1mo"/"1міс", "1y" або "1 год"
        на кількість секунд. Повертає ціле число або None, якщо формат невірний.
        """
        duration_str = duration_str.strip()
        pattern = r"^(\d+)\s*(m|h|d|w|mo|міс|y|год)$"
        match = re.match(pattern, duration_str, re.IGNORECASE)
        if not match:
            return None
        value = int(match.group(1))
        unit = match.group(2).lower()
        if unit == "m":
            return value * 60
        elif unit in ("h", "год"):
            return value * 3600
        elif unit == "d":
            return value * 86400
        elif unit == "w":
            return value * 604800
        elif unit in ("mo", "міс"):
            return value * 2592000  # 30 днів
        elif unit == "y":
            return value * 31536000
        return None

    async def delayed_unmute(self, member: discord.Member, role: discord.Role, delay: int):
        """Через delay секунд знімає роль (наприклад, Muted) з користувача, якщо вона ще є."""
        await asyncio.sleep(delay)
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Автоматичне скидання муту")
                # Очищення збережених даних користувача
                user_data = self._load_user_data(member.id)
                user_data.pop("mute_reason", None)
                self._save_user_data(member.id, user_data)
                logger.info(f"Автоматично розм'ютовано {member} після {delay} секунд.")
            except Exception as e:
                logger.error(f"Помилка при автоматичному розм'ютуванні {member}: {e}")

    async def delayed_unban(self, guild: discord.Guild, user_id: int, delay: int):
        """Через delay секунд розбанює користувача за його ID."""
        await asyncio.sleep(delay)
        try:
            user = await self.bot.fetch_user(user_id)
            await guild.unban(user, reason="Автоматичне розбанювання після строку")
            logger.info(f"Автоматично розбанено {user} після {delay} секунд.")
        except Exception as e:
            logger.error(f"Помилка при автоматичному розбані користувача {user_id}: {e}")

    async def delayed_vunmute(self, member: discord.Member, delay: int):
        """Через delay секунд знімає вимкнення мікрофону (vmute) з користувача."""
        await asyncio.sleep(delay)
        try:
            await member.edit(mute=False, reason="Автоматичне розвімкнення голосу")
            logger.info(f"Автоматично знято vmute з {member} після {delay} секунд.")
        except Exception as e:
            logger.error(f"Помилка при автоматичному знятті vmute з {member}: {e}")

    async def get_or_create_mute_role(self, guild: discord.Guild) -> discord.Role:
        """
        Повертає роль "Muted". Якщо її не існує – створює її та встановлює заборону
        на надсилання повідомлень та мовлення для всіх каналів.
        """
        mute_role = discord.utils.get(guild.roles, name="Muted")
        if not mute_role:
            mute_role = await guild.create_role(name="Muted", reason="Створення ролі для вимкнення тексту")
            for channel in guild.channels:
                try:
                    await channel.set_permissions(mute_role, send_messages=False, speak=False)
                except Exception as e:
                    logger.error(f"Не вдалося встановити дозволи для каналу {channel}: {e}")
        return mute_role

    def _get_user_file_path(self, user_id: int) -> str:
        """Повертає шлях до файлу даних користувача."""
        return os.path.join(self.user_data_path, f"{user_id}.json")

    def _load_user_data(self, user_id: int) -> dict:
        """Завантажує дані користувача з JSON-файлу."""
        file_path = self._get_user_file_path(user_id)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_user_data(self, user_id: int, data: dict) -> None:
        """Зберігає дані користувача у JSON-файл."""
        file_path = self._get_user_file_path(user_id)
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)

    def load_warnings(self) -> list:
        """Завантажує список попереджень із файлу."""
        try:
            with open(self.warns_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"load_warnings error: {e}")
            return []

    def save_warnings(self, warns: list) -> None:
        """Зберігає список попереджень у файл."""
        try:
            with open(self.warns_file, "w", encoding="utf-8") as f:
                json.dump(warns, f, indent=4)
        except Exception as e:
            logger.error(f"save_warnings error: {e}")

    def load_points(self) -> dict:
        """Завантажує бали користувачів із файлу."""
        try:
            with open(self.points_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"load_points error: {e}")
            return {}

    def save_points(self, points: dict) -> None:
        """Зберігає бали користувачів у файл."""
        try:
            with open(self.points_file, "w", encoding="utf-8") as f:
                json.dump(points, f, indent=4)
        except Exception as e:
            logger.error(f"save_points error: {e}")

    # =====================
    # Команди
    # =====================

    # 1. setnick — зміна ніка
    @commands.command(
        name="setnick", 
        help="Змінити нікнейм учасника. Використання: !setnick [користувач] (новий нік)"
    )
    @commands.has_permissions(manage_nicknames=True)
    async def setnick(self, ctx: commands.Context, member: discord.Member, *, new_nick: str):
        try:
            await member.edit(nick=new_nick, reason=f"Змінено {ctx.author}")
            embed = discord.Embed(
                title="Успішно",
                description=f"✅ Нік {member.mention} змінено на **{new_nick}**.",
                color=discord.Color.green()
            )
            embed.set_footer(text=f"Виконав: {ctx.author}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            await ctx.send(embed=embed)
            logger.info(f"{ctx.author} змінив нік {member} на {new_nick}.")
        except Exception as e:
            error_embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося змінити нікнейм.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            logger.error(f"setnick error: {e}")


    # 2. ban / заборона — бан з опціональним часом
    @commands.command(
        name="бан",
        aliases=["заборона"],
        help="Бан користувача. Використання: !бан [користувач] (час) (причина)"
    )
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, time_or_reason: str = None, *, reason: str = None):
        duration = None
        final_reason = ""
        if time_or_reason:
            duration = self.parse_duration(time_or_reason)
            if duration is not None:
                final_reason = reason or ""
            else:
                final_reason = f"{time_or_reason} {reason}" if reason else time_or_reason

        try:
            await member.ban(reason=final_reason)
        
            embed = discord.Embed(
                title="Бан виконано",
                description=f"✅ Забанено {member.mention}.",
                color=discord.Color.green()
            )
            if final_reason:
                embed.add_field(name="Причина", value=final_reason, inline=False)
            embed.set_footer(text=f"Виконав: {ctx.author}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
        
            await ctx.send(embed=embed)
            logger.info(f"{ctx.author} забанив {member} | Причина: {final_reason}")
        
            if duration:
                # Автоматичне розбанювання через duration секунд
                asyncio.create_task(self.delayed_unban(ctx.guild, member.id, duration))
        except Exception as e:
            error_embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося забанити користувача.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            logger.error(f"ban error: {e}")


    # 3. unban — розбан
    @commands.command(name="unban", help="Розбан користувача. Використання: !unban [користувач або ID]")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, *, member_identifier: str):
        try:
            bans = await ctx.guild.bans()
            target = None
            # Спроба знайти користувача за ID або іменем (без врахування регістру)
            for ban_entry in bans:
                user = ban_entry.user
                if (member_identifier.isdigit() and int(member_identifier) == user.id) or \
                (member_identifier.lower() == user.name.lower()):
                    target = user
                    break

            if target:
                await ctx.guild.unban(target, reason=f"Розбанено {ctx.author}")
                embed = discord.Embed(
                    title="Розбанено",
                    description=f"✅ Розбанено {target.mention}.",
                    color=discord.Color.green()
                )
                embed.set_footer(text=f"Виконав: {ctx.author}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
                await ctx.send(embed=embed)
                logger.info(f"{ctx.author} розбанив {target}.")
            else:
                embed = discord.Embed(
                    title="Помилка",
                    description="❌ Не знайдено такого користувача серед забанених.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Помилка",
                description="❌ Помилка при розбані користувача.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            logger.error(f"unban error: {e}")


    # 4. kick — кік
    @commands.command(name="kick", help="Вигнати учасника з сервера. Використання: !kick [користувач] (причина)")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Не вказано"):
        try:
            await member.kick(reason=reason)
            embed = discord.Embed(
                title="Учасника вигнано",
                description=f"✅ Учасника {member.mention} було вигнано з сервера.",
                color=discord.Color.green()
            )
            embed.add_field(name="Причина", value=reason, inline=False)
            embed.set_footer(text=f"Виконав: {ctx.author}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            await ctx.send(embed=embed)
            logger.info(f"{ctx.author} вигнав {member} | Причина: {reason}")
        except Exception as e:
            embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося вигнати учасника.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            logger.error(f"kick error: {e}")

    # 5. vkick — кік з голосового каналу
    @commands.command(name="vkick", help="Вигнати учасника з голосового каналу. Використання: !vkick [користувач]")
    @commands.has_permissions(kick_members=True)
    async def vkick(self, ctx: commands.Context, member: discord.Member):
        try:
            if member.voice and member.voice.channel:
                await member.move_to(None, reason="vkick: вигнання з голосового каналу")
                embed = discord.Embed(
                    title="Голосовий кік",
                    description=f"✅ {member.mention} винесено з голосового каналу.",
                    color=discord.Color.green()
                )
                embed.set_footer(text=f"Виконав: {ctx.author}", 
                                icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
                await ctx.send(embed=embed)
                logger.info(f"{ctx.author} виконав vkick для {member}")
            else:
                embed = discord.Embed(
                    title="Помилка",
                    description="❌ Користувач не перебуває у голосовому каналі.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося виконати vkick.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            logger.error(f"vkick error: {e}")

    # 6. mute — текстовий mute з опціональним часом та причиною
    @commands.command(name="mute", help="Вимкнути текст учаснику. Використання: !mute [користувач] (час) (причина)")
    @commands.has_permissions(manage_roles=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: str = None, *, reason: str = None):
        try:
            guild: discord.Guild = ctx.guild
            mute_role = await self.get_or_create_mute_role(guild)
        
            # Перевірка, чи користувач уже має mute-роль
            if mute_role in member.roles:
                embed = discord.Embed(
                    title="Помилка",
                    description=f"❌ {member.mention} вже зам'ючений.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            # Додаємо mute-роль
            await member.add_roles(mute_role, reason=reason)
        
            # Записуємо причину у дані користувача
            user_data = self._load_user_data(member.id)
            user_data["mute_reason"] = reason if reason else "Не вказано"
            self._save_user_data(member.id, user_data)
        
            # Створення повідомлення-ембеду про успіх
            embed = discord.Embed(
                title="Успішно",
                description=f"✅ {member.mention} зам'ючено.\n**Причина:** {reason if reason else 'Не вказано'}",
                color=discord.Color.green()
            )
            embed.set_footer(
                text=f"Виконав: {ctx.author}",
                icon_url=ctx.author.avatar.url if ctx.author.avatar else None
            )
            await ctx.send(embed=embed)
            logger.info(f"{ctx.author} зам'ютував {member} | Причина: {reason if reason else 'Не вказано'}")
        
            # Обробка часу (якщо вказано)
            delay = None
            if duration:
                delay = self.parse_duration(duration)
                if delay is None:
                    error_embed = discord.Embed(
                        title="Помилка",
                        description="❌ Невірний формат часу для mute.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=error_embed)
                    return
            if delay:
                asyncio.create_task(self.delayed_unmute(member, mute_role, delay))
        except Exception as e:
            error_embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося зам'ютити користувача.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            logger.error(f"mute error: {e}")

    # 7. unmute — розблокування тексту
    @commands.command(name="unmute", help="Увімкнути текст учаснику. Використання: !unmute [користувач]")
    @commands.has_permissions(manage_roles=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        try:
            guild: discord.Guild = ctx.guild
            # Отримуємо роль "Muted"
            mute_role = discord.utils.get(guild.roles, name="Muted")
            if mute_role and mute_role in member.roles:
                await member.remove_roles(mute_role, reason="Розблокування тексту")
                # Видаляємо збережену причину муту у даних користувача
                user_data = self._load_user_data(member.id)
                user_data.pop("mute_reason", None)
                self._save_user_data(member.id, user_data)
                embed = discord.Embed(
                    title="Успішно",
                    description=f"✅ {member.mention} розм'ючено.",
                    color=discord.Color.green()
                )
                embed.set_footer(
                    text=f"Виконав: {ctx.author}",
                    icon_url=ctx.author.avatar.url if ctx.author.avatar else None
                )
                await ctx.send(embed=embed)
                logger.info(f"{ctx.author} розм'ютував {member}")
            else:
                embed = discord.Embed(
                    title="Помилка",
                    description="❌ У користувача відсутня роль `Muted`.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося розм'ютити користувача.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            logger.error(f"unmute error: {e}")


    # 8. vmute — вимкнути голос (mute мікрофону)
    @commands.command(
        name="vmute",
        help="Вимкнути голос учаснику. Використання: !vmute [користувач] (час) (причина)"
    )
    @commands.has_permissions(mute_members=True)
    async def vmute(self, ctx: commands.Context, member: discord.Member, duration: str = None, *, reason: str = None):
        try:
            # Перевірка, чи користувач знаходиться у голосовому каналі
            if member.voice:
                # Вимикаємо мікрофон користувача
                await member.edit(mute=True, reason=reason)
            
                embed = discord.Embed(
                    title="Голос вимкнено",
                    description=f"✅ {member.mention} отримав vmute.\n**Причина:** {reason if reason else 'Не вказано'}",
                    color=discord.Color.orange()
                )
                embed.set_footer(
                    text=f"Виконав: {ctx.author}",
                    icon_url=ctx.author.avatar.url if ctx.author.avatar else None
                )
                await ctx.send(embed=embed)
                logger.info(f"{ctx.author} vmute {member} | Причина: {reason if reason else 'Не вказано'}")
            
                # Обробка тривалості
                delay = None
                if duration:
                    delay = self.parse_duration(duration)
                    if delay is None:
                        error_embed = discord.Embed(
                            title="Помилка",
                            description="❌ Невірний формат часу для vmute.",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=error_embed)
                        return
                if delay:
                    asyncio.create_task(self.delayed_vunmute(member, delay))
            else:
                error_embed = discord.Embed(
                    title="Помилка",
                    description="❌ Користувач не перебуває у голосовому каналі.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=error_embed)
        except Exception as e:
            error_embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося вимкнути голос користувача.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            logger.error(f"vmute error: {e}")

    # 9. unvmute — увімкнути голос
    @commands.command(
        name="unvmute",
        help="Увімкнути голос учаснику. Використання: !unvmute [користувач]"
    )
    @commands.has_permissions(mute_members=True)
    async def unvmute(self, ctx: commands.Context, member: discord.Member):
        try:
            if member.voice:
                await member.edit(mute=False, reason="Розвімкнення голосу")
                embed = discord.Embed(
                    title="Голос увімкнено",
                    description=f"✅ {member.mention} розвімкнено.",
                    color=discord.Color.green()
                )
                embed.set_footer(
                    text=f"Виконав: {ctx.author}",
                    icon_url=ctx.author.avatar.url if ctx.author.avatar else None
                )
                await ctx.send(embed=embed)
                logger.info(f"{ctx.author} зняв vmute з {member}")
            else:
                embed = discord.Embed(
                    title="Помилка",
                    description="❌ Користувач не знаходиться у голосовому каналі.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
        except Exception as e:
            embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося зняти vmute.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            logger.error(f"unvmute error: {e}")

    # 10. timeout — встановити тайм-аут (discord API 2.0)
    @commands.command(
        name="timeout", 
        help="Встановити тайм-аут учаснику. Використання: !timeout [користувач] (час) (причина)"
    )
    @commands.has_permissions(moderate_members=True)
    async def timeout(self, ctx: commands.Context, member: discord.Member, time_arg: str = None, *, reason: str = None):
        try:
            if time_arg:
                seconds = self.parse_duration(time_arg)
                if seconds is None:
                    embed = discord.Embed(
                        title="Помилка",
                        description="❌ Невірний формат часу для тайм-ауту.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
                    return
                final_reason = reason if reason else ""
            else:
                embed = discord.Embed(
                    title="Помилка",
                    description="❌ Вкажіть тривалість тайм-ауту.",
                    color=discord.Color.red()
                )
                await ctx.send(embed=embed)
                return

            until = datetime.utcnow() + timedelta(seconds=seconds)
            await member.edit(timeout=until, reason=final_reason)

            embed = discord.Embed(
                title="Тайм-аут встановлено",
                description=f"✅ {member.mention} отримав тайм-аут на {time_arg}.",
                color=discord.Color.green()
            )
            if final_reason:
                embed.add_field(name="Причина", value=final_reason, inline=False)
            embed.set_footer(
                text=f"Виконав: {ctx.author}",
                icon_url=ctx.author.avatar.url if ctx.author.avatar else None
            )
            await ctx.send(embed=embed)
            logger.info(f"{ctx.author} встановив тайм-аут для {member} на {time_arg} | Причина: {final_reason}")

        except Exception as e:
            embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося встановити тайм-аут.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            logger.error(f"timeout error: {e}")

    # 11. untimeout — скидання тайм-ауту
    @commands.command(
        name="untimeout", 
        help="Прибрати тайм-аут у учасника. Використання: !untimeout [користувач]"
    )
    @commands.has_permissions(moderate_members=True)
    async def untimeout(self, ctx: commands.Context, member: discord.Member):
        try:
            await member.edit(timeout=None, reason="Скидання тайм-ауту")
            embed = discord.Embed(
                title="Тайм-аут скинуто",
                description=f"✅ Тайм-аут у {member.mention} скинуто.",
                color=discord.Color.green()
            )
            embed.set_footer(
                text=f"Виконав: {ctx.author}",
                icon_url=ctx.author.avatar.url if ctx.author.avatar else None
            )
            await ctx.send(embed=embed)
            logger.info(f"{ctx.author} скинув тайм-аут у {member}")
        except Exception as e:
            embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося скинути тайм-аут.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            logger.error(f"untimeout error: {e}")

    # 12. clear — очищення повідомлень
    @commands.command(
        name="clear", 
        help=(
            "Очищення повідомлень. Приклади: !clear, !clear 1000, !clear bots 10, "
            "!clear @User 10"
        )
    )
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx: commands.Context, *args):
        try:
            # За замовчуванням видаляємо 100 повідомлень
            limit = 100
            check = None

            if not args:
                pass  # Без фільтрації, використовується обмеження за замовчуванням
            elif args[0].lower() == "bots":
                if len(args) > 1 and args[1].isdigit():
                    limit = int(args[1])
                check = lambda m: m.author.bot
            else:
                member = None
                try:
                    member = await commands.MemberConverter().convert(ctx, args[0])
                except Exception:
                    member = None
                if member:
                    if len(args) > 1 and args[1].isdigit():
                        limit = int(args[1])
                    check = lambda m: m.author.id == member.id
                elif args[0].isdigit():
                    limit = int(args[0])
            if check is None:
                check = lambda m: True

            deleted = await ctx.channel.purge(limit=limit, check=check)
            embed = discord.Embed(
                title="Очищення повідомлень",
                description=f"✅ Видалено **{len(deleted)}** повідомлень.",
                color=discord.Color.green()
            )
            embed.set_footer(
                text=f"Команда виконана: {ctx.author}",
                icon_url=ctx.author.avatar.url if ctx.author.avatar else None
            )
            confirmation = await ctx.send(embed=embed)
            logger.info(f"{ctx.author} очистив {len(deleted)} повідомлень у {ctx.channel}.")
            await asyncio.sleep(5)
            await confirmation.delete()
        except Exception as e:
            embed = discord.Embed(
                title="Помилка",
                description="❌ Помилка при очищенні повідомлень.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            logger.error(f"clear error: {e}")

    # 13. move — перемістити учасника в голосовий канал
    @commands.command(
        name="move",
        help="Перемістити учасника. Використання: !move [користувач]/all (цільовий канал)"
    )
    @commands.has_permissions(move_members=True)
    async def move(self, ctx: commands.Context, target: str, *, destination: str = None):
        try:
            if target.lower() == "all":
                if ctx.author.voice and destination:
                    dest_channel = discord.utils.find(
                        lambda c: c.name.lower() == destination.lower(),
                        ctx.guild.voice_channels
                    )
                    if not dest_channel:
                        embed = discord.Embed(
                            title="Помилка",
                            description="❌ Не знайдено канал з такою назвою.",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed)
                        return

                    moved = 0
                    # Переміщуємо всіх учасників з каналу автора
                    for member in ctx.author.voice.channel.members:
                        await member.move_to(dest_channel)
                        moved += 1

                    embed = discord.Embed(
                        title="Успіх",
                        description=f"✅ Переміщено **{moved}** учасників у {dest_channel.mention}.",
                        color=discord.Color.green()
                    )
                    await ctx.send(embed=embed)
                else:
                    embed = discord.Embed(
                        title="Помилка",
                        description="❌ Для команди 'all' необхідно, щоб ви були у голосовому каналі та вказали цільовий канал.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed)
            else:
                # Конвертуємо зазначеного користувача
                member = await commands.MemberConverter().convert(ctx, target)
                if destination:
                    dest_channel = discord.utils.find(
                        lambda c: c.name.lower() == destination.lower(),
                        ctx.guild.voice_channels
                    )
                    if not dest_channel:
                        embed = discord.Embed(
                            title="Помилка",
                            description="❌ Не знайдено канал з такою назвою.",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed)
                        return
                else:
                    if ctx.author.voice:
                        dest_channel = ctx.author.voice.channel
                    else:
                        embed = discord.Embed(
                            title="Помилка",
                            description="❌ Вкажіть цільовий голосовий канал.",
                            color=discord.Color.red()
                        )
                        await ctx.send(embed=embed)
                        return

                await member.move_to(dest_channel)
                embed = discord.Embed(
                    title="Успіх",
                    description=f"✅ {member.mention} переміщено у {dest_channel.mention}.",
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed)
                logger.info(f"{ctx.author} перемістив {member} у {dest_channel}.")
        except Exception as e:
            embed = discord.Embed(
                title="Помилка",
                description="❌ Помилка при переміщенні учасника.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            logger.error(f"move error: {e}")

    # 14. роль — видати/зняти роль(і)
    @commands.command(
        name="роль",
        help="Видати або зняти роль(і). Приклади: !роль @User Адміністратор, !роль усі учасники, !роль боти Система"
    )
    @commands.has_permissions(manage_roles=True)
    async def роль(self, ctx: commands.Context, target: str, *, roles_str: str):
        try:
            # Визначаємо цільових учасників
            lower_target = target.lower()
            if lower_target in ["усі", "боти", "люди"]:
                if lower_target == "боти":
                    targets = [m for m in ctx.guild.members if m.bot]
                elif lower_target == "люди":
                    targets = [m for m in ctx.guild.members if not m.bot]
                else:
                    targets = ctx.guild.members
            else:
                member = await commands.MemberConverter().convert(ctx, target)
                targets = [member]

            # Отримуємо список назв ролей із введеного рядка
            roles_names = [r.strip() for r in re.split(r",|\s+", roles_str) if r.strip()]
            changes = []

            for role_name in roles_names:
                action = "add"  # за замовчуванням додаємо роль
                # Якщо роль починається з певного символу, визначаємо дію
                if role_name[0] in "+-!":
                    if role_name[0] == "-":
                        action = "remove"
                    elif role_name[0] == "!":
                        action = "toggle"
                    # Якщо символ "+", залишаємо "add"
                    role_name = role_name[1:].strip()

                # Шукаємо роль за ім'ям
                role = discord.utils.get(ctx.guild.roles, name=role_name)
                if not role:
                    embed_not_found = discord.Embed(
                        title="Помилка",
                        description=f"❌ Роль **{role_name}** не знайдена.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed_not_found)
                    continue

                # Застосовуємо дію до кожного цільового учасника
                for member in targets:
                    if action == "add" and role not in member.roles:
                        await member.add_roles(role, reason=f"{ctx.author} через команду роль")
                        changes.append(f"додано **{role.name}** для **{member.display_name}**")
                    elif action == "remove" and role in member.roles:
                        await member.remove_roles(role, reason=f"{ctx.author} через команду роль")
                        changes.append(f"знято **{role.name}** з **{member.display_name}**")
                    elif action == "toggle":
                        if role in member.roles:
                            await member.remove_roles(role, reason=f"{ctx.author} через команду роль (toggle)")
                            changes.append(f"знято **{role.name}** з **{member.display_name}**")
                        else:
                            await member.add_roles(role, reason=f"{ctx.author} через команду роль (toggle)")
                            changes.append(f"додано **{role.name}** для **{member.display_name}**")

            # Відправляємо результат у вигляді embed
            if changes:
                embed_success = discord.Embed(
                    title="Успіх",
                    description="✅ Зміни ролей:\n" + "\n".join(changes),
                    color=discord.Color.green()
                )
                await ctx.send(embed=embed_success)
            else:
                embed_info = discord.Embed(
                    title="Інформація",
                    description="ℹ️ Ніяких змін не виконано.",
                    color=discord.Color.blue()
                )
                await ctx.send(embed=embed_info)
        except Exception as e:
            embed_error = discord.Embed(
                title="Помилка",
                description="❌ Помилка при зміні ролей.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed_error)
            logger.error(f"роль error: {e}")

    # 15. points (балів) — керування балами
    @commands.command(name="points", help="Керування балами. Приклади: !points, !points @User +1, !points reset, !points Адмін")
    @commands.has_permissions(administrator=True)
    async def points(self, ctx: commands.Context, *args):
        try:
            pts = self.load_points()
            if not args:
                if not pts:
                    await ctx.send("ℹ️ Балів ще не встановлено.")
                    return
                msg = "Бал:\n"
                for uid, point in pts.items():
                    user = ctx.guild.get_member(int(uid))
                    name = user.display_name if user else uid
                    msg += f"{name}: {point}\n"
                await ctx.send(msg)
            else:
                arg0 = args[0].lower()
                if arg0 == "reset":
                    pts = {}
                    self.save_points(pts)
                    await ctx.send("✅ Всі бали скинуто.")
                else:
                    try:
                        member = await commands.MemberConverter().convert(ctx, args[0])
                        uid = str(member.id)
                        if len(args) > 1:
                            try:
                                delta = int(args[1])
                                pts[uid] = delta
                            except ValueError:
                                if args[1][0] in "+-":
                                    change = int(args[1])
                                    pts[uid] = pts.get(uid, 0) + change
                                else:
                                    pts[uid] = 0
                            self.save_points(pts)
                            await ctx.send(f"✅ Бал {member.display_name} тепер: {pts.get(uid, 0)}")
                        else:
                            await ctx.send(f"ℹ️ Бал {member.display_name}: {pts.get(uid, 0)}")
                    except Exception:
                        role = discord.utils.get(ctx.guild.roles, name=args[0])
                        if role:
                            msg = f"Бал для учасників з роллю {role.name}:\n"
                            for member in ctx.guild.members:
                                if role in member.roles:
                                    uid = str(member.id)
                                    msg += f"{member.display_name}: {pts.get(uid, 0)}\n"
                            await ctx.send(msg)
                        else:
                            await ctx.send("❌ Не вдалося розпізнати цільову сутність.")
        except Exception as e:
            await ctx.send("❌ Помилка при керуванні балами.")
            logger.error(f"points error: {e}")

    # 16. warn — попередити користувача
    @commands.command(
        name="warn",
        help="Попередити користувача. Використання: !warn [користувач] [причина]"
    )
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "Немає причини"):
        try:
            # Оновлення глобального списку попереджень
            warns = self.load_warnings()  # Має повертати список словників з попередженнями
            warn_id = int(datetime.utcnow().timestamp())
            warn_entry = {
                "id": warn_id,
                "user_id": member.id,
                "mod_id": ctx.author.id,
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat()
            }
            warns.append(warn_entry)
            self.save_warnings(warns)

            # Оновлення даних користувача (кількість попереджень та причини)
            user_data = self._load_user_data(member.id)
            user_data["warnings"] = user_data.get("warnings", 0) + 1
            user_data.setdefault("reasons", []).append(reason)
            self._save_user_data(member.id, user_data)

            # Формування embed-повідомлення
            embed = discord.Embed(
                title="Попередження",
                description=f"{member.mention} було попереджено.",
                color=discord.Color.orange()
            )
            embed.add_field(name="warnID", value=str(warn_id), inline=True)
            embed.add_field(name="Причина", value=reason, inline=False)
            embed.add_field(name="Загальна кількість попереджень", value=str(user_data["warnings"]), inline=True)
            embed.set_footer(
                text=f"Попередив {ctx.author}",
                icon_url=ctx.author.avatar.url if ctx.author.avatar else None
            )

            await ctx.send(embed=embed)
            logger.info(f"{ctx.author} попередив {member} | warnID: {warn_id} | Причина: {reason}")
        except Exception as e:
            error_embed = discord.Embed(
                title="Помилка",
                description="❌ Помилка при видачі попередження.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            logger.error(f"warn error: {e}")

    # 17. removewarn — видалити попередження
    @commands.command(
        name="removewarn",
        help="Видалити попередження. Використання: !removewarn [warnID] / [користувач] / все"
    )
    @commands.has_permissions(manage_messages=True)
    async def removewarn(self, ctx: commands.Context, target: str):
        try:
            warns = self.load_warnings()  # Повинен повертати список словників із попередженнями
            embed = discord.Embed(color=discord.Color.green())
            
            if target.lower() in ["all", "все"]:
                warns = []
                self.save_warnings(warns)
                embed.title = "Успішно"
                embed.description = "✅ Всі попередження видалено."
                await ctx.send(embed=embed)
            else:
                # Якщо задано warnID
                try:
                    warn_id = int(target)
                    new_warns = [w for w in warns if w["id"] != warn_id]
                    if len(new_warns) == len(warns):
                        embed.color = discord.Color.red()
                        embed.title = "Помилка"
                        embed.description = "❌ Попередження з таким ID не знайдено."
                        await ctx.send(embed=embed)
                    else:
                        self.save_warnings(new_warns)
                        embed.title = "Успішно"
                        embed.description = f"✅ Попередження {warn_id} видалено."
                        await ctx.send(embed=embed)
                except ValueError:
                    # Якщо задано ім'я/ID користувача
                    member = await commands.MemberConverter().convert(ctx, target)
                    new_warns = [w for w in warns if w["user_id"] != member.id]
                    self.save_warnings(new_warns)
                    embed.title = "Успішно"
                    embed.description = f"✅ Попередження для {member.mention} видалено."
                    await ctx.send(embed=embed)
        except Exception as e:
            error_embed = discord.Embed(
                title="Помилка",
                description="❌ Помилка при видаленні попередження.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            logger.error(f"removewarn error: {e}")


    # 17b. clear_warnings — очистити всі попередження користувача
    @commands.command(
        name="clear_warnings",
        help="Очищає всі попередження користувача. Використання: !clear_warnings [користувач]"
    )
    @commands.has_permissions(manage_messages=True)
    async def clear_warnings(self, ctx: commands.Context, member: discord.Member):
        try:
            logger.info("Команда clear_warnings викликана %s для користувача %s", ctx.author, member)
            user_data = self._load_user_data(member.id)
            embed = discord.Embed()
            if user_data.get("warnings", 0) > 0:
                user_data["warnings"] = 0
                user_data["reasons"] = []
                self._save_user_data(member.id, user_data)
                embed.title = "Успішно"
                embed.description = f"✅ Усі попередження для {member.mention} очищено."
                embed.color = discord.Color.green()
                await ctx.send(embed=embed)
            else:
                embed.title = "Інформація"
                embed.description = f"❌ У {member.mention} немає жодного попередження."
                embed.color = discord.Color.orange()
                await ctx.send(embed=embed)
        except Exception as e:
            error_embed = discord.Embed(
                title="Помилка",
                description="❌ Помилка при очищенні попереджень.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            logger.error(f"clear_warnings error: {e}")

    # 18. warnings — показати список warnings
    @commands.command(
        name="warnings", 
        help="Показати список warnings. Використання: !warnings [користувач]"
    )
    async def warnings(self, ctx: commands.Context, member: discord.Member = None):
        try:
            warns = self.load_warnings()  # Завантажує warnings (повинен повертати список словників)
            embed = discord.Embed(color=discord.Color.blurple())
            
            if member:
                user_warns = [w for w in warns if w["user_id"] == member.id]
                if not user_warns:
                    embed.title = "Інформація"
                    embed.description = f"ℹ️ Для {member.mention} warnings немає."
                    return await ctx.send(embed=embed)
                
                embed.title = f"Warnings для {member.display_name}"
                for w in user_warns:
                    field_name = f"ID {w['id']}"
                    field_value = f"**Причина:** {w['reason']}\n**Модератор:** <@{w['mod_id']}>"
                    embed.add_field(name=field_name, value=field_value, inline=False)
                await ctx.send(embed=embed)
            else:
                if not warns:
                    embed.title = "Інформація"
                    embed.description = "ℹ️ warnings немає."
                    return await ctx.send(embed=embed)
                
                embed.title = "Список warnings"
                for w in warns:
                    member_obj = ctx.guild.get_member(w["user_id"])
                    user_name = member_obj.display_name if member_obj else str(w["user_id"])
                    field_name = f"ID {w['id']} для {user_name}"
                    field_value = f"**Причина:** {w['reason']}\n**Модератор:** <@{w['mod_id']}>"
                    embed.add_field(name=field_name, value=field_value, inline=False)
                await ctx.send(embed=embed)
        except Exception as e:
            error_embed = discord.Embed(
                title="Помилка",
                description="❌ Помилка при виведенні warnings.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            logger.error(f"warnings error: {e}")

    # 19. lock — заблокувати канал (@everyone не може писати)
    @commands.command(
        name="lock",
        help="Заблокувати канал. Використання: !lock [канал або ID] [причина]"
    )
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context, channel: str = None, *, reason: str = None):
        try:
            # Якщо канал не заданий, використовуємо поточний канал
            if channel is None:
                text_channel = ctx.channel
            else:
                # Якщо заданий параметр є числом, вважати, що це ID каналу
                if channel.isdigit():
                    text_channel = ctx.guild.get_channel(int(channel))
                else:
                    # Спробувати конвертувати згадку або ім'я каналу в discord.TextChannel
                    converter = commands.TextChannelConverter()
                    text_channel = await converter.convert(ctx, channel)
            
            if not isinstance(text_channel, discord.TextChannel):
                raise commands.CommandError("Невірний тип каналу. Повинно бути текстовим каналом.")
            
            # Отримуємо поточні налаштування прав для ролі @everyone
            overwrite = text_channel.overwrites_for(ctx.guild.default_role)
            # Забороняємо надсилання повідомлень для @everyone
            overwrite.send_messages = False
            await text_channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=reason)
            
            embed = discord.Embed(
                title="Канал заблоковано",
                description=f"Канал {text_channel.mention} успішно заблоковано для @everyone.",
                color=discord.Color.green()
            )
            if reason:
                embed.add_field(name="Причина", value=reason, inline=False)
            await ctx.send(embed=embed)
            logger.info(f"{ctx.author} заблокував {text_channel} | Причина: {reason}")
        except Exception as e:
            error_embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося заблокувати канал.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            logger.error(f"lock error: {e}")

            # Заборонити @everyone надсилати повідомлення
            overwrite = text_channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = False
            await text_channel.set_permissions(
                ctx.guild.default_role,
                overwrite=overwrite,
                reason=reason or f"{ctx.author} встановив блокування каналу"
            )

            embed = discord.Embed(
                title="Успіх",
                description=f"✅ Канал {text_channel.mention} заблоковано для @everyone.",
                color=discord.Color.green()
            )
            if reason:
                embed.add_field(name="Причина", value=reason, inline=False)
            embed.set_footer(text=f"Виконав: {ctx.author}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            await ctx.send(embed=embed)

            logger.info(f"{ctx.author} заблокував {text_channel} | Причина: {reason}")
        except Exception as e:
            embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося заблокувати канал.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed)
            logger.error(f"lock error: {e}")

    # 20. unlock — розблокувати канал
    @commands.command(
        name="unlock", 
        help="Розблокувати канал. Використання: !unlock (канал)"
    )
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context, channel: discord.TextChannel = None):
        try:
            text_channel = channel or ctx.channel
            overwrite = text_channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = None  # Видаляємо обмеження для надсилання повідомлень
            await text_channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
            
            embed = discord.Embed(
                title="Канал розблоковано",
                description=f"Канал {text_channel.mention} розблоковано для @everyone.",
                color=discord.Color.green()
            )
            await ctx.send(embed=embed)
            logger.info(f"{ctx.author} розблокував {text_channel}.")
        except Exception as e:
            error_embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося розблокувати канал.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            logger.error(f"unlock error: {e}")

    # 21. setcolor — встановити колір ролі
    @commands.command(
        name="setcolor",
        help="Встановити колір ролі. Використання: !setcolor [роль] [#hex]"
    )
    @commands.has_permissions(manage_roles=True)
    async def setcolor(self, ctx: commands.Context, role: discord.Role, color_code: str):
        try:
            # Видаляємо символ '#' (якщо є) та перетворюємо hex-рядок на ціле число
            hex_code = color_code.lstrip("#")
            color_int = int(hex_code, 16)
            await role.edit(color=discord.Color(color_int), reason=f"{ctx.author} встановив колір")
            
            embed = discord.Embed(
                title="Успіх",
                description=f"✅ Колір ролі **{role.name}** змінено на **#{hex_code.upper()}**.",
                color=discord.Color.green()
            )
            embed.set_footer(text=f"Встановив: {ctx.author}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            await ctx.send(embed=embed)
            
            logger.info(f"{ctx.author} встановив колір для {role.name}: #{hex_code.upper()}")
        except ValueError:
            # Помилка перетворення hex-рядка на число
            error_embed = discord.Embed(
                title="Помилка",
                description="❌ Невірний формат коду кольору. Будь ласка, введіть дійсний hex-код (наприклад, #FF5733).",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            logger.error(f"setcolor error: Невірний hex-код '{color_code}' від {ctx.author}")
        except Exception as e:
            error_embed = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося змінити колір ролі.",
                color=discord.Color.red()
            )
            await ctx.send(embed=error_embed)
            logger.error(f"setcolor error: {e}")

    # 22. slowmode — встановити повільний режим
    @commands.command(
        name="slowmode",
        help="Встановити або скинути повільний режим каналу. Використання: !slowmode [час] / вимкнено"
    )
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, time_arg: str = None):
        try:
            # Якщо аргумент не задано або вказано "вимкнено", встановлюємо затримку 0
            if not time_arg or time_arg.lower() == "вимкнено":
                delay = 0
            else:
                delay = self.parse_duration(time_arg)
                if delay is None:
                    embed_error = discord.Embed(
                        title="Помилка",
                        description="❌ Невірний формат часу. Будь ласка, введіть час у правильному форматі.",
                        color=discord.Color.red()
                    )
                    await ctx.send(embed=embed_error)
                    return

            # Оновлюємо повільний режим каналу
            await ctx.channel.edit(slowmode_delay=delay, reason=f"{ctx.author} встановив slowmode")
            
            # Формуємо embed-повідомлення
            if delay == 0:
                description = "✅ Повільний режим вимкнено."
            else:
                description = f"✅ Повільний режим каналу встановлено на **{delay} секунд**."
                
            embed_success = discord.Embed(
                title="Повільний режим",
                description=description,
                color=discord.Color.green()
            )
            embed_success.set_footer(text=f"Встановив: {ctx.author}", icon_url=ctx.author.avatar.url if ctx.author.avatar else None)
            await ctx.send(embed=embed_success)
            logger.info(f"{ctx.author} встановив slowmode {delay} сек у {ctx.channel}.")
        except Exception as e:
            embed_error = discord.Embed(
                title="Помилка",
                description="❌ Не вдалося встановити повільний режим.",
                color=discord.Color.red()
            )
            await ctx.send(embed=embed_error)
            logger.error(f"slowmode error: {e}")

    # 23. reset — скидання даних (наприклад, скидання балів)
    @commands.command(name="reset", help="Скинути дані. Використання: !reset [категорія] [усі/користувач]")
    @commands.has_permissions(administrator=True)
    async def reset(self, ctx: commands.Context, category: str, target: str = "усі"):
        try:
            category = category.lower()
            if category == "бали":
                pts = self.load_points()
                if target.lower() in ["усі", "all"]:
                    pts = {}
                    self.save_points(pts)
                    await ctx.send("✅ Всі бали скинуто.")
                else:
                    member = await commands.MemberConverter().convert(ctx, target)
                    uid = str(member.id)
                    pts[uid] = 0
                    self.save_points(pts)
                    await ctx.send(f"✅ Бал для {member.display_name} скинуто.")
            else:
                await ctx.send("ℹ️ Скидання вибраної категорії не реалізовано.")
        except Exception as e:
            await ctx.send("❌ Помилка при скиданні даних.")
            logger.error(f"reset error: {e}")

    # =====================
    # Обробка помилок
    # =====================
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        logger.error(f"Помилка в команді {ctx.command}: {error}")
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ У вас немає прав для використання цієї команди.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Ви пропустили обов'язковий аргумент. Використайте: {ctx.prefix}{ctx.command} {ctx.command.signature}")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Невірний аргумент. Перевірте введені дані.")
        else:
            await ctx.send("❌ Сталася помилка при виконанні команди. Перевірте лог для подробиць.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
    logger.info("✅ Cog Moderation успішно завантажено.")



    
