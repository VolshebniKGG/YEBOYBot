import subprocess
from typing import List

# constant string exempt from i18n
DEFAULT_FOOTER_TEXT: str = f"Volshebnik_/YEBOYBot"
DEFAULT_BOT_NAME: str = "YEBOYBot"
DEFAULT_BOT_ICON: str = "https://i.imgur.com/gFHBoZA.png"
DEFAULT_OWNER_GROUP_NAME: str = "Owner (auto)"
DEFAULT_PERMS_GROUP_NAME: str = "Default"
# Цей рядок UA використовується YEBOYBot тільки для сесії aiohttp.
# Мається на увазі зв'язок між discord API та spotify API.
# НЕ використовується ytdlp, вони мають функцію динамічного вибору UA.
MUSICBOT_USER_AGENT_AIOHTTP: str = f"YEBOYBot"


# Константи шляху до файлу
DEFAULT_OPTIONS_FILE: str = "config/options.ini"
DEFAULT_PERMS_FILE: str = "config/permissions.ini"
DEFAULT_COMMAND_ALIAS_FILE: str = "config/aliases.json"
DEFAULT_USER_BLOCKLIST_FILE: str = "config/blocklist_users.txt"
DEFAULT_SONG_BLOCKLIST_FILE: str = "config/blocklist_songs.txt"
DEPRECATED_USER_BLACKLIST: str = "config/blacklist.txt"
OLD_DEFAULT_AUTOPLAYLIST_FILE: str = "config/autoplaylist.txt"
OLD_BUNDLED_AUTOPLAYLIST_FILE: str = "config/_autoplaylist.txt"
DEFAULT_PLAYLIST_DIR: str = "config/playlists/"
DEFAULT_MEDIA_FILE_DIR: str = "media/"
DEFAULT_AUDIO_CACHE_DIR: str = "audio_cache/"
DEFAULT_DATA_DIR: str = "data/"

# Назви файлів у теках DEFAULT_DATA_DIR або гільдії.
DATA_FILE_SERVERS: str = "server_names.txt"
DATA_FILE_CACHEMAP: str = "playlist_cachemap.json"
DATA_FILE_COOKIES: str = "cookies.txt"  # Це не підтримується, прочитайте документацію з yt-dlp.
DATA_FILE_YTDLP_OAUTH2: str = "oauth2.token"
DATA_GUILD_FILE_QUEUE: str = "queue.json"
DATA_GUILD_FILE_CUR_SONG: str = "current.txt"
DATA_GUILD_FILE_OPTIONS: str = "options.json"

# Приклади конфігураційних файлів.
EXAMPLE_OPTIONS_FILE: str = "config/example_options.ini"
EXAMPLE_PERMS_FILE: str = "config/example_permissions.ini"
EXAMPLE_COMMAND_ALIAS_FILE: str = "config/example_aliases.json"

# Налаштування, пов'язані зі списком відтворення.
APL_FILE_DEFAULT: str = "default.txt"
APL_FILE_HISTORY: str = "history.txt"
APL_FILE_APLCOPY: str = "autoplaylist.txt"

# Константи, пов'язані з веденням журналу
DEFAULT_MUSICBOT_LOG_FILE: str = "logs/musicbot.log"
DEFAULT_DISCORD_LOG_FILE: str = "logs/discord.log"
# За замовчуванням 0, для відсутності обертання взагалі.
DEFAULT_LOGS_KEPT: int = 0
MAXIMUM_LOGS_LIMIT: int = 100
# Це значення пропускається через strftime(), а потім вставляється між
DEFAULT_LOGS_ROTATE_FORMAT: str = ".ended-%Y-%j-%H%m%S"
# Рівень журналу за замовчуванням може бути одним з:
# CRITICAL, ERROR, WARNING, INFO, DEBUG,
# VOICEDEBUG, FFMPEG, NOISY або ВСЕ
DEFAULT_LOG_LEVEL: str = "INFO"

# За замовчуванням цільовий FQDN або IP для пінгування мережевим тестером.
DEFAULT_PING_TARGET: str = "discord.com"
# URI за замовчуванням, який використовується для резервного тестування мережі HTTP.
# Цей URI має бути доступним через стандартний HTTP на вищевказаному домені/IP-адресі.
DEFAULT_PING_HTTP_URI: str = "/robots.txt"
# Максимальний час у секундах, протягом якого пінг повинен чекати на відповідь.
DEFAULT_PING_TIMEOUT: int = 5
# Час у секундах для очікування між пінгами.
DEFAULT_PING_SLEEP: float = 2
# Налаштування часу пінгу для резервного HTTP.
FALLBACK_PING_TIMEOUT: int = 15
FALLBACK_PING_SLEEP: float = 4

# Мінімальна кількість секунд для очікування з'єднання VoiceClient.
VOICE_CLIENT_RECONNECT_TIMEOUT: int = 5
# Максимальна кількість повторних спроб для з'єднання з VoiceClient.
# Кожна повторна спроба збільшує таймаут, множачи кількість спроб на вищевказаний таймаут.
VOICE_CLIENT_MAX_RETRY_CONNECT: int = 5

# Максимальна кількість потоків, які MusicBot буде використовувати для завантаження та видобування інформації.
DEFAULT_MAX_INFO_DL_THREADS: int = 2
# Максимальна кількість секунд очікування на запит HEAD для медіафайлів.
DEFAULT_MAX_INFO_REQUEST_TIMEOUT: int = 10

# Час очікування перед початком попереднього завантаження під час відтворення нової пісні.
DEFAULT_PRE_DOWNLOAD_DELAY: float = 4.0

# Час у секундах для очікування перед невдалою авторизацією oauth2.
# Це дає час для авторизації, а також запобігає зависанню процесу при вимкненні.
DEFAULT_YTDLP_OAUTH2_TTL: float = 180.0

# Діапазони за замовчуванням / запасні діапазони, що використовуються для плагіна OAuth2 ytdlp.
DEFAULT_YTDLP_OAUTH2_SCOPES: str = (
    "http://gdata.youtube.com https://www.googleapis.com/auth/youtube"
)
# Екстрактори інформації для виключення з виправлень OAuth2, коли OAuth2 увімкнено.
YTDLP_OAUTH2_EXCLUDED_IES: List[str] = [
    "YoutubeBaseInfoExtractor",
    "YoutubeTabBaseInfoExtractor",
]
# Творці клієнтів Yt-dlp, які не сумісні з плагіном OAuth2.
YTDLP_OAUTH2_UNSUPPORTED_CLIENTS: List[str] = [
    "web_creator",
    "android_creator",
    "ios_creator",
]
# Додаткові клієнти Yt-dlp, які слід додати до списку клієнтів OAuth2.
YTDLP_OAUTH2_CLIENTS: List[str] = ["mweb"]

# Discord та інші константи API
DISCORD_MSG_CHAR_LIMIT: int = 2000
