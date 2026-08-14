import re
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

import requests


PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from notifier import (  # noqa: E402
    NotificationConfigurationError,
    NotificationDeliveryError,
    TelegramNotifier,
    TelegramSettings,
)


class TelegramSettingsTests(unittest.TestCase):
    def test_from_env_requires_both_values(self):
        with self.assertRaises(NotificationConfigurationError) as error:
            TelegramSettings.from_env({})

        message = str(error.exception)
        self.assertIn("TELEGRAM_BOT_TOKEN", message)
        self.assertIn("TELEGRAM_CHAT_ID", message)

    def test_from_env_strips_values(self):
        settings = TelegramSettings.from_env({
            "TELEGRAM_BOT_TOKEN": " token-value ",
            "TELEGRAM_CHAT_ID": " chat-value ",
        })

        self.assertEqual(settings.bot_token, "token-value")
        self.assertEqual(settings.chat_id, "chat-value")


class TelegramNotifierTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.response = self.session.post.return_value
        self.response.json.return_value = {"ok": True, "result": {}}
        self.notifier = TelegramNotifier(
            TelegramSettings("secret-token", "chat-id"),
            session=self.session,
            timeout_seconds=3.0,
        )

    def test_send_is_explicit_and_uses_configured_destination(self):
        result = self.notifier.send("rebalance required")

        self.assertTrue(result["ok"])
        self.session.post.assert_called_once_with(
            "https://api.telegram.org/botsecret-token/sendMessage",
            data={"chat_id": "chat-id", "text": "rebalance required"},
            timeout=3.0,
        )
        self.response.raise_for_status.assert_called_once_with()

    def test_send_rejects_empty_message_without_network_call(self):
        with self.assertRaises(ValueError):
            self.notifier.send("   ")

        self.session.post.assert_not_called()

    def test_request_error_does_not_expose_token(self):
        self.session.post.side_effect = requests_error = requests.ConnectionError(
            "request failed for https://api.telegram.org/botsecret-token/sendMessage"
        )

        with self.assertRaises(NotificationDeliveryError) as error:
            self.notifier.send("rebalance required")

        self.assertNotIn("secret-token", str(error.exception))
        self.assertIsNone(error.exception.__cause__)
        self.assertIsNotNone(requests_error)

    def test_provider_rejection_is_a_delivery_error(self):
        self.response.json.return_value = {"ok": False}

        with self.assertRaises(NotificationDeliveryError):
            self.notifier.send("rebalance required")


class SecretScanningTests(unittest.TestCase):
    def test_repository_sources_do_not_contain_telegram_bot_tokens(self):
        token_pattern = re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b")
        text_suffixes = {
            ".py", ".md", ".yml", ".yaml", ".json", ".toml", ".txt",
        }
        excluded_directories = {
            ".git", ".pytest_cache", ".uv-cache", ".yf-cache",
            "data", "data_extended", "data_multimarket",
            "data_probability_signals", "data_risk_assets", "results",
        }
        exposed = []
        for source in PROJECT_DIR.rglob("*"):
            if not source.is_file():
                continue
            if any(part in excluded_directories for part in source.parts):
                continue
            if source.suffix.lower() not in text_suffixes and source.name != ".env.example":
                continue
            if token_pattern.search(source.read_text(encoding="utf-8")):
                exposed.append(str(source.relative_to(PROJECT_DIR)))

        self.assertEqual(exposed, [])


if __name__ == "__main__":
    unittest.main()
