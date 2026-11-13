"""Readable, grouped and sequential console UI without third-party packages."""
import shutil
import textwrap

STATUS_LABELS = {"running": "ПРАЦЮЄ", "stopped": "ЗУПИНЕНО",
                 "starting": "ЗАПУСКАЄТЬСЯ", "unknown": "СТАН НЕ ВИЗНАЧЕНО"}


def _lines(text, width, *, continuation=""):
    return textwrap.wrap(text, width=width, subsequent_indent=continuation,
                         break_long_words=False, break_on_hyphens=False)


def show_menu(actions, status, version=None):
    width = min(100, max(40, shutil.get_terminal_size(fallback=(80, 24)).columns))
    content = width - 4

    def line(value=""):
        # Keep full words on one line when possible, while still bounding
        # extremely long branch names to the current terminal width.
        remaining = str(value)
        if not remaining:
            print("║ " + " " * content + " ║")
        while remaining:
            if len(remaining) <= content:
                part, remaining = remaining, ""
            else:
                space = remaining.rfind(" ", 0, content + 1)
                if space > 0:
                    part, remaining = remaining[:space], remaining[space + 1:].lstrip()
                else:
                    part, remaining = remaining[:content], remaining[content:]
            print("║ " + part.ljust(content) + " ║")

    print("\n" + "╔" + "═" * (width - 2) + "╗")
    line("YEBOYBot   /   ПАНЕЛЬ КЕРУВАННЯ")
    line("Discord  •  Музика  •  Спільнота  •  GitHub")
    print("╠" + "═" * (width - 2) + "╣")
    line("Стан: " + STATUS_LABELS.get(status, str(status)).replace("\n", " ")[:content - 7])
    if version is not None:
        line("Версія: " + str(version).replace("\n", " ")[:160])
    line("Запуск, оновлення та відновлення в одному меню.")
    print("╚" + "═" * (width - 2) + "╝")
    print()
    for part in _lines("[0]  Закрити панель (Discord-бот продовжить працювати)",
                       content, continuation="     "):
        print("  " + part)
    previous = None
    groups = {
        "ОСНОВНІ ДІЇ": range(1, 7),
        "ДАНІ ТА ЖУРНАЛ": range(7, 11),
        "НАЛАШТУВАННЯ ТА ДІАГНОСТИКА": range(11, 15),
        "ДОДАТКОВІ ІНСТРУМЕНТИ": range(15, 1000),
    }
    for key in sorted(actions, key=int):
        number = int(key)
        group = next((title for title, ids in groups.items() if number in ids), "ІНШІ ДІЇ")
        if group != previous:
            print()
            print("  " + "─" * content)
            print("  " + group)
            previous = group
        row = f"[{number}]".ljust(6) + actions[key][0]
        for segment in _lines(row, content, continuation="      "):
            print("  " + segment)
    print()
    print("  " + "─" * content)
    for tip in (
        "ПЕРШИЙ ЗАПУСК · Discord → музика → перевірка → запуск.",
        "Оновлення GitHub: [6] → вибір гілки → підтвердження.",
        "Вихід — кнопка 0; для зупинки Discord-бота використайте [3].",
    ):
        for segment in _lines(tip, content):
            print("  " + segment)
    print("  " + "─" * content)
