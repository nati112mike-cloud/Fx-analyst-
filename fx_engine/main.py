"""Command-line entrypoint.

    python -m fx_engine.main backtest --pair EURUSD --strategy trend_pullback
    python -m fx_engine.main backtest-all --pairs EURUSD,GBPUSD
    python -m fx_engine.main walk-forward --pair EURUSD --strategy trend_pullback
    python -m fx_engine.main signal-once --pair EURUSD
    python -m fx_engine.main paper --pairs EURUSD,GBPUSD --interval 900
    python -m fx_engine.main dashboard --port 8080
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone

from fx_engine import config
from fx_engine.backtest.engine import BacktestEngine
from fx_engine.broker.paper import PaperBrokerAdapter
from fx_engine.backtest.metrics import compute_metrics
from fx_engine.backtest.walk_forward import run_walk_forward
from fx_engine.data.models import Timeframe
from fx_engine.data.providers import get_provider
from fx_engine.db import Database
from fx_engine.news import NewsFilter
from fx_engine.paper_trading import PaperTradingLoop
from fx_engine.signal_engine import NoTradeReason, SignalEngine
from fx_engine.strategies import ALL_STRATEGIES, build_all
from fx_engine.telegram_bot import TelegramNotifier

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def cmd_backtest(args) -> None:
    provider = get_provider(args.provider)
    timeframe = Timeframe(args.timeframe)
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    df = provider.get_ohlc(args.pair, timeframe, start, end)

    strategies = build_all() if args.strategy == "all" else {args.strategy: ALL_STRATEGIES[args.strategy]()}
    engine = BacktestEngine()
    db = Database() if args.save else None
    if db:
        db.init_schema()

    for name, strat in strategies.items():
        result = engine.run(strat, df, args.pair)
        metrics = compute_metrics(result.trades)
        print(f"\n=== {name} on {args.pair} ({timeframe.value}, {args.provider} data, "
              f"{start.date()}..{end.date()}) ===")
        print(json.dumps(metrics.as_dict(), indent=2))
        if db:
            db.save_strategy_version(strat.spec.name, strat.spec.version, strat.spec.__dict__)
            db.save_backtest(strat.spec.name, strat.spec.version, args.pair, timeframe.value,
                              start.isoformat(), end.isoformat(), metrics.as_dict(), data_provider=args.provider)


def cmd_walk_forward(args) -> None:
    provider = get_provider(args.provider)
    timeframe = Timeframe(args.timeframe)
    start = _parse_date(args.start)
    end = _parse_date(args.end)
    df = provider.get_ohlc(args.pair, timeframe, start, end)

    strat = ALL_STRATEGIES[args.strategy]()
    report = run_walk_forward(strat, df, args.pair)
    print("\n".join(report.summary_lines()))

    if args.save:
        db = Database()
        db.init_schema()
        db.save_strategy_version(strat.spec.name, strat.spec.version, strat.spec.__dict__)
        db.save_backtest(
            strat.spec.name, strat.spec.version, args.pair, timeframe.value,
            start.isoformat(), end.isoformat(),
            {"note": "see walk_forward_json for fold detail"},
            walk_forward_verdict=report.verdict,
            walk_forward_json={
                "folds": [{"label": f.label, "metrics": f.metrics.as_dict()} for f in report.folds],
                "holdout": {"metrics": report.holdout.metrics.as_dict()} if report.holdout else None,
                "notes": report.notes,
            },
            data_provider=args.provider,
        )


def cmd_signal_once(args) -> None:
    provider = get_provider(args.provider)
    notifier = TelegramNotifier() if args.telegram else None
    db = Database()
    db.init_schema()
    engine = SignalEngine(provider, db=db, notifier=notifier, min_signal_score=args.min_score)
    result = engine.evaluate_pair(args.pair, Timeframe(args.timeframe))
    if isinstance(result, NoTradeReason):
        print(f"NO TRADE for {result.pair}: {result.reason}")
    else:
        signal_id, msg = result
        print(f"SIGNAL {signal_id}:\n")
        print(msg.format())


def cmd_paper(args) -> None:
    provider = get_provider(args.provider)
    notifier = TelegramNotifier() if args.telegram else None
    db = Database()
    db.init_schema()
    news_filter = NewsFilter()
    if args.news_csv:
        news_filter.load_from_csv(args.news_csv)
    paper_broker = PaperBrokerAdapter(provider, starting_balance=config.ACCOUNT_STARTING_BALANCE)
    engine = SignalEngine(provider, db=db, notifier=notifier, news_filter=news_filter,
                           min_signal_score=args.min_score, broker=paper_broker)
    pairs = args.pairs.split(",") if args.pairs else config.PAIRS
    loop = PaperTradingLoop(engine, provider, pairs=pairs, timeframe=Timeframe(args.timeframe),
                             poll_interval_seconds=args.interval, broker=paper_broker)
    if args.once:
        outcomes = loop.run_once()
        for o in outcomes:
            if isinstance(o, NoTradeReason):
                print(f"NO TRADE {o.pair}: {o.reason}")
            else:
                _, msg = o
                print(msg.format())
        print(f"\nPaper account balance: {paper_broker.balance:.2f} {config.ACCOUNT_CURRENCY} "
              f"(started at {config.ACCOUNT_STARTING_BALANCE:.2f})")
    else:
        loop.run_forever()


def cmd_dashboard(args) -> None:
    from fx_engine.dashboard import create_app

    db = Database()
    db.init_schema()
    app = create_app(db)
    print(f"Dashboard running at http://{args.host}:{args.port} (Ctrl+C to stop)")
    app.run(host=args.host, port=args.port, debug=False)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fx_engine")
    sub = p.add_subparsers(dest="command", required=True)

    def common_data_args(sp):
        sp.add_argument("--provider", default=config.DATA_PROVIDER, choices=["synthetic", "yahoo", "mt5"])
        sp.add_argument("--timeframe", default="H4", choices=[t.value for t in Timeframe])

    bt = sub.add_parser("backtest", help="Backtest one or all strategies on one pair")
    bt.add_argument("--pair", required=True)
    bt.add_argument("--strategy", default="all", choices=list(ALL_STRATEGIES.keys()) + ["all"])
    bt.add_argument("--start", default=(datetime.now(timezone.utc) - timedelta(days=730)).date().isoformat())
    bt.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat())
    bt.add_argument("--save", action="store_true", help="persist results to the database")
    common_data_args(bt)
    bt.set_defaults(func=cmd_backtest)

    wf = sub.add_parser("walk-forward", help="Walk-forward / holdout validation for one strategy")
    wf.add_argument("--pair", required=True)
    wf.add_argument("--strategy", required=True, choices=list(ALL_STRATEGIES.keys()))
    wf.add_argument("--start", default=(datetime.now(timezone.utc) - timedelta(days=730)).date().isoformat())
    wf.add_argument("--end", default=datetime.now(timezone.utc).date().isoformat())
    wf.add_argument("--save", action="store_true")
    common_data_args(wf)
    wf.set_defaults(func=cmd_walk_forward)

    so = sub.add_parser("signal-once", help="Evaluate one pair right now and print/send the signal (or NO TRADE)")
    so.add_argument("--pair", required=True)
    so.add_argument("--telegram", action="store_true", help="actually send to Telegram if configured")
    so.add_argument("--min-score", type=float, default=55.0)
    common_data_args(so)
    so.set_defaults(func=cmd_signal_once)

    pt = sub.add_parser("paper", help="Run the paper-trading loop (--once for a single cycle)")
    pt.add_argument("--pairs", default="")
    pt.add_argument("--interval", type=int, default=900)
    pt.add_argument("--telegram", action="store_true")
    pt.add_argument("--once", action="store_true")
    pt.add_argument("--min-score", type=float, default=55.0)
    pt.add_argument("--news-csv", default="")
    common_data_args(pt)
    pt.set_defaults(func=cmd_paper)

    dash = sub.add_parser("dashboard", help="Run the local read-only dashboard over the database")
    dash.add_argument("--host", default="127.0.0.1")
    dash.add_argument("--port", type=int, default=8080)
    dash.set_defaults(func=cmd_dashboard)

    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
