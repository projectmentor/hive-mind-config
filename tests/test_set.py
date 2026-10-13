"""H2: `set` and `unset` for node: keys and node-scope module keys. The write goes through the core's own verb; these
tests pin what hconfig refuses before it starts one, what it never puts on an argv, and what it leaves behind."""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import stubcore
from conftest import HCONFIG, TOKEN

OWNER_VARS = ("HIVE_OWNER_PASSPHRASE", "HIVE_OWNER_KEY_PASSPHRASE", "HIVE_OWNER_SEED")
WRITE_VERBS = ("set", "unset")
SECRET = "s3cr3t-never-on-argv"


def run(*args, env, stdin=None):
    return subprocess.run([sys.executable, "-I", str(HCONFIG), *args], env=env, capture_output=True, text=True,
                          input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL)


def answers_with_writes():
    a = stubcore.standard_answers()
    a["hv config node set --json -- port *"] = {"out": {"schema": 1, "ok": True, "note": "restart the daemon"}}
    a["hv config node set --json -- dashboard_share *"] = {"out": {"schema": 1, "ok": True}}
    a["hive-mind module config --non-interactive -- alpha set greeting *"] = {"out": "alpha: set greeting\n"}
    a["hive-mind module config --non-interactive -- alpha set mode *"] = {"out": "alpha: set mode\n"}
    a["hive-mind module config alpha set token --stdin --non-interactive"] = {"out": "alpha: set token\n"}
    a["hive-mind module config alpha unset greeting"] = {"out": "alpha: unset greeting\n"}
    return a


@pytest.fixture
def stub(tmp_path):
    state = tmp_path / "hive"
    return stubcore.install(tmp_path, answers_with_writes(), HIVE_HOME=str(state), HIVE_OWNER_KEY_PASSPHRASE="pp",
                            HIVE_OWNER_PASSPHRASE="pp", HIVE_OWNER_SEED="00" * 32, HIVE_KEY_DIR="/some/keys"), state


def writes(calls):
    """The calls that could change something: a `set` or `unset` of the core's node or module config verbs."""
    return [c for c in calls() if any(a in ("set", "unset") for a in c["argv"][1:8])]


def audit_lines(state):
    p = Path(state) / "hconfig" / "audit.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


# ── refused before any write verb starts ──────────────────────────────────────────────────────────

def test_a_secret_on_argv_is_refused_before_any_write_and_the_value_is_nowhere(stub):
    (env, calls), state = stub
    r = run("set", "x-alpha:token", SECRET, "--json", env=env)
    out = json.loads(r.stdout)
    assert r.returncode == 1 and out["error"]["code"] == "secret-on-argv"
    assert SECRET not in r.stdout + r.stderr
    assert writes(calls) == [] and all(SECRET not in json.dumps(c) for c in calls())
    assert audit_lines(state) == []


def test_the_secret_reaches_the_core_on_stdin_only(stub):
    (env, calls), state = stub
    r = run("set", "x-alpha:token", "--stdin", "--json", env=env, stdin=SECRET + "\n")
    assert r.returncode == 0 and SECRET not in r.stdout + r.stderr
    w = writes(calls)
    assert len(w) == 1 and w[0]["stdin"] == SECRET + "\n"
    assert all(SECRET not in " ".join(c["argv"]) for c in calls())            # the argv of every call, read or write
    assert all(SECRET not in json.dumps(c["environ"]) for c in calls())        # nor the environment
    line = audit_lines(state)[-1]
    assert line["id"] == "x-alpha:token" and "old" not in line and "new" not in line and SECRET not in json.dumps(line)


def test_owner_only_keys_start_no_write_and_name_the_command(stub):
    (env, calls), state = stub
    for id_, via in (("core:cap_self", "hive-mind config set cap_self <value>"),
                     ("x-alpha:limit", "hive-mind module config alpha set limit <value> --fleet"),
                     ("x-alpha:seat.dev.enabled", "--fleet-node <device>")):
        r = run("set", id_, "1", "--json", env=env)
        out = json.loads(r.stdout)
        assert r.returncode == 1 and out["error"]["code"] == "owner-only" and via in out["error"]["hint"], id_
    n = len(calls())
    r = run("set", "node:owner_quota", "5", "--json", env=env)
    assert json.loads(r.stdout)["error"]["code"] == "owner-only" and "module quota" in json.loads(r.stdout)["error"]["hint"]
    assert len(calls()) == n                                  # not even a read: it is refused on the name alone
    assert writes(calls) == [] and not any(c["argv"][:3] == ["hive-mind", "config", "set"] for c in calls())
    assert audit_lines(state) == []


def test_validation_refuses_before_a_signer_or_a_write_and_leaves_no_audit_line(stub):
    (env, calls), state = stub
    cases = (("node:port", "99999"), ("node:port", "12x"), ("node:port", "1.5"), ("node:dashboard_share", "maybe"),
             ("x-alpha:mode", "zz"), ("x-alpha:greeting", "bad\x07value"), ("x-alpha:greeting", "x" * 5000),
             ("x-alpha:seat.{role}.enabled", "true"))
    for id_, value in cases:
        r = run("set", id_, value, "--json", env=env)
        out = json.loads(r.stdout)
        assert r.returncode == 1 and out["error"]["code"] == "validation", (id_, value)
        assert value not in r.stdout + r.stderr or len(value) < 3
    assert writes(calls) == [] and audit_lines(state) == []


def test_typed_refusals_unknown_not_installed_env_and_unset_of_a_node_key(stub):
    (env, calls), state = stub
    for argv, code in ((("set", "core:nope", "1"), "unknown-key"), (("set", "x-alpha:nope", "1"), "unknown-key"),
                       (("set", "x-gamma:k", "1"), "not-installed"), (("set", "node:bind", "100.64.0.2"), "env-overridden"),
                       (("unset", "node:port"), "read-only"), (("unset", "core:cap_self"), "owner-only")):
        r = run(*argv, "--json", env=env)
        out = json.loads(r.stdout)
        assert r.returncode == 1 and out["error"]["code"] == code and out["ok"] is False, argv
    assert writes(calls) == []


def test_usage_errors_exit_two(stub):
    (env, calls), _ = stub
    for argv, stdin in ((("set", "x-alpha:greeting"), None), (("set", "x-alpha:greeting", "a", "--stdin"), "b\n"), (("set",), None)):
        assert run(*argv, env=env, stdin=stdin).returncode == 2, argv
    assert writes(calls) == []


# ── what the write verb is and how it is started ──────────────────────────────────────────────────

def test_the_write_verbs_are_exactly_the_core_node_and_module_config_verbs(stub):
    (env, calls), _ = stub
    assert run("set", "node:port", "9001", env=env).returncode == 0
    assert run("set", "x-alpha:greeting", "hello", env=env).returncode == 0
    assert run("unset", "x-alpha:greeting", env=env).returncode == 0           # `greeting` is in the node config in the stub
    assert [" ".join(c["argv"]) for c in writes(calls)] == [
        "hv config node set --json -- port 9001",
        "hive-mind module config --non-interactive -- alpha set greeting hello",
        "hive-mind module config alpha unset greeting"]


def test_a_value_starting_with_a_dash_cannot_become_an_option(stub):
    (env, calls), _ = stub
    assert run("set", "x-alpha:greeting", "--", "-rf", env=env).returncode == 0
    assert writes(calls)[0]["argv"][-5:] == ["--", "alpha", "set", "greeting", "-rf"]


def test_the_write_child_has_no_owner_variables_and_keeps_the_key_dir(stub):
    (env, calls), _ = stub
    assert all(v in env for v in OWNER_VARS)
    assert run("set", "node:port", "9001", env=env).returncode == 0
    w = writes(calls)
    assert len(w) == 1
    assert not set(OWNER_VARS) & set(w[0]["env"])
    assert w[0]["environ"]["HIVE_KEY_DIR"] == "/some/keys"      # a path: dropping it would give the child another key dir
    assert w[0]["stdin"] == ""


def test_a_node_write_compares_the_node_layer_not_the_merged_value(stub):
    (env, calls), state = stub
    # `port` is at its default (9876) in the node list: writing 9876 is a write, because the node file has no entry.
    r = run("set", "node:port", "9876", "--json", env=env)
    assert r.returncode == 0 and json.loads(r.stdout)["changed"] is True and len(writes(calls)) == 1
    # `dashboard_share` is `off` by default and not in the node file: writing `off` is still a write.
    r = run("set", "node:dashboard_share", "off", "--json", env=env)
    assert json.loads(r.stdout)["changed"] is True and len(writes(calls)) == 2
    # `x-alpha:greeting` is `yo` in the module's node config: writing `yo` is a true no-op.
    r = run("set", "x-alpha:greeting", "yo", "--json", env=env)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["changed"] is False and len(writes(calls)) == 2
    # `x-alpha:mode` is `a` only by default: writing `a` writes the node layer.
    r = run("set", "x-alpha:mode", "a", "--json", env=env)
    assert json.loads(r.stdout)["changed"] is True and len(writes(calls)) == 3
    assert [l["id"] for l in audit_lines(state)] == ["node:port", "node:dashboard_share", "x-alpha:mode"]   # no-op: no line


def test_a_concrete_key_under_a_node_scope_template_can_be_set_before_it_exists(tmp_path):
    a = answers_with_writes()
    a["hive-mind module config alpha schema --json"] = {"out": {**stubcore.ALPHA_SCHEMA, "seat.{role}.fleet": {
        "type": "string", "scope": "node", "description": "d", "default": "off"}}}
    a["hive-mind module config --non-interactive -- alpha set seat.qa.fleet *"] = {"out": "ok\n"}
    env, calls = stubcore.install(tmp_path, a, HIVE_HOME=str(tmp_path / "hive"))
    r = run("set", "x-alpha:seat.qa.fleet", "on", "--json", env=env)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["changed"] is True and out["key"]["id"] == "x-alpha:seat.qa.fleet"
    assert [w["argv"][-4:] for w in writes(calls)] == [["alpha", "set", "seat.qa.fleet", "on"]]


def test_a_failing_write_is_core_failed_audited_and_carries_no_value(tmp_path):
    a = answers_with_writes()
    a["hive-mind module config --non-interactive -- alpha set greeting *"] = {
        "rc": 1, "err": f"hive-mind module: could not write value={SECRET}\n"}
    env, calls = stubcore.install(tmp_path, a, HIVE_HOME=str(tmp_path / "hive"))
    r = run("set", "x-alpha:greeting", SECRET, "--json", env=env)
    out = json.loads(r.stdout)
    assert r.returncode == 1 and out["error"]["code"] == "core-failed" and SECRET not in r.stdout + r.stderr
    line = audit_lines(tmp_path / "hive")[-1]
    assert line["ok"] is False and line["error"] == {"code": "core-failed"} and line["old"] == "yo"


# ── the lock and the audit file ───────────────────────────────────────────────────────────────────

def test_two_concurrent_sets_serialize_on_one_lock(tmp_path):
    a = answers_with_writes()
    a["hv config node set --json -- port *"] = {"out": {"ok": True}, "sleep": 0.6}
    a["hive-mind module config --non-interactive -- alpha set greeting *"] = {"out": "ok\n", "sleep": 0.6}
    env, calls = stubcore.install(tmp_path, a, HIVE_HOME=str(tmp_path / "hive"))
    procs = [subprocess.Popen([sys.executable, "-I", str(HCONFIG), "set", i, v, "--json"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
             for i, v in (("node:port", "9001"), ("x-alpha:greeting", "hi"))]
    for p in procs:
        out, err = p.communicate(timeout=60)
        assert p.returncode == 0, err
    w = sorted(writes(calls), key=lambda c: c["t0"])
    assert len(w) == 2 and w[0]["t1"] <= w[1]["t0"]           # the second write began after the first ended
    assert len(audit_lines(tmp_path / "hive")) == 2


def test_the_state_directory_and_audit_file_are_private(stub):
    (env, calls), state = stub
    assert run("set", "node:port", "9001", env=env).returncode == 0
    d, f = state / "hconfig", state / "hconfig" / "audit.jsonl"
    assert oct(d.stat().st_mode & 0o777) == "0o700" and oct(f.stat().st_mode & 0o777) == "0o600"
    d.chmod(0o755)
    f.chmod(0o644)
    assert run("set", "node:port", "9002", env=env).returncode == 0
    assert oct(d.stat().st_mode & 0o777) == "0o700" and oct(f.stat().st_mode & 0o777) == "0o600"   # tightened, not trusted


def test_a_symlinked_audit_file_is_refused_not_followed(stub, tmp_path):
    (env, calls), state = stub
    (state / "hconfig").mkdir(parents=True)
    (state / "hconfig").chmod(0o700)
    victim = tmp_path / "victim"
    victim.write_text("keep")
    (state / "hconfig" / "audit.jsonl").symlink_to(victim)
    r = run("set", "node:port", "9001", "--json", env=env)
    assert r.returncode == 1 and json.loads(r.stdout)["error"]["code"] == "read-only"
    assert victim.read_text() == "keep" and writes(calls) == []             # refused before the core verb ran


def test_the_audit_line_has_the_documented_fields_and_the_caller_source(stub):
    (env, calls), state = stub
    r = run("set", "x-alpha:mode", "b", "--json", env={**env, "HCONFIG_SOURCE": "hdash"})
    assert r.returncode == 0
    line = audit_lines(state)[-1]
    assert set(line) == {"ts", "id", "class", "action", "source", "uid", "pid", "old", "new", "ok", "error"}
    assert (line["id"], line["class"], line["action"], line["source"], line["old"], line["new"], line["ok"], line["error"]) == (
        "x-alpha:mode", "module", "set", "hdash", None, "b", True, None)
    assert line["uid"] == os.getuid()
    run("set", "x-alpha:mode", "a", env={**env, "HCONFIG_SOURCE": "../../etc/passwd"})
    assert audit_lines(state)[-1]["source"] == "other"             # a caller cannot put anything in the field


# ── against the real core ─────────────────────────────────────────────────────────────────────────

def test_set_and_unset_a_module_key_through_the_real_core(whive):
    code, out = whive.json("set", "x-alpha:greeting", "hello", check=0)
    assert out["changed"] is True and out["key"]["value"] == "hello" and out["key"]["source"] == "node"
    assert whive.core_run("module", "config", "alpha", "get", "greeting").stdout.strip().startswith("hello")
    code, out = whive.json("set", "x-alpha:greeting", "hello", check=0)
    assert out["changed"] is False
    code, out = whive.json("unset", "x-alpha:greeting", check=0)
    assert out["changed"] is True and out["key"]["source"] == "default" and out["key"]["value"] == "hi"
    assert whive.json("unset", "x-alpha:greeting", check=0)[1]["changed"] is False
    lines = [json.loads(l) for l in (whive.home / "hive" / "hconfig" / "audit.jsonl").read_text().splitlines()]
    assert [(l["action"], l["old"], l["new"]) for l in lines if l["id"] == "x-alpha:greeting"] == [
        ("set", "yo", "hello"), ("unset", "hello", None)]


def test_a_secret_set_on_stdin_is_stored_and_never_shown(whive):
    r = subprocess.run([sys.executable, "-I", str(HCONFIG), "set", "x-alpha:token", "--stdin", "--json"],
                       env=whive.hconfig_env(), capture_output=True, text=True, input="new-secret-value-77\n")
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["key"]["value"] == "********" and "new-secret-value-77" not in r.stdout + r.stderr
    assert json.loads((whive.home / "modules" / "alpha" / "config").read_text())["token"] == "new-secret-value-77"
    audit = (whive.home / "hive" / "hconfig" / "audit.jsonl").read_text()
    assert "new-secret-value-77" not in audit and TOKEN not in audit
    assert whive.hconfig("set", "x-alpha:token", "x").returncode == 1


def test_a_node_key_set_through_the_real_core_keeps_the_other_keys(whive):
    whive.core_run("config", "node", "set", "bind", "127.0.0.1")
    code, out = whive.json("set", "node:port", "9123", check=0)
    assert out["changed"] is True and out["key"]["value"] == 9123 and "restart" in out["note"]
    peers = json.loads((whive.home / "hive" / ".peers.json").read_text())
    assert peers["port"] == 9123 and peers["bind"] == "127.0.0.1"
    assert whive.json("set", "node:port", "9123")[1]["changed"] is False
    code, out = whive.json("set", "node:dashboard_viewers", "b, a", check=0)
    assert out["key"]["value"] == ["a", "b"] and whive.json("set", "node:dashboard_viewers", "a,b")[1]["changed"] is False
    r = whive.hconfig("set", "node:bind", "not-an-address", "--json")
    assert r.returncode == 1 and json.loads(r.stdout)["error"]["code"] == "core-failed"
    assert json.loads((whive.home / "hive" / ".peers.json").read_text())["bind"] == "127.0.0.1"


def test_an_env_override_blocks_a_node_write_through_the_real_core(whive):
    r = whive.hconfig("set", "node:sync_auth", "enforce", "--json", env=whive.hconfig_env(HIVE_SYNC_AUTH="off"))
    assert r.returncode == 1 and json.loads(r.stdout)["error"]["code"] == "env-overridden"
    assert "sync_auth" not in json.loads((whive.home / "hive" / ".peers.json").read_text())


def test_concurrent_sets_through_the_real_core_both_land(whive):
    procs = [subprocess.Popen([sys.executable, "-I", str(HCONFIG), "set", i, v], env=whive.hconfig_env(),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, stdin=subprocess.DEVNULL)
             for i, v in (("x-beta:mode", "b"), ("node:dashboard_viewer_port", "9444"), ("x-alpha:greeting", "par"))]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    assert whive.core_run("module", "config", "beta", "get", "mode").stdout.startswith("b")
    assert json.loads((whive.home / "hive" / ".peers.json").read_text())["dashboard_viewer_port"] == 9444
    lines = [json.loads(l) for l in (whive.home / "hive" / "hconfig" / "audit.jsonl").read_text().splitlines()]
    assert {"x-beta:mode", "node:dashboard_viewer_port"} <= {l["id"] for l in lines}


def test_nothing_in_the_hive_changes_when_a_set_is_refused(whive):
    def snap():
        return {str(p): p.read_bytes() for p in sorted(whive.home.rglob("*")) if p.is_file() and "audit" not in p.name
                and p.name != "lock" and ".git" not in p.parts and "src-" not in str(p)}
    before = snap()
    for argv in (("set", "core:cap_self", "0.1"), ("set", "node:port", "0"), ("set", "x-alpha:token", "plain"),
                 ("set", "x-alpha:limit", "3"), ("unset", "node:port"), ("set", "x-alpha:nope", "1")):
        assert whive.hconfig(*argv).returncode == 1, argv
    assert snap() == before
