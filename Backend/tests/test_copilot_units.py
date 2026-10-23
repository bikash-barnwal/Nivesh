"""Unit tests for the copilot's building blocks: JSON extraction, grounding, temporal guard, retrieval, client."""

from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from nivesh.config import settings
from nivesh.copilot import grounding, temporal
from nivesh.copilot.client import LLMError, OpenAICompatibleLLM, PrivacyError, parse_json
from nivesh.copilot.config import LLMConfig, LLMConfigError, load_config
from nivesh.copilot.knowledge import KnowledgeIndex

THROUGH = date(2026, 10, 7)


# ---- parse_json ------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Sure! Here you go: {"sql": "SELECT 1"} hope it helps', {"sql": "SELECT 1"}),
        ('{"a": {"b": [1, {"c": 2}]}}', {"a": {"b": [1, {"c": 2}]}}),
        ('{"sql": "SELECT \'}\' AS brace"}', {"sql": "SELECT '}' AS brace"}),
        ('{"q": "say \\"hi\\" }"}', {"q": 'say "hi" }'}),
    ],
)
def test_parse_json_variants(text, expected):
    assert parse_json(text) == expected


@pytest.mark.parametrize("text", ["no json here", '{"unterminated": ', "[1, 2, 3]", ""])
def test_parse_json_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_json(text)


# ---- temporal guard --------------------------------------------------------------------------------------------
def test_future_dates_and_words_are_refused():
    assert temporal.check("close of RELIANCE on 2026-10-08", THROUGH).refuse
    assert temporal.check("What happened on 2030-01-01?", THROUGH).refuse
    assert temporal.check("Will TCS close higher tomorrow?", THROUGH).refuse
    assert temporal.check("outlook for next month", THROUGH).refuse
    assert "2026-10-07" in temporal.check("price on 2027-01-01", THROUGH).reason


def test_past_and_present_questions_pass_with_a_note_for_today():
    assert not temporal.check("close of RELIANCE on 2026-10-07", THROUGH).refuse
    assert not temporal.check("close on 2024-02-29", THROUGH).refuse
    today = temporal.check("What is the close today?", THROUGH)
    assert not today.refuse and "2026-10-07" in today.notes[0]
    assert not temporal.check("not a real date 2026-13-45", THROUGH).refuse


def test_no_data_loaded_refuses():
    assert temporal.check("anything", None).refuse


# ---- numeric grounding -----------------------------------------------------------------------------------------
EVIDENCE = {
    "rows": [
        ["RELIANCE", date(2026, 10, 7), 1207.7, 0.0412, 14500234],
        ["TCS", date(2026, 10, 7), 3010.25, -0.0108, 900],
    ],
    "row_count": 2,
    "data_through": date(2026, 10, 7),
    "sql": "SELECT symbol, date, close FROM stock_daily LIMIT 2",
}


@pytest.mark.parametrize(
    "answer",
    [
        "[FACT] RELIANCE closed at 1,207.7 on 2026-10-07.",
        "[FACT] Close was 1207.70 (rounded).",
        "[CALCULATION] The 21-day return was 4.12% for RELIANCE.",
        "[FACT] Volume was 14,500,234 shares.",
        "[FACT] 2 rows came back: 1. RELIANCE 2. TCS",
        "[INTERPRETATION] Based on the 52-week and SMA20 columns, momentum is firm.",
        "[FACT] TCS fell 1.08%.",
        "Data valid through 7 October 2026.",
    ],
)
def test_grounded_wording_passes(answer):
    assert grounding.check(answer, EVIDENCE, "close of RELIANCE").ok, grounding.check(answer, EVIDENCE).ungrounded


@pytest.mark.parametrize(
    ("answer", "bad"),
    [
        ("[FACT] RELIANCE closed at 1,250.5.", "1,250.5"),
        ("[CALCULATION] The return was 9.9%.", "9.9"),
        ("[FACT] Volume was 15,000,000.", "15,000,000"),
        ("[FACT] TCS is up 12 percent.", "12"),
        ("[FACT] PE ratio is 27.4.", "27.4"),
    ],
)
def test_invented_numbers_are_caught(answer, bad):
    verdict = grounding.check(answer, EVIDENCE)
    assert not verdict.ok and bad in verdict.ungrounded


def test_numbers_from_the_question_and_passages_count_as_grounded():
    assert grounding.check("You asked about 20 days.", {"rows": [], "row_count": 0}, "return over 20 days").ok
    passage = {
        "passages": [{"text": "RVol20 is volume divided by the 20-session average", "source": "x"}],
        "row_count": 1,
    }
    assert grounding.check("It divides by the average of 20 sessions.", passage).ok
    assert not grounding.check("It divides by the average of 30 sessions.", passage).ok


def test_precision_is_respected():
    ev = {"rows": [[0.1234]], "row_count": 1}
    assert grounding.check("12.34%", ev).ok
    assert grounding.check("12.3%", ev).ok
    assert not grounding.check("12.5%", ev).ok


# ---- knowledge retrieval ---------------------------------------------------------------------------------------
@pytest.fixture
def docs(tmp_path):
    (tmp_path / "strategies.md").write_text(
        "# Strategies\n\n## VCP\nA volatility contraction pattern: tightening ranges before a breakout.\n\n"
        "## ORB\nOpening range breakout uses the first 15 minute candle.\n\n## Risk\nStops protect capital.\n",
        encoding="utf-8",
    )
    (tmp_path / "metrics.yaml").write_text(
        "version: 1\nevents:\n  - name: vocal_distribution\n    description: heavy selling with weak breadth\n"
        "  - name: breadth_thrust\n    description: buying and participation broaden together\n",
        encoding="utf-8",
    )
    return KnowledgeIndex(roots=[(tmp_path, "*.md"), (tmp_path, "*.yaml")])


def test_acronym_questions_find_the_exact_section(docs):
    top = docs.search("What is VCP?", k=2)
    assert top[0]["heading"] == "VCP" and "contraction" in top[0]["text"]
    assert docs.search("explain ORB", k=1)[0]["heading"] == "ORB"


def test_yaml_lists_are_chunked_per_item(docs):
    hit = docs.search("what is vocal distribution", k=1)[0]
    assert "vocal_distribution" in hit["text"] and "breadth_thrust" not in hit["text"]


def test_unrelated_queries_return_nothing(docs):
    assert docs.search("quantum chromodynamics lagrangian", k=3) == []


# ---- provider client -------------------------------------------------------------------------------------------
def _config(**overrides) -> LLMConfig:
    base = {
        "providers": {
            "hosted": {"base_url": "https://example.test/v1", "key_env": "FAKE_KEY"},
            "backup": {"base_url": "https://backup.test/v1", "key_env": "FAKE_KEY"},
            "local": {"base_url": "http://localhost:11434/v1", "key_env": None, "local": True},
        },
        "roles": {
            "sql": {"provider": "hosted", "model": "m1", "json": True},
            "synth": {"provider": "hosted", "model": "m2"},
        },
        "fallbacks": {"sql": [{"provider": "backup", "model": "m1b"}]},
        "limits": {"max_calls_per_day": 100},
    }
    base.update(overrides)
    return LLMConfig.model_validate(base)


def _reply(text: str, usage=(11, 7)):
    msg = SimpleNamespace(content=text)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=msg)],
        usage=SimpleNamespace(prompt_tokens=usage[0], completion_tokens=usage[1]),
    )


class FakeSDK:
    """Mimics `OpenAI(...).chat.completions.create` and records the parameters it received."""

    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **params):
        self.calls.append(params)
        return self.behaviour(params)


@pytest.fixture(autouse=True)
def _isolated_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)


def test_success_returns_parsed_json_usage_and_logs_without_prompt_text():
    sdk = FakeSDK(lambda p: _reply('{"sql": "SELECT 1"}'))
    llm = OpenAICompatibleLLM(_config(), client_factory=lambda name: sdk)
    result = llm.chat("sql", [{"role": "user", "content": "super secret question"}])
    assert result.data == {"sql": "SELECT 1"} and (result.prompt_tokens, result.completion_tokens) == (11, 7)
    assert sdk.calls[0]["response_format"] == {"type": "json_object"} and sdk.calls[0]["model"] == "m1"
    log = (settings.logs_dir / "llm_calls.jsonl").read_text(encoding="utf-8")
    assert "super secret question" not in log and '"ok": true' in log


def test_falls_back_to_the_next_model_when_the_primary_fails():
    def behaviour_for(name):
        if name == "hosted":
            return FakeSDK(lambda p: (_ for _ in ()).throw(TimeoutError("slow")))
        return FakeSDK(lambda p: _reply('{"sql": "SELECT 2"}'))

    llm = OpenAICompatibleLLM(_config(), client_factory=behaviour_for)
    result = llm.chat("sql", [{"role": "user", "content": "q"}])
    assert result.provider == "backup" and result.model == "m1b"
    assert result.attempts and "TimeoutError" in result.attempts[0]


def test_bad_json_from_one_model_falls_through_to_the_next():
    sdks = {
        "hosted": FakeSDK(lambda p: _reply("I am sorry, I cannot")),
        "backup": FakeSDK(lambda p: _reply('{"ok": true}')),
    }
    llm = OpenAICompatibleLLM(_config(), client_factory=lambda name: sdks[name])
    assert llm.chat("sql", [{"role": "user", "content": "q"}]).provider == "backup"


def test_all_models_failing_raises_with_every_reason():
    sdk = FakeSDK(lambda p: (_ for _ in ()).throw(RuntimeError("down")))
    llm = OpenAICompatibleLLM(_config(), client_factory=lambda name: sdk)
    with pytest.raises(LLMError) as err:
        llm.chat("sql", [{"role": "user", "content": "q"}])
    assert "hosted/m1" in str(err.value) and "backup/m1b" in str(err.value)


def test_json_mode_retries_without_response_format_when_the_provider_rejects_it():
    def behaviour(params):
        if "response_format" in params:
            raise ValueError("response_format is not supported")
        return _reply('```json\n{"a": 1}\n```')

    sdk = FakeSDK(behaviour)
    llm = OpenAICompatibleLLM(_config(), client_factory=lambda name: sdk)
    assert llm.chat("sql", [{"role": "user", "content": "q"}]).data == {"a": 1}
    assert len(sdk.calls) == 2 and "response_format" not in sdk.calls[1]


def test_private_data_is_never_sent_to_hosted_models():
    sdk = FakeSDK(lambda p: _reply("hello"))
    llm = OpenAICompatibleLLM(_config(), client_factory=lambda name: sdk)
    with pytest.raises(LLMError):
        llm.chat("synth", [{"role": "user", "content": "my portfolio"}], private=True)
    assert sdk.calls == []
    assert issubclass(PrivacyError, LLMConfigError)

    cfg = _config(roles={"synth": {"provider": "local", "model": "qwen"}})
    local_sdk = FakeSDK(lambda p: _reply("fine"))
    assert (
        OpenAICompatibleLLM(cfg, client_factory=lambda name: local_sdk)
        .chat("synth", [{"role": "user", "content": "x"}], private=True)
        .text
        == "fine"
    )


def test_missing_api_key_is_a_clear_configuration_error(monkeypatch):
    monkeypatch.delenv("FAKE_KEY", raising=False)
    llm = OpenAICompatibleLLM(_config())  # real factory, no key in the environment
    with pytest.raises(LLMConfigError, match="FAKE_KEY"):
        llm.chat("synth", [{"role": "user", "content": "q"}])


def test_daily_call_limit_is_enforced():
    sdk = FakeSDK(lambda p: _reply("ok"))
    cfg = _config(limits={"max_calls_per_day": 2})
    llm = OpenAICompatibleLLM(cfg, client_factory=lambda name: sdk)
    llm.chat("synth", [{"role": "user", "content": "1"}])
    llm.chat("synth", [{"role": "user", "content": "2"}])
    with pytest.raises(LLMError, match="daily call limit"):
        llm.chat("synth", [{"role": "user", "content": "3"}])


def test_ping_reports_each_role():
    sdk = FakeSDK(lambda p: _reply('{"ok": true}' if "response_format" in p else "ok"))
    report = OpenAICompatibleLLM(_config(), client_factory=lambda name: sdk).ping()
    assert {r["role"] for r in report} == {"sql", "synth"} and all(r["ok"] for r in report)


# ---- configuration ---------------------------------------------------------------------------------------------
def test_shipped_config_is_valid_and_complete():
    config = load_config()
    assert {"router", "sql", "repair", "synth", "extract"} <= set(config.roles)
    for role in config.roles:
        assert config.chain(role)


def test_unknown_provider_references_are_rejected():
    bad = _config(roles={"sql": {"provider": "nope", "model": "x"}})
    with pytest.raises(LLMConfigError, match="unknown provider"):
        bad.validate_references()
    with pytest.raises(LLMConfigError):
        _config().chain("missing_role")


def test_extra_config_keys_are_rejected():
    with pytest.raises(ValueError):
        LLMConfig.model_validate({**json.loads(json.dumps({"providers": {}, "roles": {}})), "surprise": 1})


def test_llm_local_yaml_overrides_committed_config_only_when_enabled(tmp_path, monkeypatch):
    from nivesh.config import settings
    from nivesh.copilot.config import load_config

    base = tmp_path / "llm.yaml"
    base.write_text(
        "providers: {a: {base_url: 'http://a', key_env: null}}\nroles: {router: {provider: a, model: committed}}\n",
        encoding="utf-8",
    )
    (tmp_path / "llm.local.yaml").write_text(
        "providers: {b: {base_url: 'http://b', key_env: null, local: true}}\n"
        "roles: {router: {provider: b, model: local}}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "llm_config", base)
    monkeypatch.setattr(settings, "llm_local_override", True)
    assert load_config().roles["router"].model == "local"
    monkeypatch.setattr(settings, "llm_local_override", False)
    assert load_config().roles["router"].model == "committed"
    assert load_config(base).roles["router"].model == "committed"  # an explicit path is never overridden
