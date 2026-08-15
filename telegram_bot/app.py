from __future__ import annotations

import html
import logging
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from .config import Config
from .database import BotDataError, Database, OutOfStock, PrizeLimitReached
from .telegram_api import TelegramApi, TelegramApiError
from .workbook import PromoWorkbook, WorkbookError


LOGGER = logging.getLogger(__name__)
START_PAYLOAD = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PHONE_PATTERN = re.compile(r"^\+[1-9][0-9]{6,14}$")
COMMON_PRIZE_TERMS = (
    "• Все призы действуют только для новых клиентов (кроме абонементов — "
    "они распространяются на всех клиентов).\n"
    "• Призом можно воспользоваться только после пробного занятия.\n"
    "• Приз можно использовать только один раз и только тому, кто его выиграл.\n"
    "• Скидки и бонусы не суммируются с другими акциями."
)
BOT_WELCOME_DESCRIPTION = (
    "Привет! Тут Flexy Way раздает призы! Скидку 50% на пробное занятие получает каждый! "
    "А еще тебя ждет персональный приз! Жми СТАРТ!"
)
BOT_SHORT_DESCRIPTION = "Раздача призов от Flexy Way! Скидка 50% на пробное занятие и персональный приз!"
WELCOME_IMAGE_PATH = Path(__file__).resolve().parent / "welcome_logo.png"


class BotApp:
    def __init__(
        self,
        config: Config,
        database: Database,
        workbook: PromoWorkbook,
        api: TelegramApi,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.database = database
        self.workbook = workbook
        self.api = api
        self.clock = clock
        self._activity: dict[int, deque[float]] = defaultdict(deque)
        self._subscription_checks: dict[int, deque[float]] = defaultdict(deque)

    @staticmethod
    def _contact_keyboard() -> dict:
        return {
            "keyboard": [[{"text": "📱 Поделиться номером", "request_contact": True}]],
            "resize_keyboard": True,
            "one_time_keyboard": True,
            "input_field_placeholder": "Нажмите кнопку ниже",
        }

    @staticmethod
    def _consent_keyboard() -> dict:
        return {
            "inline_keyboard": [
                [{"text": "✅ Согласен и продолжить", "callback_data": "consent"}],
                [{"text": "Отказаться и удалить заявку", "callback_data": "delete_confirm"}],
            ]
        }

    def _subscription_keyboard(self) -> dict:
        return {
            "inline_keyboard": [
                [{"text": "📣 Подписаться на канал", "url": self.config.channel_url}],
                [{"text": "✅ Проверить подписку", "callback_data": "check_subscription"}],
            ]
        }

    @staticmethod
    def _delete_keyboard() -> dict:
        return {
            "inline_keyboard": [
                [{"text": "Удалить мои данные", "callback_data": "delete_confirm"}],
                [{"text": "Отмена", "callback_data": "delete_cancel"}],
            ]
        }

    def _privacy_text(self) -> str:
        return (
            f"🔐 <b>Как {html.escape(self.config.organizer_name)} обрабатывает данные</b>\n\n"
            "Для фиксации и передачи приза бот получает ваш Telegram ID, имя профиля, "
            "username и номер телефона, который вы добровольно отправляете кнопкой Telegram. "
            "Данные используются только для проверки заявки, выдачи приза и связи менеджера.\n\n"
            "Контактные данные шифруются локально. В Excel по умолчанию записываются только "
            "псевдоним и маска номера. Данные удаляются по команде /delete_me и автоматически "
            f"через {self.config.data_retention_days} дней после выдачи.\n\n"
            f"Контакт по вопросам данных: {html.escape(self.config.privacy_contact)}. "
            "Согласие можно отозвать командой /delete_me."
        )

    def _allow(self, user_id: int, bucket: dict[int, deque[float]], limit: int) -> bool:
        now = self.clock()
        events = bucket[user_id]
        while events and events[0] <= now - 60:
            events.popleft()
        if len(events) >= limit:
            return False
        events.append(now)
        return True

    def _send_subscription_step(self, chat_id: int) -> None:
        self.api.send_message(
            chat_id,
            "Номер сохранён. Подписка на официальный канал Flexy Way является заранее "
            "объявленным условием участия. Подпишитесь и один раз нажмите «Проверить подписку». "
            "Бот не добавляет вас автоматически и не публикует сообщения от вашего имени.",
            reply_markup=self._subscription_keyboard(),
        )

    def _is_prize_limit_exempt(self, user: Mapping) -> bool:
        configured = self.config.prize_limit_exempt_username.strip().lstrip("@").casefold()
        username = str(user.get("username") or "").strip().lstrip("@").casefold()
        return bool(configured and username and username == configured)

    def _send_final(self, chat_id: int, claim: Mapping) -> None:
        message = (
            "🎉 Подписка подтверждена. Ваш приз зафиксирован.\n\n"
            f"Приз: <b>{html.escape(str(claim['prize_name']))}</b>\n"
            f"Промокод: <code>{html.escape(str(claim['promo_code']))}</code>\n"
            f"Условия конкретного приза: {html.escape(str(claim.get('prize_terms') or 'уточните у менеджера'))}\n\n"
            f"<b>Общие условия получения:</b>\n{html.escape(COMMON_PRIZE_TERMS)}\n\n"
            f"{html.escape(self.config.manager_message)}\n\n"
            "Не пересылайте промокод другим людям. Удалить контактные данные: /delete_me"
        )
        self.api.send_message(
            chat_id,
            message,
            parse_mode="HTML",
            protect_content=True,
            reply_markup={"remove_keyboard": True},
        )

    def handle_update(self, update: Mapping) -> None:
        source = update.get("callback_query") or update.get("message") or {}
        user = source.get("from") or {}
        user_id = int(user.get("id") or 0)
        if not user_id:
            return
        if not self._allow(user_id, self._activity, self.config.rate_limit_per_minute):
            if "callback_query" in update:
                self.api.answer_callback_query(
                    update["callback_query"]["id"],
                    "Слишком много запросов. Подождите минуту.",
                    show_alert=True,
                )
            return
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return
        message = update.get("message")
        if not message or message.get("chat", {}).get("type") != "private":
            return
        if "contact" in message:
            self._handle_contact(message)
            return
        text = str(message.get("text") or "").strip()
        command = text.split(maxsplit=1)[0].split("@", 1)[0].casefold()
        if command == "/start":
            self._handle_start(message, text)
        elif command == "/privacy":
            self.api.send_message(message["chat"]["id"], self._privacy_text(), parse_mode="HTML")
        elif command == "/delete_me":
            self.api.send_message(
                message["chat"]["id"],
                "Удалить контактные данные и отменить незавершённую заявку? Уже выданный "
                "промокод останется отмечен как использованный для защиты от повторной выдачи.",
                reply_markup=self._delete_keyboard(),
            )
        elif command == "/help":
            self.api.send_message(
                message["chat"]["id"],
                "Откройте персональную ссылку или QR-код из игры. Политика данных: /privacy. "
                "Удаление данных: /delete_me.",
            )
        else:
            self.api.send_message(
                message["chat"]["id"],
                "Откройте персональную ссылку из игры и нажмите Start. Помощь: /help",
            )

    def _send_greeting_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        if WELCOME_IMAGE_PATH.is_file():
            try:
                photo_bytes = WELCOME_IMAGE_PATH.read_bytes()
                self.api.send_photo(
                    chat_id=chat_id,
                    photo_bytes=photo_bytes,
                    filename="welcome_logo.png",
                    caption=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                )
                return
            except Exception:
                LOGGER.exception("Не удалось отправить приветственное фото, отправка обычным сообщением")
        self.api.send_message(chat_id, text, parse_mode="HTML", reply_markup=reply_markup)

    def _handle_start(self, message: Mapping, text: str) -> None:
        chat_id = message["chat"]["id"]
        parts = text.split(maxsplit=1)
        if len(parts) != 2 or not START_PAYLOAD.fullmatch(parts[1]):
            self._send_greeting_message(
                chat_id,
                "Привет! Тут Flexy Way раздает призы! 🎁\n\n"
                "Скидку 50% на пробное занятие получает каждый! А еще тебя ждет персональный приз!\n\n"
                "Чтобы забрать приз, отсканируйте персональный QR-код из игры на экране.",
            )
            return
        result, claim = self.database.bind_token(
            parts[1],
            message["from"],
            allow_multiple_prizes=self._is_prize_limit_exempt(message["from"]),
        )
        if result == "invalid":
            self.api.send_message(chat_id, "Ссылка недействительна. Проверьте QR-код или обратитесь к организатору.")
            return
        if result == "expired":
            self.api.send_message(chat_id, "Срок действия ссылки истёк. Обратитесь к организатору розыгрыша.")
            return
        if result == "owned":
            self.api.send_message(chat_id, "Этот выигрыш уже привязан к другому Telegram-аккаунту.")
            return
        if result == "already_received":
            self.api.send_message(
                chat_id,
                "Вы уже получили приз в этом розыгрыше. По правилам один Telegram-аккаунт "
                "может получить только один приз.",
            )
            return
        if result == "already_active":
            self.api.send_message(
                chat_id,
                "У вас уже есть другая активная заявка на приз. Завершите её по первой "
                "персональной ссылке: один Telegram-аккаунт может получить только один приз.",
            )
            return
        if result == "cancelled":
            self.api.send_message(chat_id, "Эта заявка отменена. Обратитесь к организатору.")
            return
        if result == "issued":
            self._send_final(chat_id, claim)
            return

        greeting = (
            "Привет! Тут Flexy Way раздает призы! 🎁\n\n"
            "Скидку 50% на пробное занятие получает каждый!\n"
            f"А еще тебя ждет персональный приз: <b>{html.escape(str(claim['prize_name']))}</b>!\n\n"
            "Для получения подтвердите согласие и подписку на официальный канал, "
            "а затем поделитесь номером телефона для связи по призу. Подробности: /privacy\n\n"
            f"<b>Условия получения:</b>\n{html.escape(COMMON_PRIZE_TERMS)}"
        )
        if not claim.get("consent_at"):
            self._send_greeting_message(chat_id, greeting, reply_markup=self._consent_keyboard())
        elif claim.get("phone"):
            self._send_greeting_message(chat_id, greeting, reply_markup={"remove_keyboard": True})
            self._send_subscription_step(chat_id)
        else:
            self._send_greeting_message(chat_id, greeting, reply_markup=self._contact_keyboard())

    @staticmethod
    def _normalize_phone(value: str) -> str:
        raw = str(value or "").strip()
        digits = "".join(character for character in raw if character.isdigit())
        return "+" + digits

    def _handle_contact(self, message: Mapping) -> None:
        chat_id = message["chat"]["id"]
        sender_id = int(message["from"]["id"])
        contact = message["contact"]
        if int(contact.get("user_id") or 0) != sender_id:
            self.api.send_message(
                chat_id,
                "Отправьте именно свой номер штатной кнопкой «Поделиться номером».",
            )
            return
        claim = self.database.active_claim_for_user(sender_id)
        if claim is None:
            self.api.send_message(chat_id, "Сначала откройте персональную ссылку из игры.")
            return
        if not claim.get("consent_at"):
            self.api.send_message(chat_id, "Сначала подтвердите согласие в сообщении бота. Подробности: /privacy")
            return
        phone = self._normalize_phone(contact.get("phone_number") or "")
        if not PHONE_PATTERN.fullmatch(phone):
            self.api.send_message(chat_id, "Telegram передал номер в неверном формате. Обратитесь к организатору.")
            return
        self.database.store_phone(claim["token"], sender_id, phone, message["from"])
        self.api.send_message(
            chat_id,
            "Спасибо! Номер сохранён в зашифрованном виде.",
            reply_markup={"remove_keyboard": True},
        )
        self._send_subscription_step(chat_id)

    @staticmethod
    def _is_member(member: Mapping) -> bool:
        status = member.get("status")
        if status in {"creator", "administrator", "member"}:
            return True
        return status == "restricted" and member.get("is_member") is True

    def _handle_callback(self, callback: Mapping) -> None:
        callback_id = callback["id"]
        data = str(callback.get("data") or "")
        message = callback.get("message") or {}
        chat_id = message.get("chat", {}).get("id")
        user_id = int(callback["from"]["id"])
        if chat_id is None or message.get("chat", {}).get("type") != "private":
            self.api.answer_callback_query(callback_id, "Кнопка доступна только в личном чате", show_alert=True)
            return

        if data == "delete_cancel":
            self.api.answer_callback_query(callback_id, "Удаление отменено")
            return
        if data == "delete_confirm":
            self._delete_user_data(callback_id, chat_id, user_id)
            return

        claim = self.database.active_claim_for_user(user_id)
        if claim is None:
            self.api.answer_callback_query(
                callback_id,
                "Активная заявка не найдена. Снова откройте QR-ссылку.",
                show_alert=True,
            )
            return

        if data == "consent":
            self.database.accept_consent(claim["token"], callback["from"])
            self.api.answer_callback_query(callback_id, "Согласие сохранено")
            self.api.send_message(
                chat_id,
                "Теперь нажмите штатную кнопку Telegram, чтобы передать свой номер.",
                reply_markup=self._contact_keyboard(),
            )
            return

        if data != "check_subscription" and not data.startswith("check:"):
            self.api.answer_callback_query(callback_id, "Неизвестная кнопка", show_alert=True)
            return
        if not claim.get("phone"):
            self.api.answer_callback_query(
                callback_id, "Сначала поделитесь номером телефона", show_alert=True
            )
            return
        if not self._allow(user_id, self._subscription_checks, 6):
            self.api.answer_callback_query(
                callback_id, "Слишком много проверок. Подождите минуту.", show_alert=True
            )
            return

        self.api.answer_callback_query(callback_id, "Проверяю подписку…")
        try:
            member = self.api.get_chat_member(self.config.channel_id, user_id)
        except TelegramApiError:
            LOGGER.exception("Telegram не позволил проверить подписку")
            self.api.send_message(
                chat_id,
                "Проверка временно недоступна. Бот должен быть администратором канала с "
                "минимальными правами. Попробуйте позже.",
                reply_markup=self._subscription_keyboard(),
            )
            return
        if not self._is_member(member):
            self.api.send_message(
                chat_id,
                "Подписка пока не найдена. Подпишитесь на официальный канал и повторите "
                "проверку. Бот проверяет только факт членства.",
                reply_markup=self._subscription_keyboard(),
            )
            return

        try:
            self.database.mark_subscription(claim["token"], user_id)
            claim = self.database.reserve_promo(
                claim["token"],
                user_id,
                allow_multiple_prizes=self._is_prize_limit_exempt(callback["from"]),
            )
            issued_at = datetime.now(timezone.utc)
            self.workbook.record_issue(claim, issued_at)
            claim = self.database.finalize_issue(claim["token"], issued_at)
        except PrizeLimitReached:
            LOGGER.warning("Остановлена повторная выдача для Telegram user %s", user_id)
            self.api.send_message(
                chat_id,
                "Вы уже получили или сейчас оформляете другой приз. По правилам один "
                "Telegram-аккаунт может получить только один приз.",
            )
            return
        except OutOfStock:
            LOGGER.exception("Закончились промокоды для типа приза %s", claim["prize_code"])
            self.api.send_message(
                chat_id,
                "Свободные коды этого типа временно закончились. Заявка сохранена; "
                "обратитесь к организатору.",
            )
            return
        except (WorkbookError, BotDataError):
            LOGGER.exception("Не удалось завершить выдачу для заявки %s", claim["token_ref"])
            self.api.send_message(
                chat_id,
                "Приз зарезервирован, но локальная запись пока не завершена. Организатору "
                "нужно закрыть Excel и повторить проверку; второй код выдан не будет.",
                reply_markup=self._subscription_keyboard(),
            )
            return
        self._send_final(chat_id, claim)

    def _delete_user_data(self, callback_id: str, chat_id: int, user_id: int) -> None:
        user_ref = self.database.user_reference(user_id)
        try:
            self.workbook.anonymize_user(user_ref)
            result = self.database.delete_user_data(user_id)
        except WorkbookError:
            LOGGER.exception("Не удалось обезличить Excel для %s", user_ref)
            self.api.answer_callback_query(
                callback_id,
                "Не удалось открыть Excel. Закройте файл и повторите удаление.",
                show_alert=True,
            )
            return
        self.api.answer_callback_query(callback_id, "Данные удалены")
        if result["claims"]:
            self.api.send_message(
                chat_id,
                "Контактные данные удалены. Незавершённые заявки отменены. Минимальная "
                "обезличенная запись о выданном коде сохранена против повторной выдачи.",
                reply_markup={"remove_keyboard": True},
            )
        else:
            self.api.send_message(chat_id, "Сохранённых контактных данных не найдено.")


def run_polling(config: Config, app: BotApp, api: TelegramApi) -> None:
    startup_backoff = 1
    while True:
        try:
            me = api.get_me()
            api.delete_webhook()
            LOGGER.info("Бот @%s подключён к Telegram", me.get("username"))
            try:
                api.set_my_description(BOT_WELCOME_DESCRIPTION)
                api.set_my_description(BOT_WELCOME_DESCRIPTION, language_code="ru")
                api.set_my_short_description(BOT_SHORT_DESCRIPTION)
                api.set_my_short_description(BOT_SHORT_DESCRIPTION, language_code="ru")
                LOGGER.info("Приветственное описание бота до нажатия СТАРТ обновлено.")
            except Exception:
                LOGGER.warning("Не удалось автоматически обновить описание через setMyDescription")
            break
        except TelegramApiError:
            LOGGER.exception(
                "Telegram пока недоступен при запуске; повтор через %s секунд",
                startup_backoff,
            )
            time.sleep(startup_backoff)
            startup_backoff = min(startup_backoff * 2, 30)
    cleanup = app.database.purge_personal_data(config.data_retention_days)
    try:
        app.workbook.anonymize_users(cleanup["user_refs"])
    except WorkbookError:
        LOGGER.exception("Не удалось выполнить плановое обезличивание Excel")
    last_cleanup = time.monotonic()
    offset = None
    backoff = 1
    while True:
        try:
            updates = api.get_updates(offset, config.polling_timeout)
            backoff = 1
            for update in updates:
                try:
                    app.handle_update(update)
                except Exception:
                    LOGGER.exception("Ошибка обработки Telegram update_id=%s", update.get("update_id"))
                finally:
                    offset = int(update["update_id"]) + 1
            if time.monotonic() - last_cleanup >= 86400:
                cleanup = app.database.purge_personal_data(config.data_retention_days)
                try:
                    app.workbook.anonymize_users(cleanup["user_refs"])
                except WorkbookError:
                    LOGGER.exception("Не удалось выполнить плановое обезличивание Excel")
                last_cleanup = time.monotonic()
        except TelegramApiError:
            LOGGER.exception("Ошибка long polling; повтор через %s секунд", backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)
