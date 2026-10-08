"""월간 보고서 HTML 렌더링 (외부 의존성 없는 단일 파일, 인라인 SVG 차트)."""
from __future__ import annotations

import math
from datetime import datetime
from html import escape
from pathlib import Path

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .metrics import delta, num, pct, roas_fmt, won

TEMPLATES = Path(__file__).parent / "templates"


def _nice_max(v: float) -> float:
    if not v or v <= 0 or math.isnan(v):
        return 1
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * exp >= v:
            return m * exp
    return 10 * exp


def _axis_label(v: float, fmt) -> str:
    return fmt(v)


def svg_bars(labels: list[str], values: list[float], fmt, tip_fmt=None, height: int = 220) -> Markup:
    """단일 계열 세로 막대 (기준선에 붙고 윗쪽 끝만 4px 라운드)."""
    W, H, L, R, T, B = 720, height, 64, 8, 12, 28
    pw, ph = W - L - R, H - T - B
    vmax = _nice_max(max(values) if values else 0)
    n = max(len(values), 1)
    slot = pw / n
    bw = max(2.0, min(18.0, slot - 2))
    tip_fmt = tip_fmt or fmt
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img">']
    for i in range(5):
        y = T + ph - ph * i / 4
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{L - 8}" y="{y + 4:.1f}" class="tick" text-anchor="end">{escape(_axis_label(vmax * i / 4, fmt))}</text>')
    for i, (lab, v) in enumerate(zip(labels, values)):
        x = L + slot * i + (slot - bw) / 2
        h = ph * (v / vmax) if vmax else 0
        y0 = T + ph
        r = min(4, bw / 2, h)
        if h > 0:
            d = (f"M{x:.1f},{y0:.1f} V{y0 - h + r:.1f} Q{x:.1f},{y0 - h:.1f} {x + r:.1f},{y0 - h:.1f} "
                 f"H{x + bw - r:.1f} Q{x + bw:.1f},{y0 - h:.1f} {x + bw:.1f},{y0 - h + r:.1f} V{y0:.1f} Z")
            out.append(f'<path d="{d}" class="bar"/>')
        # 막대보다 넓은 히트 영역 + 툴팁
        out.append(f'<rect x="{L + slot * i:.1f}" y="{T}" width="{slot:.1f}" height="{ph}" class="hit">'
                   f'<title>{escape(lab)}: {escape(tip_fmt(v))}</title></rect>')
        if n <= 31 and (i % max(1, n // 8) == 0 or i == n - 1):
            out.append(f'<text x="{x + bw / 2:.1f}" y="{H - 8}" class="tick" text-anchor="middle">{escape(lab)}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{T + ph}" y2="{T + ph}" class="axis"/></svg>')
    return Markup("".join(out))


def svg_line(labels: list[str], values: list[float], fmt, target: float | None = None,
             target_label: str = "목표", height: int = 200) -> Markup:
    """단일 계열 꺾은선 + (선택) 목표 기준선."""
    W, H, L, R, T, B = 720, height, 64, 8, 12, 28
    pw, ph = W - L - R, H - T - B
    clean = [v for v in values if v is not None and not math.isnan(v)]
    vmax = _nice_max(max(clean + ([target] if target else [])) if clean else 0)
    n = max(len(values), 1)
    step = pw / max(n - 1, 1)
    out = [f'<svg viewBox="0 0 {W} {H}" class="chart" role="img">']
    for i in range(5):
        y = T + ph - ph * i / 4
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{L - 8}" y="{y + 4:.1f}" class="tick" text-anchor="end">{escape(fmt(vmax * i / 4))}</text>')
    if target:
        ty = T + ph - ph * target / vmax
        out.append(f'<line x1="{L}" x2="{W - R}" y1="{ty:.1f}" y2="{ty:.1f}" class="target"/>')
        out.append(f'<text x="{W - R - 4}" y="{ty - 6:.1f}" class="tick strong" text-anchor="end">{escape(target_label)} {escape(fmt(target))}</text>')
    pts = []
    for i, v in enumerate(values):
        if v is None or math.isnan(v):
            continue
        pts.append((L + step * i, T + ph - ph * v / vmax, labels[i], v))
    if pts:
        out.append('<polyline class="line" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y, *_ in pts) + '"/>')
        lx, ly, _, lv = pts[-1]
        out.append(f'<circle cx="{lx:.1f}" cy="{ly:.1f}" r="4" class="dot"/>')
    for x, y, lab, v in pts:
        out.append(f'<rect x="{x - step / 2:.1f}" y="{T}" width="{max(step, 6):.1f}" height="{ph}" class="hit">'
                   f'<title>{escape(lab)}: {escape(fmt(v))}</title></rect>')
    for i, lab in enumerate(labels):
        if n <= 31 and (i % max(1, n // 8) == 0 or i == n - 1):
            out.append(f'<text x="{L + step * i:.1f}" y="{H - 8}" class="tick" text-anchor="middle">{escape(lab)}</text>')
    out.append(f'<line x1="{L}" x2="{W - R}" y1="{T + ph}" y2="{T + ph}" class="axis"/></svg>')
    return Markup("".join(out))


def meter(v: float, vmax: float = 1.0) -> Markup:
    w = 0 if not vmax or pd.isna(v) else max(0, min(100, v / vmax * 100))
    return Markup(f'<span class="meter"><span style="width:{w:.1f}%"></span></span>')


def render_html(rep) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    env.filters.update(won=won, pct=pct, num=num, roas=roas_fmt)
    k, kp, tg = rep.kpi, rep.kpi_prev, rep.targets

    def tile(key, label, fmt, up_good=True, target=None, target_good_if_above=True):
        ch = delta(k[key], kp[key])
        good = None if pd.isna(ch) else ((ch > 0) == up_good)
        status = None
        if target is not None and not pd.isna(k[key]):
            status = (k[key] >= target) if target_good_if_above else (k[key] <= target)
        return {"label": label, "value": fmt(k[key]), "prev": fmt(kp[key]),
                "delta": None if pd.isna(ch) else f"{ch * 100:+.0f}%", "good": good,
                "target": fmt(target) if target is not None else None, "on_target": status}

    tiles = [
        tile("spend", "광고비", won, up_good=True),
        tile("revenue", "매출", won),
        tile("roas", "ROAS", roas_fmt, target=tg["roas"]),
        tile("conversions", "전환", num),
        tile("cpa", "CPA", won, up_good=False, target=tg["cpa"], target_good_if_above=False),
        tile("ctr", "링크 CTR", pct, target=tg["ctr"]),
        tile("cpc", "CPC", won, up_good=False),
        tile("cpm", "CPM", won, up_good=False),
    ]
    d = rep.daily
    labels = [str(x)[5:].replace("-", "/") for x in d["date"]]
    charts = {
        "spend": svg_bars(labels, d["spend"].tolist(), lambda v: f"{v / 10000:,.1f}".rstrip("0").rstrip(".") + "만" if v >= 10000 else f"{v:,.0f}",
                          tip_fmt=won),
        "roas": svg_line(labels, d["roas"].tolist(), roas_fmt, target=tg["roas"]),
        "ctr": svg_line(labels, d["ctr"].tolist(), lambda v: pct(v, 1), target=tg["ctr"], target_label="기준"),
    }
    judged = rep.ads[(rep.ads["spend"] >= tg["cpa"] * 3) & ~rep.ads["awareness"]]
    top_ads = judged.sort_values("roas", ascending=False).head(5)
    bottom_ads = judged.sort_values("roas").head(5)
    demo = rep.demo.sort_values(["age_order", "gender"]) if not rep.demo.empty else rep.demo
    tpl = env.get_template("monthly.html")
    return tpl.render(
        rep=rep, tiles=tiles, charts=charts, top_ads=top_ads, bottom_ads=bottom_ads,
        demo=demo, placement=rep.placement, meter=meter,
        max_share=lambda df: max(df["spend_share"].max(), df["conv_share"].max()) if len(df) else 1,
        generated=datetime.now().strftime("%Y-%m-%d %H:%M"), isnan=lambda v: v is None or pd.isna(v),
    )
