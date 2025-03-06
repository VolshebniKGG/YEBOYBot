import discord
from discord.ext import commands
import logging

# Налаштування логування
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s:%(name)s: %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger("bot")

class Help(commands.Cog):
    """
    Клас ког для відображення довідкової інформації (списку команд).
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="helpme", help="Показує список команд")
    async def helpme(self, ctx: commands.Context):
        embed = discord.Embed(
            title="Список команд",
            description="Команди за категоріями",
            color=discord.Color.green()
        )

        # Категорія Help
        embed.add_field(
            name="Help",
            value="`helpme` - Показує список команд",
            inline=False
        )

        # Категорія Moderation
        embed.add_field(
            name="Moderation",
            value=(
                "`clear` - Очищення повідомлень. Приклади: !clear, !clear 1000, !clear...\n"
                "`clear_warnings` - Очищає всі попередження користувача\n"
                "`kick` - Вигнати учасника з сервера\n"
                "`lock` - Заблокувати канал\n"
                "`mute` - Вимкнути текст учаснику\n"
                "`points` - Керування балами\n"
                "`removewarn` - Видалити попередження\n"
                "`reset` - Скинути дані\n"
                "`setcolor` - Встановити колір ролі\n"
                "`setnick` - Змінити нікнейм учасника\n"
                "`slowmode` - Встановити або скинути повільний режим каналу\n"
                "`timeout` - Встановити тайм-аут учаснику\n"
                "`unban` - Розбан користувача\n"
                "`unlock` - Розблокувати канал\n"
                "`unmute` - Увімкнути текст учаснику\n"
                "`untimeout` - Прибрати тайм-аут у учасника\n"
                "`vkick` - Вигнати учасника з голосового каналу\n"
                "`warn` - Попередити користувача\n"
                "`warnings` - Показати список warnings\n"
                "`бан` - Бан користувача\n"
                "`роль` - Видати або зняти роль(і)"
            ),
            inline=False
        )

        # Категорія Music
        embed.add_field(
            name="Music",
            value=(
                "`clearqueue` - Очистити всю чергу (без зупинки поточного треку)\n"
                "`jump` - Запускає трек з черги за заданим індексом\n"
                "`nowplaying` - Показати інформацію про поточний трек\n"
                "`pause` - Призупинити відтворення\n"
                "`play` - Додати трек або пошуковий запит у чергу\n"
                "`queue` - Показати перелік треків у черзі\n"
                "`remove` - Видалити трек з черги за індексом\n"
                "`resume` - Продовжити відтворення\n"
                "`shuffle` - Перемішати чергу треків\n"
                "`skip` - Пропустити поточний трек\n"
                "`stop` - Зупинити відтворення та очистити чергу\n"
                "`volume` - Встановити гучність відтворення (0-100%)\n"
                "`youtube_auth` - Запустити процес авторизації YouTube OAuth2"
            ),
            inline=False
        )

        # Категорія RankCog
        embed.add_field(
            name="RankCog",
            value="`rank` - Показує ранг користувача",
            inline=False
        )

        # Категорія User
        embed.add_field(
            name="User",
            value=(
                "`avatar` - Показує аватар користувача\n"
                "`info` - Показує інформацію про користувача"
            ),
            inline=False
        )

        # Категорія No Category
        embed.add_field(
            name="No Category",
            value="`help` - Shows this message",
            inline=False
        )

        embed.set_footer(
            text=(
                "Type !help <command> for more info on a command.\n"
                "You can also type !help <category> for more info on a category."
            )
        )

        await ctx.send(embed=embed)
        logger.info(f"Help command used by {ctx.author} ({ctx.author.id})")

async def setup(bot: commands.Bot):
    # Використовуємо await, оскільки add_cog є корутиною
    await bot.add_cog(Help(bot))
    logger.info("Loaded Help extension")
