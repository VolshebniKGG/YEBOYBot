"""Portable console presentation; no terminal control codes or side effects."""
import shutil
import textwrap


GROUPS = (
    ("КЕРУВАННЯ БОТОМ", ("1", "2", "3", "9")),
    ("НАЛАШТУВАННЯ", ("12", "11", "25", "15")),
    ("ДАНІ ТА РЕЗЕРВНІ КОПІЇ", ("7", "8", "10", "20", "21", "23", "26")),
    ("ОБСЛУГОВУВАННЯ", ("4", "18", "6", "5", "24", "22")),
)


def _section(title, keys, actions, width):
    lines = [title, "─" * min(width, len(title) + 5)]
    for key in keys:
        label = actions[key][0]
        lines.extend(textwrap.wrap(f"[{key}] {label}", width=width,
            subsequent_indent="     ", break_long_words=False, break_on_hyphens=False))
    return lines


def show_menu(actions, status):
    width = min(120, max(60, shutil.get_terminal_size(fallback=(80, 24)).columns))
    print("\n" + "═" * width)
    print("  YEBOYBot  /  ПАНЕЛЬ КЕРУВАННЯ")
    print("  Core V2 · Ваш сервер, музика та спільнота")
    print("─" * width)
    for line in textwrap.wrap(f"Стан бота: {status}", width=width - 4):
        print("  " + line)
    print("═" * width)
    if width >= 112:
        column = (width - 6) // 2
        for index in (0, 2):
            left = _section(*GROUPS[index], actions, column)
            right = _section(*GROUPS[index + 1], actions, column)
            print()
            for row in range(max(len(left), len(right))):
                first = left[row] if row < len(left) else ""
                second = right[row] if row < len(right) else ""
                print("  " + first.ljust(column) + "  " + second)
    else:
        for title, keys in GROUPS:
            print()
            for line in _section(title, keys, actions, width - 4):
                print("  " + line)
    print("\n" + "─" * width)
    print("  Перший запуск: [12] Налаштування → [4] Перевірка → [1] Запуск")
    print("  [0] Закрити панель · Працюючий бот залишиться увімкненим")
    print("  Щоб зупинити бота, виберіть [3].")
    print("─" * width)
