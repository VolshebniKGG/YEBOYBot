

import discord
from discord.ext import commands
import json
import os

class User(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.user_data_path = os.path.join("data", "user")
        os.makedirs(self.user_data_path, exist_ok=True)

    def _get_user_file_path(self, user_id: int) -> str:
        """Отримати шлях до файлу користувача за його ID."""
        return os.path.join(self.user_data_path, f"{user_id}.json")

    def _load_user_data(self, user_id: int) -> dict:
        """Завантажити дані користувача з файлу."""
        file_path = self._get_user_file_path(user_id)
        if os.path.exists(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as file:
                    return json.load(file)
            except json.JSONDecodeError:
                return {}
        return {}

    def _save_user_data(self, user_id: int, data: dict) -> None:
        """Зберегти дані користувача до файлу."""
        file_path = self._get_user_file_path(user_id)
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(data, file, indent=4)

    @commands.command(name='info', help="Показує інформацію про користувача")
    async def info(self, ctx, member: discord.Member = None):
        """Показує докладну інформацію про користувача."""
        member = member or ctx.author
        user_data = self._load_user_data(member.id)

        warnings = user_data.get("warnings", 0)
        roles = [role.mention for role in member.roles if role != ctx.guild.default_role]

        # Перевіряємо, чи є у користувача роль Muted
        is_muted = discord.utils.get(member.roles, name="Muted")
        mute_reason = user_data.get("mute_reason", "Немає даних")

        embed = discord.Embed(title=f"Інформація про {member.display_name}", color=member.color)
        embed.add_field(name="Ім'я", value=member.name, inline=True)
        embed.add_field(name="ID", value=member.id, inline=True)
        embed.add_field(name="Статус", value=member.status, inline=True)
        embed.add_field(name="Дата створення", value=member.created_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        embed.add_field(name="Дата приєднання", value=member.joined_at.strftime("%Y-%m-%d %H:%M:%S"), inline=True)
        embed.add_field(name="Ролі", value=", ".join(roles) if roles else "Немає ролей", inline=False)
        embed.add_field(name="Попередження", value=f"{warnings} попередження(нь)", inline=True)
        
        # Якщо користувач зараз зам'ючений, додамо поле з причиною
        if is_muted:
            embed.add_field(name="Mute Reason", value=mute_reason, inline=False)

        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Запитано {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)

        await ctx.send(embed=embed)

    @commands.command(name='add_warning', help="Додає попередження користувачу")
    @commands.has_permissions(manage_messages=True)
    async def add_warning(self, ctx, member: discord.Member, *, reason="Немає причини"):
        """Додає попередження користувачу."""
        user_data = self._load_user_data(member.id)
        user_data["warnings"] = user_data.get("warnings", 0) + 1
        user_data.setdefault("reasons", []).append(reason)
        self._save_user_data(member.id, user_data)

        await ctx.send(f"⚠️ {member.mention} отримав попередження. Причина: {reason}.\nЗагальна кількість попереджень: {user_data['warnings']}")

    @commands.command(name='clear_warnings', help="Очищає всі попередження користувача")
    @commands.has_permissions(manage_messages=True)
    async def clear_warnings(self, ctx, member: discord.Member):
        """Очищає всі попередження користувача."""
        user_data = self._load_user_data(member.id)
        if "warnings" in user_data:
            user_data["warnings"] = 0
            user_data["reasons"] = []
            self._save_user_data(member.id, user_data)
            await ctx.send(f"✅ Усі попередження для {member.mention} очищено.")
        else:
            await ctx.send(f"❌ У {member.mention} немає жодного попередження.")

    @commands.command(name='avatar', help="Показує аватар користувача")
    async def avatar(self, ctx, member: discord.Member = None):
        """Відправляє аватар вибраного користувача."""
        member = member or ctx.author
        embed = discord.Embed(title=f"Аватар {member.display_name}", color=member.color)
        embed.set_image(url=member.display_avatar.url)
        embed.set_footer(text=f"Запитано {ctx.author.display_name}", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(User(bot))

