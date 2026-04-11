# src/infra/notifier.py
import requests
from src.core.interfaces import INotifier


class TelegramNotifier(INotifier):
    def __init__(self, token: str, chat_id: str, logger):
        self.token = token
        self.chat_id = chat_id
        self.logger = logger
        self.base_url = f"https://api.telegram.org/bot{token}/sendMessage"

    def send_message(self, message: str) -> None:
        self._send(f"[MagicSplit]\n{message}")

    def send_alert(self, message: str) -> None:
        self._send(f"[WARNING]\n{message}")

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

    def send_message(self, message: str) -> None:
        self._send(f"*[MagicSplit]*\n{message}")

    def send_alert(self, message: str) -> None:
        self._send(f"*[WARNING]* <!channel>\n{message}")

    def _send(self, text: str):
        if not self.webhook_url:
            self.logger.info(f"[Slack Mock] {text}")
            return
        try:
            payload = {"text": text}
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
