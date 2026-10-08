"""설정 로딩: config.yaml + 환경변수."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

DEFAULT_RULES = {
    "recent_days": 3,
    "baseline_days": 7,
    "min_impressions": 1000,
    "fatigue_frequency": 3.0,
    "high_frequency": 4.5,
    "ctr_drop": 0.25,
    "ctr_drop_z": 2.58,
    "peak_ctr_drop": 0.4,
    "cpa_warning_ratio": 1.3,
    "cpa_critical_ratio": 1.6,
    "roas_warning_ratio": 0.8,
    "no_conversion_cpa_multiple": 2.0,
    "cpm_spike": 0.4,
    "winner_roas_ratio": 1.3,
    "min_ads_per_adset": 2,
    "awareness_keywords": ["트래픽", "브랜딩", "도달", "인지", "awareness", "traffic", "reach"],
}

DEFAULT_TARGETS = {"cpa": 18000, "roas": 3.0, "ctr": 0.008}


@dataclass
class ClientConfig:
    name: str
    account_id: str
    currency: str = "KRW"
    conversion_actions: list[str] = field(
        default_factory=lambda: ["offsite_conversion.fb_pixel_purchase"]
    )
    monthly_budget: float = 0
    targets: dict = field(default_factory=lambda: dict(DEFAULT_TARGETS))
    rules: dict = field(default_factory=lambda: dict(DEFAULT_RULES))

    @property
    def act_id(self) -> str:
        return self.account_id if self.account_id.startswith("act_") else f"act_{self.account_id}"


@dataclass
class Config:
    clients: list[ClientConfig]
    api_version: str = "v21.0"
    timezone: str = "Asia/Seoul"
    output_dir: Path = Path("reports")
    notify: dict = field(default_factory=dict)
    access_token: str | None = None

    def client(self, key: str | None) -> ClientConfig:
        if key is None:
            return self.clients[0]
        for c in self.clients:
            if key in (c.name, c.account_id, c.act_id):
                return c
        raise KeyError(f"설정에 없는 클라이언트입니다: {key}")


def load_config(path: str | Path | None = None) -> Config:
    """config.yaml(또는 지정 경로)을 읽는다."""
    candidates = [Path(path)] if path else [Path("config.yaml")]
    raw: dict = {}
    for p in candidates:
        if p.exists():
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            break
    else:
        raise FileNotFoundError(candidates[0])

    global_rules = {**DEFAULT_RULES, **(raw.get("rules") or {})}
    clients = []
    for c in raw.get("clients") or []:
        clients.append(
            ClientConfig(
                name=c["name"],
                account_id=str(c["account_id"]),
                currency=c.get("currency", "KRW"),
                conversion_actions=c.get("conversion_actions")
                or ["offsite_conversion.fb_pixel_purchase"],
                monthly_budget=float(c.get("monthly_budget") or 0),
                targets={**DEFAULT_TARGETS, **(c.get("targets") or {})},
                rules={**global_rules, **(c.get("rules") or {})},
            )
        )
    if not clients:
        raise ValueError("config 에 clients 가 하나 이상 필요합니다.")

    notify = dict(raw.get("notify") or {})
    for key, env in [
        ("slack_webhook_url", "SLACK_WEBHOOK_URL"),
        ("telegram_bot_token", "TELEGRAM_BOT_TOKEN"),
        ("telegram_chat_id", "TELEGRAM_CHAT_ID"),
    ]:
        if os.environ.get(env):
            notify[key] = os.environ[env]

    return Config(
        clients=clients,
        api_version=raw.get("api_version", "v21.0"),
        timezone=raw.get("timezone", "Asia/Seoul"),
        output_dir=Path(raw.get("output_dir", "reports")),
        notify=notify,
        access_token=os.environ.get("META_ACCESS_TOKEN"),
    )
