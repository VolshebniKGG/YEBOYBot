


import logging
import os
from typing import Optional

def setup_logging(log_dir: str = "logs") -> logging.Logger:
    """
    Налаштовує логування для бота:
      - Створює папку logs (якщо немає)
      - Очищує/створює файли bot.log і errors.log
      - Додає FileHandler для INFO-рівня
      - Додає FileHandler для ERROR-рівня
      - Додає StreamHandler для виводу в консоль
    Повертає логгер з ім’ям "bot".
    """

    # Створюємо логгер для бота
    logger = logging.getLogger("bot")

    # Перевіряємо, чи логгер ще не налаштовано (немає обробників)
    if not logger.hasHandlers():
        logger.setLevel(logging.INFO)

        # Створюємо папку logs, якщо її немає
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # Очищаємо/створюємо файли log при кожному запуску
        open(os.path.join(log_dir, "bot.log"), "w").close()
        open(os.path.join(log_dir, "errors.log"), "w").close()

        # Форматери
        info_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        error_formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )

        # FileHandler для INFO
        info_handler = logging.FileHandler(
            os.path.join(log_dir, "bot.log"), encoding="utf-8"
        )
        info_handler.setLevel(logging.INFO)
        info_handler.setFormatter(info_formatter)
        logger.addHandler(info_handler)

        # FileHandler для ERROR
        error_handler = logging.FileHandler(
            os.path.join(log_dir, "errors.log"), encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(error_formatter)
        logger.addHandler(error_handler)

        # Консольний обробник
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(info_formatter)
        logger.addHandler(console_handler)

        # Забороняємо наслідування обробників (не передаємо повідомлення вище в ієрархії)
        logger.propagate = False

        logger.info("Логування налаштовано.")

    # Повертаємо логгер, щоб за потреби можна було використати його в інших місцях
    return logger

# Приклад використання:
if __name__ == "__main__":
    logger = setup_logging()
    logger.info("Це тестовий запис у логи.")

