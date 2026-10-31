"""Local, read-only slash-command discovery and bounded Ukrainian help pages.

The command tree describes this process. It does not prove that a command has
been published by a remote Discord sync or that an actor can execute it.
"""
from dataclasses import dataclass
from itertools import islice
import unicodedata

import discord
from discord import app_commands

MAX_ENTRIES = 100
PAGE_SIZE = 10
CATEGORY_LABELS = {
    "general": "Початок",
    "rank": "Ранг і XP",
    "moderation": "Модерація",
    "reminders": "Нагадування",
    "music": "Музика",
    "community": "Спільнота",
    "other": "Інші команди",
}
_INTRO = {
    "general": "Команди, підключені до поточного процесу бота. Виберіть розділ у меню. "
               "Цей список сам собою не підтверджує публікацію команд у Discord.",
    "rank": "Чат: 5 XP не частіше разу на 60 секунд. Голос: 5 XP за повну спостережену "
            "хвилину з щонайменше двома придатними учасниками. Аудіо не прослуховується. "
            "У `/leaderboard xp_type` виберіть рейтинг чату, голосу або їхньої суми. "
            "Нагороди ролями: `/level-reward`; отримання зароблених ролей: `/claim-rewards`.",
    "moderation": "Вимоги нижче — довідка. Остаточні перевірки виконує команда; ієрархія "
                  "ролей і доступ до каналу можуть обмежити дію.",
    "reminders": "Створюйте нагадування в поточному каналі, переглядайте й скасовуйте свої "
                 "активні нагадування цього сервера.",
    "music": "Приєднайтеся до голосового каналу, де перебуває бот. Для підключення та "
             "відтворення вам і боту потрібні права бачити канал, підключатися та говорити. "
             "Кожен сервер має власну чергу. Непідтверджену зупинку можна повторити через `/music stop`.",
    "community": "Привітання, безпечні авторолі, журнал подій і базовий антиспам. "
                 "Модулі вмикає адміністратор окремо для кожного сервера.",
    "other": "Команди інших підключених модулів.",
}
_CATEGORIES = {
    "level-reward set": "rank", "level-reward remove": "rank",
    "level-reward list": "rank", "claim-rewards": "rank",
    "role-panel create": "community", "role-panel list": "community",
    "role-panel remove": "community",
    "automod": "community", "community welcome": "community",
    "community autorole": "community", "community logs": "community",
    "help": "general", "modules": "general", "status": "general",
    "rank": "rank", "levels": "rank", "leaderboard": "rank",
    "kick": "moderation", "ban": "moderation", "unban": "moderation",
    "warn": "moderation", "warnings": "moderation", "unwarn": "moderation",
    "timeout": "moderation", "untimeout": "moderation", "clear": "moderation",
    "clear-status": "moderation", "modhistory": "moderation",
    "remind": "reminders", "reminders": "reminders", "cancel-reminder": "reminders",
}
_PERMISSIONS = {
    "level-reward set": "Автору: керувати сервером і ролями; боту: керувати ролями. Лише безпечні ролі.",
    "level-reward remove": "Автору: керувати сервером і ролями. Вже видані ролі залишаються.",
    "level-reward list": "Автору: керувати сервером і ролями. Приватна відповідь.",
    "claim-rewards": "Власні зароблені ролі; боту: керувати ролями. XP має бути увімкнено.",
    "role-panel create": "Автору: керувати сервером і ролями; боту: керувати ролями й надсилати в канал.",
    "role-panel list": "Автору: керувати сервером і ролями. Приватна відповідь.",
    "role-panel remove": "Автору: керувати сервером і ролями. Вже видані ролі залишаються.",
    "help": "Доступно учасникам сервера.",
    "modules": "Доступно учасникам сервера; відповідь приватна.",
    "status": "Доступно учасникам сервера.",
    "rank": "Боту: прикріплювати файли й вбудовувати посилання.",
    "leaderboard": "Боту: вбудовувати посилання.",
    "levels": "Автору: керувати сервером.",
    "warn": "Автору й боту: обмежувати учасників; перевіряється ієрархія.",
    "warnings": "Автору: обмежувати учасників.",
    "unwarn": "Автору: обмежувати учасників.",
    "modhistory": "Власнику або автору з правом вигнання, блокування чи обмеження учасників.",
    "timeout": "Автору й боту: обмежувати учасників; перевіряється ієрархія.",
    "untimeout": "Автору й боту: обмежувати учасників; перевіряється ієрархія.",
    "kick": "Автору й боту: виганяти учасників; перевіряється ієрархія.",
    "ban": "Автору й боту: блокувати учасників; перевіряється ієрархія.",
    "unban": "Автору й боту: блокувати учасників.",
    "clear": "Автору й боту: керувати повідомленнями, бачити канал та історію; підтвердження.",
    "clear-status": "Лише власний запит у початковому каналі; перевіряється доступ.",
    "remind": "Доступ до поточного каналу; боту потрібне надсилання повідомлень.",
    "reminders": "Лише власні нагадування цього сервера.",
    "cancel-reminder": "Лише власне активне нагадування цього сервера.",
    "music play": "Один voice-канал; автору й боту: бачити канал, підключатися та говорити.",
    "music queue": "Перебування в тому самому голосовому каналі.",
    "music skip": "Власний трек або автотрек; чужий — керувати сервером.",
    "music join": "Керувати сервером; автору й боту: підключатися та говорити.",
    "music stop": "Керувати сервером; той самий голосовий канал. Зупинка не потребує права бота говорити.",
    "music pause": "Керувати сервером; той самий голосовий канал. Ручна пауза поточної сесії.",
    "music resume": "Керувати сервером; той самий голосовий канал. Знімає ручну паузу; AutoPause лишається чинною.",
    "music volume": "Керувати сервером; той самий голосовий канал. Гучність 0–100% наявної сесії; без параметра — перегляд.",
    "music autoplay": "Керувати сервером; той самий голосовий канал.",
}
_PERMISSION_LABELS = {
    "administrator": "адміністратор", "manage_guild": "керувати сервером",
    "manage_messages": "керувати повідомленнями", "manage_roles": "керувати ролями",
    "kick_members": "виганяти учасників", "ban_members": "блокувати учасників",
    "moderate_members": "обмежувати учасників",
}


@dataclass(frozen=True)
class CommandEntry:
    name: str
    description: str
    usage: str
    permissions: str
    category: str


def _clean(value):
    if not isinstance(value, str):
        return ""
    return "".join(character for character in value[:4000]
                   if not unicodedata.category(character).startswith("C")).strip()


def _clip_utf16(value, limit):
    if len(value.encode("utf-16-le")) // 2 <= limit:
        return value
    result, units = [], 0
    for character in value:
        width = 2 if ord(character) > 0xFFFF else 1
        if units + width > limit - 1:
            break
        result.append(character)
        units += width
    return "".join(result) + "…"


def _safe(value, limit):
    escaped = discord.utils.escape_mentions(discord.utils.escape_markdown(_clean(value)))
    return _clip_utf16(escaped, limit)


def _permission_hint(command, name):
    if name in _PERMISSIONS:
        return _PERMISSIONS[name]
    permissions = command.default_permissions
    if permissions is None:
        return "Права перевіряються під час виконання команди."
    enabled = [label for flag, label in _PERMISSION_LABELS.items() if getattr(permissions, flag, False)]
    return "Автору: " + ", ".join(enabled[:4]) + "." if enabled else "Права перевіряються під час виконання команди."


def collect_commands(tree, guild_id=None):
    """Return at most100 local slash leaves; guild roots override global roots.

    No fetch/sync API is called. Context-menu commands are intentionally omitted.
    A guild override replaces the whole global root, including its descendants.
    """
    if guild_id is not None and (type(guild_id) is not int or not 0 < guild_id < 2**64):
        raise ValueError("Invalid help guild ID")
    roots = {command.name: command for command in tree.get_commands()
             if isinstance(command, (app_commands.Command, app_commands.Group))}
    if guild_id is not None:
        roots.update((command.name, command) for command in tree.get_commands(guild=discord.Object(id=guild_id))
                     if isinstance(command, (app_commands.Command, app_commands.Group)))
    result, seen = [], set()

    def walk(command, prefix="", depth=0):
        if len(result) >= MAX_ENTRIES or depth > 8 or id(command) in seen:
            return
        seen.add(id(command))
        name = f"{prefix} {command.name}".strip()
        if isinstance(command, app_commands.Group):
            for child in sorted(command.commands, key=lambda item: item.name):
                walk(child, name, depth + 1)
            return
        category = "music" if name.startswith("music ") else _CATEGORIES.get(name, "other")
        arguments = [f"<{parameter.display_name}>" if parameter.required else f"[{parameter.display_name}]"
                     for parameter in command.parameters[:25]]
        usage = " ".join([f"/{name}", *arguments])
        result.append(CommandEntry(_clean(name), _clean(command.description), _clean(usage),
                                   _permission_hint(command, name), category))

    for root in sorted(roots.values(), key=lambda command: command.name):
        walk(root)
    return tuple(result)


def _bounded(entries):
    return tuple(entry for entry in islice(entries, MAX_ENTRIES) if isinstance(entry, CommandEntry))


def available_categories(entries):
    """Offer only populated categories, plus a stable general landing page."""
    populated = {entry.category for entry in _bounded(entries)}
    return tuple(category for category in CATEGORY_LABELS if category == "general" or category in populated)


def _selected(entries, category):
    if not isinstance(category, str) or category not in CATEGORY_LABELS:
        raise ValueError("Unknown help category")
    return tuple(entry for entry in _bounded(entries) if entry.category == category)


def page_count(entries, category="general"):
    return max(1, (len(_selected(entries, category)) + PAGE_SIZE - 1) // PAGE_SIZE)


def render_help(entries, category="general", page=1):
    """Return one private-help page; the caller owns message/view permissions.

    Ten fields at≤96+400UTF16 units leave ample room for title/description/footer
    under Discord's aggregate6000 limit. Optional future command metadata cannot
    expand this page without bound.
    """
    if type(page) is not int or page < 1:
        raise ValueError("Invalid help page")
    entries = _bounded(entries)
    selected = _selected(entries, category)
    pages = max(1, (len(selected) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    embed = discord.Embed(title=f"Довідка · {CATEGORY_LABELS[category]}", description=_INTRO[category],
                          colour=discord.Colour.blurple())
    if category == "general":
        overview = " · ".join(f"{label}: {sum(entry.category == key for entry in entries)}"
                              for key, label in CATEGORY_LABELS.items())
        embed.description += f"\n\nРозділи: {overview}.\nІнший розділ: /help topic."
    for entry in selected[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]:
        description = _safe(entry.description, 120) or "Опис не вказано."
        usage = _safe(entry.usage, 140)
        permissions = _safe(entry.permissions, 100)
        value = f"{description}\n**Виклик:** {usage}\n**Дозволи:** {permissions}"
        embed.add_field(name=_safe("/" + entry.name, 96), value=_clip_utf16(value, 400), inline=False)
    if not selected:
        empty = ("Музичні команди не підключені. Перевірте стан через /modules або зверніться до власника бота."
                 if category == "music" else "У цьому розділі немає підключених команд.")
        embed.add_field(name="Команди", value=empty, inline=False)
    embed.set_footer(text=f"Сторінка {page}/{pages} · Показано підключені команди; вимоги перевіряються при виконанні.")
    return embed
