



import discord
from discord.ext import commands
import logging

logger = logging.getLogger("bot")

class Help(commands.Cog):
    """
    Клас-ког для виведення довідкової інформації (списку команд).
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="helpme", help="Показує список команд")
    async def helpme(self, ctx: commands.Context):
        """
        Формує Embed зі списком команд і категорій,
        надає короткі описи та показує, де знайти докладніше.
        """
        embed = discord.Embed(
            title="Список команд за категоріями",
            color=discord.Color.green()
        )

        # Категорія Admin
        embed.add_field(
            name="**Admin**",
            value=(
                "`ban` - Забороняє доступ користувачу на сервер\n"
                "`kick` - Виключає користувача з сервера\n"
                "`unban` - Розблоковує доступ користувачу\n"
            ),
            inline=False
        )

        # Категорія Help
        embed.add_field(
            name="**Help**",
            value="`helpme` - Показує список команд",
            inline=False
        )

        # Категорія Moderation
        embed.add_field(
            name="**Moderation**",
            value=(
                "`mute` - Зам'ючує користувача\n"
                "`unmute` - Розм'ючує користувача\n"
            ),
            inline=False
        )

        # Категорія Music
        embed.add_field(
            name="**Music**",
            value=(
                "`pause` - Pauses playback\n"
                "`play` - Add a track or play immediately\n"
                "`play_playlist` - Команда для відтворення плейлисту\n"
                "`queue` - Displays the queue\n"
                "`resume` - Resumes playback\n"
                "`skip` - Skips the current track\n"
                "`stop` - Stops music and clears the queue\n"
            ),
            inline=False
        )

        # Категорія User
        embed.add_field(
            name="**User**",
            value=(
                "`add_warning` - Додає попередження користувачу\n"
                "`clear_warnings` - Очищає всі попередження користувача\n"
                "`info` - Показує інформацію про користувача\n"
                "`avatar` - Показує аватар користувача\n"
            ),
            inline=False
        )

        # No Category
        embed.add_field(
            name="**No Category**",
            value="`help` - Shows this message",
            inline=False
        )

        # Додаткова інформація
        embed.set_footer(
            text=(
                "Type !help <command> for more info on a command.\n"
                "You can also type !help <category> for more info on a category."
            )
        )

        await ctx.send(embed=embed)
        logger.info(f"Help command used by {ctx.author} ({ctx.author.id})")

def setup(bot: commands.Bot):
    """
    Функція підключення ког-а до бота (py-cord).
    Використовується при виклику: bot.load_extension('help')
    """
    bot.add_cog(Help(bot))
    logger.info("Loaded Help extension")

    
    
    