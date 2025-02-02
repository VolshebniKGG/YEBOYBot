

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
    Cog для адміністративних та модераційних команд:
    • Зміна ніка, бан/розбан, кік, vkick (кік з голосового), mute/unmute (текст),
      vmute/unvmute (голос), timeout/untimeout,
    • Очистка повідомлень, переміщення учасників,
    • Видання ролей, керування балами,
    • Попередження, блокування каналів, встановлення кольору, slowmode,
    • Скидання деяких даних.
    Попередження та бали зберігаються в окремих JSON-файлах.
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
                json.dump([], f)
        if not os.path.exists(self.points_file):
            with open(self.points_file, "w", encoding="utf-8") as f:
                json.dump({}, f)

    # =====================
    #  Додаткові допоміжні методи
    # =====================

    @staticmethod
    def parse_duration(duration_str: str) -> int:
        """
        Перетворює рядок типу "1m", "1h", "1d", "1w", "1mo"/"1міс", "1y" або "1 год"
        на кількість секунд. Повертає ціле число або None, якщо формат невірний.
        """
        pattern = r"(\d+)\s*(m|h|d|w|mo|міс|y|год)"
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
        """Через delay (секунд) знімає роль (наприклад, Muted) з користувача, якщо вона ще є."""
        await asyncio.sleep(delay)
        if role in member.roles:
            try:
                await member.remove_roles(role, reason="Автоматичне скидання муту")
                logger.info(f"Автоматично розм'ютовано {member} після {delay} секунд.")
            except Exception as e:
                logger.error(f"Помилка при автоматичному розм'ютуванні {member}: {e}")

    async def delayed_unban(self, guild: discord.Guild, user_id: int, delay: int):
        """Через delay розбанює користувача за його ID."""
        await asyncio.sleep(delay)
        try:
            user = await self.bot.fetch_user(user_id)
            await guild.unban(user, reason="Автоматичне розбанювання після строку")
            logger.info(f"Автоматично розбанено {user} після {delay} секунд.")
        except Exception as e:
            logger.error(f"Помилка при автоматичному розбані користувача {user_id}: {e}")

    async def delayed_vunmute(self, member: discord.Member, delay: int):
        """Через delay знімає вимкнення мікрофону (vmute) з користувача."""
        await asyncio.sleep(delay)
        try:
            await member.edit(mute=False, reason="Автоматичне розвімкнення голосу")
            logger.info(f"Автоматично знято vmute з {member} після {delay} секунд.")
        except Exception as e:
            logger.error(f"Помилка при автоматичному знятті vmute з {member}: {e}")

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
        except Exception:
            return []

    def save_warnings(self, warns: list) -> None:
        """Зберігає список попереджень у файл."""
        with open(self.warns_file, "w", encoding="utf-8") as f:
            json.dump(warns, f, indent=4)

    def load_points(self) -> dict:
        """Завантажує бали користувачів із файлу."""
        try:
            with open(self.points_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def save_points(self, points: dict) -> None:
        """Зберігає бали користувачів у файл."""
        with open(self.points_file, "w", encoding="utf-8") as f:
            json.dump(points, f, indent=4)

    # =====================
    #  Команди
    # =====================

    # 1. setnick — зміна ніка
    @commands.command(name="setnick", help="Змінити нікнейм учасника. Використання: !setnick [користувач] (новий нік)")
    @commands.has_permissions(manage_nicknames=True)
    async def setnick(self, ctx: commands.Context, member: discord.Member, *, new_nick: str):
        try:
            await member.edit(nick=new_nick, reason=f"Змінено {ctx.author}")
            await ctx.send(f"✅ Нік {member.mention} змінено на **{new_nick}**.")
            logger.info(f"{ctx.author} змінив нік {member} на {new_nick}.")
        except Exception as e:
            await ctx.send("❌ Не вдалося змінити нікнейм.")
            logger.error(f"setnick error: {e}")

    # 2. бан / заборона — бан з опціональним часом
    @commands.command(name="бан", aliases=["заборона"], help="Бан користувача. Використання: !бан [користувач] (час) (причина)")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, time_or_reason: str = None, *, reason: str = None):
        duration = None
        final_reason = ""
        if time_or_reason:
            dur = self.parse_duration(time_or_reason)
            if dur:
                duration = dur
                final_reason = reason if reason else ""
            else:
                final_reason = f"{time_or_reason} {reason}" if reason else time_or_reason
        try:
            await member.ban(reason=final_reason)
            await ctx.send(f"✅ Забанено {member.mention}. Причина: {final_reason}")
            logger.info(f"{ctx.author} забанив {member} | Причина: {final_reason}")
            if duration:
                # Плануємо автоматичне розбанювання через duration секунд
                asyncio.create_task(self.delayed_unban(ctx.guild, member.id, duration))
        except Exception as e:
            await ctx.send("❌ Не вдалося забанити користувача.")
            logger.error(f"ban error: {e}")

    # 3. unban — розбан
    @commands.command(name="unban", help="Розбан користувача. Використання: !unban [користувач або ID]")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, *, member_identifier: str):
        try:
            bans = await ctx.guild.bans()
            target = None
            for ban_entry in bans:
                user = ban_entry.user
                if (member_identifier.isdigit() and int(member_identifier) == user.id) or (member_identifier.lower() == user.name.lower()):
                    target = user
                    break
            if target:
                await ctx.guild.unban(target)
                await ctx.send(f"✅ Розбанено {target.mention}.")
                logger.info(f"{ctx.author} розбанив {target}.")
            else:
                await ctx.send("❌ Не знайдено такого користувача серед забанених.")
        except Exception as e:
            await ctx.send("❌ Помилка при розбані користувача.")
            logger.error(f"unban error: {e}")

    # 4. kick — кік
    @commands.command(name="kick", help="Вигнати учасника з сервера. Використання: !kick [користувач] (причина)")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = None):
        try:
            await member.kick(reason=reason)
            await ctx.send(f"✅ Вигнано {member.mention}. Причина: {reason}")
            logger.info(f"{ctx.author} вигнав {member} | Причина: {reason}")
        except Exception as e:
            await ctx.send("❌ Не вдалося вигнати користувача.")
            logger.error(f"kick error: {e}")

    # 5. vkick — кік з голосового каналу
    @commands.command(name="vkick", help="Вигнати учасника з голосового каналу. Використання: !vkick [користувач]")
    @commands.has_permissions(kick_members=True)
    async def vkick(self, ctx: commands.Context, member: discord.Member):
        try:
            if member.voice:
                await member.move_to(None, reason="vkick: вигнання з голосового каналу")
                await ctx.send(f"✅ {member.mention} винесено з голосового каналу.")
                logger.info(f"{ctx.author} vkick {member}")
            else:
                await ctx.send("❌ Користувач не перебуває у голосовому каналі.")
        except Exception as e:
            await ctx.send("❌ Не вдалося виконати vkick.")
            logger.error(f"vkick error: {e}")

    # 6. mute — текстовий mute з опціональним часом та причиною
    @commands.command(name="mute", help="Вимкнути текст учаснику. Використання: !mute [користувач] (час) (причина)")
    @commands.has_permissions(manage_roles=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: str = None, *, reason: str = None):
        try:
            guild: discord.Guild = ctx.guild
            # Шукаємо або створюємо роль Muted
            mute_role = discord.utils.get(guild.roles, name="Muted")
            if not mute_role:
                mute_role = await guild.create_role(name="Muted")
                for channel in guild.channels:
                    await channel.set_permissions(mute_role, send_messages=False, speak=False)
            await member.add_roles(mute_role, reason=reason)
            # Записуємо причину у файл користувача
            user_data = self._load_user_data(member.id)
            user_data["mute_reason"] = reason if reason else "Не вказано"
            self._save_user_data(member.id, user_data)
            await ctx.send(f"✅ {member.mention} зам'ючено. Причина: {reason}")
            logger.info(f"{ctx.author} зам'ютував {member} | Причина: {reason}")
            # Якщо вказано час – плануємо автоматичне розм'ютення
            delay = self.parse_duration(duration) if duration and self.parse_duration(duration) else None
            if delay:
                asyncio.create_task(self.delayed_unmute(member, mute_role, delay))
        except Exception as e:
            await ctx.send("❌ Не вдалося зам'ютити користувача.")
            logger.error(f"mute error: {e}")

    # 7. unmute — розблокування тексту
    @commands.command(name="unmute", help="Увімкнути текст учаснику. Використання: !unmute [користувач]")
    @commands.has_permissions(manage_roles=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        try:
            guild: discord.Guild = ctx.guild
            mute_role = discord.utils.get(guild.roles, name="Muted")
            if mute_role and mute_role in member.roles:
                await member.remove_roles(mute_role)
                # Очищення збережених даних
                user_data = self._load_user_data(member.id)
                user_data.pop("mute_reason", None)
                self._save_user_data(member.id, user_data)
                await ctx.send(f"✅ {member.mention} розм'ючено.")
                logger.info(f"{ctx.author} розм'ютував {member}")
            else:
                await ctx.send("❌ У користувача відсутня роль Muted.")
        except Exception as e:
            await ctx.send("❌ Не вдалося розм'ютити користувача.")
            logger.error(f"unmute error: {e}")

    # 8. vmute — вимкнути голос (mute мікрофону)
    @commands.command(name="vmute", help="Вимкнути голос учаснику. Використання: !vmute [користувач] (час) (причина)")
    @commands.has_permissions(mute_members=True)
    async def vmute(self, ctx: commands.Context, member: discord.Member, duration: str = None, *, reason: str = None):
        try:
            if member.voice:
                await member.edit(mute=True, reason=reason)
                await ctx.send(f"✅ {member.mention} отримав vmute. Причина: {reason}")
                logger.info(f"{ctx.author} vmute {member} | Причина: {reason}")
                delay = self.parse_duration(duration) if duration and self.parse_duration(duration) else None
                if delay:
                    asyncio.create_task(self.delayed_vunmute(member, delay))
            else:
                await ctx.send("❌ Користувач не перебуває у голосовому каналі.")
        except Exception as e:
            await ctx.send("❌ Не вдалося вимкнути голос користувача.")
            logger.error(f"vmute error: {e}")

    # 9. unvmute — увімкнути голос
    @commands.command(name="unvmute", help="Увімкнути голос учаснику. Використання: !unvmute [користувач]")
    @commands.has_permissions(mute_members=True)
    async def unvmute(self, ctx: commands.Context, member: discord.Member):
        try:
            if member.voice:
                await member.edit(mute=False, reason="Розвімкнення голосу")
                await ctx.send(f"✅ {member.mention} розвімкнено.")
                logger.info(f"{ctx.author} зняв vmute з {member}")
            else:
                await ctx.send("❌ Користувач не знаходиться у голосовому каналі.")
        except Exception as e:
            await ctx.send("❌ Не вдалося зняти vmute.")
            logger.error(f"unvmute error: {e}")

    # 10. timeout — встановити тайм-аут (discord API 2.0)
    @commands.command(name="timeout", help="Встановити тайм-аут учаснику. Використання: !timeout [користувач] (час) (причина)")
    @commands.has_permissions(moderate_members=True)
    async def timeout(self, ctx: commands.Context, member: discord.Member, time_arg: str = None, *, reason: str = None):
        try:
            if time_arg:
                seconds = self.parse_duration(time_arg)
                if seconds is None:
                    # Якщо не вдалося розпізнати час – вважати його частиною причини
                    final_reason = f"{time_arg} {reason}" if reason else time_arg
                    seconds = None
                else:
                    final_reason = reason if reason else ""
            else:
                final_reason = reason if reason else ""
                seconds = None
            if seconds:
                until = datetime.utcnow() + timedelta(seconds=seconds)
            else:
                await ctx.send("❌ Вкажіть тривалість тайм-ауту.")
                return
            await member.edit(timeout=until, reason=final_reason)
            await ctx.send(f"✅ {member.mention} отримав тайм-аут на {time_arg}. Причина: {final_reason}")
            logger.info(f"{ctx.author} встановив тайм-аут для {member} на {time_arg} | Причина: {final_reason}")
        except Exception as e:
            await ctx.send("❌ Не вдалося встановити тайм-аут.")
            logger.error(f"timeout error: {e}")

    # 11. untimeout — скидання тайм-ауту
    @commands.command(name="untimeout", help="Прибрати тайм-аут у учасника. Використання: !untimeout [користувач]")
    @commands.has_permissions(moderate_members=True)
    async def untimeout(self, ctx: commands.Context, member: discord.Member):
        try:
            await member.edit(timeout=None, reason="Скидання тайм-ауту")
            await ctx.send(f"✅ Тайм-аут у {member.mention} скинуто.")
            logger.info(f"{ctx.author} скинув тайм-аут у {member}")
        except Exception as e:
            await ctx.send("❌ Не вдалося скинути тайм-аут.")
            logger.error(f"untimeout error: {e}")

    # 12. clear — очищення повідомлень
    @commands.command(name="clear", help="Очищення повідомлень. Приклади: !clear, !clear 1000, !clear bots 10, !clear @User 10")
    @commands.has_permissions(manage_messages=True)
    async def clear(self, ctx: commands.Context, *args):
        try:
            limit = 100
            check = None
            if not args:
                # Якщо немає аргументів, очищаємо за замовчуванням
                pass
            elif args[0].lower() == "bots":
                if len(args) > 1 and args[1].isdigit():
                    limit = int(args[1])
                check = lambda m: m.author.bot
            else:
                # Спроба перетворити перший аргумент у користувача
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
            # Якщо check залишається None, встановлюємо його як функцію, яка завжди повертає True
            if check is None:
                check = lambda m: True

            deleted = await ctx.channel.purge(limit=limit, check=check)
            await ctx.send(f"✅ Видалено {len(deleted)} повідомлень.", delete_after=5)
            logger.info(f"{ctx.author} очистив {len(deleted)} повідомлень у {ctx.channel}.")
        except Exception as e:
            await ctx.send("❌ Помилка при очищенні повідомлень.")
            logger.error(f"clear error: {e}")

    # 13. move — перемістити учасника в голосовий канал
    @commands.command(name="move", help="Перемістити учасника. Використання: !move [користувач]/all (цільовий канал)")
    @commands.has_permissions(move_members=True)
    async def move(self, ctx: commands.Context, target: str, *, destination: str = None):
        try:
            if target.lower() == "all":
                # Переміщення всіх учасників з каналу командивника
                if ctx.author.voice and destination:
                    dest_channel = discord.utils.find(lambda c: c.name.lower() == destination.lower(), ctx.guild.voice_channels)
                    if not dest_channel:
                        await ctx.send("❌ Не знайдено канал з такою назвою.")
                        return
                    moved = 0
                    for member in ctx.author.voice.channel.members:
                        await member.move_to(dest_channel)
                        moved += 1
                    await ctx.send(f"✅ Переміщено {moved} учасників у {dest_channel.mention}.")
                else:
                    await ctx.send("❌ Для команди 'all' необхідно, щоб ви були у голосовому каналі та вказали цільовий канал.")
            else:
                # Переміщення окремого користувача
                member = await commands.MemberConverter().convert(ctx, target)
                if destination:
                    dest_channel = discord.utils.find(lambda c: c.name.lower() == destination.lower(), ctx.guild.voice_channels)
                    if not dest_channel:
                        await ctx.send("❌ Не знайдено канал з такою назвою.")
                        return
                else:
                    if ctx.author.voice:
                        dest_channel = ctx.author.voice.channel
                    else:
                        await ctx.send("❌ Вкажіть цільовий голосовий канал.")
                        return
                await member.move_to(dest_channel)
                await ctx.send(f"✅ {member.mention} переміщено у {dest_channel.mention}.")
                logger.info(f"{ctx.author} перемістив {member} у {dest_channel}.")
        except Exception as e:
            await ctx.send("❌ Помилка при переміщенні учасника.")
            logger.error(f"move error: {e}")

    # 14. роль — видати/зняти роль(і)
    @commands.command(name="роль", help="Видати або зняти роль(і). Приклади: !роль @User Адміністратор, !роль усі учасники, !роль боти Система")
    @commands.has_permissions(manage_roles=True)
    async def роль(self, ctx: commands.Context, target: str, *, roles_str: str):
        try:
            # Визначаємо цільову групу: окремий користувач, усі, боти або люди
            targets = []
            if target.lower() in ["усі", "боти", "люди"]:
                if target.lower() == "боти":
                    targets = [m for m in ctx.guild.members if m.bot]
                elif target.lower() == "люди":
                    targets = [m for m in ctx.guild.members if not m.bot]
                else:
                    targets = ctx.guild.members
            else:
                member = await commands.MemberConverter().convert(ctx, target)
                targets = [member]
            # Розділяємо ролі за комами або пробілами
            roles_names = [r.strip() for r in re.split(r",|\s+", roles_str) if r.strip()]
            changes = []
            for role_name in roles_names:
                # Перевіряємо перший символ: + (додати), - (зняти) або ! (перемкнути)
                action = "add"
                if role_name[0] in "+-!":
                    if role_name[0] == "-":
                        action = "remove"
                    elif role_name[0] == "!":
                        action = "toggle"
                    role_name = role_name[1:].strip()
                role = discord.utils.get(ctx.guild.roles, name=role_name)
                if not role:
                    await ctx.send(f"❌ Роль **{role_name}** не знайдена.")
                    continue
                for member in targets:
                    if action == "add" and role not in member.roles:
                        await member.add_roles(role, reason=f"{ctx.author} через команду роль")
                        changes.append(f"додано {role.name} для {member.display_name}")
                    elif action == "remove" and role in member.roles:
                        await member.remove_roles(role, reason=f"{ctx.author} через команду роль")
                        changes.append(f"знято {role.name} з {member.display_name}")
                    elif action == "toggle":
                        if role in member.roles:
                            await member.remove_roles(role, reason=f"{ctx.author} через команду роль (toggle)")
                            changes.append(f"знято {role.name} з {member.display_name}")
                        else:
                            await member.add_roles(role, reason=f"{ctx.author} через команду роль (toggle)")
                            changes.append(f"додано {role.name} для {member.display_name}")
            if changes:
                await ctx.send("✅ Зміни ролей:\n" + "\n".join(changes))
            else:
                await ctx.send("ℹ️ Ніяких змін не виконано.")
        except Exception as e:
            await ctx.send("❌ Помилка при зміні ролей.")
            logger.error(f"роль error: {e}")

    # 15. points (балів) — керування балами
    @commands.command(name="points", help="Керування балами. Приклади: !points, !points @User +1, !points reset, !points Адмін")
    @commands.has_permissions(administrator=True)
    async def points(self, ctx: commands.Context, *args):
        try:
            pts = self.load_points()
            if not args:
                # Показати список всіх користувачів з балами
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
                    # Спроба обробити команду для конкретного користувача або ролі
                    try:
                        member = await commands.MemberConverter().convert(ctx, args[0])
                        uid = str(member.id)
                        if len(args) > 1:
                            # Наприклад, !points @User +1 / -1 / 0 / 1 (встановити)
                            try:
                                delta = int(args[1])
                                pts[uid] = delta
                            except ValueError:
                                # Якщо починається з + або -
                                if args[1][0] in "+-":
                                    change = int(args[1])
                                    pts[uid] = pts.get(uid, 0) + change
                                else:
                                    pts[uid] = 0
                            self.save_points(pts)
                            await ctx.send(f"✅ Бал {member.display_name} тепер: {pts.get(uid,0)}")
                        else:
                            await ctx.send(f"ℹ️ Бал {member.display_name}: {pts.get(uid,0)}")
                    except Exception:
                        # Можливо, це назва ролі – фільтрація балів
                        role = discord.utils.get(ctx.guild.roles, name=args[0])
                        if role:
                            msg = f"Бал для учасників з роллю {role.name}:\n"
                            for member in ctx.guild.members:
                                if role in member.roles:
                                    uid = str(member.id)
                                    msg += f"{member.display_name}: {pts.get(uid,0)}\n"
                            await ctx.send(msg)
                        else:
                            await ctx.send("❌ Не вдалося розпізнати цільову сутність.")
        except Exception as e:
            await ctx.send("❌ Помилка при керуванні балами.")
            logger.error(f"points error: {e}")

    # 16. warn — попередити користувача
    @commands.command(name="warn", help="Попередити користувача. Використання: !warn [користувач] [причина]")
    @commands.has_permissions(manage_messages=True)
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str):
        try:
            warns = self.load_warnings()
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
            await ctx.send(f"✅ {member.mention} попереджено. warnID: {warn_id}")
            logger.info(f"{ctx.author} попередив {member} | warnID: {warn_id} | Причина: {reason}")
        except Exception as e:
            await ctx.send("❌ Помилка при видачі попередження.")
            logger.error(f"warn error: {e}")

    # 17. removewarn — видалити попередження
    @commands.command(name="removewarn", help="Видалити попередження. Використання: !removewarn [warnID] / [користувач] / все")
    @commands.has_permissions(manage_messages=True)
    async def removewarn(self, ctx: commands.Context, target: str):
        try:
            warns = self.load_warnings()
            if target.lower() == "all" or target.lower() == "все":
                warns = []
                self.save_warnings(warns)
                await ctx.send("✅ Всі попередження видалено.")
            else:
                # Спробуємо сприйняти як warnID
                try:
                    warn_id = int(target)
                    new_warns = [w for w in warns if w["id"] != warn_id]
                    if len(new_warns) == len(warns):
                        await ctx.send("❌ Попередження з таким ID не знайдено.")
                    else:
                        self.save_warnings(new_warns)
                        await ctx.send(f"✅ Попередження {warn_id} видалено.")
                except ValueError:
                    # Можливо, це користувач – видалити всі попередження для нього
                    member = await commands.MemberConverter().convert(ctx, target)
                    new_warns = [w for w in warns if w["user_id"] != member.id]
                    self.save_warnings(new_warns)
                    await ctx.send(f"✅ Попередження для {member.mention} видалено.")
        except Exception as e:
            await ctx.send("❌ Помилка при видаленні попередження.")
            logger.error(f"removewarn error: {e}")

    # 18. попередження — показати список попереджень
    @commands.command(name="попередження", help="Показати список попереджень. Використання: !попередження [користувач]")
    async def попередження(self, ctx: commands.Context, member: discord.Member = None):
        try:
            warns = self.load_warnings()
            if member:
                user_warns = [w for w in warns if w["user_id"] == member.id]
                if not user_warns:
                    await ctx.send(f"ℹ️ Для {member.mention} попереджень немає.")
                    return
                msg = f"Попередження для {member.display_name}:\n"
                for w in user_warns:
                    msg += f"ID {w['id']}: {w['reason']} (модератор: <@{w['mod_id']}>)\n"
                await ctx.send(msg)
            else:
                if not warns:
                    await ctx.send("ℹ️ Попереджень немає.")
                    return
                msg = "Список попереджень:\n"
                for w in warns:
                    user = ctx.guild.get_member(w["user_id"])
                    name = user.display_name if user else str(w["user_id"])
                    msg += f"ID {w['id']} для {name}: {w['reason']}\n"
                await ctx.send(msg)
        except Exception as e:
            await ctx.send("❌ Помилка при виведенні попереджень.")
            logger.error(f"попередження error: {e}")

    # 19. lock — заблокувати канал (@everyone не може писати)
    @commands.command(name="lock", help="Заблокувати канал. Використання: !lock (канал) (причина)")
    @commands.has_permissions(manage_channels=True)
    async def lock(self, ctx: commands.Context, channel: discord.TextChannel = None, *, reason: str = None):
        try:
            channel = channel or ctx.channel
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = False
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=reason)
            await ctx.send(f"✅ Канал {channel.mention} заблоковано для @everyone.")
            logger.info(f"{ctx.author} заблокував {channel} | Причина: {reason}")
        except Exception as e:
            await ctx.send("❌ Не вдалося заблокувати канал.")
            logger.error(f"lock error: {e}")

    # 20. unlock — розблокувати канал
    @commands.command(name="unlock", help="Розблокувати канал. Використання: !unlock (канал)")
    @commands.has_permissions(manage_channels=True)
    async def unlock(self, ctx: commands.Context, channel: discord.TextChannel = None):
        try:
            channel = channel or ctx.channel
            overwrite = channel.overwrites_for(ctx.guild.default_role)
            overwrite.send_messages = None
            await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite)
            await ctx.send(f"✅ Канал {channel.mention} розблоковано для @everyone.")
            logger.info(f"{ctx.author} розблокував {channel}.")
        except Exception as e:
            await ctx.send("❌ Не вдалося розблокувати канал.")
            logger.error(f"unlock error: {e}")

    # 21. setcolor — встановити колір ролі
    @commands.command(name="setcolor", help="Встановити колір ролі. Використання: !setcolor [роль] [#hex]")
    @commands.has_permissions(manage_roles=True)
    async def setcolor(self, ctx: commands.Context, role: discord.Role, color_code: str):
        try:
            if color_code.startswith("#"):
                color_code = color_code[1:]
            color_int = int(color_code, 16)
            await role.edit(color=discord.Color(color_int), reason=f"{ctx.author} встановив колір")
            await ctx.send(f"✅ Колір ролі **{role.name}** змінено.")
            logger.info(f"{ctx.author} встановив колір для {role.name}: #{color_code}")
        except Exception as e:
            await ctx.send("❌ Не вдалося змінити колір ролі.")
            logger.error(f"setcolor error: {e}")

    # 22. slowmode — встановити повільний режим
    @commands.command(name="slowmode", help="Встановити або скинути повільний режим каналу. Використання: !slowmode [час]/вимкнено")
    @commands.has_permissions(manage_channels=True)
    async def slowmode(self, ctx: commands.Context, time_arg: str = None):
        try:
            if not time_arg or time_arg.lower() == "вимкнено":
                delay = 0
            else:
                delay = self.parse_duration(time_arg)
                if delay is None:
                    await ctx.send("❌ Невірний формат часу.")
                    return
            await ctx.channel.edit(slowmode_delay=delay, reason=f"{ctx.author} встановив slowmode")
            await ctx.send(f"✅ Повільний режим каналу встановлено на {delay} секунд.")
            logger.info(f"{ctx.author} встановив slowmode {delay} сек у {ctx.channel}.")
        except Exception as e:
            await ctx.send("❌ Не вдалося встановити повільний режим.")
            logger.error(f"slowmode error: {e}")

    # 23. reset — скидання даних (реалізовано приклад для скидання балів)
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
    #  Обробка помилок
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

def setup(bot: commands.Bot):
    bot.add_cog(Moderation(bot))
    logger.info("✅ Cog Moderation успішно завантажено.")



    
