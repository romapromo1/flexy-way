from __future__ import annotations

import secrets
import sqlite3
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

from .security import LocalDataProtector


PRIZE_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SESSION_PATTERN = re.compile(r"^[^\x00-\x1f\x7f]{1,128}$")


class BotDataError(RuntimeError):
    pass


class PrizeNotFound(BotDataError):
    pass


class OutOfStock(BotDataError):
    pass


class SessionAlreadyExists(BotDataError):
    pass


class PrizeLimitReached(BotDataError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def from_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Database:
    SCHEMA_VERSION = 2

    def __init__(self, path: Path, protector: LocalDataProtector | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.protector = protector or LocalDataProtector(self.path.parent / "local_secret.key")
        self.connection = sqlite3.connect(self.path, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.execute("PRAGMA secure_delete = ON")
        self.connection.execute("PRAGMA trusted_schema = OFF")
        self.connection.execute("PRAGMA busy_timeout = 5000")

    def close(self) -> None:
        self.connection.close()

    def _table_exists(self, table: str) -> bool:
        return self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone() is not None

    def _columns(self, table: str) -> set[str]:
        return {row["name"] for row in self.connection.execute(f"PRAGMA table_info({table})")}

    def _create_inventory_table(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_inventory (
                promo_code TEXT PRIMARY KEY,
                prize_code TEXT NOT NULL,
                prize_name TEXT NOT NULL,
                prize_terms TEXT NOT NULL DEFAULT '',
                excel_row INTEGER NOT NULL,
                status TEXT NOT NULL,
                token TEXT,
                token_hash TEXT UNIQUE,
                issued_at TEXT
            )
            """
        )
        columns = self._columns("promo_inventory")
        if "token_hash" not in columns:
            self.connection.execute("ALTER TABLE promo_inventory ADD COLUMN token_hash TEXT")
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_inventory_prize_status "
            "ON promo_inventory(prize_code, status, excel_row)"
        )

    def _create_tokens_table(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS prize_tokens (
                token_hash TEXT PRIMARY KEY,
                token_enc TEXT NOT NULL,
                session_hash TEXT NOT NULL UNIQUE,
                session_enc TEXT NOT NULL,
                prize_code TEXT NOT NULL,
                prize_name TEXT NOT NULL,
                prize_terms TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                telegram_user_hash TEXT,
                telegram_user_id_enc TEXT,
                phone_enc TEXT,
                username_enc TEXT,
                first_name_enc TEXT,
                last_name_enc TEXT,
                consent_at TEXT,
                subscription_verified_at TEXT,
                promo_code TEXT UNIQUE,
                issued_at TEXT,
                pii_deleted_at TEXT,
                FOREIGN KEY (promo_code) REFERENCES promo_inventory(promo_code)
            )
            """
        )
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_tokens_user_status "
            "ON prize_tokens(telegram_user_hash, status, created_at DESC)"
        )

    def _migrate_v1_tokens(self) -> None:
        rows = self.connection.execute("SELECT * FROM prize_tokens").fetchall()
        inventory_tokens = {
            row["promo_code"]: row["token"]
            for row in self.connection.execute(
                "SELECT promo_code, token FROM promo_inventory WHERE token IS NOT NULL"
            )
        }
        self.connection.execute("PRAGMA foreign_keys = OFF")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self.connection.execute("ALTER TABLE prize_tokens RENAME TO prize_tokens_v1")
            self._create_tokens_table()
            token_hashes: dict[str, str] = {}
            for row in rows:
                raw_token = str(row["token"])
                raw_session = str(row["session_id"])
                token_hash = self.protector.lookup("token", raw_token)
                user_id = row["telegram_user_id"]
                token_hashes[raw_token] = token_hash
                self.connection.execute(
                    """
                    INSERT INTO prize_tokens (
                        token_hash, token_enc, session_hash, session_enc,
                        prize_code, prize_name, prize_terms, status, created_at, expires_at,
                        telegram_user_hash, telegram_user_id_enc, phone_enc, username_enc,
                        first_name_enc, last_name_enc, consent_at, promo_code, issued_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        token_hash,
                        self.protector.encrypt(raw_token),
                        self.protector.lookup("session", raw_session),
                        self.protector.encrypt(raw_session),
                        row["prize_code"],
                        row["prize_name"],
                        row["prize_terms"],
                        row["status"],
                        row["created_at"],
                        row["expires_at"],
                        self.protector.lookup("telegram", user_id) if user_id is not None else None,
                        self.protector.encrypt(user_id),
                        self.protector.encrypt(row["phone"]),
                        self.protector.encrypt(row["username"]),
                        self.protector.encrypt(row["first_name"]),
                        self.protector.encrypt(row["last_name"]),
                        row["created_at"] if row["phone"] else None,
                        row["promo_code"],
                        row["issued_at"],
                    ),
                )
            for promo_code, raw_token in inventory_tokens.items():
                self.connection.execute(
                    "UPDATE promo_inventory SET token_hash = ?, token = NULL WHERE promo_code = ?",
                    (token_hashes.get(raw_token), promo_code),
                )
            self.connection.execute("UPDATE promo_inventory SET token = NULL")
            self.connection.execute("DROP TABLE prize_tokens_v1")
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")

    def initialize(self) -> None:
        self._create_inventory_table()
        if self._table_exists("prize_tokens"):
            columns = self._columns("prize_tokens")
            if "token_hash" not in columns:
                self._migrate_v1_tokens()
        else:
            self._create_tokens_table()
        self._create_tokens_table()
        self.connection.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    @staticmethod
    def _workbook_status(value: str) -> str:
        normalized = (value or "").strip().casefold()
        if normalized == "не использован":
            return "available"
        if normalized == "использован":
            return "external_used"
        return "cancelled"

    def _claim(self, row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        claim = dict(row)
        claim["token"] = self.protector.decrypt(row["token_enc"])
        claim["session_id"] = self.protector.decrypt(row["session_enc"])
        user_id = self.protector.decrypt(row["telegram_user_id_enc"])
        claim["telegram_user_id"] = int(user_id) if user_id else None
        claim["phone"] = self.protector.decrypt(row["phone_enc"])
        claim["username"] = self.protector.decrypt(row["username_enc"])
        claim["first_name"] = self.protector.decrypt(row["first_name_enc"])
        claim["last_name"] = self.protector.decrypt(row["last_name_enc"])
        claim["token_ref"] = self.protector.reference("token", claim["token"])
        claim["session_ref"] = self.protector.reference("session", claim["session_id"])
        claim["telegram_user_ref"] = (
            f"tg_{row['telegram_user_hash'][:12]}" if row["telegram_user_hash"] else ""
        )
        for name in (
            "token_enc",
            "session_enc",
            "telegram_user_id_enc",
            "phone_enc",
            "username_enc",
            "first_name_enc",
            "last_name_enc",
        ):
            claim.pop(name, None)
        return claim

    def sync_inventory(self, records: Iterable[Mapping]) -> dict[str, int]:
        inserted = 0
        updated = 0
        records = list(records)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for record in records:
                promo_code = str(record["promo_code"])
                current = self.connection.execute(
                    "SELECT status, token_hash FROM promo_inventory WHERE promo_code = ?",
                    (promo_code,),
                ).fetchone()
                desired = self._workbook_status(str(record["status"]))
                if current is None:
                    self.connection.execute(
                        """
                        INSERT INTO promo_inventory
                            (promo_code, prize_code, prize_name, prize_terms, excel_row, status)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            promo_code,
                            record["prize_code"],
                            record["prize_name"],
                            record.get("prize_terms", ""),
                            record["excel_row"],
                            desired,
                        ),
                    )
                    inserted += 1
                    continue

                next_status = current["status"]
                if current["token_hash"] is None and current["status"] in {
                    "available",
                    "external_used",
                    "cancelled",
                }:
                    next_status = desired
                self.connection.execute(
                    """
                    UPDATE promo_inventory
                    SET prize_code = ?, prize_name = ?, prize_terms = ?,
                        excel_row = ?, status = ?
                    WHERE promo_code = ?
                    """,
                    (
                        record["prize_code"],
                        record["prize_name"],
                        record.get("prize_terms", ""),
                        record["excel_row"],
                        next_status,
                        promo_code,
                    ),
                )
                updated += 1
            self.connection.execute("COMMIT")
        except Exception:
            self.connection.execute("ROLLBACK")
            raise
        return {"total": len(records), "inserted": inserted, "updated": updated}

    def prize_summary(self) -> list[dict]:
        rows = self.connection.execute(
            """
            SELECT prize_code, MAX(prize_name) AS prize_name,
                   SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available,
                   COUNT(*) AS total
            FROM promo_inventory
            GROUP BY prize_code
            ORDER BY MIN(excel_row)
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def create_token(
        self,
        session_id: str,
        prize_code: str,
        ttl_hours: int,
        now: datetime | None = None,
    ) -> dict:
        now = now or utc_now()
        session_id = str(session_id).strip()
        prize_code = str(prize_code).strip()
        if not SESSION_PATTERN.fullmatch(session_id):
            raise BotDataError("ID игровой сессии должен содержать от 1 до 128 печатных символов")
        if not PRIZE_CODE_PATTERN.fullmatch(prize_code):
            raise BotDataError("Код приза имеет недопустимый формат")
        if not 1 <= int(ttl_hours) <= 168:
            raise BotDataError("Срок действия ссылки должен быть от 1 до 168 часов")
        session_hash = self.protector.lookup("session", session_id)
        existing = self.connection.execute(
            "SELECT * FROM prize_tokens WHERE session_hash = ?", (session_hash,)
        ).fetchone()
        if existing is not None:
            if existing["prize_code"] == prize_code and existing["status"] in {
                "pending",
                "issuing",
            }:
                return self._claim(existing)
            raise SessionAlreadyExists("Игровая сессия уже зарегистрирована")

        prize = self.connection.execute(
            """
            SELECT prize_code, prize_name, prize_terms,
                   SUM(CASE WHEN status = 'available' THEN 1 ELSE 0 END) AS available
            FROM promo_inventory
            WHERE prize_code = ?
            GROUP BY prize_code, prize_name, prize_terms
            """,
            (prize_code,),
        ).fetchone()
        if prize is None:
            raise PrizeNotFound(f"Неизвестный код приза: {prize_code}")
        if prize["available"] <= 0:
            raise OutOfStock(f"Для приза {prize_code} нет свободных промокодов")

        token = "fw_" + secrets.token_urlsafe(24)
        token_hash = self.protector.lookup("token", token)
        expires_at = now + timedelta(hours=ttl_hours)
        self.connection.execute(
            """
            INSERT INTO prize_tokens (
                token_hash, token_enc, session_hash, session_enc,
                prize_code, prize_name, prize_terms, status, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (
                token_hash,
                self.protector.encrypt(token),
                session_hash,
                self.protector.encrypt(session_id),
                prize["prize_code"],
                prize["prize_name"],
                prize["prize_terms"],
                to_iso(now),
                to_iso(expires_at),
            ),
        )
        return self.get_claim(token)

    def get_claim(self, token: str) -> dict | None:
        return self._claim(
            self.connection.execute(
                "SELECT * FROM prize_tokens WHERE token_hash = ?",
                (self.protector.lookup("token", token),),
            ).fetchone()
        )

    def get_claim_by_promo(self, promo_code: str) -> dict | None:
        return self._claim(
            self.connection.execute(
                "SELECT * FROM prize_tokens WHERE promo_code = ?", (promo_code,)
            ).fetchone()
        )

    def user_reference(self, telegram_user_id: int) -> str:
        return f"tg_{self.protector.lookup('telegram', telegram_user_id)[:12]}"

    def bind_token(
        self,
        token: str,
        user: Mapping,
        now: datetime | None = None,
        allow_multiple_prizes: bool = False,
    ) -> tuple[str, dict | None]:
        now = now or utc_now()
        token_hash = self.protector.lookup("token", token)
        user_hash = self.protector.lookup("telegram", int(user["id"]))
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            row = self.connection.execute(
                "SELECT * FROM prize_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if row is None:
                self.connection.execute("ROLLBACK")
                return "invalid", None

            if row["status"] == "pending" and from_iso(row["expires_at"]) <= now:
                self.connection.execute(
                    "UPDATE prize_tokens SET status = 'expired' WHERE token_hash = ?",
                    (token_hash,),
                )
                self.connection.execute("COMMIT")
                return "expired", self.get_claim(token)

            if row["status"] in {"expired", "cancelled"}:
                self.connection.execute("ROLLBACK")
                return row["status"], self._claim(row)
            if row["telegram_user_hash"] and row["telegram_user_hash"] != user_hash:
                self.connection.execute("ROLLBACK")
                return "owned", self._claim(row)
            if row["status"] == "issued":
                self.connection.execute("ROLLBACK")
                return "issued", self._claim(row)

            if not allow_multiple_prizes:
                existing = self.connection.execute(
                    """
                    SELECT * FROM prize_tokens
                    WHERE telegram_user_hash = ? AND token_hash <> ?
                      AND (
                        status IN ('issued', 'issuing')
                        OR (status = 'pending' AND expires_at > ?)
                      )
                    ORDER BY
                      CASE status WHEN 'issued' THEN 0 WHEN 'issuing' THEN 1 ELSE 2 END,
                      created_at DESC
                    LIMIT 1
                    """,
                    (user_hash, token_hash, to_iso(now)),
                ).fetchone()
                if existing is not None:
                    self.connection.execute("ROLLBACK")
                    result = (
                        "already_received"
                        if existing["status"] == "issued"
                        else "already_active"
                    )
                    return result, self._claim(existing)

            self.connection.execute(
                """
                UPDATE prize_tokens 
                SET telegram_user_hash = ?,
                    telegram_user_id_enc = COALESCE(?, telegram_user_id_enc),
                    username_enc = COALESCE(?, username_enc),
                    first_name_enc = COALESCE(?, first_name_enc),
                    last_name_enc = COALESCE(?, last_name_enc)
                WHERE token_hash = ?
                """,
                (
                    user_hash,
                    self.protector.encrypt(user["id"]),
                    self.protector.encrypt(user.get("username")),
                    self.protector.encrypt(user.get("first_name")),
                    self.protector.encrypt(user.get("last_name")),
                    token_hash,
                ),
            )
            self.connection.execute("COMMIT")
            claim = self.get_claim(token)
            return ("issued" if claim["status"] == "issued" else "ok"), claim
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def accept_consent(self, token: str, user: Mapping, now: datetime | None = None) -> dict:
        now = now or utc_now()
        token_hash = self.protector.lookup("token", token)
        user_hash = self.protector.lookup("telegram", int(user["id"]))
        changed = self.connection.execute(
            """
            UPDATE prize_tokens
            SET consent_at = ?, telegram_user_id_enc = ?, username_enc = ?,
                first_name_enc = ?, last_name_enc = ?, pii_deleted_at = NULL
            WHERE token_hash = ? AND telegram_user_hash = ?
              AND status IN ('pending', 'issuing')
            """,
            (
                to_iso(now),
                self.protector.encrypt(user["id"]),
                self.protector.encrypt(user.get("username")),
                self.protector.encrypt(user.get("first_name")),
                self.protector.encrypt(user.get("last_name")),
                token_hash,
                user_hash,
            ),
        ).rowcount
        if not changed:
            raise BotDataError("Активная заявка для согласия не найдена")
        return self.get_claim(token)

    def active_claim_for_user(self, telegram_user_id: int) -> dict | None:
        user_hash = self.protector.lookup("telegram", telegram_user_id)
        return self._claim(
            self.connection.execute(
                """
                SELECT * FROM prize_tokens
                WHERE telegram_user_hash = ? AND status IN ('pending', 'issuing')
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (user_hash,),
            ).fetchone()
        )

    def store_phone(
        self,
        token: str,
        telegram_user_id: int,
        phone: str,
        user: Mapping | None = None,
    ) -> dict:
        token_hash = self.protector.lookup("token", token)
        user_hash = self.protector.lookup("telegram", telegram_user_id)
        if user:
            changed = self.connection.execute(
                """
                UPDATE prize_tokens
                SET phone_enc = ?,
                    username_enc = COALESCE(?, username_enc),
                    first_name_enc = COALESCE(?, first_name_enc),
                    last_name_enc = COALESCE(?, last_name_enc)
                WHERE token_hash = ? AND telegram_user_hash = ?
                  AND consent_at IS NOT NULL AND status IN ('pending', 'issuing')
                """,
                (
                    self.protector.encrypt(phone),
                    self.protector.encrypt(user.get("username")),
                    self.protector.encrypt(user.get("first_name")),
                    self.protector.encrypt(user.get("last_name")),
                    token_hash,
                    user_hash,
                ),
            ).rowcount
        else:
            changed = self.connection.execute(
                """
                UPDATE prize_tokens SET phone_enc = ?
                WHERE token_hash = ? AND telegram_user_hash = ?
                  AND consent_at IS NOT NULL AND status IN ('pending', 'issuing')
                """,
                (self.protector.encrypt(phone), token_hash, user_hash),
            ).rowcount
        if not changed:
            raise BotDataError("Активная заявка для номера телефона не найдена")
        return self.get_claim(token)

    def mark_subscription(self, token: str, telegram_user_id: int) -> dict:
        token_hash = self.protector.lookup("token", token)
        user_hash = self.protector.lookup("telegram", telegram_user_id)
        changed = self.connection.execute(
            """
            UPDATE prize_tokens SET subscription_verified_at = ?
            WHERE token_hash = ? AND telegram_user_hash = ?
              AND status IN ('pending', 'issuing')
            """,
            (to_iso(utc_now()), token_hash, user_hash),
        ).rowcount
        if not changed:
            raise BotDataError("Активная заявка для проверки подписки не найдена")
        return self.get_claim(token)

    def reserve_promo(
        self,
        token: str,
        telegram_user_id: int,
        now: datetime | None = None,
        allow_multiple_prizes: bool = False,
    ) -> dict:
        now = now or utc_now()
        token_hash = self.protector.lookup("token", token)
        user_hash = self.protector.lookup("telegram", telegram_user_id)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            claim = self.connection.execute(
                "SELECT * FROM prize_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if claim is None or claim["telegram_user_hash"] != user_hash:
                raise BotDataError("Заявка не принадлежит пользователю")
            if claim["status"] == "issued":
                self.connection.execute("COMMIT")
                return self._claim(claim)
            if not allow_multiple_prizes:
                existing = self.connection.execute(
                    """
                    SELECT status FROM prize_tokens
                    WHERE telegram_user_hash = ? AND token_hash <> ?
                      AND status IN ('issuing', 'issued')
                    ORDER BY CASE status WHEN 'issued' THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (user_hash, token_hash),
                ).fetchone()
                if existing is not None:
                    if existing["status"] == "issued":
                        raise PrizeLimitReached("Этот Telegram-пользователь уже получил приз")
                    raise PrizeLimitReached(
                        "Для этого Telegram-пользователя уже оформляется другой приз"
                    )
            if not claim["consent_at"] or not claim["phone_enc"]:
                raise BotDataError("Сначала необходимо дать согласие и поделиться номером")
            if not claim["subscription_verified_at"]:
                raise BotDataError("Подписка ещё не подтверждена")
            if claim["status"] == "issuing" and claim["promo_code"]:
                self.connection.execute("COMMIT")
                return self._claim(claim)
            if claim["status"] != "pending":
                raise BotDataError(f"Нельзя выдать приз со статусом {claim['status']}")

            promo = self.connection.execute(
                """
                SELECT * FROM promo_inventory
                WHERE prize_code = ? AND status = 'available'
                ORDER BY excel_row
                LIMIT 1
                """,
                (claim["prize_code"],),
            ).fetchone()
            if promo is None:
                raise OutOfStock(f"Для приза {claim['prize_code']} нет свободных промокодов")

            changed = self.connection.execute(
                """
                UPDATE promo_inventory
                SET status = 'reserved', token_hash = ?, issued_at = ?
                WHERE promo_code = ? AND status = 'available'
                """,
                (token_hash, to_iso(now), promo["promo_code"]),
            ).rowcount
            if changed != 1:
                raise BotDataError("Промокод уже занят другим процессом")
            self.connection.execute(
                "UPDATE prize_tokens SET status = 'issuing', promo_code = ? WHERE token_hash = ?",
                (promo["promo_code"], token_hash),
            )
            self.connection.execute("COMMIT")
            return self.get_claim(token)
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def finalize_issue(self, token: str, issued_at: datetime | None = None) -> dict:
        issued_at = issued_at or utc_now()
        stamp = to_iso(issued_at)
        token_hash = self.protector.lookup("token", token)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            claim = self.connection.execute(
                "SELECT * FROM prize_tokens WHERE token_hash = ?", (token_hash,)
            ).fetchone()
            if claim is None or not claim["promo_code"]:
                raise BotDataError("Нельзя завершить выдачу без промокода")
            self.connection.execute(
                """
                UPDATE promo_inventory SET status = 'issued', issued_at = ?
                WHERE promo_code = ? AND token_hash = ?
                """,
                (stamp, claim["promo_code"], token_hash),
            )
            self.connection.execute(
                "UPDATE prize_tokens SET status = 'issued', issued_at = ? WHERE token_hash = ?",
                (stamp, token_hash),
            )
            self.connection.execute("COMMIT")
            return self.get_claim(token)
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def delete_user_data(self, telegram_user_id: int, now: datetime | None = None) -> dict:
        now = now or utc_now()
        stamp = to_iso(now)
        user_hash = self.protector.lookup("telegram", telegram_user_id)
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self.connection.execute(
                "SELECT status, promo_code, token_hash FROM prize_tokens WHERE telegram_user_hash = ?",
                (user_hash,),
            ).fetchall()
            for row in rows:
                if row["status"] == "issuing" and row["promo_code"]:
                    self.connection.execute(
                        """
                        UPDATE promo_inventory
                        SET status = 'available', token_hash = NULL, issued_at = NULL
                        WHERE promo_code = ? AND token_hash = ? AND status = 'reserved'
                        """,
                        (row["promo_code"], row["token_hash"]),
                    )
            self.connection.execute(
                """
                UPDATE prize_tokens
                SET status = CASE WHEN status IN ('pending', 'issuing') THEN 'cancelled' ELSE status END,
                    telegram_user_id_enc = NULL, phone_enc = NULL, username_enc = NULL,
                    first_name_enc = NULL, last_name_enc = NULL, consent_at = NULL,
                    pii_deleted_at = ?
                WHERE telegram_user_hash = ?
                """,
                (stamp, user_hash),
            )
            self.connection.execute("COMMIT")
            return {"claims": len(rows), "user_ref": f"tg_{user_hash[:12]}"}
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def purge_personal_data(
        self, retention_days: int, now: datetime | None = None
    ) -> dict:
        now = now or utc_now()
        stamp = to_iso(now)
        cutoff = to_iso(now - timedelta(days=retention_days))
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            expired = self.connection.execute(
                """
                UPDATE prize_tokens SET status = 'expired'
                WHERE status = 'pending' AND expires_at <= ?
                """,
                (stamp,),
            ).rowcount
            candidates = self.connection.execute(
                """
                SELECT DISTINCT telegram_user_hash FROM prize_tokens
                WHERE telegram_user_hash IS NOT NULL
                  AND (
                    status IN ('expired', 'cancelled')
                    OR (status = 'issued' AND issued_at <= ?)
                  )
                """,
                (cutoff,),
            ).fetchall()
            purged = self.connection.execute(
                """
                UPDATE prize_tokens
                SET telegram_user_id_enc = NULL, phone_enc = NULL, username_enc = NULL,
                    first_name_enc = NULL, last_name_enc = NULL, consent_at = NULL,
                    pii_deleted_at = ?
                WHERE pii_deleted_at IS NULL
                  AND (
                    status IN ('expired', 'cancelled')
                    OR (status = 'issued' AND issued_at <= ?)
                  )
                """,
                (stamp, cutoff),
            ).rowcount
            self.connection.execute("COMMIT")
            return {
                "expired": expired,
                "purged": purged,
                "user_refs": [f"tg_{row['telegram_user_hash'][:12]}" for row in candidates],
            }
        except Exception:
            self.connection.execute("ROLLBACK")
            raise

    def list_claims(self, limit: int = 20) -> list[dict]:
        rows = self.connection.execute(
            "SELECT * FROM prize_tokens ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [self._claim(row) for row in rows]

    def security_status(self) -> dict[str, int]:
        legacy_tokens = self.connection.execute(
            "SELECT COUNT(1) FROM promo_inventory WHERE token IS NOT NULL"
        ).fetchone()[0]
        plaintext_pii = self.connection.execute(
            """
            SELECT COUNT(1) FROM prize_tokens
            WHERE (phone_enc IS NOT NULL AND phone_enc NOT LIKE 'dpapi:v1:%' AND phone_enc NOT LIKE 'local:v1:%')
               OR (username_enc IS NOT NULL AND username_enc NOT LIKE 'dpapi:v1:%' AND username_enc NOT LIKE 'local:v1:%')
            """
        ).fetchone()[0]
        return {"legacy_tokens": legacy_tokens, "plaintext_pii": plaintext_pii}
