



import discord
from discord.ext import commands
import logging

logger = logging.getLogger('bot')

class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='helpme', help='Показує список команд')
    async def helpme(self, ctx):
        embed = discord.Embed(title="Список команд за категоріями", color=0x00ff00)

        # Категорія Admin
        embed.add_field(name="**Admin**", value="""
        `ban` - Забороняє доступ користувачу на сервер
        `kick` - Виключає користувача з сервера
        `unban` - Розблоковує доступ користувачу
        """, inline=False)

        # Категорія Help
        embed.add_field(name="**Help**", value="""
        `helpme` - Показує список команд
        """, inline=False)

        # Категорія Moderation
        embed.add_field(name="**Moderation**", value="""
        `mute` - Зам'ючує користувача
        `unmute` - Розм'ючує користувача
        """, inline=False)

        # Категорія Music
        embed.add_field(name="**Music**", value="""
        `pause` - Pauses playback
        `play` - Add a track or play immediately
        `play_playlist` - Команда для відтворення плейлисту
        `queue` - Displays the queue
        `resume` - Resumes playback
        `skip` - Skips the current track
        `stop` - Stops music and clears the queue
        """, inline=False)

        # Категорія User
        embed.add_field(name="**User**", value="""
        `add_warning` - Додає попередження користувачу
        `clear_warnings` - Очищає всі попередження користувача
        `info` - Показує інформацію про користувача
        """, inline=False)

        # No Category
        embed.add_field(name="**No Category**", value="""
        `help` - Shows this message
        """, inline=False)

        # Додаткова інформація
        embed.set_footer(text="Type !help command for more info on a command.\nYou can also type !help category for more info on a category.")

        await ctx.send(embed=embed)
        logger.info(f'Help command used by {ctx.author}')

async def setup(bot):
    await bot.add_cog(Help(bot))
    logger.info('Loaded Help extension')
    
    
    