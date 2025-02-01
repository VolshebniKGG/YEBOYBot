

import discord
from discord.ext import commands
import logging
import os
import json

# Налаштування логування
logger = logging.getLogger('moderation_bot')
logger.setLevel(logging.INFO)

# Створимо хендлер для виводу в консоль
console_handler = logging.StreamHandler()
console_format = logging.Formatter(
    '%(asctime)s %(levelname)s:%(name)s: %(message)s'
)
console_handler.setFormatter(console_format)
logger.addHandler(console_handler)

class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Папка, де зберігатимуться JSON-файли користувачів
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

    @commands.command(name='mute', help="Зам'ючує користувача")
    @commands.has_permissions(manage_roles=True)
    async def mute(self, ctx, member: discord.Member, *, reason=None):
        """Команда для видачі ролі Muted користувачу."""
        try:
            guild = ctx.guild

            # Шукаємо роль Muted
            mute_role = discord.utils.get(guild.roles, name="Muted")

            # Якщо роли немає - створимо
            if not mute_role:
                mute_role = await guild.create_role(name="Muted")
                for channel in guild.channels:
                    await channel.set_permissions(mute_role, send_messages=False, speak=False)

            # Додаємо роль Muted
            await member.add_roles(mute_role, reason=reason)

            # Зберігаємо причину муту у JSON-файл користувача
            user_data = self._load_user_data(member.id)
            user_data["mute_reason"] = reason if reason else "Не вказано причини"
            self._save_user_data(member.id, user_data)

            await ctx.send(f"✅ {member.mention} був зам'ючений. Причина: {reason}")
            logger.info(f"{ctx.author} зам'ютив користувача {member} | Причина: {reason}")

        except Exception as e:
            logger.error(f"Помилка під час виконання команди mute: {e}")
            await ctx.send(f"❌ Не вдалося зам'ютити {member.mention}. Перевірте права та спробуйте ще раз.")

    @commands.command(name='unmute', help="Розм'ючує користувача")
    @commands.has_permissions(manage_roles=True)
    async def unmute(self, ctx, member: discord.Member):
        """Команда для зняття ролі Muted з користувача."""
        try:
            guild = ctx.guild
            mute_role = discord.utils.get(guild.roles, name="Muted")

            if mute_role and mute_role in member.roles:
                # Видаляємо роль
                await member.remove_roles(mute_role)

                # Очищаємо інформацію про мут (щоб не відображалося в info)
                user_data = self._load_user_data(member.id)
                if "mute_reason" in user_data:
                    del user_data["mute_reason"]
                self._save_user_data(member.id, user_data)

                await ctx.send(f"✅ {member.mention} був розм'ючений.")
                logger.info(f"{ctx.author} розм'ютив користувача {member}")
            else:
                await ctx.send(f"❌ {member.mention} не має ролі Muted.")
        except Exception as e:
            logger.error(f"Помилка під час виконання команди unmute: {e}")
            await ctx.send(f"❌ Не вдалося розм'ютити {member.mention}. Перевірте права та спробуйте ще раз.")

    @commands.Cog.listener()
    async def on_command_error(self, ctx, error):
        """
        Слухач подій, що автоматично викликається,
        коли у межах цієї Cog виникає помилка виконання команди.
        """
        logger.error(f"Помилка в команді {ctx.command}: {error}")

        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ У вас немає прав для використання цієї команди.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"❌ Ви пропустили обов'язковий аргумент. Використайте: {ctx.prefix}{ctx.command} {ctx.command.signature}")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("❌ Невірний аргумент. Перевірте введені дані.")
        else:
            await ctx.send("❌ Сталася помилка при виконанні команди. Перевірте лог для подробиць.")

async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
    logger.info('✅ Cog Moderation успішно завантажено')
    
