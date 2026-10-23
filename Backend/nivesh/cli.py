"""Command line entry point: `uv run nivesh --help`."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime
from typing import Annotated

import typer
from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeRemainingColumn
from rich.table import Table

app = typer.Typer(help="Nivesh data and intelligence CLI", no_args_is_help=True)
console = Console()


def _parse_date(value: str) -> date:
    return date.today() if value == "today" else datetime.strptime(value, "%Y-%m-%d").date()


@app.command()
def ingest(
    start: Annotated[str, typer.Option(help="YYYY-MM-DD")] = "2010-01-01",
    end: Annotated[str, typer.Option(help="YYYY-MM-DD or 'today'")] = "today",
    datasets: Annotated[
        str, typer.Option(help="'all', 'daily', 'ref' or comma list (bhav,index,pr,bands,ref_*)")
    ] = "all",
    workers: int = 4,
    rate: Annotated[float, typer.Option(help="max requests per second across all workers")] = 2.5,
) -> None:
    """Download NSE daily archive files and reference snapshots into data/raw (idempotent)."""
    from nivesh.data import nse

    s, e = _parse_date(start), _parse_date(end)
    groups = {"all": nse.ALL_DEFAULT, "daily": nse.DAILY_DEFAULT, "ref": tuple(nse.SNAPSHOTS)}
    names = [n for tok in datasets.split(",") if tok.strip() for n in groups.get(tok.strip(), (tok.strip(),))]
    unknown = [n for n in names if n not in nse.DATASETS]
    if unknown:
        raise typer.BadParameter(f"unknown datasets: {unknown}")
    total = sum(
        1 if nse.DATASETS[n].snapshot else sum(nse.DATASETS[n].covers(d) for d in nse.weekdays(s, e)) for n in names
    )
    with Progress(
        TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeRemainingColumn(), console=console
    ) as progress:
        task = progress.add_task("downloading", total=total)
        results = nse.download_range(
            s, e, names, workers=workers, rate=rate, on_result=lambda _: progress.advance(task)
        )
    counts = Counter((r.dataset, r.status) for r in results)
    table = Table("dataset", "status", "count")
    for (ds, st), n in sorted(counts.items()):
        table.add_row(ds, st, str(n))
    console.print(table)
    errors = [r for r in results if r.status == "error"]
    for r in errors[:10]:
        console.print(f"[red]error[/] {r.dataset} {r.day} {r.detail}")


@app.command("build")
def build_cmd(years: Annotated[str, typer.Option(help="Comma list of years; default all")] = "") -> None:
    """Parse raw CSVs into Parquet and rebuild adjusted stock_daily."""
    from nivesh.data import build

    yrs = [int(y) for y in years.split(",") if y.strip()] or None
    with console.status("building parquet..."):
        result = build.build_all(yrs)
    console.print_json(json.dumps(result))


@app.command()
def quality() -> None:
    """Run data quality checks; writes data/reports/quality_<date>.json."""
    from nivesh.data.quality import run_quality

    report = run_quality()
    table = Table("check", "severity", "status", "count")
    for c in report["checks"]:
        colour = {"ok": "green", "issues": "red", "skipped": "yellow"}.get(c["status"], "white")
        table.add_row(c["name"], c["severity"], f"[{colour}]{c['status']}[/]", str(c.get("count", "")))
    console.print(table)
    console.print(f"data through: {report['data_through']}  critical issues: {report['critical_issues']}")


@app.command()
def pipeline(start: str = "", end: str = "today") -> None:
    """Run the full EOD pipeline (incremental when start is omitted)."""
    from nivesh.jobs.pipeline import run_eod_pipeline

    record = run_eod_pipeline(_parse_date(start) if start else None, _parse_date(end))
    console.print_json(json.dumps(record, default=str))


@app.command()
def freshness() -> None:
    """Show latest date per dataset."""
    from nivesh.data.engine import Engine

    engine = Engine()
    table = Table("view", "latest date")
    for name in engine.views():
        table.add_row(name, str(engine.freshness().get(name, "-")))
    console.print(table)
    console.print(f"data through: {engine.data_through()}")


@app.command()
def sql(query: str, limit: int = 50) -> None:
    """Run a read-only SQL query against the Parquet views and print the result."""
    from nivesh.data.engine import Engine

    engine = Engine()
    result = engine.query(query, max_rows=limit)
    table = Table(*result.columns)
    for row in result.rows:
        table.add_row(*["" if v is None else str(v) for v in row])
    console.print(table)
    note = " (truncated)" if result.truncated else ""
    console.print(f"{len(result.rows)} rows{note} in {result.elapsed_ms:.0f} ms")


@app.command()
def views() -> None:
    """List mounted views and their columns."""
    from nivesh.data.engine import Engine

    engine = Engine()
    for name in engine.views():
        cols = ", ".join(f"{c} {t}" for c, t in engine.schema(name))
        console.print(f"[bold]{name}[/]: {cols}")


@app.command("llm-ping")
def llm_ping() -> None:
    """Make one tiny call per configured model role, to check keys, model ids and fallbacks."""
    from nivesh.copilot.client import OpenAICompatibleLLM
    from nivesh.copilot.config import LLMConfigError

    try:
        report = OpenAICompatibleLLM().ping()
    except LLMConfigError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    table = Table("role", "status", "model", "latency", "note")
    for r in report:
        if r["ok"]:
            note = "used a fallback" if r["fell_back"] else ""
            table.add_row(r["role"], "[green]ok[/]", r["model"], f"{r['latency_s']}s", note)
        else:
            table.add_row(r["role"], "[red]failed[/]", "", "", r["error"][:90])
    console.print(table)
    if not all(r["ok"] for r in report):
        raise typer.Exit(1)


@app.command("news-ingest")
def news_ingest(
    days: Annotated[int, typer.Option(help="how many calendar days back from today")] = 7,
    rate: Annotated[float, typer.Option(help="max requests per second")] = 1.0,
) -> None:
    """Download NSE corporate announcements into data/raw/news and normalise them (idempotent, polite to NSE)."""
    from datetime import timedelta

    from nivesh.news import ingest as news

    end = date.today()
    results = news.download_days(end - timedelta(days=days), end, rate=rate)
    console.print(dict(Counter(v.split(":")[0] for v in results.values())))
    for day, outcome in results.items():
        if outcome.startswith("error"):
            console.print(f"[yellow]{day}: {outcome}[/]")
    console.print(f"{news.build_items()} announcements in news_items")


@app.command("news-reports")
def news_reports(
    days: Annotated[int, typer.Option(help="how many calendar days back from today")] = 7,
    rate: Annotated[float, typer.Option(help="max requests per second")] = 1.0,
) -> None:
    """Download NSE bulk, block and PIT reports into the immutable news raw store."""
    from datetime import timedelta

    from nivesh.news import ingest as news

    end = date.today()
    results = news.download_reports(end - timedelta(days=days), end, rate=rate)
    console.print(dict(Counter(v.split(":")[0] for v in results.values())))
    console.print(f"{news.build_items()} total normalized news items")


@app.command("news-extract")
def news_extract(
    limit: Annotated[int, typer.Option(help="max model calls this run")] = 200,
    liquid_only: Annotated[bool, typer.Option(help="only symbols in the liquid universe")] = True,
    retry_invalid: bool = False,
) -> None:
    """Extract structured events from announcements with the `extract` model role (needs LLM keys)."""
    from nivesh.copilot.client import OpenAICompatibleLLM
    from nivesh.copilot.config import LLMConfigError
    from nivesh.data.engine import Engine
    from nivesh.news.extract import run_extraction

    universe = None
    if liquid_only:
        engine = Engine()
        if "market_breadth" in engine.views() and "stock_features" in engine.views():
            universe = {
                r[0]
                for r in engine.query(
                    "SELECT DISTINCT symbol FROM stock_features WHERE date = (SELECT max(date) FROM stock_features) "
                    "AND close >= 20 AND history_sessions >= 200"
                ).rows
            }
    try:
        llm = OpenAICompatibleLLM()
    except LLMConfigError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    from nivesh.news.attachments import AttachmentFetcher

    with AttachmentFetcher() as fetcher:
        out = run_extraction(llm, limit=limit, universe=universe, retry_invalid=retry_invalid, fetch_text=fetcher)
    console.print(out)
    if out["aborted"]:
        console.print("[red]stopped after repeated model failures; run again later[/]")
        raise typer.Exit(1)


@app.command("news-study")
def news_study(min_confidence: float = 0.6) -> None:
    """Forward excess returns after each event type (gate before any strategy may use an event)."""
    from nivesh.news.study import run_study

    rows = run_study(min_confidence=min_confidence)
    if not rows:
        console.print("no events with price history yet")
        return
    table = Table("event", "n", "5d excess", "5d hit", "5d t", "20d excess", "20d t")
    for r in rows:
        table.add_row(
            r["event_type"],
            str(r["n"]),
            f"{(r['mean_excess_5d'] or 0) * 100:+.2f}%",
            f"{(r['hit_rate_5d'] or 0) * 100:.0f}%",
            f"{r['t_stat_5d'] or 0:.1f}",
            f"{(r['mean_excess_20d'] or 0) * 100:+.2f}%",
            f"{r['t_stat_20d'] or 0:.1f}",
        )
    console.print(table)


@app.command()
def ask(question: str) -> None:
    """Ask the copilot a question about the loaded data (needs LLM keys: see Backend/.env.example)."""
    from rich.markdown import Markdown

    from nivesh.copilot.client import OpenAICompatibleLLM
    from nivesh.copilot.config import LLMConfigError
    from nivesh.copilot.pipeline import Copilot
    from nivesh.data.engine import Engine

    try:
        copilot = Copilot(Engine(), OpenAICompatibleLLM())
    except LLMConfigError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(1) from exc
    for event in copilot.ask(question):
        kind = event["type"]
        if kind == "status":
            console.print(f"[dim]… {event['stage']}[/]")
        elif kind == "route":
            console.print(f"[dim]route: {event['intent']} ({event['model']})[/]")
        elif kind == "sql":
            console.print(f"[dim]sql attempt {event['attempt']}:[/] {event['sql']}")
        elif kind == "evidence":
            ev = event["evidence"]
            console.print(
                f"[dim]evidence {ev['evidence_id']}: {ev.get('row_count', 0)} rows, through {ev['data_through']}[/]"
            )
        elif kind == "answer":
            if event["mode"] == "deterministic" and not event["grounded"]:
                console.print(
                    "[yellow]model wording was rejected (numbers not in the evidence); showing the raw result[/]"
                )
            console.print(Markdown(event["markdown"]))
        elif kind == "error":
            console.print(f"[red]{event['message']}[/]")
            raise typer.Exit(1)


@app.command("demo-data")
def demo_data(
    out: Annotated[str, typer.Option(help="Output data dir (default: <data_dir>/demo)")] = "",
    sessions: int = 700,
    symbols: int = 90,
) -> None:
    """Build a synthetic dataset (NOT real prices) so the app can be explored without the NSE backfill."""
    from pathlib import Path

    from nivesh.config import settings
    from nivesh.demo import make_demo_dataset

    base = Path(out) if out else settings.data_dir / "demo"
    root = base / "parquet"
    with console.status("generating synthetic market and running analytics..."):
        days = make_demo_dataset(root, sessions=sessions, symbols=symbols)
    console.print(f"Demo data ready: {symbols} symbols x {sessions} sessions, {days[0]} to {days[-1]}")
    console.print(f"Serve it with:  NIVESH_DATA_DIR={base.resolve()} uv run nivesh serve")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 15000, reload: bool = False, scheduler: bool = False) -> None:
    """Start the API (localhost only by default; there is no auth in v0.x)."""
    import os

    import uvicorn

    if host not in {"127.0.0.1", "localhost"}:
        console.print("[yellow]warning: v0.x has no auth; binding beyond localhost exposes the API[/]")
    if scheduler:
        os.environ["NIVESH_SCHEDULER"] = "1"
    uvicorn.run("nivesh.api.main:app", host=host, port=port, reload=reload)


if __name__ == "__main__":
    app()
