


import logging
import os
from typing import Optional

def setup_logging(log_dir: str = "logs") -> logging.Logger:
    """
    Налаштовує логування для бота:
      - Створює папку logs (якщо її немає)
      - Очищує/створює файли bot.log і errors.log
      - Додає FileHandler для INFO-рівня
      - Додає FileHandler для ERROR-рівня
      - Додає StreamHandler для виводу в консоль
    Повертає логгер з ім’ям "bot".
    """
    # Створюємо логгер для бота
    logger = logging.getLogger("bot")

    # Якщо логгер ще не налаштовано (не має обробників), налаштовуємо його
    if not logger.hasHandlers():
        logger.setLevel(logging.INFO)

        # Створюємо папку для логів, якщо її немає
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # Очищаємо/створюємо файли логів при кожному запуску
        open(os.path.join(log_dir, "bot.log"), "w", encoding="utf-8").close()
        open(os.path.join(log_dir, "errors.log"), "w", encoding="utf-8").close()

        # Форматери для логів
        formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

        # FileHandler для повідомлень INFO (запис у bot.log)
        info_handler = logging.FileHandler(os.path.join(log_dir, "bot.log"), encoding="utf-8")
        info_handler.setLevel(logging.INFO)
        info_handler.setFormatter(formatter)
        logger.addHandler(info_handler)

        # FileHandler для повідомлень ERROR (запис у errors.log)
        error_handler = logging.FileHandler(os.path.join(log_dir, "errors.log"), encoding="utf-8")
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)

        # Консольний обробник для виводу у консоль
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # Забороняємо наслідування обробників від вищого рівня
        logger.propagate = False

        logger.info("Логування налаштовано.")

    return logger

# Приклад використання:
if __name__ == "__main__":
    logger = setup_logging()
    logger.info("Це тестовий запис у логи.")

