



import discord
from discord.ext import commands
import logging

# Логгер для Admin Cog
logger = logging.getLogger('bot')
logger.propagate = False  # Забороняємо успадкування обробників

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='kick', help='Виключає користувача з сервера')
    @commands.has_permissions(kick_members=True)
    async def kick(self, ctx, member: discord.Member, *, reason=None):
        try:
            await member.kick(reason=reason)
            await ctx.send(f'✅ Виключено {member.mention} з причини: {reason}')
            logger.info(f'User {member} was kicked by {ctx.author} with reason: {reason}')
        except discord.Forbidden:
            await ctx.send("❌ У мене недостатньо прав для виключення цього користувача.")
        except Exception as e:
            await ctx.send("❌ Виникла помилка при виключенні користувача.")
            logger.error(f"Error kicking user {member}: {e}")

    @commands.command(name='ban', help='Забороняє доступ користувачу на сервер')
    @commands.has_permissions(ban_members=True)
    async def ban(self, ctx, member: discord.Member, *, reason=None):
        try:
            await member.ban(reason=reason)
            await ctx.send(f'✅ Заблоковано {member.mention} з причини: {reason}')
            logger.info(f'User {member} was banned by {ctx.author} with reason: {reason}')
        except discord.Forbidden:
            await ctx.send("❌ У мене недостатньо прав для блокування цього користувача.")
        except Exception as e:
            await ctx.send("❌ Виникла помилка при блокуванні користувача.")
            logger.error(f"Error banning user {member}: {e}")

    @commands.command(name='unban', help='Розблоковує доступ користувачу')
    @commands.has_permissions(ban_members=True)
    async def unban(self, ctx, *, member_name):
        try:
            banned_users = await ctx.guild.bans()
            for ban_entry in banned_users:
                user = ban_entry.user
                if user.name == member_name:
                    await ctx.guild.unban(user)
                    await ctx.send(f'✅ Користувач {user.mention} розблокований.')
                    logger.info(f'User {user} was unbanned by {ctx.author}')
                    return
            await ctx.send(f'❌ Користувача з іменем {member_name} не знайдено серед заблокованих.')
        except Exception as e:
            await ctx.send("❌ Виникла помилка при розблокуванні користувача.")
            logger.error(f"Error unbanning user {member_name}: {e}")

async def setup(bot):
    await bot.add_cog(Admin(bot))
    logger.debug('Admin Cog loaded successfully.')




