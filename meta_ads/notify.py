"""알림 발송: 콘솔 / Slack Incoming Webhook / Telegram 봇."""
from __future__ import annotations

import logging

import requests

from .daily import filter_alerts
from .metrics import roas_fmt, won
from .rules import LEVEL_LABEL

log = logging.getLogger(__name__)


def build_message(res: dict, min_level: str = "warning", report_url: str | None = None) -> str:
    alerts = filter_alerts(res["alerts"], min_level)
    y = res["yesterday"]
    head = (
        f"[{res['client']}] 메타 광고 일일 점검 {res['date']}\n"
        f"어제 지출 {won(y['spend'])} · 전환 {y['conversions']:.0f}건 · "
        f"CPA {won(y['cpa'])} · ROAS {roas_fmt(y['roas'])}"
    )
    if not alerts:
        return head + "\n✅ 조치가 필요한 항목이 없습니다."
    body = []
    for a in alerts:
        body.append(f"{LEVEL_LABEL[a.level]} {a.title} — {a.ad or a.adset or '계정'}\n   {a.detail}\n   → {a.actions[0]}")
    tail = f"\n상세 보고서: {report_url}" if report_url else ""
    return head + "\n\n" + "\n".join(body) + tail


def send(res: dict, notify_cfg: dict, report_url: str | None = None) -> list[str]:
    msg = build_message(res, notify_cfg.get("min_level", "warning"), report_url)
    sent = []
    if notify_cfg.get("console", True):
        print(msg)
        sent.append("console")
    if url := notify_cfg.get("slack_webhook_url"):
        try:
            requests.post(url, json={"text": msg}, timeout=15).raise_for_status()
            sent.append("slack")
        except requests.RequestException as e:
            log.error("Slack 발송 실패: %s", e)
    token, chat = notify_cfg.get("telegram_bot_token"), notify_cfg.get("telegram_chat_id")
    if token and chat:
        try:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat, "text": msg}, timeout=15,
            ).raise_for_status()
            sent.append("telegram")
        except requests.RequestException as e:
            log.error("Telegram 발송 실패: %s", e)
    return sent
