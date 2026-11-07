"""Ordered, readable console presentation without terminal-specific controls."""
import shutil
import textwrap

STATUS_LABELS = {
    "running": "ПРАЦЮЄ",
    "stopped": "ЗУПИНЕНО",
    "starting": "ЗАПУСКАЄТЬСЯ",
    "unknown": "СТАН НЕ ВИЗНАЧЕНО",
}


def _lines(text, width, *, continuation=""):
    return textwrap.wrap(text, width=width, subsequent_indent=continuation,
                         break_long_words=False, break_on_hyphens=False)


def show_menu(actions, status):
    width = min(100, max(40, shutil.get_terminal_size(fallback=(80, 24)).columns))
    content_width = width - 4

    def paragraph(text):
        for line in _lines(text, content_width):
            print("  " + line)

    print("\n" + "═" * width)
    paragraph("YEBOYBot  /  ПАНЕЛЬ КЕРУВАННЯ")
    paragraph("Discord · Музика · Спільнота")
    print("─" * width)
    paragraph("Стан бота: " + STATUS_LABELS.get(status, str(status)))
    print("═" * width)
    paragraph("Оберіть дію. Номери впорядковано за зростанням.")
    print()
    paragraph("[0]  Закрити панель (бот продовжить працювати)")
    # Display original routing IDs, including gaps, to preserve documented
    # commands and existing launcher integrations. Never renumber actions.
    previous_group = None
    for key in sorted(actions, key=int):
        number = int(key)
        group = ("ОСНОВНІ ДІЇ" if number <= 6 else
                 "ДАНІ ТА ЖУРНАЛ" if number <= 10 else
                 "НАЛАШТУВАННЯ ТА ДІАГНОСТИКА" if number <= 18 else
                 "ДОДАТКОВІ ІНСТРУМЕНТИ")
        if group != previous_group:
            print()
            paragraph(group)
            print("  " + "─" * content_width)
            previous_group = group
        for line in _lines(f"[{key}]".ljust(5) + actions[key][0], content_width,
                           continuation="     "):
            print("  " + line)
    print("\n" + "─" * width)
    paragraph("ПЕРШИЙ ЗАПУСК")
    paragraph("Налаштуйте Discord → підготуйте музику → перевірте конфігурацію → запустіть бота.")
    paragraph("Закриття панелі не зупиняє бота. Для цього виберіть «Зупинити бота».")
    print("─" * width)
