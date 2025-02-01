



import logging

import discord
from discord.ext import commands

# Логгер для Admin Cog
logger = logging.getLogger("bot")
# Вимикаємо поширення обробників, щоб не дублювати записи
logger.propagate = False

class Admin(commands.Cog):
    """
    Ког для адміністративних команд: kick, ban, unban.
    Вимагає відповідних прав у виконавця і коректних настройок бота.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="kick", help="Виключає користувача з сервера")
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = None):
        """
        Виключає (kick) учасника з сервера з вказаною причиною (за потреби).
        """
        try:
            await member.kick(reason=reason)
            await ctx.send(f"✅ Виключено {member.mention} з причини: {reason}")
            logger.info(f"User {member} was kicked by {ctx.author} | Reason: {reason}")
        except discord.Forbidden:
            await ctx.send("❌ У мене недостатньо прав, аби виключити цього користувача.")
        except Exception as e:
            await ctx.send("❌ Виникла помилка при виключенні користувача.")
            logger.error(f"Error kicking user {member}: {e}")

    @commands.command(name="ban", help="Забороняє доступ користувачу на сервер")
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx: commands.Context, member: discord.Member, *, reason: str = None):
        """
        Блокує (ban) учасника на сервері з вказаною причиною (за потреби).
        """
        try:
            await member.ban(reason=reason)
            await ctx.send(f"✅ Заблоковано {member.mention} з причини: {reason}")
            logger.info(f"User {member} was banned by {ctx.author} | Reason: {reason}")
        except discord.Forbidden:
            await ctx.send("❌ У мене недостатньо прав, аби заблокувати цього користувача.")
        except Exception as e:
            await ctx.send("❌ Виникла помилка при блокуванні користувача.")
            logger.error(f"Error banning user {member}: {e}")

    @commands.command(name="unban", help="Розблоковує доступ користувачу")
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, *, member_name: str):
        """
        Знаходить заблокованого користувача за іменем (user.name) і знімає ban.
        Якщо користувача з таким іменем не знайдено серед заблокованих – виводить повідомлення.
        """
        try:
            banned_users = await ctx.guild.bans()
            for ban_entry in banned_users:
                user = ban_entry.user
                if user.name == member_name:
                    await ctx.guild.unban(user)
                    await ctx.send(f"✅ Користувач {user.mention} розблокований.")
                    logger.info(f"User {user} was unbanned by {ctx.author}")
                    return
            await ctx.send(f"❌ Користувача з іменем {member_name} не знайдено серед заблокованих.")
        except Exception as e:
            await ctx.send("❌ Виникла помилка при розблокуванні користувача.")
            logger.error(f"Error unbanning user {member_name}: {e}")


def setup(bot: commands.Bot):
    """
    Підключення Admin Cog до бота (py-cord).
    Використовується при виклику: bot.load_extension('admin').
    """
    bot.add_cog(Admin(bot))
    logger.debug("Admin Cog loaded successfully.")





