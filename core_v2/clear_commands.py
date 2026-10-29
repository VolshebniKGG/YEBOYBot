"""Private clear confirmation UI; the service owns authorization and durable state."""
import asyncio
import logging
import re

import discord

from .moderation import ModerationError
from .security import UserError, guild_context

log = logging.getLogger(__name__)
MENTIONS = discord.AllowedMentions.none()

STATES = {
    "clear_confirm": "Очікує підтвердження",
    "clear_running": "Результат ще не підтверджений",
    "clear_pending": "Результат не підтверджений",
    "clear_done": "Запит підтверджено Discord",
    "clear_cancelled": "Скасовано до надсилання",
    "clear_expired": "Час підтвердження минув",
    "clear_failed": "Зупинено до надсилання",
}


def _clip(value, limit=600):
    """Keep escaped reasons within Discord's conservative UTF-16 field budget."""
    result, units = [], 0
    for character in value:
        size = len(character.encode("utf-16-le", errors="replace")) // 2
        if units + size > limit - 1:
            return "".join(result) + "…"
        result.append(character)
        units += size
    return "".join(result)


def _record_embed(record):
    plan, state = record["plan"], record["state"]
    description = (
        "**Видалення повідомлень незворотне.** Підтвердьте збережений вибір."
        if state == "clear_confirm" else STATES.get(state, "Невідомо")
    )
    embed = discord.Embed(title="Очищення повідомлень", description=description)
    embed.add_field(name="Канал", value=f"<#{plan.channel_id}>", inline=False)
    embed.add_field(name="Збережений вибір", value=(
        f"Вибрано: **{len(plan.message_ids)}**. Ліміт перегляду: **{plan.requested}**.\n"
        "За один запит — до 25 повідомлень, молодших за 7 діб."
    ), inline=False)
    embed.add_field(name="Пропущено", value=(
        f"Закріплені: {plan.skipped_pinned} · старі: {plan.skipped_old} · "
        f"системні: {plan.skipped_system}."
    ), inline=False)
    reason = discord.utils.escape_markdown(discord.utils.escape_mentions(plan.reason))
    embed.add_field(name="Причина", value=_clip(reason), inline=False)
    embed.add_field(name="ID запиту", value=f"`{plan.id}`", inline=False)
    embed.add_field(name="Збережений стан", value=STATES.get(state, "Невідомо"), inline=False)
    if state == "clear_confirm":
        embed.add_field(name="Підтвердити до", value=f"<t:{int(plan.expires)}:R>", inline=False)
    elif state == "clear_done":
        embed.add_field(name="Результат", value=(
            f"Discord підтвердив запит для {len(plan.message_ids)} повідомлень. "
            "Це не окремий підрахунок повідомлень, видалених саме цим запитом."
        ), inline=False)
    elif state in {"clear_running", "clear_pending"}:
        embed.add_field(name="Перевірка", value=(
            "Стан сам собою не доводить, чи видалення відбулося. "
            "Не повторюйте дію навмання; звірте канал і журнал Discord."
        ), inline=False)
    embed.set_footer(text="Перевірка збереженого запиту: /clear-status request_id")
    return embed


def _result_text(result, request_id):
    state, code = result.state, result.code
    if state == "clear_done":
        text = (f"Discord підтвердив запит для {result.count} повідомлень."
                if type(result.count) is int and 1 <= result.count <= 25 else
                "Збережений запит підтверджено Discord. Повторного видалення не запускали.")
    elif code == "api_confirmed_db_unknown":
        text = (f"Discord підтвердив запит для {result.count} повідомлень, "
                "але збереження результату не підтверджено. Не повторюйте дію.")
    elif state in {"clear_running", "clear_pending"}:
        text = "Результат видалення не підтверджений. Не повторюйте дію навмання."
    elif state in {"clear_cancelled", "clear_expired", "clear_failed"}:
        text = STATES[state] + "."
    else:
        text = "Підтверджений підсумок недоступний; перевірте /clear-status."
    if result.message:
        text += "\n" + _clip(result.message)
    text += f"\nID запиту: `{request_id}`\nЗбережений стан: {STATES.get(state, 'Невідомо')}."
    return text


async def _reply(interaction, message):
    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True, allowed_mentions=MENTIONS)
    else:
        await interaction.response.send_message(message, ephemeral=True, allowed_mentions=MENTIONS)


class ClearConfirmation(discord.ui.View):
    """An ephemeral, one-use view bound to the original actor and channel."""
    def __init__(self, service, plan):
        super().__init__(timeout=60)
        self.service = service
        self.request_id = plan.id
        self.guild_id = plan.guild_id
        self.actor_id = plan.actor_id
        self.channel_id = plan.channel_id
        self.message = None
        self._consumed = False
        self._lock = asyncio.Lock()

    def _owned(self, interaction):
        return bool(interaction.guild and isinstance(interaction.user, discord.Member) and
                    interaction.guild.id == self.guild_id and interaction.user.id == self.actor_id and
                    interaction.channel_id == self.channel_id)

    def _disable(self):
        for item in self.children:
            item.disabled = True
        self.stop()

    async def _edit_view(self):
        if self.message is None:
            return
        try:
            async with asyncio.timeout(3):
                await self.message.edit(view=self, allowed_mentions=MENTIONS)
        except Exception as error:
            log.debug("Clear preview edit unconfirmed error=%s", type(error).__name__)

    async def _run(self, interaction, action):
        if not self._owned(interaction):
            await _reply(interaction, "Це підтвердження доступне лише автору в початковому каналі сервера.")
            return
        await interaction.response.defer(ephemeral=True)
        async with self._lock:
            if self._consumed:
                await _reply(interaction, "Це підтвердження вже використано або завершено. Перевірте /clear-status.")
                return
            self._consumed = True
            self._disable()
            try:
                result = await getattr(self.service, action)(
                    self.guild_id, self.actor_id, self.channel_id, self.request_id)
            except ModerationError as error:
                text = f"{error}\nПеревірте /clear-status. ID запиту: `{self.request_id}`"
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.warning("Clear confirmation outcome unavailable error=%s", type(error).__name__)
                text = ("Не вдалося підтвердити підсумок. Не повторюйте дію навмання; "
                        f"перевірте /clear-status. ID запиту: `{self.request_id}`")
            else:
                text = _result_text(result, self.request_id)
            finally:
                await self._edit_view()
        await _reply(interaction, text)

    @discord.ui.button(label="Підтвердити видалення", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "execute")

    @discord.ui.button(label="Скасувати", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._run(interaction, "cancel")

    async def on_timeout(self):
        self._consumed = True
        self._disable()
        await self._edit_view()

    async def on_error(self, interaction, error, item):
        # Discord invokes this for callback/response failures. Payloads may be private.
        log.warning("Clear component response unavailable error=%s", type(error).__name__)
        try:
            await _reply(interaction, "Відповідь недоступна. Перевірте збережений результат через /clear-status.")
        except Exception as response_error:
            log.debug("Clear component error response unavailable error=%s", type(response_error).__name__)


async def preview(interaction, service, amount, reason):
    guild = guild_context(interaction)
    await interaction.response.defer(ephemeral=True)
    try:
        record = await service.prepare(guild.id, interaction.user.id, interaction.channel_id,
                                       interaction.id, amount, reason)
    except ModerationError as error:
        raise UserError(str(error)) from None
    except Exception as error:
        log.warning("Clear preview unavailable error=%s", type(error).__name__)
        raise UserError("Не вдалося зберегти попередній вибір. Видалення не запускали.") from None
    view = ClearConfirmation(service, record["plan"]) if record["state"] == "clear_confirm" else None
    message = await interaction.followup.send(embed=_record_embed(record), view=view, ephemeral=True,
                                             allowed_mentions=MENTIONS, wait=True)
    if view is not None:
        view.message = message


async def status(interaction, service, request_id):
    guild = guild_context(interaction)
    await interaction.response.defer(ephemeral=True)
    if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-f]{32}", request_id):
        raise UserError("Вкажіть ID запиту: 32 малі шістнадцяткові символи.")
    try:
        record = await service.status(guild.id, interaction.user.id, interaction.channel_id, request_id)
    except ModerationError as error:
        raise UserError(str(error)) from None
    except Exception as error:
        log.warning("Clear status unavailable error=%s", type(error).__name__)
        raise UserError("Не вдалося прочитати стан запиту очищення.") from None
    await interaction.followup.send(embed=_record_embed(record), ephemeral=True, allowed_mentions=MENTIONS)
