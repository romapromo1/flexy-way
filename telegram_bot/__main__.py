from __future__ import annotations

import argparse
import json
import logging
import sys

from .app import BOT_SHORT_DESCRIPTION, BOT_WELCOME_DESCRIPTION, BotApp, run_polling
from .config import Config
from .database import BotDataError, Database
from .instance_lock import AlreadyRunningError, SingleInstanceLock
from .security import LocalDataProtector, mask_phone
from .telegram_api import TelegramApi
from .workbook import PromoWorkbook, WorkbookError


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Локальный Telegram-бот Flexy Way")
    subparsers = result.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init", help="создать БД и синхронизировать промокоды из Excel")
    subparsers.add_parser("prizes", help="показать доступные типы призов")
    subparsers.add_parser("set-description", help="обновить приветственное описание бота в Telegram")
    subparsers.add_parser("sync-excel", help="записать все выданные лиды с полными контактами в Excel")

    token = subparsers.add_parser("token", help="создать персональную deep link-ссылку")
    token.add_argument("--prize", required=True, help="код приза из листа «Справочник призов»")
    token.add_argument("--session", required=True, help="уникальный ID игровой сессии")
    token.add_argument("--ttl-hours", type=int, help="срок действия ссылки в часах")
    token.add_argument("--json", action="store_true", help="вывести машинно-читаемый JSON")

    claims = subparsers.add_parser("claims", help="показать последние заявки")
    claims.add_argument("--limit", type=int, default=20)
    contact = subparsers.add_parser(
        "contact", help="локально показать расшифрованный контакт по выданному промокоду"
    )
    contact.add_argument("--promo", required=True, help="выданный промокод")
    subparsers.add_parser("cleanup", help="удалить персональные данные с истёкшим сроком")
    subparsers.add_parser("security-check", help="проверить безопасные настройки хранения")
    subparsers.add_parser("run", help="запустить Telegram long polling")
    return result


def open_services(config: Config):
    protector = LocalDataProtector(config.data_secret_path)
    workbook = PromoWorkbook(config.workbook_path, config.timezone, config.excel_pii_mode)
    database = Database(config.database_path, protector)
    database.initialize()
    sync = database.sync_inventory(workbook.inventory())
    return workbook, database, sync


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = Config.load()
    database = None
    try:
        workbook, database, sync = open_services(config)
        if args.command == "init":
            print(
                f"Готово: {sync['total']} промокодов синхронизировано; "
                f"новых — {sync['inserted']}, обновлено — {sync['updated']}."
            )
            print(f"База данных: {config.database_path}")
            return 0
        if args.command == "prizes":
            for prize in database.prize_summary():
                print(
                    f"{prize['prize_code']:<24} свободно {prize['available']:>4}/{prize['total']:<4} "
                    f"{prize['prize_name']}"
                )
            return 0
        if args.command == "set-description":
            config.validate_for_run()
            api = TelegramApi(config.bot_token)
            api.set_my_description(BOT_WELCOME_DESCRIPTION)
            api.set_my_description(BOT_WELCOME_DESCRIPTION, language_code="ru")
            api.set_my_short_description(BOT_SHORT_DESCRIPTION)
            api.set_my_short_description(BOT_SHORT_DESCRIPTION, language_code="ru")
            print("Готово: приветственное описание бота в Telegram успешно установлено!")
            print(f"Текст:\n{BOT_WELCOME_DESCRIPTION}")
            return 0
        if args.command == "sync-excel":
            claims = database.list_claims(500)
            count = workbook.sync_all_issued_leads(claims)
            print(f"Готово: {count} выданных призов с полными контактами записаны в Excel-файл {config.workbook_path.name}")
            return 0
        if args.command == "token":
            config.validate_for_link()
            claim = database.create_token(
                args.session,
                args.prize,
                args.ttl_hours or config.token_ttl_hours,
            )
            deep_link = f"https://t.me/{config.bot_username}?start={claim['token']}"
            output = {
                "session_id": claim["session_id"],
                "prize_code": claim["prize_code"],
                "prize_name": claim["prize_name"],
                "token": claim["token"],
                "expires_at": claim["expires_at"],
                "deep_link": deep_link,
            }
            if args.json:
                print(json.dumps(output, ensure_ascii=False))
            else:
                print(f"Приз: {claim['prize_name']}")
                print(f"Ссылка: {deep_link}")
                print(f"Действует до: {claim['expires_at']}")
            return 0
        if args.command == "claims":
            rows = database.list_claims(max(1, min(args.limit, 500)))
            if not rows:
                print("Заявок пока нет.")
            for row in rows:
                print(
                    f"{row['created_at']} | {row['status']:<8} | {row['session_id']} | "
                    f"{row['prize_code']} | {row['telegram_user_ref'] or '-'} | "
                    f"телефон {mask_phone(row['phone']) or '-'} | {row['promo_code'] or '-'}"
                )
            return 0
        if args.command == "contact":
            claim = database.get_claim_by_promo(args.promo.strip())
            if claim is None or claim["status"] != "issued":
                print("Выданная заявка с таким промокодом не найдена.", file=sys.stderr)
                return 1
            if not claim.get("phone"):
                print("Контактные данные уже удалены.")
                return 0
            print(f"Промокод: {claim['promo_code']}")
            print(f"Телефон: {claim['phone']}")
            print(f"Username: {'@' + claim['username'] if claim.get('username') else '-'}")
            print(
                "Имя: "
                + (" ".join(part for part in [claim.get('first_name'), claim.get('last_name')] if part) or "-")
            )
            return 0
        if args.command == "cleanup":
            result = database.purge_personal_data(config.data_retention_days)
            workbook.anonymize_users(result["user_refs"])
            print(
                f"Готово: просрочено заявок — {result['expired']}; "
                f"очищено записей с персональными данными — {result['purged']}."
            )
            return 0
        if args.command == "security-check":
            config.validate_for_run()
            status = database.security_status()
            checks = {
                "шифрование персональных данных": status["plaintext_pii"] == 0,
                "отсутствие старых токенов в открытом виде": status["legacy_tokens"] == 0,
                "маскирование персональных данных в Excel": config.excel_pii_mode == "masked",
                "обязательная подписка": config.subscription_required,
                "короткий срок QR-ссылки": config.token_ttl_hours <= 24,
                "автоудаление данных": config.data_retention_days <= 30,
            }
            for title, passed in checks.items():
                print(f"{'OK' if passed else 'ВНИМАНИЕ'} | {title}")
            return 0 if all(checks.values()) else 1
        if args.command == "run":
            config.validate_for_run()
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            )
            api = TelegramApi(config.bot_token)
            application = BotApp(config, database, workbook, api)
            lock_path = config.database_path.with_suffix(config.database_path.suffix + ".run.lock")
            with SingleInstanceLock(lock_path):
                run_polling(config, application, api)
            return 0
    except (ValueError, WorkbookError, BotDataError, AlreadyRunningError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nБот остановлен.")
        return 0
    finally:
        if database is not None:
            database.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
