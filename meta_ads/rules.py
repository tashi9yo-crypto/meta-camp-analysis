"""일일 점검 규칙.

광고(소재) 단위로 '최근 N일'과 '그 직전 M일(기준)'을 비교해 이상 징후를 찾고,
각 경고마다 바로 실행할 수 있는 개선 제안을 붙인다.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

import pandas as pd

from .metrics import delta, num, pct, roas_fmt, totals, won

LEVELS = {"critical": 3, "warning": 2, "info": 1, "good": 0}
LEVEL_LABEL = {"critical": "🔴 위험", "warning": "🟠 주의", "info": "🔵 참고", "good": "🟢 우수"}


@dataclass
class Alert:
    level: str
    code: str
    title: str
    detail: str
    actions: list[str]
    campaign: str = ""
    adset: str = ""
    ad: str = ""
    ad_id: str = ""
    metrics: dict = field(default_factory=dict)

    @property
    def target(self) -> str:
        return " › ".join(x for x in (self.campaign, self.adset, self.ad) if x)

    def to_dict(self) -> dict:
        return asdict(self) | {"target": self.target}


def windows(as_of: date, rules: dict) -> dict[str, tuple[date, date]]:
    """as_of(오늘) 기준 점검 구간. 오늘 데이터는 미완성이라 어제까지만 본다."""
    r_end = as_of - timedelta(days=1)
    r_start = r_end - timedelta(days=rules["recent_days"] - 1)
    b_end = r_start - timedelta(days=1)
    b_start = b_end - timedelta(days=rules["baseline_days"] - 1)
    return {
        "recent": (r_start, r_end),
        "baseline": (b_start, b_end),
        "freq": (r_end - timedelta(days=6), r_end),
        "lookback": (r_end - timedelta(days=27), r_end),
    }


def _slice(df: pd.DataFrame, rng: tuple[date, date]) -> pd.DataFrame:
    d = pd.to_datetime(df["date"]).dt.date
    return df[(d >= rng[0]) & (d <= rng[1])]


def ctr_drop_significant(rec: dict, base: dict, z_crit: float = 2.58) -> bool:
    """두 비율 z-검정: 최근 CTR 이 기준 CTR 보다 통계적으로 유의하게 낮은가.

    매일 여러 소재를 동시에 검사하므로 오탐을 줄이려고 기본 99% 수준(z=2.58)을 쓴다."""
    n1, n2 = rec["impressions"], base["impressions"]
    if not n1 or not n2:
        return False
    p1, p2 = rec["clicks"] / n1, base["clicks"] / n2
    p = (rec["clicks"] + base["clicks"]) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return se > 0 and (p1 - p2) / se <= -z_crit


def peak_ctr_window(ad_daily: pd.DataFrame, min_impressions: float) -> dict | None:
    """최근 4주 중 CTR 이 가장 높았던 연속 7일 구간 (장기 피로 판단의 기준점)."""
    if ad_daily.empty:
        return None
    g = ad_daily.groupby("date")[["impressions", "clicks"]].sum().sort_index()
    roll = g.rolling(7).sum().dropna()
    roll = roll[roll["impressions"] >= min_impressions]
    if roll.empty:
        return None
    ctr = roll["clicks"] / roll["impressions"]
    best = ctr.idxmax()
    return {"impressions": roll.loc[best, "impressions"], "clicks": roll.loc[best, "clicks"],
            "ctr": ctr[best], "end": best}


def evaluate(
    daily: pd.DataFrame,
    freq_window: pd.DataFrame,
    ads: pd.DataFrame,
    client_cfg,
    as_of: date,
) -> list[Alert]:
    rules, tg = client_cfg.rules, client_cfg.targets
    w = windows(as_of, rules)
    recent_all, base_all = _slice(daily, w["recent"]), _slice(daily, w["baseline"])
    lookback = _slice(daily, w["lookback"])
    active = ads[ads["status"] == "ACTIVE"]
    alerts: list[Alert] = []

    awareness = [k.lower() for k in rules.get("awareness_keywords", [])]
    for ad in active.itertuples():
        rec = totals(recent_all[recent_all["ad_id"] == ad.ad_id])
        base = totals(base_all[base_all["ad_id"] == ad.ad_id])
        if rec["impressions"] < rules["min_impressions"]:
            continue
        fw = freq_window[freq_window["ad_id"] == ad.ad_id]
        wk = totals(fw)  # 최근 7일 합계 (CTR 수준 판단용 — 3일보다 안정적)
        freq = wk["impressions"] / wk["reach"] if wk["reach"] else float("nan")
        ctx = dict(campaign=ad.campaign_name, adset=ad.adset_name, ad=ad.ad_name, ad_id=ad.ad_id)
        m = {
            "spend": rec["spend"], "clicks": rec["clicks"], "ctr": rec["ctr"], "ctr_base": base["ctr"],
            "cpa": rec["cpa"], "roas": rec["roas"], "cpm": rec["cpm"],
            "cpm_base": base["cpm"], "frequency_7d": freq, "conversions": rec["conversions"],
        }
        has_base = base["impressions"] >= rules["min_impressions"]
        ctr_chg = delta(rec["ctr"], base["ctr"]) if has_base else float("nan")
        cpm_chg = delta(rec["cpm"], base["cpm"]) if has_base else float("nan")
        # 노출이 적을 때 우연한 등락을 걸러내기 위해 CTR 하락은 유의할 때만 인정
        z = rules.get("ctr_drop_z", 2.58)
        ctr_down = has_base and ctr_chg <= -rules["ctr_drop"] and ctr_drop_significant(rec, base, z)
        # 서서히 진행된 피로: 기준 구간도 이미 떨어져 있으면 위 비교로는 안 잡히므로 4주 최고치와 비교
        ad_hist = lookback[lookback["ad_id"] == ad.ad_id]
        peak = peak_ctr_window(ad_hist, rules["min_impressions"] * 3)
        peak_chg = delta(rec["ctr"], peak["ctr"]) if peak else float("nan")
        decayed = bool(peak) and peak_chg <= -rules.get("peak_ctr_drop", 0.4) and ctr_drop_significant(rec, peak, z)
        is_awareness = any(k in str(ad.campaign_name).lower() for k in awareness)
        n = rules["recent_days"]

        # 1) 소재 피로: CTR 유의미한 하락 (+빈도↑ 이면 확실) → 교체
        fatigued = decayed or (ctr_down and (freq >= rules["fatigue_frequency"] or ctr_chg <= -rules["ctr_drop"] * 1.6))
        if fatigued:
            peak_txt = f", 4주 최고 {pct(peak['ctr'])} 대비 {pct(peak_chg, 0)}" if decayed else ""
            alerts.append(Alert(
                "critical", "CREATIVE_FATIGUE", "소재 피로 — 교체 필요",
                f"최근 {n}일 CTR {pct(rec['ctr'])} (직전 {rules['baseline_days']}일 {pct(base['ctr'])}{peak_txt}), "
                f"7일 빈도 {num(freq, 1)}회, CPM {'+' if cpm_chg > 0 else ''}{pct(cpm_chg, 0)}",
                [
                    "새 소재 2~3개를 같은 광고세트에 추가하고, 신규 소재가 학습되면 기존 소재 OFF",
                    "첫 3초 훅(문구·장면)을 바꾼 변형 소재로 테스트 — 메시지는 유지, 표현만 교체",
                    "빈도가 높은 경우 타겟 확장(유사타겟 % 확대, Advantage+ 오디언스) 검토"
                    if freq >= rules["fatigue_frequency"] else
                    "빈도는 낮으므로 타겟 문제보다 소재 자체의 신선도 문제 — 새 콘셉트 필요",
                ],
                metrics=m, **ctx,
            ))
        elif ctr_down:
            alerts.append(Alert(
                "warning", "CTR_DROP", "CTR 하락 — 피로 초기 신호",
                f"최근 {n}일 CTR {pct(rec['ctr'])} (기준 {pct(base['ctr'])}, {pct(ctr_chg, 0)}), 7일 빈도 {num(freq, 1)}회",
                ["교체용 소재를 미리 준비 (2~3일 더 하락하면 교체)", "썸네일/첫 문장만 바꾼 A/B 변형 추가"],
                metrics=m, **ctx,
            ))
        elif freq >= rules["high_frequency"]:
            alerts.append(Alert(
                "warning", "HIGH_FREQUENCY", "노출 빈도 과다",
                f"7일 빈도 {num(freq, 1)}회 — 같은 사람에게 과다 노출 중",
                ["타겟 모수 확대 또는 리타겟팅 기간 확장(7일→14/30일)", "소재 로테이션용 신규 소재 추가"],
                metrics=m, **ctx,
            ))

        # 2) 전환 성과 (인지/트래픽 캠페인 제외)
        if not is_awareness:
            base_cvr = base["cvr"] if has_base and base["conversions"] >= 3 else float("nan")
            expected = rec["clicks"] * base_cvr  # 평소 전환율이면 기대되는 전환 수
            if rec["conversions"] == 0 and expected >= 3:
                alerts.append(Alert(
                    "critical", "NO_CONVERSION", "클릭은 정상인데 전환 0건 — 추적/랜딩 점검",
                    f"최근 {n}일 링크클릭 {num(rec['clicks'])}회 · {won(rec['spend'])} 지출, 전환 0건 "
                    f"(평소 전환율 {pct(base_cvr)} 기준 약 {num(expected, 1)}건 기대)",
                    [
                        "랜딩페이지·결제 오류, 픽셀/전환 API 이벤트 수신 여부를 이벤트 관리자에서 즉시 확인",
                        "품절·가격 변경·쿠폰 만료 등 상품/프로모션 상태 확인",
                        "원인 확인 전까지 해당 광고 예산 축소 또는 일시중지",
                    ],
                    metrics=m, **ctx,
                ))
            elif rec["conversions"] == 0 and rec["spend"] >= tg["cpa"] * rules["no_conversion_cpa_multiple"] and not fatigued:
                alerts.append(Alert(
                    "warning", "NO_CONVERSION_LOW_CLICKS", "지출 대비 전환 0건",
                    f"최근 {n}일 {won(rec['spend'])} 지출(목표 CPA의 {rec['spend'] / tg['cpa']:.1f}배), 링크클릭 {num(rec['clicks'])}회, 전환 0건",
                    ["클릭 자체가 부족 — 소재 후킹 개선 또는 예산을 효율 소재로 이동", "1~2일 더 지켜본 뒤 개선 없으면 OFF"],
                    metrics=m, **ctx,
                ))
            elif rec["conversions"] > 0 and rec["spend"] >= tg["cpa"] * 2 and rec["cpa"] > tg["cpa"] * rules["cpa_warning_ratio"]:
                critical = rec["cpa"] > tg["cpa"] * rules["cpa_critical_ratio"]
                alerts.append(Alert(
                    "critical" if critical else "warning", "HIGH_CPA",
                    "CPA 목표 크게 초과" if critical else "CPA 목표 초과",
                    f"최근 {n}일 CPA {won(rec['cpa'])} (목표 {won(tg['cpa'])}), ROAS {roas_fmt(rec['roas'])}, "
                    f"전환 {num(rec['conversions'])}건, CVR {pct(rec['cvr'])}",
                    [
                        "같은 광고세트 내 효율 좋은 소재로 예산이 몰리도록 저효율 소재 OFF",
                        "CTR은 정상인데 CVR이 낮다면 랜딩페이지(첫 화면 혜택·후기·CTA) 개선"
                        if rec["ctr"] >= tg["ctr"] else "CTR부터 낮음 — 소재 후킹 개선이 우선",
                        "타겟·입찰 전략(비용 한도) 검토",
                    ],
                    metrics=m, **ctx,
                ))
            elif rec["revenue"] > 0 and rec["spend"] >= tg["cpa"] * 2 and rec["roas"] < tg["roas"] * rules["roas_warning_ratio"]:
                alerts.append(Alert(
                    "warning", "LOW_ROAS", "CPA는 괜찮지만 ROAS 미달 — 객단가 낮음",
                    f"최근 {n}일 ROAS {roas_fmt(rec['roas'])} (목표 {roas_fmt(tg['roas'])}), CPA {won(rec['cpa'])}",
                    ["객단가를 올리는 세트/번들·무료배송 기준 금액 소재 테스트", "고가 상품 중심 카탈로그/소재로 전환"],
                    metrics=m, **ctx,
                ))

        # 3) 클릭률 자체가 낮음 (피로가 아닌 소재 매력 문제) — 7일 기준
        if (wk["impressions"] >= rules["min_impressions"] * 3 and wk["ctr"] < tg["ctr"]
                and not fatigued and not ctr_down):
            alerts.append(Alert(
                "warning", "LOW_CTR", "CTR 기준 미달 — 소재 매력도 낮음",
                f"최근 7일 링크 CTR {pct(wk['ctr'])} (기준 {pct(tg['ctr'])}), CPC {won(wk['cpc'])}",
                [
                    "정지 이미지라면 숏폼 영상/릴스 형식으로 재제작",
                    "첫 문장에 고객 문제·혜택(가격, 할인, 후기 수)을 숫자로 제시",
                    "CTA 버튼과 문구 일치 여부 확인",
                ],
                metrics=m, **ctx,
            ))

        # 4) CPM 급등 (경쟁 심화/소재 품질 저하)
        if cpm_chg >= rules["cpm_spike"]:
            alerts.append(Alert(
                "info", "CPM_SPIKE", "CPM 급등",
                f"CPM {won(rec['cpm'])} (기준 {won(base['cpm'])}, +{pct(cpm_chg, 0)})",
                ["시즌 경쟁 영향인지 계정 전체 CPM 추이와 비교", "광고 관련성 진단(품질 순위) 확인"],
                metrics=m, **ctx,
            ))

        # 5) 잘 되는 소재 → 확장
        if not is_awareness and rec["roas"] >= tg["roas"] * rules["winner_roas_ratio"] and rec["conversions"] >= 3:
            alerts.append(Alert(
                "good", "WINNER", "고효율 소재 — 확장 후보",
                f"최근 {n}일 ROAS {roas_fmt(rec['roas'])}, CPA {won(rec['cpa'])}, 전환 {num(rec['conversions'])}건",
                [
                    "광고세트 예산 20% 이내로 증액 (학습 재시작 방지)",
                    "같은 콘셉트로 변형 소재 2~3개 제작해 다른 광고세트/유사타겟에 확장",
                ],
                metrics=m, **ctx,
            ))

    # ---- 광고세트 / 계정 단위 ------------------------------------------
    for (cname, sname), g in active.groupby(["campaign_name", "adset_name"]):
        if len(g) < rules["min_ads_per_adset"]:
            alerts.append(Alert(
                "info", "FEW_CREATIVES", "광고세트 소재 수 부족",
                f"활성 소재 {len(g)}개 — 소재가 1개뿐이면 피로가 와도 대체가 없습니다",
                [f"광고세트당 3~5개 소재 운영 권장 (현재 {len(g)}개)"],
                campaign=cname, adset=sname,
            ))

    if client_cfg.monthly_budget:
        month_start = (as_of - timedelta(days=1)).replace(day=1)
        mtd = _slice(daily, (month_start, as_of - timedelta(days=1)))
        if mtd["date"].nunique() >= 1 and month_start >= pd.to_datetime(daily["date"]).min().date():
            import calendar

            days_in_month = calendar.monthrange(month_start.year, month_start.month)[1]
            elapsed = (as_of - month_start).days
            spent = mtd["spend"].sum()
            expected = client_cfg.monthly_budget * elapsed / days_in_month
            pace = spent / expected if expected else float("nan")
            if pace > 1.15 or pace < 0.85:
                alerts.append(Alert(
                    "warning", "BUDGET_PACING", "월 예산 소진 속도 이탈",
                    f"이번 달 {won(spent)} 소진 (계획 대비 {pct(pace, 0)}, 월 예산 {won(client_cfg.monthly_budget)})",
                    ["일 예산을 남은 일수 기준으로 재조정: 남은 예산 "
                     + won(client_cfg.monthly_budget - spent) + " ÷ " + str(days_in_month - elapsed) + "일"],
                ))

    alerts.sort(key=lambda a: (-LEVELS[a.level], a.campaign, a.adset, a.ad))
    return alerts
