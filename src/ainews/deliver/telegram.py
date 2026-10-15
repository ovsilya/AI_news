"""Telegram Bot API: post the brief to the channel (sendMessage, HTML parse mode; sendVoice for the brief read
aloud) and clean up old posts."""
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

    def call(self, method: str, payload: dict | None = None, files: dict | None = None) -> dict:
        url = API.format(token=self.token, method=method)
        for _ in range(3):
            if files:   # an upload: multipart form, and minutes rather than seconds to send it
                r = self.client.post(url, data=payload or {}, files=files, timeout=300)
            else:
                r = self.client.post(url, json=payload or {})
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

    def send_messages(self, messages: list[str], *, notify: bool = True) -> list[int]:
        """Sends the messages in order; only the first notifies (none with notify=False)."""
        ids = []
        for n, text in enumerate(messages):
            result = self.call("sendMessage", {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "link_preview_options": {"is_disabled": True},
                "disable_notification": n > 0 or not notify,  # one notification per brief, not one per message
            })
            ids.append(result["message_id"])
            self.sleep(1.0)  # stay well under the per-chat limit
        return ids

    def send_voice(self, ogg: bytes, *, caption: str = "", seconds: float = 0, notify: bool = True) -> int:
        """Sends OGG/Opus audio as a voice message (bots may send up to 50 MB); returns its id."""
        payload = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML",
                   "duration": str(round(seconds)), "disable_notification": "false" if notify else "true"}
        result = self.call("sendVoice", payload, files={"voice": ("brief.ogg", ogg, "audio/ogg")})
        self.sleep(1.0)
        return result["message_id"]

    def delete_messages(self, message_ids: list[int]) -> dict[str, list[int]]:
        """Delete messages one by one; returns their ids as {"deleted", "missing", "refused"}.

        In a channel the bot may delete only the posts it sent itself, and only within 48 hours."""
        out = {"deleted": [], "missing": [], "refused": []}
        for mid in message_ids:
            try:
                self.call("deleteMessage", {"chat_id": self.chat_id, "message_id": mid})
                out["deleted"].append(mid)
            except TelegramError as exc:
                out["missing" if "not found" in str(exc) else "refused"].append(mid)
            self.sleep(0.2)
        return out
