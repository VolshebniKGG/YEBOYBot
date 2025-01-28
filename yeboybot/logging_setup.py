


import logging
import os

def setup_logging(log_dir='logs'):
    """Налаштування логування з уникненням дублювання обробників."""
    # Створюємо логгер для бота
    logger = logging.getLogger('bot')

    # Перевіряємо, чи вже є обробники
    if not logger.hasHandlers():
        logger.setLevel(logging.INFO)

        # Створюємо папку logs, якщо її немає
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # Очищуємо файли log при кожному запуску
        open(f'{log_dir}/bot.log', 'w').close()
        open(f'{log_dir}/errors.log', 'w').close()

        # Лог для загальної інформації
        info_handler = logging.FileHandler(f'{log_dir}/bot.log', encoding='utf-8')
        info_handler.setLevel(logging.INFO)
        info_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
        info_handler.setFormatter(info_formatter)
        logger.addHandler(info_handler)

        # Лог для помилок
        error_handler = logging.FileHandler(f'{log_dir}/errors.log', encoding='utf-8')
        error_handler.setLevel(logging.ERROR)
        error_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
        error_handler.setFormatter(error_formatter)
        logger.addHandler(error_handler)

        # Консольний лог
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(info_formatter)
        logger.addHandler(console_handler)

        # Забороняємо наслідування обробників
        logger.propagate = False

        logger.info("Логування налаштовано.")
