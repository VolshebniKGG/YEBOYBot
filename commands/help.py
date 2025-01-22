



import discord
from discord.ext import commands
import logging

logger = logging.getLogger('bot')

class Help(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='helpme', help='Показує список команд')
    async def helpme(self, ctx):
        embed = discord.Embed(title="Список команд", color=0x00ff00)
        embed.add_field(name="!play [url]", value="Відтворює пісню з YouTube", inline=False)
        embed.add_field(name="!pause", value="Пауза відтворення", inline=False)
        embed.add_field(name="!skip", value="Пропустити пісню", inline=False)
        embed.add_field(name="!stop", value="Зупинити відтворення", inline=False)
        embed.add_field(name="!kick [user]", value="Кикає користувача", inline=False)
        embed.add_field(name="!ban [user]", value="Банить користувача", inline=False)
        await ctx.send(embed=embed)
        logger.info(f'Help command used by {ctx.author}')

async def setup(bot):
    await bot.add_cog(Help(bot))
    logger.info('Loaded Help extension')

    
    
    