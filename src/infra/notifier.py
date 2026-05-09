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
    def __init__(self, webhook_url: str, logger, bot_token: str = "", channel_id: str = ""):
        self.webhook_url = webhook_url
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.logger = logger

    def send_message(self, message: str, detail: Optional[str] = None) -> None:
        self._send_formatted(f"*[MagicSplit]*\n{message}", detail)

    def send_alert(self, message: str, detail: Optional[str] = None) -> None:
        self._send_formatted(f"*[WARNING]* <!channel>\n{message}", detail)

    def _send_formatted(self, summary: str, detail: Optional[str] = None):
        # 1. API 방식 (스레드 지원) 시도
        if self.bot_token and self.channel_id:
            try:
                # 메인 메시지 전송
                parent_ts = self._send_via_api(self.channel_id, summary)
                if parent_ts and detail:
                    # 상세 정보를 스레드로 전송
                    self._send_via_api(self.channel_id, f"```\n{detail}\n```", thread_ts=parent_ts)
                return
            except Exception as e:
                self.logger.error(f"[Slack API Error] Failed: {e}")
                # 실패 시 웹후크로 폴백 시도

        # 2. 웹후크 방식 (기존 방식)
        if not detail:
            self._send_via_webhook({"text": summary})
            return

        # Block Kit을 사용하여 요약과 상세 로그 분리 (웹후크용)
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
        self._send_via_webhook(payload)

    def _send_via_api(self, channel: str, text: str, thread_ts: Optional[str] = None) -> Optional[str]:
        """Slack Web API (chat.postMessage)를 사용하여 메시지를 전송한다."""
        url = "https://slack.com/api/chat.postMessage"
        headers = {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json; charset=utf-8"
        }
        payload = {
            "channel": channel,
            "text": text
        }
        if thread_ts:
            payload["thread_ts"] = thread_ts

        response = requests.post(url, json=payload, headers=headers, timeout=5)
        res_json = response.json()
        if not res_json.get("ok"):
            error_msg = res_json.get("error", "unknown error")
            raise Exception(f"Slack API error: {error_msg}")
        
        return res_json.get("ts")

    def _send_via_webhook(self, payload: dict):
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
