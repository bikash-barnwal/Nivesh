"""Alert rules, delivery and the daily brief.

Alerts are deterministic: each one is derived from the analytics tables, carries the date it refers to, and has a
dedupe key so the same thing is never delivered twice. Wording is templated from data; no model is involved.
"""

from __future__ import annotations

import json
import os
import smtplib
from dataclasses import dataclass
from datetime import date, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

import httpx

from nivesh import appdb
from nivesh.backtest.engine import BacktestError, load_panel
from nivesh.backtest.spec import get_strategy
from nivesh.data.engine import Engine
from nivesh.stock_panel import lifecycle

SEVERITY_RANK = {"CRITICAL": 0, "WARNING": 1, "WATCH": 2, "INFO": 3}
KINDS = ("regime_change", "market_event", "scan_entry", "watchlist", "strategy_signal")
TOP_N = 20  # a scan "entry" means newly appearing in the top N
MAX_ITEMS_PER_RULE = 10
UPCOMING_ACTION_DAYS = 7
DEFAULT_RULES = (
    {"name": "Regime changes", "kind": "regime_change", "params": {}},
    {"name": "Warnings and critical events", "kind": "market_event", "params": {"severity_min": "WARNING"}},
    {"name": "Watchlist changes", "kind": "watchlist", "params": {}},
)


@dataclass(frozen=True)
class AlertItem:
    key: str
    kind: str
    severity: str
    title: str
    body: str
    as_of: date
    rule_id: int | None = None


# ------------------------------------------------------------------------------------------------ evaluation
def _latest_two(engine: Engine, view: str = "stock_features") -> tuple[date | None, date | None]:
    rows = engine.query(f'SELECT DISTINCT date FROM "{view}" ORDER BY date DESC LIMIT 2').rows
    return (rows[0][0] if rows else None, rows[1][0] if len(rows) > 1 else None)


def _regime_change(engine: Engine) -> list[AlertItem]:
    if "market_regime" not in engine.views():
        return []
    rows = engine.query("SELECT date, regime, posture FROM market_regime ORDER BY date DESC LIMIT 2").rows
    if len(rows) < 2 or rows[0][1] == rows[1][1]:
        return []
    (today, now, posture), (_, before, _) = rows
    severity = "WARNING" if posture in ("DEFENSIVE", "CAPITAL PRESERVATION") else "INFO"
    return [
        AlertItem(
            f"regime_change:{today}",
            "regime_change",
            severity,
            f"Market regime: {before} → {now}",
            f"On {today} the regime moved from {before} to {now}. Posture: {posture}.",
            today,
        )
    ]


def _market_events(engine: Engine, severity_min: str = "WARNING") -> list[AlertItem]:
    if "market_events" not in engine.views() or "market_engines" not in engine.views():
        return []
    latest = engine.query("SELECT max(date) FROM market_engines").rows[0][0]
    threshold = SEVERITY_RANK.get(severity_min.upper(), 1)
    rows = engine.query(
        "SELECT event, severity, confidence, description FROM market_events WHERE date = ? AND streak = 1", [latest]
    ).rows
    return [
        AlertItem(
            f"market_event:{event}:{latest}",
            "market_event",
            severity,
            f"{severity}: {event.replace('_', ' ')}",
            f"{description} Confidence {confidence:.0f}%.",
            latest,
        )
        for event, severity, confidence, description in rows
        if SEVERITY_RANK.get(severity, 9) <= threshold
    ]


def _scan_entries(engine: Engine, scan_id: str, min_score: float) -> list[AlertItem]:
    if "scan_results" not in engine.views():
        return []
    latest, previous = _latest_two(engine)
    if latest is None or previous is None:
        return []
    today = engine.query(
        "SELECT symbol, rank, score, state FROM scan_results WHERE scan_id = ? AND date = ? AND rank <= ? "
        "AND score >= ? ORDER BY rank",
        [scan_id, latest, TOP_N, min_score],
    ).rows
    before = {
        r[0]
        for r in engine.query(
            "SELECT symbol FROM scan_results WHERE scan_id = ? AND date = ? AND rank <= ?", [scan_id, previous, TOP_N]
        ).rows
    }
    return [
        AlertItem(
            f"scan_entry:{scan_id}:{symbol}:{latest}",
            "scan_entry",
            "INFO",
            f"{symbol} entered {scan_id} (#{rank})",
            f"{symbol} is now ranked #{rank} in '{scan_id}' with score {score:.0f} ({state}).",
            latest,
        )
        for symbol, rank, score, state in today
        if symbol not in before
    ][:MAX_ITEMS_PER_RULE]


def _watchlist(engine: Engine, symbols: list[str]) -> list[AlertItem]:
    if not symbols or "stock_features" not in engine.views():
        return []
    latest, _ = _latest_two(engine)
    if latest is None:
        return []
    views = set(engine.views())
    items: list[AlertItem] = []
    for symbol in symbols:
        seen = engine.query("SELECT max(date) FROM stock_features WHERE symbol = ?", [symbol]).rows[0][0]
        if seen != latest:
            continue
        life = lifecycle(engine, symbol, latest)
        if life["dwell_sessions"] == 1 and life["state"] not in (None, "UNKNOWN") and life["previous_state"]:
            items.append(
                AlertItem(
                    f"watch_state:{symbol}:{latest}",
                    "watchlist",
                    "WATCH",
                    f"{symbol}: {life['previous_state']} → {life['state']}",
                    f"{symbol} changed lifecycle state on {latest}.",
                    latest,
                )
            )
        if "circuit_hits" in views:
            hit = engine.query("SELECT side FROM circuit_hits WHERE symbol = ? AND date = ?", [symbol, latest]).rows
            if hit:
                side = "upper" if hit[0][0] == "H" else "lower"
                items.append(
                    AlertItem(
                        f"watch_circuit:{symbol}:{latest}",
                        "watchlist",
                        "WARNING",
                        f"{symbol} hit its {side} circuit",
                        f"{symbol} closed at its {side} circuit on {latest}.",
                        latest,
                    )
                )
        if "corporate_actions" in views:
            for ex_date, purpose in engine.query(
                "SELECT ex_date, purpose FROM corporate_actions WHERE symbol = ? AND ex_date > ? AND ex_date <= ?",
                [symbol, latest, latest + timedelta(days=UPCOMING_ACTION_DAYS)],
            ).rows:
                items.append(
                    AlertItem(
                        f"watch_action:{symbol}:{ex_date}:{purpose[:24]}",
                        "watchlist",
                        "INFO",
                        f"{symbol}: {purpose} on {ex_date}",
                        f"Upcoming corporate action for {symbol}: {purpose} (ex-date {ex_date}).",
                        latest,
                    )
                )
    return items


def _strategy_signals(engine: Engine, strategy_id: str) -> list[AlertItem]:
    try:
        strategy = get_strategy(strategy_id)
    except KeyError:
        return []
    latest, previous = _latest_two(engine)
    if latest is None or previous is None or "stock_features" not in engine.views():
        return []
    try:
        panel = load_panel(strategy, engine.parquet_dir, previous, latest)
    except BacktestError:
        return []
    last = len(panel.dates) - 1
    earlier = {s["symbol"] for s in panel.signals.get(last - 1, [])}
    return [
        AlertItem(
            f"strategy_signal:{strategy.id}:{s['symbol']}:{latest}",
            "strategy_signal",
            "INFO",
            f"{strategy.label or strategy.id}: new candidate {s['symbol']} (#{s['rank']})",
            f"{s['symbol']} newly meets '{strategy.entry.when}' (rank {s['rank']}). Fills at the next open if taken.",
            latest,
        )
        for s in panel.signals.get(last, [])
        if s["symbol"] not in earlier
    ][:MAX_ITEMS_PER_RULE]


def evaluate(engine: Engine, rules: list[dict[str, Any]], watchlist: list[str]) -> list[AlertItem]:
    items: list[AlertItem] = []
    for rule in rules:
        if not rule.get("enabled", 1):
            continue
        params = (
            json.loads(rule.get("params") or "{}")
            if isinstance(rule.get("params"), str)
            else (rule.get("params") or {})
        )
        kind = rule.get("kind", "scan_entry")
        if kind == "regime_change":
            found = _regime_change(engine)
        elif kind == "market_event":
            found = _market_events(engine, params.get("severity_min", "WARNING"))
        elif kind == "scan_entry":
            found = (
                _scan_entries(engine, rule["scan_id"], float(rule.get("min_score") or 0)) if rule.get("scan_id") else []
            )
        elif kind == "watchlist":
            found = _watchlist(engine, watchlist)
        elif kind == "strategy_signal":
            found = _strategy_signals(engine, params.get("strategy_id", ""))
        else:
            found = []
        items.extend(AlertItem(**{**item.__dict__, "rule_id": rule.get("id")}) for item in found)
    return items


# ------------------------------------------------------------------------------------------------ delivery
class Channel(Protocol):
    name: str

    def send(self, subject: str, body: str) -> None: ...


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None) -> None:
        self.token, self.chat_id, self._client = token, chat_id, client

    def send(self, subject: str, body: str) -> None:
        text = f"{subject}\n\n{body}"[:4000]
        client = self._client or httpx.Client(timeout=15)
        try:
            resp = client.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage", json={"chat_id": self.chat_id, "text": text}
            )
            resp.raise_for_status()
        finally:
            if self._client is None:
                client.close()


class EmailChannel:
    name = "email"

    def __init__(self, host: str, port: int, user: str | None, password: str | None, sender: str, to: str) -> None:
        self.host, self.port, self.user, self.password, self.sender, self.to = host, port, user, password, sender, to

    def send(self, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["Subject"], msg["From"], msg["To"] = f"[Nivesh] {subject}", self.sender, self.to
        msg.set_content(body)
        with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
            smtp.starttls()
            if self.user:
                smtp.login(self.user, self.password or "")
            smtp.send_message(msg)


def configured_channels() -> list[Channel]:
    """Channels enabled by environment variables (see Backend/.env.example). The in-app inbox is always on."""
    channels: list[Channel] = []
    token, chat = os.environ.get("NIVESH_TELEGRAM_TOKEN"), os.environ.get("NIVESH_TELEGRAM_CHAT_ID")
    if token and chat:
        channels.append(TelegramChannel(token, chat))
    host, to = os.environ.get("NIVESH_SMTP_HOST"), os.environ.get("NIVESH_EMAIL_TO")
    if host and to:
        channels.append(
            EmailChannel(
                host,
                int(os.environ.get("NIVESH_SMTP_PORT", "587")),
                os.environ.get("NIVESH_SMTP_USER"),
                os.environ.get("NIVESH_SMTP_PASSWORD"),
                os.environ.get("NIVESH_EMAIL_FROM", to),
                to,
            )
        )
    return channels


def dispatch(items: list[AlertItem], channels: list[Channel], path: Path | None = None) -> dict[str, int]:
    """Log each alert once (the inbox) and send new ones to every channel; one failing channel never blocks the rest."""
    stats = {"new": 0, "duplicates": 0, "sent": 0, "send_errors": 0}
    for item in items:
        log_id = appdb.log_alert(
            item.key, item.rule_id, item.kind, item.severity, item.title, item.body, item.as_of.isoformat(), path
        )
        if log_id is None:
            stats["duplicates"] += 1
            continue
        stats["new"] += 1
        delivered, errors = [], []
        for channel in channels:
            try:
                channel.send(item.title, item.body)
                delivered.append(channel.name)
                stats["sent"] += 1
            except Exception as exc:  # recorded on the alert, never raised into the pipeline
                errors.append(f"{channel.name}: {type(exc).__name__}: {str(exc)[:120]}")
                stats["send_errors"] += 1
        appdb.finish_alert(log_id, delivered, errors, path)
    return stats


def run_alerts(engine: Engine, channels: list[Channel] | None = None, path: Path | None = None) -> dict[str, Any]:
    rules = appdb.list_alerts(path)
    watch = [w["symbol"] for w in appdb.list_watch(path)]
    items = evaluate(engine, rules, watch)
    stats = dispatch(items, configured_channels() if channels is None else channels, path)
    return {"rules": len(rules), "evaluated": len(items), **stats}


# ------------------------------------------------------------------------------------------------ daily brief
def build_brief(engine: Engine, as_of: date | None = None) -> dict[str, Any]:
    """A templated morning summary. Every number comes from the analytics tables."""
    views = set(engine.views())
    cap = f" AND date <= DATE '{as_of}'" if as_of else ""
    lines: list[str] = []
    state: date | None = None

    if "market_regime" in views:
        rows = engine.query(
            "SELECT date, regime, posture, confidence, sessions_active FROM market_regime "
            f"WHERE TRUE{cap} ORDER BY date DESC LIMIT 2"
        ).rows
        if rows:
            state, regime, posture, conf, active = rows[0]
            line = (
                f"**Regime:** {regime} ({posture}), {active} sessions active, stable in {conf:.0f}% of recent sessions."
            )
            if len(rows) > 1 and rows[1][1] != regime:
                line += f" Changed from {rows[1][1]} on this session."
            lines += [line, ""]

    if "market_engines" in views and state:
        movers = engine.query(
            "SELECT engine, value, delta_10, label FROM market_engines WHERE date = ? AND value IS NOT NULL "
            "ORDER BY abs(coalesce(delta_10, 0)) DESC LIMIT 3",
            [state],
        ).rows
        if movers:
            lines.append(
                "**Engine movers (10 sessions):** "
                + "; ".join(f"{e.replace('_', ' ')} {v:.0f} ({d:+.0f}, {lab})" for e, v, d, lab in movers)
            )
            lines.append("")

    if "market_events" in views and state:
        events = engine.query(
            "SELECT event, severity, confidence, streak FROM market_events WHERE date = ?", [state]
        ).rows
        events.sort(key=lambda r: (SEVERITY_RANK.get(r[1], 9), -r[2]))
        if events:
            lines.append(
                "**Active events:** "
                + "; ".join(
                    f"{sev} {ev.replace('_', ' ')} ({streak}d, {conf:.0f}%)" for ev, sev, conf, streak in events
                )
            )
            lines.append("")

    if "sector_daily" in views and state:
        sectors = engine.query(
            "SELECT sector, rank, rs_trend FROM sector_daily WHERE date = ? AND rank IS NOT NULL ORDER BY rank", [state]
        ).rows
        if sectors:
            top, bottom = sectors[:3], sectors[-3:]
            lines.append("**Sector leaders:** " + ", ".join(f"{s} (#{r}, {t.lower()})" for s, r, t in top))
            lines.append("**Sector laggards:** " + ", ".join(f"{s} (#{r}, {t.lower()})" for s, r, t in bottom))
            lines.append("")
        if "sector_events" in views:
            recent = engine.query(
                "SELECT sector, event FROM sector_events WHERE date = ? ORDER BY sector", [state]
            ).rows
            if recent:
                lines.append(
                    "**Group events today:** " + "; ".join(f"{s} {e.replace('_', ' ').lower()}" for s, e in recent)
                )
                lines.append("")

    if "scan_results" in views and state:
        counts = engine.query(
            "SELECT scan_id, count(*) FROM scan_results WHERE date = ? GROUP BY scan_id ORDER BY scan_id", [state]
        ).rows
        if counts:
            parts = []
            for scan_id, n in counts:
                top3 = [
                    r[0]
                    for r in engine.query(
                        "SELECT symbol FROM scan_results WHERE date = ? AND scan_id = ? ORDER BY rank LIMIT 3",
                        [state, scan_id],
                    ).rows
                ]
                parts.append(f"{scan_id.replace('_', ' ')}: {n} (top {', '.join(top3)})")
            lines.append("**Scans:** " + "; ".join(parts))
            lines.append("")

    if not lines:
        lines = ["No analytics are available yet. Build the data (`nivesh pipeline`) to see a brief."]
    title = f"Nivesh daily brief — {state or 'no data'}"
    lines.append("_Educational analytics, not investment advice._")
    return {"as_of": state, "title": title, "markdown": "\n".join(lines).strip()}
