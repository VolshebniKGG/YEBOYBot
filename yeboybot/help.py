



import discord
from discord.ext import commands
import logging

# Налаштування логування: повідомлення будуть виводитись у консоль.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
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
                "`clearqueue` - Очистити всю чергу, не зупиняючи поточний трек\n"
                "`nowplaying` - Показати зараз відтворюваний трек\n"
                "`pause` - Призупинити відтворення\n"
                "`play` - Додати трек або пошуковий запит у чергу та відтворити (YouTube/Spotify)\n"
                "`queue` - Показати перелік треків у черзі\n"
                "`remove` - Видалити трек з черги за індексом\n"
                "`resume` - Продовжити відтворення\n"
                "`shuffle` - Перемішати чергу\n"
                "`skip` - Пропустити поточний трек\n"
                "`stop` - Зупинити музику і очистити чергу\n"
                "`volume` - Встановити гучність відтворення (0-100%)\n"
            ),
            inline=False
        )

        # Категорія RankCog
        embed.add_field(
            name="**RankCog**",
            value="`rank` - Показує ранг користувача",
            inline=False
        )

        # Категорія User
        embed.add_field(
            name="**User**",
            value=(
                "`add_warning` - Додає попередження користувачу\n"
                "`avatar` - Показує аватар користувача\n"
                "`clear_warnings` - Очищає всі попередження користувача\n"
                "`info` - Показує інформацію про користувача\n"
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


    
    
    