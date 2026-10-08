"""모의 광고 데이터.

API 토큰 없이 전체 파이프라인(일일 점검 → 알림 → 월간 보고서)을 시험하기 위한 클라이언트.
MetaGraphClient 와 같은 메서드/스키마를 제공한다. 숫자는 결정적(seed 고정)이며
소재 피로, 랜딩 오류(전환 0), 고CPA, 저CTR, 위너 소재 같은 시나리오가 일부러 들어 있다.
일정은 `anchor`(기본: 오늘) 기준 상대 날짜라 언제 실행해도 같은 시나리오가 재현된다.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd

from .client import AD_COLUMNS, INSIGHT_COLUMNS


@dataclass
class _Ad:
    ad_id: str
    ad_name: str
    adset_id: str
    adset_name: str
    campaign_id: str
    campaign_name: str
    start: int                 # anchor 기준 상대일 (음수 = 과거)
    budget: float              # 일 예산(원)
    ctr: float                 # 기본 링크 CTR
    cvr: float                 # 클릭 → 전환율
    aov: float                 # 객단가
    audience: float            # 도달 가능 모수
    cpm: float = 8000
    end: int | None = None     # 일시중지일
    fatigue_onset: int | None = None
    broken_from: int | None = None  # 이 날부터 전환 0 (랜딩 오류 시나리오)


C1, C2, C3 = ("120210000000000001", "봄날_전환_신규고객"), ("120210000000000002", "봄날_전환_리타겟팅"), ("120210000000000003", "봄날_트래픽_브랜딩")
S1 = ("120211000000000011", "관심사_25-44여성")
S2 = ("120211000000000012", "유사타겟_구매자1%")
S3 = ("120211000000000021", "장바구니_7일")
S4 = ("120211000000000022", "방문자_30일")
S5 = ("120211000000000031", "광범위_전국")


def _ad(i, name, s, c, **kw) -> _Ad:
    return _Ad(f"1202120000000000{i:02d}", name, s[0], s[1], c[0], c[1], **kw)


ADS = [
    _ad(1, "영상_후기모음_v1", S1, C1, start=-100, budget=30000, ctr=0.014, cvr=0.05, aov=52000, audience=250000, fatigue_onset=-11),
    _ad(2, "이미지_베스트3종", S1, C1, start=-85, budget=18000, ctr=0.0055, cvr=0.04, aov=50000, audience=250000),
    _ad(3, "릴스_언박싱", S1, C1, start=-28, budget=35000, ctr=0.019, cvr=0.065, aov=56000, audience=250000),
    _ad(4, "영상_사용법", S2, C1, start=-100, budget=25000, ctr=0.011, cvr=0.045, aov=51000, audience=300000),
    _ad(5, "캐러셀_신상품", S2, C1, start=-48, budget=22000, ctr=0.012, cvr=0.045, aov=53000, audience=300000, broken_from=-4),
    _ad(6, "할인쿠폰_이미지", S3, C2, start=-100, budget=15000, ctr=0.02, cvr=0.08, aov=48000, audience=2000, cpm=14000, fatigue_onset=-13),
    _ad(7, "후기_카드뉴스", S3, C2, start=-37, budget=12000, ctr=0.018, cvr=0.075, aov=48000, audience=2000, cpm=14000),
    _ad(8, "브랜드스토리_영상", S4, C2, start=-68, budget=20000, ctr=0.009, cvr=0.015, aov=49000, audience=40000, cpm=12000),
    _ad(9, "시즌_룩북_영상", S5, C3, start=-68, budget=25000, ctr=0.022, cvr=0.008, aov=50000, audience=500000, cpm=6000),
    _ad(10, "이벤트_배너", S5, C3, start=-100, end=-23, budget=15000, ctr=0.01, cvr=0.03, aov=50000, audience=500000, cpm=6000),
]

# 세그먼트: (값들, 지출 비중, CTR 배수, CVR 배수)
AGE_GENDER = [
    (("18-24", "female"), 0.08, 1.10, 0.60), (("18-24", "male"), 0.03, 0.80, 0.40),
    (("25-34", "female"), 0.27, 1.15, 1.25), (("25-34", "male"), 0.06, 0.85, 0.70),
    (("35-44", "female"), 0.26, 1.05, 1.30), (("35-44", "male"), 0.05, 0.80, 0.80),
    (("45-54", "female"), 0.14, 0.95, 0.95), (("45-54", "male"), 0.03, 0.70, 0.60),
    (("55-64", "female"), 0.05, 0.85, 0.60), (("55-64", "male"), 0.01, 0.60, 0.50),
    (("65+", "female"), 0.015, 0.70, 0.40), (("65+", "male"), 0.005, 0.50, 0.30),
    (("25-34", "unknown"), 0.01, 0.90, 0.80), (("35-44", "unknown"), 0.01, 0.90, 0.80),
]
PLACEMENT = [
    (("instagram", "feed"), 0.30, 1.05, 1.15), (("instagram", "instagram_reels"), 0.22, 1.30, 1.05),
    (("instagram", "instagram_stories"), 0.16, 0.90, 0.95), (("facebook", "feed"), 0.18, 0.95, 1.00),
    (("facebook", "facebook_reels"), 0.06, 1.00, 0.70), (("audience_network", "classic"), 0.05, 1.40, 0.20),
    (("messenger", "messenger_inbox"), 0.03, 0.50, 0.50),
]
BREAKDOWN_SEGMENTS = {
    ("age", "gender"): AGE_GENDER,
    ("publisher_platform", "platform_position"): PLACEMENT,
}


class MockClient:
    def __init__(self, client_cfg=None, anchor: date | None = None, seed: int = 7):
        self.anchor = anchor or date.today()
        self.seed = seed
        self.name = getattr(client_cfg, "name", "봄날")
        self.account_id = getattr(client_cfg, "account_id", "1828231244405674")

    def _rel(self, d: date) -> int:
        return (d - self.anchor).days

    def account_info(self) -> dict:
        return {"id": f"act_{self.account_id}", "name": self.name, "currency": "KRW",
                "timezone_name": "Asia/Seoul", "account_status": 1}

    def ads(self) -> pd.DataFrame:
        rows = []
        for a in ADS:
            rows.append({
                "ad_id": a.ad_id, "ad_name": a.ad_name, "adset_id": a.adset_id,
                "adset_name": a.adset_name, "campaign_id": a.campaign_id,
                "campaign_name": a.campaign_name,
                "status": "PAUSED" if a.end is not None and a.end <= 0 else "ACTIVE",
                "created_time": (self.anchor + timedelta(days=a.start)).isoformat(),
            })
        return pd.DataFrame(rows, columns=AD_COLUMNS)

    # ---- 하루치 시뮬레이션 ---------------------------------------------
    def _day(self, a: _Ad, idx: int, d: date) -> dict | None:
        t = self._rel(d)
        if t < a.start or (a.end is not None and t >= a.end) or t >= 0:
            return None
        rng = np.random.default_rng(self.seed * 1_000_003 + idx * 10_007 + (d.toordinal()))
        fatigue = 1.0
        if a.fatigue_onset is not None and t >= a.fatigue_onset:
            fatigue = max(0.3, math.exp(-(t - a.fatigue_onset) / 8))
        # 오래된 소재는 서서히 효율이 떨어진다
        fatigue *= max(0.75, 1 - 0.0015 * (t - a.start))
        weekend = 1.08 if d.weekday() >= 5 else 1.0
        spend = a.budget * rng.uniform(0.85, 1.1) * weekend
        cpm = a.cpm * rng.uniform(0.9, 1.12) * (1 + 0.6 * (1 - fatigue))
        imps = int(spend / cpm * 1000)
        ctr = a.ctr * fatigue * rng.uniform(0.88, 1.12)
        clicks = int(rng.binomial(imps, min(ctr, 0.9)))
        cvr = 0.0 if a.broken_from is not None and t >= a.broken_from else a.cvr * rng.uniform(0.8, 1.2)
        conv = int(rng.poisson(clicks * cvr))
        revenue = round(sum(a.aov * rng.uniform(0.7, 1.4) for _ in range(conv)), 0)
        reach = int(min(imps / 1.08, a.audience * (1 - math.exp(-imps / a.audience))))
        return {
            "date": d.isoformat(), "campaign_id": a.campaign_id, "campaign_name": a.campaign_name,
            "adset_id": a.adset_id, "adset_name": a.adset_name, "ad_id": a.ad_id,
            "ad_name": a.ad_name, "impressions": imps, "reach": reach, "clicks": clicks,
            "spend": round(spend, 0), "conversions": conv, "revenue": revenue,
        }

    def insights(self, since: date, until: date, daily: bool = True,
                 breakdowns: list[str] | None = None) -> pd.DataFrame:
        days = [since + timedelta(days=i) for i in range((until - since).days + 1)]
        rows = [r for i, a in enumerate(ADS) for d in days if (r := self._day(a, i, d))]
        df = pd.DataFrame(rows, columns=INSIGHT_COLUMNS)
        if daily and not breakdowns:
            return df
        if df.empty:
            return pd.DataFrame(columns=INSIGHT_COLUMNS + list(breakdowns or []))

        # 기간 합계 (reach 는 모수 기반으로 재계산 → 빈도 = 노출/순도달)
        ids = ["campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name"]
        agg = df.groupby(ids, as_index=False)[["impressions", "clicks", "spend", "conversions", "revenue"]].sum()
        audience = {a.ad_id: a.audience for a in ADS}
        agg["reach"] = [
            int(min(i / 1.15, audience[ad] * (1 - math.exp(-i / audience[ad]))))
            for i, ad in zip(agg["impressions"], agg["ad_id"])
        ]
        agg["date"] = since.isoformat()
        if not breakdowns:
            return agg[INSIGHT_COLUMNS]

        segments = BREAKDOWN_SEGMENTS.get(tuple(breakdowns))
        if segments is None:
            raise ValueError(f"모의 데이터가 지원하지 않는 breakdown: {breakdowns}")
        share = np.array([s[1] for s in segments])
        share = share / share.sum()
        click_w = share * np.array([s[2] for s in segments])
        conv_w = click_w * np.array([s[3] for s in segments])
        click_w, conv_w = click_w / click_w.sum(), conv_w / conv_w.sum()
        out = []
        for _, r in agg.iterrows():
            for k, seg in enumerate(segments):
                row = {c: r[c] for c in ids}
                row.update({
                    "date": r["date"],
                    "impressions": round(r["impressions"] * share[k]),
                    "reach": round(r["reach"] * share[k]),
                    "clicks": round(r["clicks"] * click_w[k]),
                    "spend": round(r["spend"] * share[k]),
                    "conversions": round(r["conversions"] * conv_w[k], 2),
                    "revenue": round(r["revenue"] * conv_w[k]),
                })
                row.update(dict(zip(breakdowns, seg[0])))
                out.append(row)
        return pd.DataFrame(out, columns=INSIGHT_COLUMNS + list(breakdowns))
