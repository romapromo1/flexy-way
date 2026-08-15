from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from telegram_bot.security import LocalDataProtector
from telegram_bot.telegram_api import TelegramApi, TelegramApiError


class LocalProtectionTest(unittest.TestCase):
    def test_round_trip_and_stable_non_reversible_lookup(self):
        with tempfile.TemporaryDirectory() as temp_name:
            protector = LocalDataProtector(Path(temp_name) / "secret.key")
            encrypted = protector.encrypt("+79991234567")
            self.assertNotIn("79991234567", encrypted)
            self.assertEqual(protector.decrypt(encrypted), "+79991234567")
            self.assertEqual(
                protector.lookup("telegram", 12345),
                protector.lookup("telegram", 12345),
            )
            self.assertNotEqual(
                protector.lookup("telegram", 12345),
                protector.lookup("telegram", 12346),
            )


class TelegramApiPolicyTest(unittest.TestCase):
    def setUp(self):
        self.api = TelegramApi("test-token")

    def test_channel_and_group_messages_are_blocked_before_network(self):
        with self.assertRaises(TelegramApiError):
            self.api.send_message(-100123456789, "Нельзя отправить")

    def test_mutating_channel_methods_are_not_in_allowlist(self):
        with self.assertRaises(TelegramApiError):
            self.api.call("banChatMember", {"chat_id": "@channel", "user_id": 1})


if __name__ == "__main__":
    unittest.main()
