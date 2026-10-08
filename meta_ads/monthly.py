"""월간 결과 보고서: 집계 → 분석 → 다음 달 방향 제시 → HTML/Markdown/Excel 저장."""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from .metrics import add_kpis, delta, num, pct, roas_fmt, totals, won

FORMAT_KEYWORDS = [
    ("릴스", ["릴스", "reels", "reel", "숏폼"]),
    ("영상", ["영상", "video", "vid", "동영상"]),
    ("캐러셀", ["캐러셀", "carousel", "슬라이드"]),
    ("카드뉴스", ["카드뉴스", "card"]),
    ("이미지", ["이미지", "image", "img", "배너", "banner"]),
]
AGE_ORDER = ["13-17", "18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
GENDER_KO = {"female": "여성", "male": "남성", "unknown": "미상"}
PLACEMENT_KO = {
    "feed": "피드", "instagram_reels": "릴스", "instagram_stories": "스토리", "facebook_reels": "릴스",
    "video_feeds": "동영상 피드", "story": "스토리", "classic": "네이티브·배너", "messenger_inbox": "받은편지함",
    "instagram_explore": "탐색", "marketplace": "마켓플레이스", "search": "검색", "rewarded_video": "보상형 동영상",
}
PLATFORM_KO = {"instagram": "인스타그램", "facebook": "페이스북", "audience_network": "오디언스 네트워크",
               "messenger": "메신저", "threads": "스레드"}


def ad_format(name: str) -> str:
    low = str(name).lower()
    for label, kws in FORMAT_KEYWORDS:
        if any(k in low for k in kws):
            return label
    return "기타"


def month_range(y: int, m: int) -> tuple[date, date]:
    return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])


@dataclass
class Insight:
    area: str      # 성과 | 예산 | 소재 | 타겟 | 지면
    tone: str      # good | warning | critical | info
    text: str


@dataclass
class MonthlyReport:
    client: str
    account_id: str
    period: tuple[date, date]
    prev_period: tuple[date, date]
    targets: dict
    kpi: dict
    kpi_prev: dict
    daily: pd.DataFrame
    campaigns: pd.DataFrame
    adsets: pd.DataFrame
    ads: pd.DataFrame
    formats: pd.DataFrame
    demo: pd.DataFrame
    placement: pd.DataFrame
    budget_plan: pd.DataFrame
    insights: list[Insight] = field(default_factory=list)
    next_steps: list[dict] = field(default_factory=list)
    next_targets: dict = field(default_factory=dict)
    is_mock: bool = False


def _group(df: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    cols = ["impressions", "reach", "clicks", "spend", "conversions", "revenue"]
    if df.empty:
        return add_kpis(pd.DataFrame(columns=keys + cols))
    g = df.groupby(keys, as_index=False, dropna=False)[cols].sum()
    g = add_kpis(g)
    total_spend, total_conv = g["spend"].sum(), g["conversions"].sum()
    g["spend_share"] = g["spend"] / total_spend if total_spend else 0
    g["conv_share"] = g["conversions"] / total_conv if total_conv else 0
    return g.sort_values("spend", ascending=False).reset_index(drop=True)


def build_report(api, client_cfg, year: int, month: int, mock: bool = False) -> MonthlyReport:
    tg, rules = client_cfg.targets, client_cfg.rules
    start, end = month_range(year, month)
    pstart, pend = month_range(*((year - 1, 12) if month == 1 else (year, month - 1)))

    cur = api.insights(start, end, daily=True)
    prev = api.insights(pstart, pend, daily=True)
    per_ad_month = api.insights(start, end, daily=False)
    demo_raw = api.insights(start, end, daily=False, breakdowns=["age", "gender"])
    place_raw = api.insights(start, end, daily=False, breakdowns=["publisher_platform", "platform_position"])
    ads_meta = api.ads()

    kpi, kpi_prev = totals(cur), totals(prev)
    if not per_ad_month.empty:
        kpi["reach"] = float(per_ad_month["reach"].sum())  # 광고별 순도달 합 (광고 간 중복 가능)

    # 일별 추이
    daily = _group(cur, ["date"]).sort_values("date").reset_index(drop=True)

    # 캠페인 / 광고세트 (전월 대비)
    campaigns = _group(cur, ["campaign_id", "campaign_name"])
    prev_c = _group(prev, ["campaign_id"])[["campaign_id", "spend", "conversions", "cpa", "roas"]]
    campaigns = campaigns.merge(prev_c, on="campaign_id", how="left", suffixes=("", "_prev"))
    adsets = _group(cur, ["campaign_name", "adset_id", "adset_name"])

    # 광고(소재) — 전반/후반 CTR 비교로 피로 여부 판단
    ads = _group(cur, ["campaign_name", "adset_name", "ad_id", "ad_name"])
    mid = start + timedelta(days=(end - start).days // 2)
    d = pd.to_datetime(cur["date"]).dt.date
    h1 = _group(cur[d <= mid], ["ad_id"])[["ad_id", "ctr"]].rename(columns={"ctr": "ctr_h1"})
    h2 = _group(cur[d > mid], ["ad_id"])[["ad_id", "ctr"]].rename(columns={"ctr": "ctr_h2"})
    ads = ads.merge(h1, on="ad_id", how="left").merge(h2, on="ad_id", how="left")
    ads["ctr_trend"] = ads["ctr_h2"] / ads["ctr_h1"] - 1
    freq = per_ad_month.assign(frequency_month=per_ad_month["impressions"] / per_ad_month["reach"].where(per_ad_month["reach"] > 0))
    ads = ads.merge(freq[["ad_id", "frequency_month"]], on="ad_id", how="left")
    ads = ads.merge(ads_meta[["ad_id", "status"]], on="ad_id", how="left")
    ads["format"] = ads["ad_name"].map(ad_format)
    ads["fatigued"] = (ads["ctr_trend"] <= -0.3) & (ads["impressions"] >= rules["min_impressions"] * 5)
    formats = _group(ads.assign(format=ads["format"]), ["format"])

    # 인구통계 / 지면
    demo = _group(demo_raw, ["age", "gender"]) if not demo_raw.empty else _group(demo_raw, ["age", "gender"])
    if not demo.empty:
        demo["age_order"] = demo["age"].map(lambda a: AGE_ORDER.index(a) if a in AGE_ORDER else 99)
        demo["label"] = demo["age"] + " " + demo["gender"].map(lambda g: GENDER_KO.get(g, g))
    placement = _group(place_raw, ["publisher_platform", "platform_position"])
    if not placement.empty:
        placement["label"] = (placement["publisher_platform"].map(lambda p: PLATFORM_KO.get(p, p)) + " "
                              + placement["platform_position"].map(lambda p: PLACEMENT_KO.get(p, p)))

    rep = MonthlyReport(
        client=client_cfg.name, account_id=client_cfg.account_id, period=(start, end),
        prev_period=(pstart, pend), targets=tg, kpi=kpi, kpi_prev=kpi_prev, daily=daily,
        campaigns=campaigns, adsets=adsets, ads=ads, formats=formats, demo=demo,
        placement=placement, budget_plan=pd.DataFrame(), is_mock=mock,
    )
    analyze(rep, client_cfg)
    return rep


# ---------------------------------------------------------------------------
# 분석 & 다음 달 방향
# ---------------------------------------------------------------------------
def _efficiency_index(df: pd.DataFrame, overall_roas: float, overall_cpa: float) -> pd.Series:
    """ROAS 가 있으면 ROAS 기준, 없으면 CPA 역수 기준의 상대 효율 (1.0 = 계정 평균)."""
    if overall_roas and overall_roas > 0 and df["revenue"].sum() > 0:
        return (df["roas"] / overall_roas).fillna(0)
    return (overall_cpa / df["cpa"]).replace([float("inf")], 0).fillna(0)


def analyze(rep: MonthlyReport, client_cfg) -> None:
    tg, k, kp = rep.targets, rep.kpi, rep.kpi_prev
    rules = client_cfg.rules
    ins: list[Insight] = []
    awareness = [w.lower() for w in rules.get("awareness_keywords", [])]

    # 1) 전체 성과
    roas_ok = k["roas"] >= tg["roas"]
    cpa_ok = k["cpa"] <= tg["cpa"]
    ins.append(Insight(
        "성과", "good" if roas_ok and cpa_ok else ("warning" if roas_ok or cpa_ok else "critical"),
        f"월 광고비 {won(k['spend'])} 집행으로 전환 {num(k['conversions'])}건, 매출 {won(k['revenue'])}. "
        f"ROAS {roas_fmt(k['roas'])}(목표 {roas_fmt(tg['roas'])}), CPA {won(k['cpa'])}(목표 {won(tg['cpa'])}) — "
        + ("두 목표 모두 달성." if roas_ok and cpa_ok else "목표 " + ("일부 " if roas_ok or cpa_ok else "") + "미달."),
    ))
    if kp["spend"]:
        parts = []
        for key, label, fmt, up_good in [("cpa", "CPA", won, False), ("roas", "ROAS", roas_fmt, True),
                                         ("ctr", "CTR", pct, True), ("cpm", "CPM", won, False)]:
            ch = delta(k[key], kp[key])
            if not pd.isna(ch):
                parts.append(f"{label} {fmt(kp[key])}→{fmt(k[key])}({'+' if ch > 0 else ''}{ch * 100:.0f}%)")
        ch_roas = delta(k["roas"], kp["roas"])
        ins.append(Insight("성과", "good" if ch_roas > 0.05 else ("critical" if ch_roas < -0.1 else "info"),
                           "전월 대비 " + ", ".join(parts) + "."))
        cpm_ch = delta(k["cpm"], kp["cpm"])
        if cpm_ch > 0.15:
            ins.append(Insight("성과", "warning",
                               f"CPM이 전월보다 {cpm_ch * 100:.0f}% 올랐습니다. 시즌 경쟁 심화 또는 소재 품질 저하 신호 — "
                               "CTR이 같이 떨어졌다면 소재 문제, CTR이 유지됐다면 경매 경쟁 문제로 봅니다."))

    # 2) 캠페인 효율 & 예산 재분배
    c = rep.campaigns.copy()
    c["awareness"] = c["campaign_name"].str.lower().map(lambda n: any(w in n for w in awareness))
    conv_c = c[~c["awareness"]].copy()
    conv_c["eff"] = _efficiency_index(conv_c, k["roas"], k["cpa"])
    plan = c[["campaign_name", "spend", "conversions", "cpa", "roas", "awareness"]].copy()
    plan["eff"] = conv_c["eff"].reindex(plan.index)
    # 효율 지수에 비례해 ±30% 범위에서 증감, 인지 캠페인은 유지, 합계는 다음 달 예산에 맞춤
    plan["factor"] = plan.apply(
        lambda r: 1.0 if r["awareness"] else min(1.3, max(0.7, r["eff"] ** 0.5 if r["eff"] > 0 else 0.7)), axis=1)
    raw_next = plan["spend"] * plan["factor"]
    next_budget = client_cfg.monthly_budget or k["spend"]
    plan["next_budget"] = (raw_next / raw_next.sum() * next_budget).round(-4) if raw_next.sum() else 0
    plan["change"] = plan["next_budget"] / plan["spend"] - 1
    rep.budget_plan = plan

    if len(conv_c):
        best = conv_c.sort_values("eff", ascending=False).iloc[0]
        worst = conv_c.sort_values("eff").iloc[0]
        if best["campaign_name"] != worst["campaign_name"] and worst["eff"] < 0.8:
            ins.append(Insight(
                "예산", "warning",
                f"'{worst['campaign_name']}' 효율이 계정 평균의 {worst['eff'] * 100:.0f}% 수준 "
                f"(ROAS {roas_fmt(worst['roas'])}, CPA {won(worst['cpa'])}) — 예산의 {pct(worst['spend_share'], 0)}를 사용 중. "
                f"'{best['campaign_name']}'(ROAS {roas_fmt(best['roas'])})로 예산 이동을 권장합니다.",
            ))
    for r in c[c["awareness"]].itertuples():
        ins.append(Insight("예산", "info",
                           f"'{r.campaign_name}'은 인지/트래픽 목적으로 분류해 CPA·ROAS 대신 CPC {won(r.cpc)}, "
                           f"CTR {pct(r.ctr)}로 평가했습니다. 전환 기여(직접 ROAS {roas_fmt(r.roas)})가 낮다면 "
                           "리타겟팅 모수 확보 목적인지 확인하고 예산 비중을 재검토하세요."))

    # 3) 소재
    a = rep.ads
    is_aw = a["campaign_name"].str.lower().map(lambda n: any(w in n for w in awareness))
    a["awareness"] = is_aw
    judged = a[(a["spend"] >= tg["cpa"] * 3) & ~is_aw]  # 전환 캠페인 소재만 효율 순위 대상
    fatigued = a[a["fatigued"]]
    if len(fatigued):
        ins.append(Insight("소재", "critical",
                           f"월 후반 CTR이 전반 대비 30% 이상 떨어진 소재 {len(fatigued)}개: "
                           + ", ".join(f"{r.ad_name}({r.ctr_trend * 100:+.0f}%)" for r in fatigued.itertuples())
                           + ". 다음 달 초 교체 대상입니다."))
    if len(judged):
        top = judged.sort_values("roas", ascending=False).head(1).iloc[0]
        top_share = top["conversions"] / k["conversions"] if k["conversions"] else 0
        ins.append(Insight("소재", "good",
                           f"최고 효율 소재는 '{top['ad_name']}' (ROAS {roas_fmt(top['roas'])}, CPA {won(top['cpa'])}, "
                           f"CTR {pct(top['ctr'])}) — 전체 전환의 {pct(top_share, 0)}를 차지합니다."))
        if top_share > 0.4:
            ins.append(Insight("소재", "warning",
                               "전환이 한 소재에 40% 이상 집중돼 있습니다. 이 소재가 피로해지면 계정 성과가 급락하므로 "
                               "같은 콘셉트의 변형 소재를 미리 준비해야 합니다."))
    losers = judged[(judged["roas"] < tg["roas"] * 0.5) | (judged["conversions"] == 0)] if len(judged) else judged
    if len(losers):
        ins.append(Insight("소재", "critical",
                           "목표 ROAS의 절반에 못 미치는 소재: "
                           + ", ".join(f"{r.ad_name}(광고비 {won(r.spend)}, ROAS {roas_fmt(r.roas)})" for r in losers.itertuples())
                           + f" — 합계 {won(losers['spend'].sum())}, 전체 광고비의 {pct(losers['spend'].sum() / k['spend'], 0)}."))
    f = rep.formats[rep.formats["spend"] > 0]
    if len(f) >= 2:
        fb = f.assign(eff=_efficiency_index(f, k["roas"], k["cpa"])).sort_values("eff", ascending=False)
        ins.append(Insight("소재", "info",
                           "형식별 효율: " + " > ".join(f"{r.format}(ROAS {roas_fmt(r.roas)}, CTR {pct(r.ctr)})"
                                                    for r in fb.itertuples()) + "."))

    # 4) 타겟 (연령·성별)
    seg_insight(rep.demo, "타겟", "label", k, ins, min_share=0.03)
    # 5) 지면
    seg_insight(rep.placement, "지면", "label", k, ins, min_share=0.03)

    rep.insights = ins

    # ---- 다음 달 실행 계획 ----
    steps: list[dict] = []
    if len(fatigued):
        steps.append({"area": "소재", "priority": "높음",
                      "action": f"피로 소재 {len(fatigued)}개 교체 — 첫 주에 신규 소재 {max(3, len(fatigued) * 2)}개 집행 시작",
                      "why": "CTR 하락이 이어지면 CPM·CPA가 함께 오릅니다."})
    active_losers = losers[losers["status"] == "ACTIVE"] if len(losers) else losers
    if len(active_losers):
        steps.append({"area": "소재", "priority": "높음",
                      "action": "저효율 소재 OFF 후 예산 회수: " + ", ".join(active_losers["ad_name"]),
                      "why": f"목표 ROAS의 50% 미만 — 월 {won(active_losers['spend'].sum())} 절감 가능."})
    if len(judged):
        top = judged.sort_values("roas", ascending=False).iloc[0]
        steps.append({"area": "소재", "priority": "높음",
                      "action": f"'{top['ad_name']}' 콘셉트로 변형 소재 3종 제작 (훅·썸네일·카피 각각 다르게)",
                      "why": "검증된 메시지를 유지하면서 피로를 분산합니다."})
    if len(f) >= 2:
        best_fmt = f.assign(eff=_efficiency_index(f, k["roas"], k["cpa"])).sort_values("eff", ascending=False).iloc[0]
        steps.append({"area": "소재", "priority": "중간",
                      "action": f"신규 소재의 절반 이상을 '{best_fmt['format']}' 형식으로 제작",
                      "why": f"이번 달 형식별 효율 1위 (ROAS {roas_fmt(best_fmt['roas'])})."})
    up = plan[plan["change"] > 0.05].sort_values("change", ascending=False)
    down = plan[plan["change"] < -0.05].sort_values("change")
    if len(up) or len(down):
        moves = [f"{r.campaign_name} {r.change * 100:+.0f}%" for r in pd.concat([up, down]).itertuples()]
        steps.append({"area": "예산", "priority": "높음",
                      "action": "캠페인 예산 재배분: " + ", ".join(moves) + " (주 단위로 20%씩 단계 조정)",
                      "why": "효율 지수(ROAS/CPA 기준)에 비례해 배분했습니다."})
    for seg, area in [(rep.demo, "타겟"), (rep.placement, "지면")]:
        if seg is not None and len(seg) and k["roas"]:
            s = seg[seg["spend_share"] >= 0.03].copy()
            s["eff"] = _efficiency_index(s, k["roas"], k["cpa"])
            weak = s[s["eff"] < 0.5]
            if len(weak):
                steps.append({"area": area, "priority": "중간",
                              "action": ("제외/축소 테스트: " if area == "타겟" else "지면 제외 테스트: ")
                              + ", ".join(weak["label"].head(3)),
                              "why": f"지출 {pct(weak['spend'].sum() / k['spend'], 0)}를 쓰고 효율은 평균의 절반 미만."})
    small_sets = rep.adsets.merge(
        a[a["status"] == "ACTIVE"].groupby("adset_name").size().rename("n_ads").reset_index(), on="adset_name", how="left")
    small_sets = small_sets[small_sets["n_ads"].fillna(0) < 3]
    if len(small_sets):
        steps.append({"area": "구조", "priority": "중간",
                      "action": "활성 소재 3개 미만 광고세트 보강: " + ", ".join(small_sets["adset_name"].head(4)),
                      "why": "광고세트당 3~5개 소재가 있어야 메타가 효율 소재를 찾고 피로에도 대응할 수 있습니다."})
    steps.append({"area": "측정", "priority": "낮음",
                  "action": "픽셀·전환 API(CAPI) 이벤트 매칭 품질과 구매 이벤트 중복 여부 월 1회 점검",
                  "why": "추적 누락은 성과 저하로 오인되어 잘못된 의사결정을 만듭니다."})
    order = {"높음": 0, "중간": 1, "낮음": 2}
    rep.next_steps = sorted(steps, key=lambda st: order[st["priority"]])

    # 다음 달 목표: 이번 달 실적과 목표 중 '조금 더 나은' 수준
    rep.next_targets = {
        "budget": float(plan["next_budget"].sum()),
        "cpa": min(tg["cpa"], k["cpa"] * 0.95) if not pd.isna(k["cpa"]) else tg["cpa"],
        "roas": max(tg["roas"], k["roas"] * 1.05) if not pd.isna(k["roas"]) else tg["roas"],
        "ctr": max(tg["ctr"], k["ctr"]) if not pd.isna(k["ctr"]) else tg["ctr"],
    }
    rep.next_targets["conversions"] = rep.next_targets["budget"] / rep.next_targets["cpa"]


def seg_insight(seg: pd.DataFrame, area: str, label_col: str, k: dict, ins: list[Insight], min_share: float) -> None:
    if seg is None or seg.empty or not k["conversions"]:
        return
    s = seg[seg["spend_share"] >= min_share].copy()
    if s.empty:
        return
    s["eff"] = _efficiency_index(s, k["roas"], k["cpa"])
    best = s.sort_values("eff", ascending=False).head(2)
    worst = s.sort_values("eff").head(2)
    ins.append(Insight(area, "good",
                       "효율 상위: " + ", ".join(
                           f"{r[label_col]}(지출 {pct(r.spend_share, 0)} → 전환 {pct(r.conv_share, 0)}, ROAS {roas_fmt(r.roas)})"
                           for _, r in best.iterrows()) + "."))
    w = worst[worst["eff"] < 0.7]
    if len(w):
        ins.append(Insight(area, "warning",
                           "효율 하위: " + ", ".join(
                               f"{r[label_col]}(지출 {pct(r.spend_share, 0)} → 전환 {pct(r.conv_share, 0)}, ROAS {roas_fmt(r.roas)})"
                               for _, r in w.iterrows()) + "."))


# ---------------------------------------------------------------------------
def render_markdown(rep: MonthlyReport) -> str:
    k, kp = rep.kpi, rep.kpi_prev
    s, e = rep.period
    L = [f"# [{rep.client}] {s.year}년 {s.month}월 메타 광고 결과 보고서", ""]
    if rep.is_mock:
        L += ["> ⚠️ 모의(테스트) 데이터로 생성된 보고서입니다.", ""]
    L += [f"기간 {s} ~ {e} · 광고 계정 act_{rep.account_id}", "", "## 핵심 지표", "",
          "| 지표 | 이번 달 | 전월 | 변화 |", "|---|---:|---:|---:|"]
    for key, label, fmt in [("spend", "광고비", won), ("impressions", "노출", num), ("clicks", "링크 클릭", num),
                            ("ctr", "CTR", pct), ("cpc", "CPC", won), ("cpm", "CPM", won),
                            ("conversions", "전환", num), ("cpa", "CPA", won), ("revenue", "매출", won),
                            ("roas", "ROAS", roas_fmt)]:
        ch = delta(k[key], kp[key])
        L.append(f"| {label} | {fmt(k[key])} | {fmt(kp[key])} | {'-' if pd.isna(ch) else f'{ch * 100:+.0f}%'} |")
    L += ["", "## 분석", ""]
    L += [f"- **[{i.area}]** {i.text}" for i in rep.insights]
    L += ["", "## 다음 달 실행 계획", "", "| 우선순위 | 영역 | 실행 | 근거 |", "|---|---|---|---|"]
    L += [f"| {st['priority']} | {st['area']} | {st['action']} | {st['why']} |" for st in rep.next_steps]
    t = rep.next_targets
    L += ["", f"**다음 달 목표:** 예산 {won(t['budget'])} · CPA {won(t['cpa'])} 이하 · ROAS {roas_fmt(t['roas'])} 이상 · "
          f"전환 {num(t['conversions'])}건 · CTR {pct(t['ctr'])} 이상"]
    return "\n".join(L)


def export_excel(rep: MonthlyReport, path: Path) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        pd.DataFrame([{"지표": k, "이번 달": v, "전월": rep.kpi_prev.get(k)} for k, v in rep.kpi.items()]).to_excel(
            xw, sheet_name="요약", index=False)
        for name, df in [("일별", rep.daily), ("캠페인", rep.campaigns), ("광고세트", rep.adsets),
                         ("소재", rep.ads), ("형식", rep.formats), ("연령성별", rep.demo),
                         ("지면", rep.placement), ("다음달예산", rep.budget_plan)]:
            df.to_excel(xw, sheet_name=name, index=False)
        pd.DataFrame(rep.next_steps).to_excel(xw, sheet_name="실행계획", index=False)


def run_monthly(api, client_cfg, year: int, month: int, out_dir: Path, mock: bool = False) -> list[Path]:
    from .report_html import render_html

    rep = build_report(api, client_cfg, year, month, mock=mock)
    folder = out_dir / client_cfg.name / "monthly"
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{year}-{month:02d}"
    paths = [folder / f"{stem}.html", folder / f"{stem}.md", folder / f"{stem}.xlsx"]
    paths[0].write_text(render_html(rep), encoding="utf-8")
    paths[1].write_text(render_markdown(rep), encoding="utf-8")
    export_excel(rep, paths[2])
    return paths
