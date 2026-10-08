"""파생 지표 계산과 포맷 유틸."""
from __future__ import annotations

import pandas as pd

SUM_COLS = ["impressions", "reach", "clicks", "spend", "conversions", "revenue"]


def _div(a, b):
    if isinstance(a, pd.Series) or isinstance(b, pd.Series):
        return (a / b.where(b != 0)).astype(float)
    return a / b if b else float("nan")


def add_kpis(df: pd.DataFrame) -> pd.DataFrame:
    """합계 컬럼에서 CTR/CPC/CPM/CPA/ROAS/CVR/빈도 를 계산해 붙인다."""
    df = df.copy()
    df["ctr"] = _div(df["clicks"], df["impressions"])
    df["cpc"] = _div(df["spend"], df["clicks"])
    df["cpm"] = _div(df["spend"], df["impressions"]) * 1000
    df["cpa"] = _div(df["spend"], df["conversions"])
    df["roas"] = _div(df["revenue"], df["spend"])
    df["cvr"] = _div(df["conversions"], df["clicks"])
    df["frequency"] = _div(df["impressions"], df["reach"])
    return df


def totals(df: pd.DataFrame) -> dict:
    """전체 합계 + KPI. reach 는 행 합계라 중복이 있을 수 있어 빈도는 참고용."""
    t = {c: float(df[c].sum()) if c in df else 0.0 for c in SUM_COLS}
    t["ctr"] = _div(t["clicks"], t["impressions"])
    t["cpc"] = _div(t["spend"], t["clicks"])
    t["cpm"] = _div(t["spend"], t["impressions"]) * 1000
    t["cpa"] = _div(t["spend"], t["conversions"])
    t["roas"] = _div(t["revenue"], t["spend"])
    t["cvr"] = _div(t["conversions"], t["clicks"])
    return t


def won(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v:,.0f}원"


def pct(v, digits: int = 2) -> str:
    return "-" if v is None or pd.isna(v) else f"{v * 100:.{digits}f}%"


def num(v, digits: int = 0) -> str:
    return "-" if v is None or pd.isna(v) else f"{v:,.{digits}f}"


def roas_fmt(v) -> str:
    return "-" if v is None or pd.isna(v) else f"{v * 100:,.0f}%"


def delta(cur, prev) -> float:
    if prev is None or cur is None or pd.isna(prev) or pd.isna(cur) or prev == 0:
        return float("nan")
    return cur / prev - 1
