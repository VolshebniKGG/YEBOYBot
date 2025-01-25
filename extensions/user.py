

import discord
from discord.ext import commands
import json
import os

class User(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cache_path = "cache/user_data.json"
        os.makedirs("cache", exist_ok=True)
        if not os.path.exists(self.cache_path):
            with open(self.cache_path, "w", encoding="utf-8") as file:
                json.dump({}, file, indent=4)

    def _load_user_data(self):
        """Завантажити дані користувачів з файлу."""
        with open(self.cache_path, "r", encoding="utf-8") as file:
            return json.load(file)

    def _save_user_data(self, data):
        """Зберегти дані користувачів у файл."""
        with open(self.cache_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4)

    @commands.command(name='info', help="Показує інформацію про користувача")
    async def info(self, ctx, member: discord.Member = None):
        """Показує докладну інформацію про користувача."""
        member = member or ctx.author
        user_data = self._load_user_data()
        warnings = user_data.get(str(member.id), {}).get("warnings", 0)
        roles = [role.mention for role in member.roles if role != ctx.guild.default_role]

        embed = discord.Embed(title=f"Інформація про {member.display_name}", color=member.color)
        embed.add_field(name="Ім'я", value=member.name, inline=True)
        embed.add_field(name="ID", value=member.id, inline=True)
        embed.add_field(name="Статус", value=member.status, inline=True)
        embed.add_field(name="Дата створення", value=member.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        embed.add_field(name="Дата приєднання", value=member.joined_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        embed.add_field(name="Ролі", value=", ".join(roles) if roles else "Немає ролей", inline=False)
        embed.add_field(name="Попередження", value=f"{warnings} попередження(нь)", inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Запитано {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

        await ctx.send(embed=embed)

    @commands.command(name='add_warning', help="Додає попередження користувачу")
    @commands.has_permissions(manage_messages=True)
    async def add_warning(self, ctx, member: discord.Member, *, reason="Немає причини"):
        """Додає попередження користувачу."""
        user_data = self._load_user_data()
        user_info = user_data.setdefault(str(member.id), {"warnings": 0, "reasons": []})
        user_info["warnings"] += 1
        user_info["reasons"].append(reason)
        self._save_user_data(user_data)

        await ctx.send(f"⚠️ {member.mention} отримав попередження. Причина: {reason}.\nЗагальна кількість попереджень: {user_info['warnings']}")

    @commands.command(name='clear_warnings', help="Очищає всі попередження користувача")
    @commands.has_permissions(manage_messages=True)
    async def clear_warnings(self, ctx, member: discord.Member):
        """Очищає всі попередження користувача."""
        user_data = self._load_user_data()
        if str(member.id) in user_data:
            user_data[str(member.id)]["warnings"] = 0
            user_data[str(member.id)]["reasons"] = []
            self._save_user_data(user_data)
            await ctx.send(f"✅ Усі попередження для {member.mention} очищено.")
        else:
            await ctx.send(f"❌ У {member.mention} немає жодного попередження.")

async def setup(bot):
    await bot.add_cog(User(bot))


