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
) -> None:
    """Download NSE daily archive files and reference snapshots into data/raw (idempotent)."""
    from nivesh.data import nse

    s, e = _parse_date(start), _parse_date(end)
    groups = {"all": nse.ALL_DEFAULT, "daily": nse.DAILY_DEFAULT, "ref": tuple(nse.SNAPSHOTS)}
    names = [n for tok in datasets.split(",") if tok.strip() for n in groups.get(tok.strip(), (tok.strip(),))]
    unknown = [n for n in names if n not in nse.DATASETS]
    if unknown:
        raise typer.BadParameter(f"unknown datasets: {unknown}")
    days = sum(1 for _ in nse.weekdays(s, e))
    total = sum(1 if nse.DATASETS[n].snapshot else days for n in names)
    with Progress(
        TextColumn("{task.description}"), BarColumn(), MofNCompleteColumn(), TimeRemainingColumn(), console=console
    ) as progress:
        task = progress.add_task("downloading", total=total)
        results = nse.download_range(s, e, names, workers=workers, on_result=lambda _: progress.advance(task))
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


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False, scheduler: bool = False) -> None:
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
