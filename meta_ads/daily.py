"""일일 점검: 데이터 수집 → 규칙 평가 → 보고서 저장 → 알림."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .metrics import delta, num, pct, roas_fmt, totals, won
from .rules import LEVEL_LABEL, LEVELS, Alert, evaluate, windows


def run_daily(api, client_cfg, as_of: date) -> dict:
    w = windows(as_of, client_cfg.rules)
    month_start = w["recent"][1].replace(day=1)
    since = min(w["baseline"][0], w["lookback"][0], month_start)
    daily = api.insights(since, w["recent"][1], daily=True)
    freq = api.insights(*w["freq"], daily=False)
    ads = api.ads()
    alerts = evaluate(daily, freq, ads, client_cfg, as_of)

    yday = w["recent"][1]
    d = pd.to_datetime(daily["date"]).dt.date
    y = totals(daily[d == yday])
    prev7 = daily[(d < yday) & (d >= yday - timedelta(days=7))]
    p = totals(prev7)
    ndays = max(prev7["date"].nunique(), 1)
    for k in ("impressions", "clicks", "spend", "conversions", "revenue"):
        p[k] = p[k] / ndays  # 일평균으로 환산 (비율 지표는 그대로 유효)
    return {
        "client": client_cfg.name,
        "account_id": client_cfg.account_id,
        "as_of": as_of.isoformat(),
        "date": yday.isoformat(),
        "windows": {k: [v[0].isoformat(), v[1].isoformat()] for k, v in w.items()},
        "yesterday": y,
        "prev7_avg": p,
        "active_ads": int((ads["status"] == "ACTIVE").sum()),
        "alerts": alerts,
    }


def render_markdown(res: dict, client_cfg) -> str:
    y, p = res["yesterday"], res["prev7_avg"]
    tg = client_cfg.targets
    counts = {lv: sum(a.level == lv for a in res["alerts"]) for lv in LEVELS}

    def row(label, cur, prev, fmt, better_up=True):
        ch = delta(cur, prev)
        arrow = "" if pd.isna(ch) else ("▲" if ch > 0 else "▼")
        return f"| {label} | {fmt(cur)} | {fmt(prev)} | {arrow} {pct(abs(ch), 0) if not pd.isna(ch) else '-'} |"

    lines = [
        f"# [{res['client']}] 메타 광고 일일 점검 — {res['date']}",
        "",
        f"광고 계정 `act_{res['account_id']}` · 활성 소재 {res['active_ads']}개 · "
        f"목표 CPA {won(tg['cpa'])} / ROAS {roas_fmt(tg['roas'])}",
        "",
        f"**요약:** 🔴 위험 {counts['critical']} · 🟠 주의 {counts['warning']} · "
        f"🔵 참고 {counts['info']} · 🟢 우수 {counts['good']}",
        "",
        "## 어제 성과 (직전 7일 일평균 대비)",
        "",
        "| 지표 | 어제 | 7일 평균 | 변화 |",
        "|---|---:|---:|---:|",
        row("지출", y["spend"], p["spend"], won),
        row("노출", y["impressions"], p["impressions"], num),
        row("링크 클릭", y["clicks"], p["clicks"], num),
        row("CTR", y["ctr"], p["ctr"], pct),
        row("CPM", y["cpm"], p["cpm"], won),
        row("전환", y["conversions"], p["conversions"], lambda v: num(v, 1)),
        row("CPA", y["cpa"], p["cpa"], won),
        row("ROAS", y["roas"], p["roas"], roas_fmt),
        "",
    ]
    replace = [a for a in res["alerts"] if a.code in ("CREATIVE_FATIGUE", "NO_CONVERSION")]
    if replace:
        lines += ["## ⚠️ 오늘 바로 조치할 소재", ""]
        lines += [f"- **{a.ad}** ({a.adset}) — {a.title}" for a in replace]
        lines.append("")

    lines += ["## 상세 진단 및 제안", ""]
    for a in res["alerts"]:
        lines += [f"### {LEVEL_LABEL[a.level]} {a.title}", f"`{a.target}`", "", a.detail, ""]
        lines += [f"- {act}" for act in a.actions]
        lines.append("")
    if not res["alerts"]:
        lines.append("특이사항 없음 ✅")
    w = res["windows"]
    lines += [
        "---",
        f"비교 구간: 최근 {w['recent'][0]}~{w['recent'][1]} vs 기준 {w['baseline'][0]}~{w['baseline'][1]}, "
        f"빈도는 {w['freq'][0]}~{w['freq'][1]} 7일 순도달 기준",
    ]
    return "\n".join(lines)


def save(res: dict, md: str, out_dir: Path) -> tuple[Path, Path]:
    folder = out_dir / res["client"] / "daily"
    folder.mkdir(parents=True, exist_ok=True)
    md_path = folder / f"{res['date']}.md"
    md_path.write_text(md, encoding="utf-8")
    js_path = folder / f"{res['date']}.json"
    payload = {**res, "alerts": [a.to_dict() for a in res["alerts"]]}
    js_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return md_path, js_path


def filter_alerts(alerts: list[Alert], min_level: str) -> list[Alert]:
    floor = LEVELS.get(min_level, 2)
    return [a for a in alerts if LEVELS[a.level] >= floor or a.code == "WINNER"]
