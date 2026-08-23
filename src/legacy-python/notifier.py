"""Notification adapters used by the application service.

Importing this module never sends a notification. Credentials are supplied at
runtime so that secrets are not stored in source control.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping
from dataclasses import dataclass

import requests


TELEGRAM_API_ROOT = "https://api.telegram.org"


class NotificationConfigurationError(RuntimeError):
    """Raised when notification credentials are missing."""


class NotificationDeliveryError(RuntimeError):
    """Raised when a notification provider rejects or cannot deliver a message."""


@dataclass(frozen=True)
class TelegramSettings:
    """Telegram credentials loaded from the process environment."""

    bot_token: str
    chat_id: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TelegramSettings":
        values = os.environ if env is None else env
        bot_token = values.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = values.get("TELEGRAM_CHAT_ID", "").strip()
        missing = [
            name
            for name, value in (
                ("TELEGRAM_BOT_TOKEN", bot_token),
                ("TELEGRAM_CHAT_ID", chat_id),
            )
            if not value
        ]
        if missing:
            raise NotificationConfigurationError(
                f"Missing notification environment variables: {', '.join(missing)}"
            )
        return cls(bot_token=bot_token, chat_id=chat_id)


class TelegramNotifier:
    """Send explicit text notifications through the Telegram Bot API."""

    def __init__(
        self,
        settings: TelegramSettings,
        *,
        session: requests.Session | None = None,
        timeout_seconds: float = 10.0,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._settings = settings
        self._session = session or requests.Session()
        self._timeout_seconds = timeout_seconds

    def send(self, message: str) -> dict:
        """Send one message and return the provider response.

        Provider exceptions are deliberately replaced with a token-free error.
        Telegram embeds the bot token in the request URL, so propagating a
        ``requests`` exception could leak the credential into application logs.
        """
        if not message or not message.strip():
            raise ValueError("message must not be empty")

        url = (
            f"{TELEGRAM_API_ROOT}/bot{self._settings.bot_token}/sendMessage"
        )
        try:
            response = self._session.post(
                url,
                data={
                    "chat_id": self._settings.chat_id,
                    "text": message,
                },
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError):
            raise NotificationDeliveryError(
                "Telegram notification delivery failed"
            ) from None

        if not isinstance(result, dict) or result.get("ok") is not True:
            raise NotificationDeliveryError(
                "Telegram notification delivery was rejected"
            )
        return result


def main(argv: list[str] | None = None) -> int:
    """Send a manually supplied message using environment credentials."""
    parser = argparse.ArgumentParser(description="Send a Telegram notification")
    parser.add_argument("message", help="text to send")
    args = parser.parse_args(argv)

    notifier = TelegramNotifier(TelegramSettings.from_env())
    notifier.send(args.message)
    print("Telegram notification sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
