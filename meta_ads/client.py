"""Meta Marketing API(Graph API) 클라이언트.

모든 메서드는 같은 스키마의 pandas DataFrame 을 돌려준다. 그래서 일일 점검/월간 보고서
로직은 실제 API(`MetaGraphClient`)와 모의 데이터(`MockClient`, mock.py)를 구분하지 않는다.

insights 컬럼:
    date, campaign_id, campaign_name, adset_id, adset_name, ad_id, ad_name,
    impressions, reach, clicks, spend, conversions, revenue  (+ breakdown 컬럼)
    - clicks 는 링크 클릭(inline_link_clicks)
    - conversions / revenue 는 설정한 conversion_actions 의 actions / action_values 합계
    - daily=False 이면 date 는 기간 시작일, reach 는 기간 순도달(중복 제거)
ads 컬럼:
    ad_id, ad_name, adset_id, adset_name, campaign_id, campaign_name, status, created_time
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import Iterable

import pandas as pd
import requests

log = logging.getLogger(__name__)

INSIGHT_COLUMNS = [
    "date", "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name",
    "impressions", "reach", "clicks", "spend", "conversions", "revenue",
]
AD_COLUMNS = [
    "ad_id", "ad_name", "adset_id", "adset_name", "campaign_id", "campaign_name",
    "status", "created_time",
]
# 다른 요청 한도/일시 오류 코드: https://developers.facebook.com/docs/graph-api/overview/rate-limiting
RETRYABLE_ERROR_CODES = {1, 2, 4, 17, 32, 341, 613, 80000, 80004}


class MetaApiError(RuntimeError):
    pass


class MetaGraphClient:
    def __init__(
        self,
        access_token: str,
        account_id: str,
        conversion_actions: Iterable[str],
        api_version: str = "v21.0",
        session: requests.Session | None = None,
        max_retries: int = 5,
    ):
        if not access_token:
            raise MetaApiError("META_ACCESS_TOKEN 환경변수가 비어 있습니다.")
        self.token = access_token
        self.act_id = account_id if account_id.startswith("act_") else f"act_{account_id}"
        self.conversion_actions = set(conversion_actions)
        self.base = f"https://graph.facebook.com/{api_version}"
        self.http = session or requests.Session()
        self.max_retries = max_retries

    # ---- 저수준 ----------------------------------------------------------
    def _get(self, url: str, params: dict | None = None) -> dict:
        params = {**(params or {}), "access_token": self.token}
        for attempt in range(self.max_retries + 1):
            resp = self.http.get(url, params=params, timeout=60)
            try:
                body = resp.json()
            except ValueError:
                body = {}
            if resp.ok and "error" not in body:
                return body
            err = body.get("error", {})
            code = err.get("code")
            if attempt < self.max_retries and (resp.status_code >= 500 or code in RETRYABLE_ERROR_CODES):
                wait = min(2 ** attempt * 5, 120)
                log.warning("Graph API 재시도 %s/%s (code=%s) %ss 대기", attempt + 1, self.max_retries, code, wait)
                time.sleep(wait)
                continue
            raise MetaApiError(f"Graph API 오류 {resp.status_code}: {err.get('message') or resp.text[:300]}")
        raise MetaApiError("Graph API 재시도 한도 초과")

    def _paginate(self, path: str, params: dict) -> list[dict]:
        out: list[dict] = []
        body = self._get(f"{self.base}/{path}", params)
        while True:
            out.extend(body.get("data", []))
            nxt = body.get("paging", {}).get("next")
            if not nxt:
                return out
            body = self._get(nxt)  # next URL 에 파라미터가 모두 들어 있음

    # ---- 공개 API -------------------------------------------------------
    def account_info(self) -> dict:
        return self._get(f"{self.base}/{self.act_id}", {"fields": "name,currency,timezone_name,account_status"})

    def ads(self) -> pd.DataFrame:
        rows = self._paginate(
            f"{self.act_id}/ads",
            {
                "fields": "id,name,effective_status,created_time,adset{id,name},campaign{id,name}",
                "limit": 500,
            },
        )
        data = [
            {
                "ad_id": r["id"],
                "ad_name": r.get("name", ""),
                "adset_id": r.get("adset", {}).get("id"),
                "adset_name": r.get("adset", {}).get("name"),
                "campaign_id": r.get("campaign", {}).get("id"),
                "campaign_name": r.get("campaign", {}).get("name"),
                "status": r.get("effective_status"),
                "created_time": (r.get("created_time") or "")[:10],
            }
            for r in rows
        ]
        return pd.DataFrame(data, columns=AD_COLUMNS)

    def insights(
        self,
        since: date,
        until: date,
        daily: bool = True,
        breakdowns: list[str] | None = None,
    ) -> pd.DataFrame:
        params = {
            "level": "ad",
            "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
            "time_increment": 1 if daily else "all_days",
            "fields": ",".join([
                "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name",
                "impressions", "reach", "inline_link_clicks", "spend", "actions", "action_values",
            ]),
            "action_attribution_windows": json.dumps(["7d_click", "1d_view"]),
            "limit": 500,
        }
        if breakdowns:
            params["breakdowns"] = ",".join(breakdowns)
        rows = self._paginate(f"{self.act_id}/insights", params)
        return pd.DataFrame(
            [self._normalize(r, breakdowns or []) for r in rows],
            columns=INSIGHT_COLUMNS + list(breakdowns or []),
        )

    def _normalize(self, r: dict, breakdowns: list[str]) -> dict:
        def action_sum(key: str) -> float:
            return sum(
                float(a.get("value", 0))
                for a in r.get(key) or []
                if a.get("action_type") in self.conversion_actions
            )

        row = {
            "date": r.get("date_start"),
            "campaign_id": r.get("campaign_id"),
            "campaign_name": r.get("campaign_name"),
            "adset_id": r.get("adset_id"),
            "adset_name": r.get("adset_name"),
            "ad_id": r.get("ad_id"),
            "ad_name": r.get("ad_name"),
            "impressions": int(r.get("impressions") or 0),
            "reach": int(r.get("reach") or 0),
            "clicks": int(r.get("inline_link_clicks") or 0),
            "spend": float(r.get("spend") or 0),
            "conversions": action_sum("actions"),
            "revenue": action_sum("action_values"),
        }
        for b in breakdowns:
            row[b] = r.get(b)
        return row


def make_client(cfg, client_cfg, mock: bool = False):
    if mock:
        from .mock import MockClient

        return MockClient(client_cfg)
    return MetaGraphClient(
        access_token=cfg.access_token or "",
        account_id=client_cfg.account_id,
        conversion_actions=client_cfg.conversion_actions,
        api_version=cfg.api_version,
    )
