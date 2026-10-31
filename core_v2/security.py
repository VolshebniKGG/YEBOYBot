import discord
from discord import app_commands


class UserError(app_commands.AppCommandError):
    pass


def guild_context(interaction):
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        raise UserError("Команда доступна лише на сервері.")
    return interaction.guild


def permission(interaction, name):
    guild = guild_context(interaction)
    if not getattr(interaction.user.guild_permissions, name, False):
        raise UserError(f"Потрібен дозвіл: {name}.")
    return guild


def target_check(actor, target, bot_member, owner_id):
    if target.id in {actor.id, bot_member.id, owner_id}:
        raise UserError("Не можна застосувати цю дію до себе, бота або власника.")
    if actor.id != owner_id and actor.top_role <= target.top_role:
        raise UserError("Роль учасника не нижча за вашу.")
    if bot_member.top_role <= target.top_role:
        raise UserError("Роль бота має бути вищою за роль учасника.")


DANGEROUS = {"administrator", "manage_guild", "manage_roles", "manage_channels",
             "manage_messages", "manage_webhooks", "ban_members", "kick_members",
             "moderate_members", "mention_everyone", "manage_threads",
             "mute_members", "deafen_members", "move_members", "manage_nicknames",
             "manage_events", "manage_expressions", "create_events", "create_expressions",
             "view_audit_log", "view_guild_insights", "view_creator_monetization_analytics",
             "pin_messages", "bypass_slowmode"}


def safe_role(role, bot_member, actor=None, owner_id=None):
    if role.is_default() or role.managed or role >= bot_member.top_role:
        raise UserError("Цю роль бот не може видавати.")
    if any(getattr(role.permissions, name, False) for name in DANGEROUS):
        raise UserError("Автоматична видача привілейованих ролей заборонена.")
    if actor and actor.id != owner_id and role >= actor.top_role:
        raise UserError("Виберіть роль нижче вашої.")


async def respond(interaction, message, ephemeral=True):
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=ephemeral)
    else:
        await interaction.response.send_message(message, ephemeral=ephemeral)
