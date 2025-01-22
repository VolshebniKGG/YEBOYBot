

import discord
from discord.ext import commands
import logging
from database.db_manager import DatabaseManager  # Імпорт менеджера бази даних

logger = logging.getLogger('bot')

class User(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.db = DatabaseManager()  # Ініціалізація менеджера бази даних

    @commands.command(name='info', help='Показує інформацію про користувача')
    async def info(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        roles = [role.mention for role in member.roles if role != ctx.guild.default_role]
        embed = discord.Embed(title=f"Інформація про {member.display_name}", color=member.color)
        embed.add_field(name="Ім'я", value=member.name, inline=True)
        embed.add_field(name="ID", value=member.id, inline=True)
        embed.add_field(name="Статус", value=member.status, inline=True)
        embed.add_field(name="Ролі", value=", ".join(roles) if roles else "Немає ролей", inline=False)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Запитано {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

        # Запис дії до бази даних
        self.db.add_user_action(
            user_id=ctx.author.id,
            username=ctx.author.name,
            action=f"Viewed info of {member.name} (ID: {member.id})"
        )
        logger.info(f"User info command used by {ctx.author} for {member}")

    @commands.command(name='avatar', help='Показує аватар користувача')
    async def avatar(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        avatar_url = member.display_avatar.url
        embed = discord.Embed(title=f"Аватар {member.display_name}", description=f"[Завантажити аватар]({avatar_url})", color=member.color)
        embed.set_image(url=avatar_url)
        embed.set_footer(text=f"Запитано {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

        # Запис дії до бази даних
        self.db.add_user_action(
            user_id=ctx.author.id,
            username=ctx.author.name,
            action=f"Viewed avatar of {member.name} (ID: {member.id})"
        )
        logger.info(f"Avatar command used by {ctx.author} for {member}")

async def setup(bot):
    await bot.add_cog(User(bot))
    logger.info('Loaded User extension')


