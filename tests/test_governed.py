"""H3: governed writes (a module's fleet and fleet-node keys, through `module config set --non-interactive`), `validate`
and `doctor`. The unlock agent needs a terminal to unlock, so the signed path is driven by a stub core that answers as the
core does (a signed set, or exit 5); the locked path also runs against the real core."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import stubcore
from conftest import HCONFIG
from test_set import audit_lines, run, writes

DEV_A, DEV_B = "k1:0123456789abcdef", "k1:fedcba9876543210"      # `hv whoami` in the stub says this device is A


def answers():
    a = stubcore.standard_answers()
    a["hive-mind module config --non-interactive --fleet -- alpha set limit *"] = {"out": "set x-alpha:limit = 8\nalpha: set limit (fleet)\n"}
    a["hive-mind module config --non-interactive --fleet -- alpha set operator.name *"] = {"out": "alpha: set operator.name (fleet)\n"}
    for dev in (DEV_A, DEV_B):
        a[f"hive-mind module config --non-interactive --fleet-node {dev} -- alpha set seat.dev.enabled *"] = {
            "out": "alpha: set seat.dev.enabled (fleet-node)\n"}
    return a


@pytest.fixture
def stub(tmp_path):
    return stubcore.install(tmp_path, answers(), HIVE_HOME=str(tmp_path / "hive"), HIVE_OWNER_KEY_PASSPHRASE="pp",
                            XDG_RUNTIME_DIR=str(tmp_path / "xdg")), tmp_path / "hive"


def stub_with(tmp_path, mutate, **env):
    a = answers()
    mutate(a)
    return stubcore.install(tmp_path, a, HIVE_HOME=str(tmp_path / "hive"), **env)


# ── governed writes ───────────────────────────────────────────────────────────────────────────────

def test_a_fleet_set_runs_the_core_verb_non_interactively_and_is_audited(stub):
    (env, calls), state = stub
    r = run("set", "x-alpha:limit", "8", "--json", env=env)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["changed"] is True
    w = writes(calls)
    assert [" ".join(c["argv"]) for c in w] == ["hive-mind module config --non-interactive --fleet -- alpha set limit 8"]
    assert w[0]["stdin"] == "" and not {"HIVE_OWNER_KEY_PASSPHRASE"} & set(w[0]["env"])      # never a prompt, never the passphrase
    line = audit_lines(state)[-1]
    assert (line["id"], line["class"], line["old"], line["new"], line["ok"]) == ("x-alpha:limit", "governed", None, "8", True)


@pytest.mark.parametrize("scope, layer, extra", (("fleet", "--fleet", ()), ("fleet-node", f"--fleet-node {DEV_A}", ("--device", DEV_A))))
def test_a_secret_fleet_key_reaches_the_core_on_stdin_and_nowhere_else(tmp_path, scope, layer, extra):
    secret = "fleet-secret-value-31"

    def mutate(a):
        sch = dict(stubcore.ALPHA_SCHEMA, **{"vault": {"type": "string", "scope": scope, "description": "v", "secret": True}})
        a["hive-mind module config alpha schema --json"] = {"out": sch}
        a["hive-mind module config alpha list --json"] = {"out": stubcore.ALPHA_ROWS + [
            {"key": "vault", "value": "********", "source": "default", "secret": True, "set": False}]}
        a[f"hive-mind module config --non-interactive {layer} --stdin -- alpha set vault"] = {
            "out": f"alpha: set vault ({scope})\n"}
    env, calls = stub_with(tmp_path, mutate)
    r = run("set", "x-alpha:vault", "--stdin", "--json", *extra, env=env, stdin=secret + "\n")
    assert r.returncode == 0 and secret not in r.stdout + r.stderr, (r.stdout, r.stderr)
    w = writes(calls)
    assert len(w) == 1 and w[0]["stdin"] == secret + "\n"
    assert all(secret not in " ".join(c["argv"]) for c in calls())
    assert all(secret not in json.dumps(c["env"]) for c in calls())
    assert secret not in (tmp_path / "hive" / "hconfig" / "audit.jsonl").read_text()


def test_a_fleet_set_to_the_schema_default_is_signed_and_one_equal_to_the_journal_value_is_not(stub, tmp_path):
    (env, calls), _ = stub
    assert json.loads(run("set", "x-alpha:limit", "5", "--json", env=env).stdout)["changed"] is True     # source default: signed
    assert len(writes(calls)) == 1

    def fleet_value(a):
        a["hive-mind module config alpha list --json"] = {"out": [dict(r, value="8", source="fleet") if r["key"] == "limit" else r
                                                                  for r in stubcore.ALPHA_ROWS]}
    env2, calls2 = stub_with(tmp_path / "b", fleet_value) if (tmp_path / "b").mkdir() is None else None
    out = json.loads(run("set", "x-alpha:limit", "8", "--json", env=env2).stdout)
    assert out["changed"] is False and writes(calls2) == []                 # the journal already holds it: nothing runs


def test_a_fleet_node_set_for_another_device_is_signed_even_if_it_equals_this_devices_value(stub):
    (env, calls), state = stub
    # `seat.dev.enabled` is `true` for this device (A). Setting `true` for device B is a different journal key.
    r = run("set", "x-alpha:seat.dev.enabled", "true", "--device", DEV_B, "--json", env=env)
    assert r.returncode == 0 and json.loads(r.stdout)["changed"] is True
    assert [" ".join(c["argv"][3:7]) for c in writes(calls)] == [f"--non-interactive --fleet-node {DEV_B} --"]
    # and for this device the same value is the journal's own entry: nothing runs
    r = run("set", "x-alpha:seat.dev.enabled", "true", "--json", env=env)
    assert json.loads(r.stdout)["changed"] is False and len(writes(calls)) == 1
    r = run("set", "x-alpha:seat.dev.enabled", "false", "--json", env=env)
    assert json.loads(r.stdout)["changed"] is True and f"--fleet-node {DEV_A}" in " ".join(writes(calls)[-1]["argv"])
    assert [l["id"] for l in audit_lines(state)] == ["x-alpha:seat.dev.enabled"] * 2


def test_when_the_core_signs_nothing_the_result_is_unchanged_and_unaudited(tmp_path):
    def same(a):
        a[f"hive-mind module config --non-interactive --fleet-node {DEV_B} -- alpha set seat.dev.enabled *"] = {
            "out": "alpha: seat.dev.enabled is already set (fleet-node): nothing signed\n"}
    env, calls = stub_with(tmp_path, same)
    out = json.loads(run("set", "x-alpha:seat.dev.enabled", "true", "--device", DEV_B, "--json", env=env).stdout)
    assert out["changed"] is False and len(writes(calls)) == 1 and audit_lines(tmp_path / "hive") == []


@pytest.mark.parametrize("key, value, extra", (("limit", "8", ()), ("seat.dev.enabled", "false", ("--device", DEV_B))))
def test_a_locked_or_unwilling_agent_is_owner_locked_exit_five_and_names_the_module_and_socket_dir(stub, tmp_path, key, value, extra):
    (_, _), state = stub
    flag = ["--fleet"] if not extra else ["--fleet-node", DEV_B]

    def locked(a):
        a[" ".join(["hive-mind module config --non-interactive", *flag, "-- alpha set", key, "*"])] = {
            "rc": 5, "err": "hive-mind module: the owner key is locked: run `hive-mind owner unlock`\n"}
    env, calls = stub_with(tmp_path / "l", locked, XDG_RUNTIME_DIR="/run/user/4242") if (tmp_path / "l").mkdir() is None else None
    r = run("set", f"x-alpha:{key}", value, *extra, "--json", env=env)
    out = json.loads(r.stdout)
    assert r.returncode == 5 and out["error"]["code"] == "owner-locked" and out["ok"] is False
    assert "alpha" in out["error"]["message"] and "owner unlock --module alpha" in out["error"]["hint"]
    assert "/run/user/4242/hive-mind" in out["error"]["hint"]              # where the child looked for the agent
    assert len(writes(calls)) == 1 and audit_lines(tmp_path / "l" / "hive")[-1]["error"] == {"code": "owner-locked"}
    t = run("set", f"x-alpha:{key}", value, *extra, env=env)
    assert t.returncode == 5 and "owner unlock --module alpha" in t.stderr


def test_a_core_key_and_a_fleet_unset_start_no_write_and_the_core_set_verb_is_never_spawned(stub):
    (env, calls), _ = stub
    r = run("set", "core:cap_self", "0.3", "--json", env=env)
    out = json.loads(r.stdout)
    assert r.returncode == 1 and out["error"]["code"] == "owner-only"
    assert out["error"]["hint"] == "hive-mind config set cap_self <value>"
    assert run("unset", "x-alpha:limit", "--json", env=env).returncode == 1
    assert writes(calls) == [] and not any(c["argv"][:3] == ["hive-mind", "config", "set"] for c in calls())


def test_an_identifying_key_warns_on_write(stub):
    (env, calls), _ = stub
    out = json.loads(run("set", "x-alpha:operator.name", "Someone", "--json", env=env).stdout)
    assert out["changed"] is True and any("journaled" in w for w in out["warnings"])
    assert any("journaled" in w for w in out["key"]["warnings"])
    assert "warning" in run("get", "x-alpha:operator.name", env=env).stdout


def test_device_is_only_for_fleet_node_keys_and_must_be_a_device_id(stub):
    (env, calls), _ = stub
    for argv in (("set", "x-alpha:limit", "8", "--device", DEV_B), ("set", "x-alpha:seat.dev.enabled", "true", "--device", "nope"),
                 ("set", "x-alpha:greeting", "x", "--device", DEV_B)):
        r = run(*argv, "--json", env=env)
        assert r.returncode == 1 and json.loads(r.stdout)["error"]["code"] == "validation", argv
    assert writes(calls) == []


def test_a_locked_agent_through_the_real_core_is_exit_five_and_changes_nothing(whive):
    before = whive.core_run("module", "config", "alpha", "get", "limit", "--json").stdout
    # the operator's environment has the owner passphrase; hconfig removes it, so the core cannot sign without an agent
    r = whive.hconfig("set", "x-alpha:limit", "3", "--json")
    out = json.loads(r.stdout)
    assert r.returncode == 5 and out["error"]["code"] == "owner-locked" and "alpha" in out["error"]["message"]
    assert whive.core_run("module", "config", "alpha", "get", "limit", "--json").stdout == before
    r = whive.hconfig("set", "x-alpha:seat.dev.enabled", "false", "--device", "k1:0123456789abcdef", "--json")
    assert r.returncode == 5
    assert whive.hconfig("set", "core:cap_self", "0.1").returncode == 1


# ── validate ──────────────────────────────────────────────────────────────────────────────────────

def test_validate_is_the_cores_validator_per_module_plus_the_node_keys(tmp_path):
    def broken(a):
        a["hive-mind module config alpha validate --json"] = {"out": {
            "ok": False, "errors": ["required key token has no value"], "warnings": ["node config bogus: not in the schema"]}}
        a["hv config node list --json"] = {"out": {"schema": 1, "keys": [
            dict(r, value=99999, source="node") if r["id"] == "port" else r for r in stubcore.NODE_ROWS]}}
    env, calls = stub_with(tmp_path, broken)
    r = run("validate", "--json", env=env)
    out = json.loads(r.stdout)
    assert r.returncode == 1 and out["schema"] == 1 and out["ok"] is False
    assert {(e["id"], e["message"]) for e in out["errors"]} == {("x-alpha", "required key token has no value"),
                                                                 ("node:port", "outside 1..65535")}
    assert out["warnings"] == [{"id": "x-alpha", "message": "node config bogus: not in the schema"}]
    assert "99999" not in r.stdout
    t = run("validate", env=env)
    assert t.returncode == 1 and "error: x-alpha: required key token" in t.stdout
    one = json.loads(run("validate", "alpha", "--json", env=env).stdout)
    assert {e["id"] for e in one["errors"]} == {"x-alpha"}                  # one module: no node keys
    gone = run("validate", "nope", "--json", env=env)
    assert gone.returncode == 1 and json.loads(gone.stdout)["error"]["code"] == "not-installed"


def test_validate_with_nothing_wrong_is_ok_exit_zero(stub):
    (env, _), _ = stub
    r = run("validate", "--json", env=env)
    assert r.returncode == 0 and json.loads(r.stdout) == {"schema": 1, "ok": True, "errors": [], "warnings": []}
    assert run("validate", env=env).stdout.strip() == "ok"


def test_validate_through_the_real_core(whive):
    code, out = whive.json("validate")
    assert out["schema"] == 1 and out["errors"] == [] and code == 0


# ── doctor ────────────────────────────────────────────────────────────────────────────────────────

def doctor(env):
    r = run("doctor", "--json", env=env)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["schema"] == 1 and len(out["results"]) <= 100
    assert all(set(x) - {"fix_hint"} == {"level", "check", "message"} and x["level"] in ("ok", "warn", "fail") for x in out["results"])
    assert all(len(x["message"]) <= 400 for x in out["results"])
    return {x["check"]: x for x in out["results"]}


def make_hive(tmp_path, key_dir=None):
    home = tmp_path / "hive"
    home.mkdir(exist_ok=True)
    if key_dir:
        (home / ".key-dir").write_text(key_dir)
    return home


def clean_env(tmp_path, mutate=None, **extra):
    a = answers()
    if mutate:
        mutate(a)
    return stubcore.install(tmp_path, a, HIVE_HOME=str(tmp_path / "hive"), TMPDIR=str(tmp_path / "tmp"),
                            XDG_RUNTIME_DIR=str(tmp_path / "xdg"), **extra)[0]


def test_a_clean_node_is_all_ok(tmp_path):
    make_hive(tmp_path)

    def no_env(a):                       # the standard stub core has `bind` coming from the environment
        a["hv config node list --json"] = {"out": {"schema": 1, "keys": [dict(r, source="default", env=None)
                                                                         for r in stubcore.NODE_ROWS]}}
    rows = doctor(clean_env(tmp_path, no_env))
    assert {r["level"] for r in rows.values()} == {"ok"}
    assert set(rows) >= {"core-verbs", "config", "env-override", "governed-range", "key-dir", "agent-socket-dir",
                         "audit-file", "node-file-audit-gap", "peers-reread"}


def test_doctor_key_dir_mismatch_rows(tmp_path):
    make_hive(tmp_path, "/keys/one")
    assert doctor(clean_env(tmp_path, HIVE_KEY_DIR="/keys/one"))["key-dir"]["level"] == "ok"
    assert doctor(clean_env(tmp_path))["key-dir"]["level"] == "ok"                         # unset: nothing to differ
    bad = doctor(clean_env(tmp_path, HIVE_KEY_DIR="/keys/two"))["key-dir"]
    assert bad["level"] == "warn" and "different key directory" in bad["message"]
    (tmp_path / "hive" / ".key-dir").unlink()
    nofile = doctor(clean_env(tmp_path, HIVE_KEY_DIR="/keys/two"))["key-dir"]
    assert nofile["level"] == "warn" and "no .key-dir" in nofile["message"]


def test_doctor_agent_socket_dir_row_sees_a_socket_the_process_would_not_look_for(tmp_path):
    """The agent was unlocked in a terminal with no XDG_RUNTIME_DIR (socket under TMPDIR); this process has XDG set."""
    import hashlib
    home = make_hive(tmp_path)
    name = "owner-agent-" + hashlib.sha256(os.path.realpath(home).encode()).hexdigest()[:12] + ".sock"
    tmpd = tmp_path / "tmp" / f"hive-mind-{os.getuid()}"
    tmpd.mkdir(parents=True)
    (tmpd / name).write_text("")
    with_xdg = doctor(clean_env(tmp_path))["agent-socket-dir"]
    assert with_xdg["level"] == "warn" and str(tmpd) in with_xdg["message"] and "XDG_RUNTIME_DIR" in with_xdg["fix_hint"]
    env = clean_env(tmp_path)
    env.pop("XDG_RUNTIME_DIR")                              # the other process: it looks in TMPDIR and finds it
    assert doctor(env)["agent-socket-dir"]["level"] == "ok"
    xdg = tmp_path / "xdg" / "hive-mind"
    xdg.mkdir(parents=True)
    (xdg / name).write_text("")
    assert doctor(clean_env(tmp_path))["agent-socket-dir"]["level"] == "ok"                 # where this process looks


def test_doctor_env_override_governed_range_and_unknown_key_rows(tmp_path):
    make_hive(tmp_path)

    def trouble(a):
        a["hv config show --json"] = {"out": {"schema": 1, "governance": stubcore.GOVERNANCE, "governed": [
            dict(r, value=-3, source="fleet") if r["id"] == "quorum_m" else r for r in stubcore.SHOW_ROWS]}}
        a["hive-mind module config alpha validate --json"] = {"out": {
            "ok": True, "errors": [], "warnings": ["node config bogus: not in the schema (kept, unused)"]}}
    env, _ = stub_with(tmp_path, trouble)
    rows = doctor(env)
    assert rows["env-override"]["level"] == "warn" and "HIVE_BIND" in rows["env-override"]["message"]     # stub: bind is env
    assert rows["governed-range"]["level"] == "warn" and "core:quorum_m" in rows["governed-range"]["message"]
    assert rows["config-unknown-key"]["level"] == "warn" and "x-alpha" in rows["config-unknown-key"]["message"]
    assert "-3" not in json.dumps(rows)                       # no value in any message


def test_doctor_required_key_unset_and_invalid_node_value_are_reported(tmp_path):
    make_hive(tmp_path)

    def trouble(a):
        a["hive-mind module config alpha validate --json"] = {"out": {"ok": False, "errors": ["required key token has no value"],
                                                                      "warnings": []}}
    rows = doctor(stub_with(tmp_path, trouble)[0])
    assert rows["config-invalid"]["level"] == "warn" and "token" in rows["config-invalid"]["message"]


def test_doctor_audit_file_mode_and_node_file_gap_and_peers_reread(tmp_path):
    home = make_hive(tmp_path)
    state = home / "hconfig"
    state.mkdir(mode=0o755)
    state.chmod(0o755)
    audit = state / "audit.jsonl"
    audit.write_text(json.dumps({"ts": "2020-01-01T00:00:00Z", "id": "node:port"}) + "\n")
    audit.chmod(0o644)
    peers = home / ".peers.json"
    peers.write_text("{}")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "systemctl").write_text("#!/bin/sh\necho 'Wed 2020-01-01 00:00:00 UTC'\n")
    (fake / "systemctl").chmod(0o755)
    env = clean_env(tmp_path, PATH=f"{fake}:{os.environ['PATH']}")
    rows = doctor(env)
    assert rows["audit-file"]["level"] == "warn" and "audit.jsonl" in rows["audit-file"]["message"]
    assert rows["node-file-audit-gap"]["level"] == "warn" and "edited directly" in rows["node-file-audit-gap"]["message"]
    assert rows["peers-reread"]["level"] == "warn" and "restart" in rows["peers-reread"]["fix_hint"]
    (fake / "systemctl").write_text("#!/bin/sh\necho 'Wed 2099-01-01 00:00:00 UTC'\n")      # a daemon started after the change
    audit.write_text(json.dumps({"ts": "2099-01-01T00:00:00Z", "id": "node:port"}) + "\n")
    state.chmod(0o700)
    audit.chmod(0o600)
    rows = doctor(env)
    assert {rows[c]["level"] for c in ("audit-file", "node-file-audit-gap", "peers-reread")} == {"ok"}


def test_doctor_stays_inside_the_contracts_shape_when_the_core_is_missing(tmp_path):
    r = run("doctor", "--json", env={"PATH": "/nonexistent", "HOME": str(tmp_path)})
    out = json.loads(r.stdout)
    assert r.returncode == 0 and [x["level"] for x in out["results"]] == ["warn"] and out["results"][0]["check"] == "core-verbs"


def test_doctor_through_the_real_core(whive):
    r = whive.hconfig("doctor", "--json")
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["schema"] == 1 and out["results"][0] == {
        "level": "ok", "check": "core-verbs", "message": "the core's config verbs answer"}
    assert not [x for x in out["results"] if x["check"] in ("config-invalid", "governed-range", "env-override", "audit-file")
                and x["level"] != "ok"]
