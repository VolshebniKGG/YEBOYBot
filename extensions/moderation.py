

import discord
from discord.ext import commands
import logging

logger = logging.getLogger('bot')

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='mute', help="Зам'ючує користувача")
    @commands.has_permissions(manage_roles=True)
    async def mute(self, ctx, member: discord.Member, *, reason=None):
        guild = ctx.guild
        mute_role = discord.utils.get(guild.roles, name="Muted")

        if not mute_role:
            # Створюємо роль Muted, якщо вона не існує
            mute_role = await guild.create_role(name="Muted")
            for channel in guild.channels:
                await channel.set_permissions(mute_role, send_messages=False, speak=False)

        await member.add_roles(mute_role, reason=reason)
        await ctx.send(f"✅ {member.mention} був зам'ючений. Причина: {reason}")
        logger.info(f"User {member} was muted by {ctx.author} for reason: {reason}")

    @commands.command(name='unmute', help="Розм'ючує користувача")
    @commands.has_permissions(manage_roles=True)
    async def unmute(self, ctx, member: discord.Member):
        guild = ctx.guild
        mute_role = discord.utils.get(guild.roles, name="Muted")

        if mute_role and mute_role in member.roles:
            await member.remove_roles(mute_role)
            await ctx.send(f"✅ {member.mention} був розм'ючений.")
            logger.info(f"User {member} was unmuted by {ctx.author}")
        else:
            await ctx.send(f"❌ {member.mention} не був зам'ючений.")

async def setup(bot):
    await bot.add_cog(Moderation(bot))
    logger.info('Loaded Moderation extension')
    
