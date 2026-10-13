"""Telegram Bot API: post the brief to the channel (sendMessage, HTML parse mode)."""
from __future__ import annotations

import time

import httpx

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramError(RuntimeError):
    pass


class Telegram:
    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None, sleep=time.sleep):
        if not token or not chat_id:
            raise TelegramError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set (see .env.example)")
        self.token, self.chat_id = token, chat_id
        self.client = client or httpx.Client(timeout=30)
        self.sleep = sleep

    def call(self, method: str, payload: dict | None = None) -> dict:
        for _ in range(3):
            r = self.client.post(API.format(token=self.token, method=method), json=payload or {})
            data = r.json()
            if data.get("ok"):
                return data["result"]
            retry = (data.get("parameters") or {}).get("retry_after")
            if r.status_code == 429 and retry:
                self.sleep(float(retry))
                continue
            # Never include the URL in the error: it contains the bot token.
            raise TelegramError(f"{method}: {data.get('error_code')} {data.get('description')}")
        raise TelegramError(f"{method}: rate-limited three times")

    def get_me(self) -> dict:
        return self.call("getMe")

    def send_messages(self, messages: list[str]) -> list[int]:
        ids = []
        for text in messages:
            result = self.call("sendMessage", {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
            })
            ids.append(result["message_id"])
            self.sleep(1.0)  # stay well under the per-chat limit
        return ids
