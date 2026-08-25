"""Packet P6: config/agents.yaml + engine/config.py — the tunable-parameters
surface for the five mesh agents (master doc §7), and the GET /config route
that surfaces it live in System Health.

What's actually asserted here, per the acceptance bar:
- the shipped config/agents.yaml matches every builtin default exactly
  (byte-identical behaviour when untouched)
- precedence: explicit arg > env (if set) > yaml (if present) > builtin,
  for perception.* and (env aside) for sentinel.*
- Sentinel really does change behaviour when built from a yaml that
  overrides it (the "actually WIRED" requirement)
- judgment tunables are architecturally impossible, not just discouraged
- GET /config serves all of the above
"""

import datetime as dt

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

import engine.config as agent_config
from engine.action.sentinel import (
    BACKOFF_MINUTES,
    CIRCUIT_BREAKER_THRESHOLD,
    LINK_OPEN_TIMEOUT_HOURS,
    MAX_RETRIES,
    Sentinel,
)
from engine.judgment.state_machine import HARD_STEP_CAP, MAX_TOUCHES_PER_WEEK

NOW = dt.datetime(2026, 8, 26, 12, 0, 0)


@pytest.fixture(autouse=True)
def isolated_config_cache():
    """Never let one test's tmp-path yaml leak into another test's
    load_config() call (memoisation is keyed by resolved path, but this
    keeps every test explicit about which config it's reading)."""
    agent_config.reset_cache()
    yield
    agent_config.reset_cache()


def write_yaml(tmp_path, data: dict, name: str = "agents.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Loading + defaults
# ---------------------------------------------------------------------------


def test_shipped_yaml_matches_every_builtin_default():
    """The acceptance bar: a fresh run with the shipped yaml must be
    byte-identical to today's behaviour. Every value in config/agents.yaml
    must equal the constant it shadows."""
    cfg = agent_config.load_config()  # default path: config/agents.yaml
    assert cfg.perception.provider == "heuristic"
    assert cfg.perception.ollama_model == "qwen2.5:7b"
    assert cfg.perception.ollama_base_url == "http://localhost:11434"
    assert cfg.perception.cache_enabled is True
    assert cfg.sentinel.max_retries == MAX_RETRIES
    assert cfg.sentinel.backoff_minutes == BACKOFF_MINUTES
    assert cfg.sentinel.link_open_timeout_hours == LINK_OPEN_TIMEOUT_HOURS
    assert cfg.sentinel.circuit_breaker_threshold == CIRCUIT_BREAKER_THRESHOLD
    assert cfg.auditor.sample_rate == 0.10
    assert cfg.auditor.quarantine_threshold == 0.85
    assert cfg.judgment.model_dump() == {}


def test_missing_file_resolves_to_all_builtin_defaults(tmp_path):
    cfg = agent_config.load_config(tmp_path / "does-not-exist.yaml")
    assert cfg.perception.provider is None
    assert cfg.sentinel.max_retries is None
    provider, source = agent_config.effective_perception_provider(cfg)
    assert (provider, source) == ("heuristic", "builtin")


def test_default_yaml_sentinel_is_identical_to_bare_constructor():
    """`Sentinel()` (every existing call site) vs a Sentinel built by the
    loader from the untouched, shipped yaml -- must behave identically."""
    bare = Sentinel()
    configured = agent_config.build_sentinel()
    assert configured.max_retries == bare.max_retries
    assert configured.backoff_schedule == bare.backoff_schedule
    assert configured.link_open_timeout_hours == bare.link_open_timeout_hours
    assert configured.circuit_breaker_threshold == bare.circuit_breaker_threshold


# ---------------------------------------------------------------------------
# Precedence: explicit arg > env (if set) > yaml (if present) > builtin
# ---------------------------------------------------------------------------


def test_perception_provider_precedence_env_over_yaml_over_builtin(tmp_path, monkeypatch):
    monkeypatch.delenv("PK_PERCEPTION_PROVIDER", raising=False)

    # 1. yaml unset entirely -> builtin
    empty_cfg = agent_config.load_config(write_yaml(tmp_path, {}), force_reload=True)
    assert agent_config.effective_perception_provider(empty_cfg) == ("heuristic", "builtin")

    # 2. yaml sets it -> yaml wins over builtin
    yaml_cfg = agent_config.load_config(
        write_yaml(tmp_path, {"perception": {"provider": "anthropic"}}, "yaml2.yaml"), force_reload=True
    )
    assert agent_config.effective_perception_provider(yaml_cfg) == ("anthropic", "yaml")

    # 3. env explicitly set -> env wins over yaml
    monkeypatch.setenv("PK_PERCEPTION_PROVIDER", "ollama")
    assert agent_config.effective_perception_provider(yaml_cfg) == ("ollama", "env")


def test_ollama_model_and_base_url_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("PK_OLLAMA_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)

    cfg = agent_config.load_config(
        write_yaml(tmp_path, {"perception": {"ollama_model": "qwen2.5:3b", "ollama_base_url": "http://box:9999"}}),
        force_reload=True,
    )
    assert agent_config.effective_ollama_model(cfg) == ("qwen2.5:3b", "yaml")
    assert agent_config.effective_ollama_base_url(cfg) == ("http://box:9999", "yaml")

    monkeypatch.setenv("PK_OLLAMA_MODEL", "llama3:8b")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://elsewhere:11434")
    assert agent_config.effective_ollama_model(cfg) == ("llama3:8b", "env")
    assert agent_config.effective_ollama_base_url(cfg) == ("http://elsewhere:11434", "env")


def test_cache_enabled_precedence(tmp_path, monkeypatch):
    monkeypatch.delenv("PK_PERCEPTION_CACHE", raising=False)

    builtin_cfg = agent_config.load_config(write_yaml(tmp_path, {}), force_reload=True)
    assert agent_config.effective_cache_enabled(builtin_cfg) == (True, "builtin")

    yaml_cfg = agent_config.load_config(
        write_yaml(tmp_path, {"perception": {"cache_enabled": False}}, "yaml2.yaml"), force_reload=True
    )
    assert agent_config.effective_cache_enabled(yaml_cfg) == (False, "yaml")

    monkeypatch.setenv("PK_PERCEPTION_CACHE", "1")
    assert agent_config.effective_cache_enabled(yaml_cfg) == (True, "env")


def test_cache_module_actually_honors_the_precedence(tmp_path, monkeypatch):
    """engine/perception/cache.py's own enabled(), not just the reporting
    function above -- this is the "actually wired" half of cache_enabled.
    cache.enabled() calls engine.config.load_config() with NO path
    argument (it reads THE app's config/agents.yaml, not a test fixture),
    so this test points DEFAULT_CONFIG_PATH at a tmp yaml rather than
    passing a path explicitly -- that's what makes it a fair test of the
    real call site instead of a different one."""
    from engine.perception import cache

    monkeypatch.delenv("PK_PERCEPTION_CACHE", raising=False)
    monkeypatch.setattr(
        agent_config, "DEFAULT_CONFIG_PATH",
        write_yaml(tmp_path, {"perception": {"cache_enabled": False}}),
    )
    agent_config.load_config(force_reload=True)
    assert cache.enabled() is False

    # explicit env still wins over the yaml, even though yaml says False
    monkeypatch.setenv("PK_PERCEPTION_CACHE", "1")
    assert cache.enabled() is True


# ---------------------------------------------------------------------------
# Sentinel: actually WIRED -- a changed yaml value changes Sentinel behaviour
# ---------------------------------------------------------------------------


def test_sentinel_honors_a_changed_max_retries_from_yaml(tmp_path):
    cfg = agent_config.load_config(
        write_yaml(tmp_path, {"sentinel": {"max_retries": 1}}), force_reload=True
    )
    s = agent_config.build_sentinel(cfg)
    assert s.max_retries == 1

    outcomes = [
        s.record_send_attempt("A-1", "INV-001", "mandate_offer", False, NOW, "err")
        for _ in range(2)
    ]
    # 1 retry allowed, then dead-letter on attempt 2 -- the default (3
    # retries) would still be "retry" at this point (see test_action_layer).
    assert outcomes == ["retry", "dead_letter"]
    assert len(s.dead_letter) == 1


def test_sentinel_honors_a_changed_backoff_schedule_from_yaml(tmp_path):
    cfg = agent_config.load_config(
        write_yaml(tmp_path, {"sentinel": {"backoff_minutes": [42]}}), force_reload=True
    )
    s = agent_config.build_sentinel(cfg)
    s.record_send_attempt("A-1", "INV-001", "message", False, NOW, "err")
    assert s.backoff_minutes("A-1") == 42  # default schedule would answer 1


def test_sentinel_honors_a_changed_link_timeout_from_yaml(tmp_path):
    cfg = agent_config.load_config(
        write_yaml(tmp_path, {"sentinel": {"link_open_timeout_hours": 1}}), force_reload=True
    )
    s = agent_config.build_sentinel(cfg)
    s.track_link_sent("A-1", NOW)
    # 2h would NOT time out under the default 48h window, but does under 1h
    assert s.link_timed_out("A-1", NOW + dt.timedelta(hours=2)) is True


def test_sentinel_honors_a_changed_circuit_breaker_threshold_from_yaml(tmp_path):
    cfg = agent_config.load_config(
        write_yaml(tmp_path, {"sentinel": {"circuit_breaker_threshold": 2}}), force_reload=True
    )
    s = agent_config.build_sentinel(cfg)
    s.record_send_attempt("A-1", "INV-001", "message", False, NOW, "err")
    assert s.should_pause_outbound() is False
    s.record_send_attempt("A-2", "INV-001", "message", False, NOW, "err")
    assert s.should_pause_outbound() is True  # default threshold (5) would still be closed


def test_sentinel_kwargs_falls_back_per_field_not_all_or_nothing(tmp_path):
    """Only max_retries overridden -- everything else must still be the
    module's own constant, not some other default."""
    cfg = agent_config.load_config(
        write_yaml(tmp_path, {"sentinel": {"max_retries": 7}}), force_reload=True
    )
    kwargs = agent_config.sentinel_kwargs(cfg)
    assert kwargs["max_retries"] == 7
    assert kwargs["backoff_minutes"] == BACKOFF_MINUTES
    assert kwargs["link_open_timeout_hours"] == LINK_OPEN_TIMEOUT_HOURS
    assert kwargs["circuit_breaker_threshold"] == CIRCUIT_BREAKER_THRESHOLD


# ---------------------------------------------------------------------------
# Law 4: judgment has no tunables, architecturally
# ---------------------------------------------------------------------------


def test_judgment_section_rejects_any_key(tmp_path):
    bad = write_yaml(tmp_path, {"judgment": {"MAX_TOUCHES_PER_WEEK": 99}})
    with pytest.raises(ValidationError):
        agent_config.load_config(bad, force_reload=True)


def test_judgment_section_empty_is_fine(tmp_path):
    ok = write_yaml(tmp_path, {"judgment": {}})
    cfg = agent_config.load_config(ok, force_reload=True)
    assert cfg.judgment.model_dump() == {}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_unknown_perception_provider_rejected(tmp_path):
    bad = write_yaml(tmp_path, {"perception": {"provider": "gpt-nope"}})
    with pytest.raises(ValidationError, match="perception.provider"):
        agent_config.load_config(bad, force_reload=True)


@pytest.mark.parametrize("bad_schedule", [[], [0], [-5, 10], [1, "x"]])
def test_backoff_minutes_rejects_invalid_lists(tmp_path, bad_schedule):
    bad = write_yaml(tmp_path, {"sentinel": {"backoff_minutes": bad_schedule}})
    with pytest.raises(ValidationError):
        agent_config.load_config(bad, force_reload=True)


def test_non_mapping_yaml_top_level_raises(tmp_path):
    path = tmp_path / "list.yaml"
    path.write_text("- 1\n- 2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a YAML mapping"):
        agent_config.load_config(path, force_reload=True)


# ---------------------------------------------------------------------------
# Bounds snapshot: read live from state_machine.py, never hand-duplicated
# ---------------------------------------------------------------------------


def test_bounds_snapshot_matches_state_machine_constants():
    snap = agent_config.bounds_snapshot()
    by_name = {row["name"]: row["value"] for row in snap["numeric"]}
    assert by_name["MAX_TOUCHES_PER_WEEK"] == MAX_TOUCHES_PER_WEEK
    assert by_name["HARD_STEP_CAP"] == HARD_STEP_CAP
    assert len(snap["policy"]) == 3


# ---------------------------------------------------------------------------
# GET /config
# ---------------------------------------------------------------------------


def test_get_config_route_shape_and_values():
    from api.main import app

    with TestClient(app) as c:
        r = c.get("/config")
        assert r.status_code == 200
        body = r.json()

        assert body["config"]["perception"]["provider"] == "heuristic"
        assert body["effective"]["perception"]["provider"]["value"] == "heuristic"
        assert body["effective"]["sentinel"]["max_retries"] == MAX_RETRIES

        bound_names = {row["name"] for row in body["bounds"]["numeric"]}
        assert "MANDATE_AMOUNT_CAP" in bound_names
        assert len(body["bounds"]["policy"]) == 3

        assert set(body["wiring_notes"]) == {
            "sentinel", "perception_cache_enabled", "perception_provider_model_base_url",
            "auditor", "judgment",
        }

        live = body["live_status"]
        assert live["runtime_provider"] == "heuristic"
        assert live["runtime_model"] is None  # heuristic has no model
        assert live["ollama_fallback_events"] >= 0
        assert live["sentinel_dead_letter_count"] >= 0
        assert set(live["cache_stats"]) == {"hits", "misses", "writes"}


def test_get_config_route_is_read_only_get_only():
    from api.main import app

    with TestClient(app) as c:
        assert c.post("/config", json={}).status_code == 405
