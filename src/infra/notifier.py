# src/infra/notifier.py
import requests
from typing import Optional
from src.core.interfaces import INotifier


class TelegramNotifier(INotifier):
    def __init__(self, token: str, chat_id: str, logger):
        self.token = token
        self.chat_id = chat_id
        self.logger = logger
        self.base_url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send_message(self, message: str, detail: Optional[str] = None) -> None:
        text = f"[MagicSplit]\n{message}"
        if detail:
            text += f"\n\n[Details]\n{detail}"
        self._send(text)

    def send_alert(self, message: str, detail: Optional[str] = None) -> None:
        text = f"[WARNING]\n{message}"
        if detail:
            text += f"\n\n[Details]\n{detail}"
        self._send(text)

    def _send(self, text: str):
        if not self.token or not self.chat_id:
            self.logger.info(f"[Telegram Mock] {text}")
            return
        try:
            payload = {"chat_id": self.chat_id, "text": text}
            requests.post(self.base_url, json=payload, timeout=5)
        except Exception as e:
            self.logger.error(f"[Telegram Error] Failed to send: {e}")


class SlackNotifier(INotifier):
    def __init__(self, webhook_url: str, logger):
        self.webhook_url = webhook_url
        self.logger = logger

    def send_message(self, message: str, detail: Optional[str] = None) -> None:
        self._send_formatted(f"*[MagicSplit]*\n{message}", detail)

    def send_alert(self, message: str, detail: Optional[str] = None) -> None:
        self._send_formatted(f"*[WARNING]* <!channel>\n{message}", detail)

    def _send_formatted(self, summary: str, detail: Optional[str] = None):
        if not detail:
            self._send({"text": summary})
            return

        # Block Kit을 사용하여 요약과 상세 로그 분리
        payload = {
            "blocks": [
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": summary}
                },
                {
                    "type": "divider"
                },
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"```\n{detail}\n```"}
                }
            ]
        }
        self._send(payload)

    def _send(self, payload: dict):
        if not self.webhook_url:
            self.logger.info(f"[Slack Mock] {payload}")
            return
        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=5,
            )
            if response.status_code != 200:
                self.logger.error(
                    f"[Slack Error] Status: {response.status_code}, Body: {response.text}"
                )
        except Exception as e:
            self.logger.error(f"[Slack Error] Connection failed: {e}")
