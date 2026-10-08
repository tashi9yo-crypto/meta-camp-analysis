from datetime import date

import pandas as pd
import pytest

from meta_ads.client import MetaGraphClient
from meta_ads.config import load_config
from meta_ads.daily import render_markdown, run_daily
from meta_ads.mock import MockClient
from meta_ads.monthly import build_report, render_markdown as monthly_md
from meta_ads.rules import ctr_drop_significant

AS_OF = date(2026, 10, 8)


@pytest.fixture
def cc():
    return load_config("config.yaml").client(None)


@pytest.fixture
def api(cc):
    return MockClient(cc, anchor=AS_OF)


def codes(res):
    return {(a.ad, a.code) for a in res["alerts"]}


def test_daily_flags_planted_scenarios(api, cc):
    res = run_daily(api, cc, AS_OF)
    got = codes(res)
    assert ("영상_후기모음_v1", "CREATIVE_FATIGUE") in got      # 급격한 피로
    assert ("할인쿠폰_이미지", "CREATIVE_FATIGUE") in got        # 서서히 진행된 피로 (4주 최고치 대비)
    assert ("캐러셀_신상품", "NO_CONVERSION") in got             # 랜딩/추적 오류
    assert ("릴스_언박싱", "WINNER") in got
    assert ("이미지_베스트3종", "LOW_CTR") in got
    # 인지 캠페인 소재에는 CPA/ROAS 경고를 내지 않는다
    assert not any(a.ad == "시즌_룩북_영상" and a.code in ("HIGH_CPA", "LOW_ROAS") for a in res["alerts"])
    # 일시중지된 소재는 점검하지 않는다
    assert not any(a.ad == "이벤트_배너" for a in res["alerts"])
    md = render_markdown(res, cc)
    assert "오늘 바로 조치할 소재" in md


def test_ctr_drop_significance():
    base = {"impressions": 10000, "clicks": 150}
    assert ctr_drop_significant({"impressions": 10000, "clicks": 80}, base)
    assert not ctr_drop_significant({"impressions": 300, "clicks": 3}, base)  # 표본이 작으면 판단 보류


def test_monthly_report(api, cc, tmp_path):
    rep = build_report(api, cc, 2026, 9, mock=True)
    assert rep.kpi["spend"] > 0 and rep.kpi_prev["spend"] > 0
    assert abs(rep.budget_plan["next_budget"].sum() - cc.monthly_budget) < 50000
    assert any("릴스_언박싱" in st["action"] for st in rep.next_steps)
    assert rep.next_steps[0]["priority"] == "높음"
    assert "다음 달 목표" in monthly_md(rep)
    from meta_ads.monthly import run_monthly

    paths = run_monthly(api, cc, 2026, 9, tmp_path, mock=True)
    assert all(p.exists() and p.stat().st_size > 0 for p in paths)


def test_graph_normalize_actions():
    c = MetaGraphClient("t", "123", ["offsite_conversion.fb_pixel_purchase"])
    row = c._normalize({
        "date_start": "2026-09-01", "ad_id": "1", "impressions": "1000", "reach": "800",
        "inline_link_clicks": "12", "spend": "9500.5",
        "actions": [{"action_type": "offsite_conversion.fb_pixel_purchase", "value": "2"},
                    {"action_type": "link_click", "value": "12"}],
        "action_values": [{"action_type": "offsite_conversion.fb_pixel_purchase", "value": "90000"}],
    }, [])
    assert row["conversions"] == 2 and row["revenue"] == 90000 and row["clicks"] == 12
    assert c.act_id == "act_123"


def test_graph_pagination_and_retry(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, status, body):
            self.status_code, self._b, self.ok = status, body, status < 400
            self.text = str(body)

        def json(self):
            return self._b

    pages = [
        Resp(400, {"error": {"code": 17, "message": "User request limit reached"}}),
        Resp(200, {"data": [{"id": "a"}], "paging": {"next": "https://graph.facebook.com/next"}}),
        Resp(200, {"data": [{"id": "b"}]}),
    ]

    class Sess:
        def get(self, url, params=None, timeout=None):
            calls.append(url)
            return pages.pop(0)

    monkeypatch.setattr("time.sleep", lambda s: None)
    c = MetaGraphClient("t", "123", [], session=Sess())
    assert [r["id"] for r in c._paginate("act_123/ads", {})] == ["a", "b"]
    assert len(calls) == 3
