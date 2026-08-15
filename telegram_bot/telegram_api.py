from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class TelegramApiError(RuntimeError):
    pass


class TelegramApi:
    ALLOWED_METHODS = {
        "getMe",
        "deleteWebhook",
        "getUpdates",
        "sendMessage",
        "sendPhoto",
        "answerCallbackQuery",
        "getChatMember",
        "setMyDescription",
        "setMyShortDescription",
    }

    def __init__(self, token: str):
        self.base_url = f"https://api.telegram.org/bot{token}"

    def call(self, method: str, payload: dict[str, Any] | None = None, timeout: int = 40):
        if method not in self.ALLOWED_METHODS:
            raise TelegramApiError(f"Метод Telegram API запрещён политикой бота: {method}")
        encoded = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8")
        if len(encoded) > 65536:
            raise TelegramApiError("Запрос Telegram API превышает безопасный размер")
        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=encoded,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "FlexyWayPrizeBot/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(5 * 1024 * 1024 + 1)
                if len(body) > 5 * 1024 * 1024:
                    raise TelegramApiError("Ответ Telegram API превышает безопасный размер")
                result = json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            try:
                description = str(json.loads(body).get("description") or "ошибка API")[:300]
            except (ValueError, AttributeError):
                description = "ошибка API"
            raise TelegramApiError(f"Telegram HTTP {error.code}: {description}") from error
        except (urllib.error.URLError, TimeoutError, OSError, Exception) as error:
            raise TelegramApiError(f"Telegram недоступен: {error}") from error
        if not result.get("ok"):
            raise TelegramApiError(result.get("description", "Неизвестная ошибка Telegram API"))
        return result.get("result")

    def get_me(self):
        return self.call("getMe")

    def delete_webhook(self):
        return self.call("deleteWebhook", {"drop_pending_updates": False})

    def set_my_description(self, description: str, language_code: str = ""):
        return self.call(
            "setMyDescription",
            {"description": description, "language_code": language_code},
        )

    def set_my_short_description(self, short_description: str, language_code: str = ""):
        return self.call(
            "setMyShortDescription",
            {"short_description": short_description, "language_code": language_code},
        )

    def get_updates(self, offset: int | None, timeout: int):
        payload = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        return self.call("getUpdates", payload, timeout=timeout + 10)

    def send_message(self, chat_id: int, text: str, **kwargs):
        if not isinstance(chat_id, int) or chat_id <= 0:
            raise TelegramApiError("Бот может писать только в инициировавший личный чат")
        if len(text) > 4096:
            raise TelegramApiError("Сообщение превышает лимит Telegram")
        payload = {"chat_id": chat_id, "text": text, **kwargs}
        return self.call("sendMessage", payload)

    def send_photo(
        self,
        chat_id: int,
        photo_bytes: bytes,
        filename: str = "welcome_logo.png",
        caption: str = "",
        parse_mode: str = "HTML",
        reply_markup: dict | None = None,
        timeout: int = 40,
    ):
        if not isinstance(chat_id, int) or chat_id <= 0:
            raise TelegramApiError("Бот может писать только в инициировавший личный чат")
        boundary = "---------------------------FlexyWayBoundary9823471"
        body_parts = []

        def add_field(name: str, val: str):
            body_parts.append(
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode("utf-8")
            )

        add_field("chat_id", str(chat_id))
        if caption:
            add_field("caption", caption)
        if parse_mode:
            add_field("parse_mode", parse_mode)
        if reply_markup is not None:
            add_field("reply_markup", json.dumps(reply_markup, ensure_ascii=False))

        body_parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{filename}\"\r\nContent-Type: image/png\r\n\r\n".encode("utf-8")
        )
        body_parts.append(photo_bytes)
        body_parts.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

        data = b"".join(body_parts)
        request = urllib.request.Request(
            f"{self.base_url}/sendPhoto",
            data=data,
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "User-Agent": "FlexyWayPrizeBot/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(5 * 1024 * 1024 + 1)
                result = json.loads(body.decode("utf-8"))
        except urllib.error.HTTPError as error:
            body = error.read().decode("utf-8", errors="replace")
            try:
                description = str(json.loads(body).get("description") or "ошибка API")[:300]
            except (ValueError, AttributeError):
                description = "ошибка API"
            raise TelegramApiError(f"Telegram HTTP {error.code}: {description}") from error
        except (urllib.error.URLError, TimeoutError, OSError, Exception) as error:
            raise TelegramApiError(f"Telegram недоступен: {error}") from error
        if not result.get("ok"):
            raise TelegramApiError(result.get("description", "Неизвестная ошибка Telegram API"))
        return result.get("result")

    def answer_callback_query(self, callback_query_id: str, text: str = "", show_alert: bool = False):
        return self.call(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert,
            },
        )

    def get_chat_member(self, chat_id: str, user_id: int):
        return self.call("getChatMember", {"chat_id": chat_id, "user_id": user_id})
