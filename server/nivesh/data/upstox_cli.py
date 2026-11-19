"""CLI commands for Upstox intraday data ingestion.

Registered into the main nivesh CLI app via cli.py.
Kept in a separate file to stay within the 300-line limit per file.

Commands exposed:
  uv run nivesh intraday-backfill   -- staged one-year backfill (30-day batches)
  uv run nivesh intraday-fetch      -- fetch specific symbols / date range
  uv run nivesh intraday-status     -- show checkpoint summary and Parquet coverage
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, timedelta
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

console = Console()


def _parse_date(value: str) -> date:
    from datetime import datetime
    return date.today() if value == "today" else datetime.strptime(value, "%Y-%m-%d").date()


def register(app: typer.Typer) -> None:
    """Add all intraday CLI commands to the top-level typer app."""

    @app.command("intraday-backfill")
    def intraday_backfill(
        batch_days: Annotated[int, typer.Option(help="Calendar days per batch")] = 30,
        total_days: Annotated[int, typer.Option(help="Total calendar days to backfill")] = 365,
        workers: Annotated[int, typer.Option(help="Concurrent fetch threads")] = 4,
        rate: Annotated[float, typer.Option(help="Max requests/min (global)")] = 100.0,
        symbols_csv: Annotated[str, typer.Option(help="Comma-separated symbols; default=all EQ")] = "",
    ) -> None:
        """Staged one-year backfill of 1-minute candles for all NSE EQ symbols.

        Runs in sequential 30-day batches so interrupted jobs resume safely.
        Already-fetched symbol+date pairs are skipped (idempotent).
        """
        from nivesh.data.upstox_calendar import calendar_days_back
        from nivesh.data.upstox_checkpoint import FetchCheckpoint
        from nivesh.data.upstox_fetcher import build_symbol_universe, fetch_universe
        from nivesh.data.upstox_limiter import UpstoxRateLimiter
        from nivesh.data.upstox_resolver import UpstoxResolver

        end = date.today() - timedelta(days=1)  # don't fetch today (incomplete)
        full_start = end - timedelta(days=total_days)

        universe = (
            [{"symbol": s.strip(), "isin": ""} for s in symbols_csv.split(",") if s.strip()]
            if symbols_csv else build_symbol_universe()
        )
        console.print(f"Universe: [bold]{len(universe)}[/] symbols | "
                      f"Range: {full_start} → {end} | Batch: {batch_days}d")

        resolver = UpstoxResolver()
        with console.status("Loading Upstox instrument master…"):
            resolver.load()
        console.print(f"Instrument master loaded: {len(resolver.all_symbols())} NSE symbols indexed.")

        limiter = UpstoxRateLimiter(rate)
        checkpoint = FetchCheckpoint()

        # Build batch list: most recent first so latest data is available quickest
        batches: list[tuple[date, date]] = []
        batch_end = end
        while batch_end > full_start:
            batch_start = max(full_start, batch_end - timedelta(days=batch_days - 1))
            batches.append((batch_start, batch_end))
            batch_end = batch_start - timedelta(days=1)

        total_ok = total_failed = total_skipped = 0

        for i, (b_start, b_end) in enumerate(batches, 1):
            console.rule(f"Batch {i}/{len(batches)}: {b_start} → {b_end}")
            pending = checkpoint.pending_count([e["symbol"] for e in universe], [])
            console.print(f"Checkpoint has {checkpoint.summary().get('ok', 0)} ok records so far.")

            batch_results: list = []
            with Progress(TextColumn("{task.description}"), BarColumn(),
                          MofNCompleteColumn(), TimeRemainingColumn(), console=console) as prog:
                task = prog.add_task(f"batch {i}", total=None)

                def _on_result(r):
                    batch_results.append(r)
                    prog.advance(task)

                fetch_universe(
                    universe, b_start, b_end,
                    resolver=resolver, limiter=limiter, checkpoint=checkpoint,
                    workers=workers, on_result=_on_result,
                )

            counts = Counter(r.status for r in batch_results)
            total_ok += counts.get("ok", 0)
            total_failed += counts.get("failed", 0)
            total_skipped += counts.get("skipped", 0) + counts.get("empty", 0)
            console.print(f"Batch {i}: ok={counts.get('ok',0)} failed={counts.get('failed',0)} "
                          f"empty={counts.get('empty',0)} holiday={counts.get('holiday',0)}")

            failed_examples = [r for r in batch_results if r.status == "failed"][:3]
            for r in failed_examples:
                console.print(f"  [red]FAILED[/] {r.symbol} {r.trade_date}: {r.error}")

        console.rule("Backfill complete")
        console.print(f"Total ok={total_ok}  failed={total_failed}  skipped/empty={total_skipped}")
        if total_failed > 0:
            console.print("[yellow]Some fetches failed. Re-run to retry (checkpoint skips successes).[/]")

    @app.command("intraday-fetch")
    def intraday_fetch(
        start: Annotated[str, typer.Option(help="YYYY-MM-DD")] = "",
        end: Annotated[str, typer.Option(help="YYYY-MM-DD or 'today'")] = "today",
        symbols_csv: Annotated[str, typer.Option(help="Comma-separated symbols; default=all EQ")] = "",
        workers: int = 4,
        rate: float = 100.0,
        force: Annotated[bool, typer.Option(help="Ignore checkpoint and re-fetch")] = False,
    ) -> None:
        """Fetch 1-minute candles for a specific date range (incremental by default)."""
        from nivesh.data.upstox_checkpoint import FetchCheckpoint
        from nivesh.data.upstox_fetcher import build_symbol_universe, fetch_universe
        from nivesh.data.upstox_limiter import UpstoxRateLimiter
        from nivesh.data.upstox_resolver import UpstoxResolver

        end_d = _parse_date(end)
        start_d = _parse_date(start) if start else end_d
        universe = (
            [{"symbol": s.strip(), "isin": ""} for s in symbols_csv.split(",") if s.strip()]
            if symbols_csv else build_symbol_universe()
        )

        resolver = UpstoxResolver()
        resolver.load()
        limiter = UpstoxRateLimiter(rate)
        checkpoint = FetchCheckpoint() if not force else None

        results = fetch_universe(universe, start_d, end_d, resolver=resolver,
                                 limiter=limiter, checkpoint=checkpoint, workers=workers)
        counts = Counter(r.status for r in results)
        table = Table("status", "count")
        for status, n in sorted(counts.items()):
            table.add_row(status, str(n))
        console.print(table)

    @app.command("intraday-status")
    def intraday_status() -> None:
        """Show checkpoint summary and Parquet coverage for intraday candles."""
        from nivesh.data.upstox_checkpoint import FetchCheckpoint
        from nivesh.config import settings

        cp = FetchCheckpoint()
        summary = cp.summary()
        table = Table("status", "count")
        for status, n in sorted(summary.items()):
            table.add_row(status, str(n))
        console.print(table)

        intraday = settings.parquet_dir / "stock_intraday_1m"
        if intraday.exists():
            symbols = [p.name.replace("symbol=", "") for p in intraday.iterdir() if p.is_dir()]
            console.print(f"Parquet partitions: {len(symbols)} symbols in {intraday}")
        else:
            console.print("[yellow]No stock_intraday_1m Parquet directory yet.[/]")
