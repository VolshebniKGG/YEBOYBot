import aiohttp
import asyncio
import json
import logging
import pathlib
import datetime
import time
import uuid
import urllib.parse

from .constants import (
    DATA_FILE_YTDLP_OAUTH2,
    DEFAULT_DATA_DIR,
    DEFAULT_YTDLP_OAUTH2_SCOPES,
    DEFAULT_YTDLP_OAUTH2_TTL,
)

log = logging.getLogger(__name__)


class YouTubeOAuth2Exception(Exception):
    pass


class YouTubeOAuth2Handler:
    _oauth2_token_path = pathlib.Path(DEFAULT_DATA_DIR) / DATA_FILE_YTDLP_OAUTH2
    _client_token_data: dict = {}
    _client_id: str = ""
    _client_secret: str = ""
    _client_scopes: str = DEFAULT_YTDLP_OAUTH2_SCOPES

    @classmethod
    def set_client_id(cls, client_id: str) -> None:
        cls._client_id = client_id

    @classmethod
    def set_client_secret(cls, client_secret: str) -> None:
        cls._client_secret = client_secret

    @classmethod
    async def _save_token_data(cls, token_data: dict) -> None:
        try:
            with open(cls._oauth2_token_path, "w", encoding="utf8") as fh:
                json.dump(token_data, fh)
        except Exception as e:
            log.error("Failed to save OAuth2 token data: %s", e)

    @classmethod
    async def _load_token_data(cls) -> dict:
        if not cls._oauth2_token_path.is_file():
            return {}
        try:
            with open(cls._oauth2_token_path, "r", encoding="utf8") as fh:
                return json.load(fh)
        except Exception as e:
            log.error("Failed to load OAuth2 token data: %s", e)
            return {}

    @classmethod
    async def store_token(cls, token_data: dict) -> None:
        await cls._save_token_data(token_data)
        cls._client_token_data = token_data

    @classmethod
    def validate_token_data(cls, token_data: dict) -> bool:
        required_keys = ("access_token", "expires", "refresh_token", "token_type")
        return all(key in token_data for key in required_keys)

    @classmethod
    async def refresh_token(cls, refresh_token: str) -> dict:
        log.info("Refreshing OAuth2 token...")
        async with aiohttp.ClientSession() as session:
            payload = {
                "client_id": cls._client_id,
                "client_secret": cls._client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            }
            headers = {"Content-Type": "application/json", "__youtube_oauth__": "true"}
            async with session.post(
                "https://www.youtube.com/o/oauth2/token",
                json=payload,
                headers=headers,
            ) as resp:
                token_response = await resp.json()

        if "error" in token_response:
            log.warning("Error refreshing token: %s", token_response["error"])
            return await cls.authorize()  # перезапустити потік авторизації

        new_token = {
            "access_token": token_response["access_token"],
            "expires": datetime.datetime.now(datetime.timezone.utc).timestamp()
            + token_response["expires_in"],
            "token_type": token_response["token_type"],
            "refresh_token": token_response.get("refresh_token", refresh_token),
        }
        return new_token

    @classmethod
    async def authorize(cls) -> dict:
        log.info("Starting OAuth2 authorization flow...")
        async with aiohttp.ClientSession() as session:
            payload = {
                "client_id": cls._client_id,
                "scope": cls._client_scopes,
                "device_id": uuid.uuid4().hex,
                "device_model": "musicbot",
            }
            headers = {"Content-Type": "application/json", "__youtube_oauth__": "true"}
            async with session.post(
                "https://www.youtube.com/o/oauth2/device/code",
                json=payload,
                headers=headers,
            ) as resp:
                code_response = await resp.json()

        verification_url = code_response.get("verification_url")
        user_code = code_response.get("user_code")
        interval = code_response.get("interval", 5)
        ttl = DEFAULT_YTDLP_OAUTH2_TTL

        # Повідомте адміністратора/власника через Discord (наприклад, через команду)
        log.info(
            "Для авторизації перейдіть за посиланням: %s\nВведіть код: %s\nУ вас є %s секунд.",
            verification_url,
            user_code,
            ttl,
        )

        expiry_time = time.time() + ttl
        while time.time() < expiry_time:
            async with aiohttp.ClientSession() as session:
                token_payload = {
                    "client_id": cls._client_id,
                    "client_secret": cls._client_secret,
                    "code": code_response.get("device_code"),
                    "grant_type": "http://oauth.net/grant_type/device/1.0",
                }
                async with session.post(
                    "https://www.youtube.com/o/oauth2/token",
                    json=token_payload,
                    headers=headers,
                ) as token_resp:
                    token_response = await token_resp.json()
            if "error" in token_response:
                error = token_response["error"]
                if error == "authorization_pending":
                    await asyncio.sleep(interval)
                    continue
                elif error == "expired_token":
                    log.warning("Device code expired, restarting authorization flow.")
                    return await cls.authorize()
                else:
                    raise YouTubeOAuth2Exception(f"Unhandled OAuth2 error: {error}")
            else:
                new_token = {
                    "access_token": token_response["access_token"],
                    "expires": datetime.datetime.now(datetime.timezone.utc).timestamp()
                    + token_response["expires_in"],
                    "refresh_token": token_response["refresh_token"],
                    "token_type": token_response["token_type"],
                }
                log.info("OAuth2 authorization successful.")
                return new_token

        raise YouTubeOAuth2Exception("OAuth2 authorization timed out.")

    @classmethod
    async def initialize_oauth(cls) -> dict:
        token_data = await cls._load_token_data()
        if token_data and not cls.validate_token_data(token_data):
            log.warning("Invalid cached OAuth2 token data.")
            token_data = {}
        if not token_data:
            token_data = await cls.authorize()
            await cls.store_token(token_data)
        else:
            # Перевірка чи токен не спливає найближчим часом (60 сек.)
            if token_data.get("expires", 0) < datetime.datetime.now(datetime.timezone.utc).timestamp() + 60:
                log.info("Access token expired or about to expire; refreshing...")
                token_data = await cls.refresh_token(token_data["refresh_token"])
                await cls.store_token(token_data)
        return token_data

    @classmethod
    async def handle_oauth(cls, request: dict) -> None:
        """
        Функція для модифікації запиту (наприклад, для yt-dlp),
        додаванням OAuth2 заголовку, якщо URL стосується youtube.com.
        Очікується, що request має ключ "url" та "headers".
        """
        parsed = urllib.parse.urlparse(request.get("url", ""))
        if not parsed.netloc.endswith("youtube.com"):
            return

        token_data = await cls.initialize_oauth()
        headers = request.get("headers", {})
        # Видаляємо потенційні конфліктуючі заголовки
        for key in [
            "X-Goog-PageId",
            "X-Goog-AuthUser",
            "Authorization",
            "X-Origin",
            "X-Youtube-Identity-Token",
        ]:
            headers.pop(key, None)
        headers["Authorization"] = f'{token_data["token_type"]} {token_data["access_token"]}'
        request["headers"] = headers
