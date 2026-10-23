"""The copilot orchestrator.

    question → temporal guard → router → (analytical: SQL → guard → DuckDB → evidence | knowledge: retrieval → evidence)
             → synthesis restricted to the evidence → numeric grounding check → answer

LLMs never compute: the database does. A model may route, write one read-only query, and phrase the result; every
number in its wording is checked against the evidence, and if that check fails the answer is rendered deterministically.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import duckdb
import sqlglot
import yaml
from sqlglot import exp

from nivesh.config import settings
from nivesh.copilot import grounding, temporal
from nivesh.copilot.client import LLM, LLMError, Message
from nivesh.copilot.config import LLMConfigError
from nivesh.copilot.knowledge import KnowledgeIndex
from nivesh.copilot.prompts import KNOWLEDGE_PROMPT, OUT_OF_SCOPE, ROUTER_PROMPT, SQL_PROMPT, SYNTH_PROMPT
from nivesh.data.engine import Engine
from nivesh.llm import validate_readonly_sql

HIDDEN_VIEWS = {"stock_daily_raw"}
INTENTS = {"analytical", "knowledge", "out_of_scope"}
PROMPT_ROWS = 40
RETRY_HINT = "Return corrected JSON with a valid read-only DuckDB query over the listed views."
UNGROUNDED_NOTE = "The model wording had numbers that are not in the evidence, so the raw result is shown instead."


def _view_notes() -> dict[str, Any]:
    path = settings.context_dir / "views.yaml"
    if not path.exists():
        return {}
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("views", {})


def _failed_evidence(
    question: str, through: Any, *, sql: str, explanation: str, heal_log: list[dict[str, Any]], error: str
) -> dict[str, Any]:
    return make_evidence(
        "analytical", question, through, sql=sql, columns=[], rows=[], row_count=0, truncated=False,
        source_views=[], calculation=explanation, heal_log=heal_log, error=error,
    )  # fmt: skip


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def make_evidence(kind: str, question: str, through: Any, **fields: Any) -> dict[str, Any]:
    digest = hashlib.sha1(f"{kind}{question}{time.time_ns()}".encode()).hexdigest()[:8]
    return {
        "evidence_id": f"ev_{digest}",
        "kind": kind,
        "question": question,
        "data_through": through,
        "executed_at": _now(),
        "confidence": "deterministic" if kind in {"analytical", "knowledge"} else "n/a",
        **fields,
    }


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.4f}".rstrip("0").rstrip(".")
    return str(value)


def render_evidence(evidence: dict[str, Any], notes: list[str] | None = None) -> str:
    """Deterministic fallback: show exactly what the database returned, with no model wording."""
    lines: list[str] = []
    if evidence["kind"] == "knowledge":
        for p in evidence.get("passages", []):
            lines += [f"**{p['heading']}** · `{p['source']}`", "", p["text"][:700], ""]
    else:
        cols, rows = evidence.get("columns", []), evidence.get("rows", [])
        lines.append(
            f"**Result** ({evidence.get('row_count', len(rows))} rows · data through {evidence.get('data_through')})"
        )
        if rows:
            lines += ["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
            lines += ["| " + " | ".join(_cell(v) for v in r) + " |" for r in rows[:25]]
            if len(rows) > 25:
                lines.append(f"\n_Showing 25 of {len(rows)} rows._")
        else:
            lines.append("\nNo rows matched.")
    lines += [f"\n_{n}_" for n in notes or []]
    return "\n".join(lines).strip()


class Copilot:
    def __init__(
        self,
        engine: Engine,
        llm: LLM,
        knowledge: KnowledgeIndex | None = None,
        *,
        max_rows: int = 200,
        max_attempts: int = 3,
        query_timeout_s: float = 30.0,
    ) -> None:
        self.engine = engine
        self.llm = llm
        self._knowledge = knowledge
        self.max_rows = max_rows
        self.max_attempts = max_attempts
        self.query_timeout_s = query_timeout_s
        self.models: dict[str, str] = {}

    # ----------------------------------------------------------------------------------------------- helpers
    @property
    def knowledge(self) -> KnowledgeIndex:
        if self._knowledge is None:
            self._knowledge = KnowledgeIndex()
        return self._knowledge

    def allowed_views(self) -> set[str]:
        return {v for v in self.engine.views() if v not in HIDDEN_VIEWS}

    def schema_context(self) -> str:
        notes = _view_notes()
        lines = []
        for view in sorted(self.allowed_views()):
            cols = ", ".join(f"{name} {typ}" for name, typ in self.engine.schema(view) if name != "year")
            info = notes.get(view, {})
            lines.append(f"- {view}: {info.get('grain', '')}. {str(info.get('notes', '')).strip()}\n  columns: {cols}")
        return "\n".join(lines)

    def _chat(self, role: str, messages: list[Message], *, json_mode: bool | None = None):
        try:
            result = self.llm.chat(role, messages, json_mode=json_mode)
        except LLMConfigError:
            if role != "repair":
                raise
            result = self.llm.chat("sql", messages, json_mode=json_mode)  # no dedicated repair model configured
        self.models[role] = result.model_id
        return result

    # ----------------------------------------------------------------------------------------------- main flow
    def ask(self, question: str) -> Iterator[dict[str, Any]]:
        started = time.perf_counter()
        through = self.engine.data_through()
        try:
            yield {"type": "status", "stage": "checking dates"}
            check = temporal.check(question, through)
            if check.refuse:
                evidence = make_evidence("refusal", question, through, reason=check.reason)
                yield {"type": "evidence", "evidence": evidence}
                yield self._answer(check.reason, grounded=True, mode="deterministic")
                yield self._done(started)
                return

            yield {"type": "status", "stage": "routing"}
            route = self._route(question)
            yield {"type": "route", **route}
            if route["intent"] == "out_of_scope":
                yield self._answer(OUT_OF_SCOPE, grounded=True, mode="deterministic")
                yield self._done(started)
                return

            evidence = None
            if route["intent"] == "knowledge":
                yield {"type": "status", "stage": "searching docs"}
                evidence = self._knowledge_evidence(question, through)
            else:
                yield {"type": "status", "stage": "writing and checking SQL"}
                gen = self._analytical_evidence(question, through)
                while True:
                    try:
                        yield next(gen)
                    except StopIteration as stop:
                        evidence = stop.value
                        break
            yield {"type": "evidence", "evidence": evidence}

            yield {"type": "status", "stage": "writing the answer"}
            yield self._synthesize(question, evidence, check.notes)
            yield self._done(started)
        except (LLMError, LLMConfigError) as exc:
            yield {"type": "error", "message": str(exc)}
        except Exception as exc:  # the stream must always end with an event the UI can show
            yield {"type": "error", "message": f"{type(exc).__name__}: {exc}"}

    def _done(self, started: float) -> dict[str, Any]:
        return {"type": "done", "elapsed_s": round(time.perf_counter() - started, 2), "models": dict(self.models)}

    def _answer(
        self, markdown: str, *, grounded: bool, mode: str, ungrounded: list[str] | None = None
    ) -> dict[str, Any]:
        return {
            "type": "answer",
            "markdown": markdown,
            "grounded": grounded,
            "mode": mode,
            "ungrounded": ungrounded or [],
        }

    # ----------------------------------------------------------------------------------------------- routing
    def _route(self, question: str) -> dict[str, Any]:
        result = self._chat(
            "router",
            [{"role": "system", "content": ROUTER_PROMPT}, {"role": "user", "content": question}],
            json_mode=True,
        )
        data = result.data or {}
        intent = str(data.get("intent", "")).strip().lower()
        if intent not in INTENTS:
            intent = "out_of_scope"
        return {"intent": intent, "reason": str(data.get("reason", ""))[:200], "model": result.model_id}

    # ----------------------------------------------------------------------------------------------- knowledge
    def _knowledge_evidence(self, question: str, through: Any) -> dict[str, Any]:
        passages = self.knowledge.search(question, k=4)
        return make_evidence(
            "knowledge",
            question,
            through,
            passages=passages,
            row_count=len(passages),
            source_views=[],
            calculation="lexical retrieval (BM25 + acronym boost) over docs and YAML",
        )

    # ----------------------------------------------------------------------------------------------- analytical
    def _analytical_evidence(self, question: str, through: Any):
        allowed = self.allowed_views()
        system = SQL_PROMPT.format(limit=self.max_rows, through=through, schema=self.schema_context())
        messages: list[Message] = [{"role": "system", "content": system}, {"role": "user", "content": question}]
        heal_log: list[dict[str, Any]] = []
        explanation = ""
        for attempt in range(1, self.max_attempts + 1):
            result = self._chat("sql" if attempt == 1 else "repair", messages, json_mode=True)
            data = result.data or {}
            sql = str(data.get("sql", "")).strip()
            explanation = str(data.get("explanation", ""))
            if not sql:
                heal_log.append({"attempt": attempt, "sql": "", "error": explanation or "the model returned no query"})
                return _failed_evidence(
                    question, through, sql="", explanation=explanation, heal_log=heal_log,
                    error="no query could be written",
                )  # fmt: skip
            yield {"type": "sql", "attempt": attempt, "sql": sql, "model": result.model_id}
            try:
                safe = validate_readonly_sql(sql, allowed)
                bounded = f"SELECT * FROM ({safe}) AS guarded LIMIT {self.max_rows + 1}"
                res = self.engine.query(bounded, max_rows=self.max_rows, timeout_s=self.query_timeout_s)
            except (ValueError, duckdb.Error, sqlglot.errors.SqlglotError) as exc:
                heal_log.append({"attempt": attempt, "sql": sql, "error": str(exc)[:400]})
                messages += [
                    {"role": "assistant", "content": result.text},
                    {
                        "role": "user",
                        "content": f"That query failed: {str(exc)[:400]}" + chr(10) + RETRY_HINT,
                    },
                ]
                continue
            views = sorted({t.name for t in sqlglot.parse_one(safe, read="duckdb").find_all(exp.Table)} & allowed)
            return make_evidence(
                "analytical",
                question,
                through,
                sql=safe,
                columns=res.columns,
                rows=[list(r) for r in res.rows],
                row_count=len(res.rows),
                truncated=res.truncated,
                source_views=views,
                calculation=explanation,
                heal_log=heal_log,
            )
        return _failed_evidence(
            question, through, sql=heal_log[-1]["sql"] if heal_log else "", explanation=explanation,
            heal_log=heal_log, error=f"no valid query after {self.max_attempts} attempts",
        )  # fmt: skip

    # ----------------------------------------------------------------------------------------------- synthesis
    def _synthesize(self, question: str, evidence: dict[str, Any], notes: list[str]) -> dict[str, Any]:
        if evidence.get("error"):
            text = f"I could not answer that from the data: {evidence['error']}."
            if evidence.get("heal_log"):
                text += f" Last problem: {evidence['heal_log'][-1]['error']}"
            return self._answer(text, grounded=True, mode="deterministic")

        shown = {**evidence, "rows": evidence.get("rows", [])[:PROMPT_ROWS]}
        prompt = KNOWLEDGE_PROMPT if evidence["kind"] == "knowledge" else SYNTH_PROMPT
        user = f"QUESTION: {question}\n\nEVIDENCE:\n{json.dumps(shown, default=str)}"
        try:
            result = self._chat(
                "synth", [{"role": "system", "content": prompt}, {"role": "user", "content": user}], json_mode=False
            )
        except (LLMError, LLMConfigError):
            return self._answer(render_evidence(evidence, notes), grounded=True, mode="deterministic")

        verdict = grounding.check(result.text, evidence, question)
        if verdict.ok:
            footer = "".join(f"\n\n_{n}_" for n in notes)
            return {**self._answer(result.text + footer, grounded=True, mode="model"), "model": result.model_id}
        return {
            **self._answer(
                render_evidence(evidence, notes), grounded=False, mode="deterministic", ungrounded=verdict.ungrounded
            ),
            "model": result.model_id,
            "note": UNGROUNDED_NOTE,
        }
