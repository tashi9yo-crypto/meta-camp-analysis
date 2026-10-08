"""CLI.

    python -m meta_ads daily   [--client 봄날] [--date 2026-10-08] [--mock] [--no-notify]
    python -m meta_ads monthly [--client 봄날] [--month 2026-09]    [--mock]
    python -m meta_ads check-token
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .client import make_client
from .config import load_config


def _today(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="meta_ads", description="메타 광고 일일 점검/월간 보고서")
    ap.add_argument("--config", help="설정 파일 (기본: config.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name in ("daily", "monthly", "check-token"):
        p = sub.add_parser(name)
        p.add_argument("--client", help="클라이언트 이름 또는 광고계정 ID (기본: 첫 번째)")
        p.add_argument("--all", action="store_true", help="설정된 모든 클라이언트 실행")
        p.add_argument("--mock", action="store_true", help="API 대신 모의 데이터 사용")
        if name == "daily":
            p.add_argument("--date", help="점검 기준일 YYYY-MM-DD (기본: 오늘, 어제까지 데이터 점검)")
            p.add_argument("--no-notify", action="store_true")
        if name == "monthly":
            p.add_argument("--month", help="YYYY-MM (기본: 지난달)")

    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config(args.config)
    targets = cfg.clients if args.all else [cfg.client(args.client)]
    today = _today(cfg.timezone)

    for cc in targets:
        if args.mock:
            from .mock import MockClient

            api = MockClient(cc, anchor=today)
        else:
            api = make_client(cfg, cc)

        if args.cmd == "check-token":
            info = api.account_info()
            print(f"OK: {info.get('name')} ({info.get('id')}) 통화={info.get('currency')} TZ={info.get('timezone_name')}")
            continue

        if args.cmd == "daily":
            from . import daily, notify

            as_of = date.fromisoformat(args.date) if args.date else today
            res = daily.run_daily(api, cc, as_of)
            md = daily.render_markdown(res, cc)
            md_path, _ = daily.save(res, md, cfg.output_dir)
            print(f"일일 점검 저장: {md_path}", file=sys.stderr)
            if not args.no_notify:
                notify.send(res, cfg.notify)

        if args.cmd == "monthly":
            from . import monthly

            if args.month:
                y, m = map(int, args.month.split("-"))
            else:
                first = today.replace(day=1)
                y, m = (first.year - 1, 12) if first.month == 1 else (first.year, first.month - 1)
            paths = monthly.run_monthly(api, cc, y, m, cfg.output_dir, mock=args.mock)
            for p in paths:
                print(f"월간 보고서 저장: {p}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
