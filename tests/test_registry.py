"""H1: the registry. Two kinds of test: against the real core on a decoy hive (what the three sources really say) and
against a stub core (exactly what hconfig asks, and what it does with an answer it should not trust)."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import stubcore
from conftest import ALPHA, BETA, HCONFIG, TOKEN, REPO

GOLDEN = Path(__file__).resolve().parent / "golden"
OWNER_VARS = ("HIVE_OWNER_PASSPHRASE", "HIVE_OWNER_KEY_PASSPHRASE", "HIVE_OWNER_SEED")
VERBS = (("schema",), ("list",), ("get", "x-alpha:token"), ("explain", "x-alpha:token"))


def run(*args, env):
    return subprocess.run([sys.executable, "-I", str(HCONFIG), *args], env=env, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)


@pytest.fixture
def stub(tmp_path):
    """(env, calls) for a core that answers `stubcore.standard_answers()`; the operator's owner variables are set."""
    return stubcore.install(tmp_path, stubcore.standard_answers(), HIVE_OWNER_KEY_PASSPHRASE="pp",
                            HIVE_OWNER_PASSPHRASE="pp", HIVE_OWNER_SEED="00" * 32, HIVE_KEY_DIR="/some/keys")


def stub_with(tmp_path, name, answers):
    (tmp_path / name).mkdir()
    return stubcore.install(tmp_path / name, answers)


def core_json(hive, *args):
    return json.loads(hive.core_run(*args, "--json").stdout)


# ── the registry is the union of the three sources ────────────────────────────────────────────────

def test_registry_equals_the_union_of_the_three_sources(hive):
    """Compared with the core's own answers, read independently: every id, in order, and every value."""
    schema = core_json(hive, "config", "schema")
    show = {r["id"]: r for r in core_json(hive, "config", "show")["governed"]}
    node = {r["id"]: r for r in core_json(hive, "config", "node", "list")["keys"]}
    expected = [(f"node:{k['id']}", node[k["id"]]["value"]) for k in schema["node"]]
    expected += [(f"core:{k['id']}", show[k["id"]]["value"]) for k in schema["governed"]]
    for name in ("alpha", "beta"):
        sch = json.loads(hive.core_run("module", "config", name, "schema", "--json").stdout)
        rows = {r["key"]: r for r in json.loads(hive.core_run("module", "config", name, "list", "--json").stdout)}
        for key in sorted(sch):
            expected.append((f"x-{name}:{key}", None if "{" in key else rows[key]["value"]))
            if "{" in key:
                expected += [(f"x-{name}:{k}", rows[k]["value"]) for k in sorted(rows) if k not in sch]
    code, out = hive.json("list")
    got = [(r["id"], r["value"]) for r in out["keys"]]
    masked = {"x-alpha:token": "********"}
    assert code == 0 and got == [(i, masked.get(i, v)) for i, v in expected]
    assert len(got) == len(set(i for i, _ in got))


def test_schema_is_the_same_keys_without_values(hive):
    _, listed = hive.json("list")
    _, schema = hive.json("schema")
    assert [r["id"] for r in schema["keys"]] == [r["id"] for r in listed["keys"]]
    assert not any({"value", "source", "set", "set_by"} & set(r) for r in schema["keys"])


def test_golden_registry_and_schema_from_a_known_core(stub):
    env, _ = stub
    assert json.loads(run("list", "--json", env=env).stdout) == json.loads((GOLDEN / "registry.json").read_text())
    assert json.loads(run("schema", "--json", env=env).stdout) == json.loads((GOLDEN / "schema.json").read_text())


def test_a_node_key_the_core_adds_appears_without_a_code_change(stub):
    env, _ = stub                                      # `dashboard_share` is in the stub core's schema, not in hconfig
    out = json.loads(run("get", "node:dashboard_share", "--json", env=env).stdout)["key"]
    assert (out["class"], out["type"], out["values"], out["visibility"]) == ("node", "enum", ["off", "on"], "operator")


def test_the_core_programs_hconfig_runs_are_exactly_the_read_verbs(stub):
    env, calls = stub
    run("list", env=env)
    assert {" ".join(c["argv"]) for c in calls()} == {
        "hv config schema --json", "hv config node list --json", "hv config show --json",
        "hive-mind module list --json", "hive-mind module config alpha schema --json",
        "hive-mind module config alpha list --json"}       # `plain` has no schema and is not asked


# ── secrets ───────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("verb", VERBS)
def test_a_secret_is_masked_in_every_verb_even_if_the_core_forgets(stub, verb):
    env, _ = stub                                      # the stub core returns the secret in clear
    for extra in ((), ("--json",)):
        r = run(*verb, *extra, env=env)
        assert r.returncode == 0 and stubcore.CLEAR not in r.stdout + r.stderr
    assert "********" in run(*verb, "--json", env=env).stdout or verb[0] == "schema"


@pytest.mark.parametrize("verb", VERBS)
def test_the_real_secret_never_appears(hive, verb):
    for extra in ((), ("--json",)):
        r = hive.hconfig(*verb, *extra)
        assert r.returncode == 0 and TOKEN not in r.stdout + r.stderr


def test_an_env_override_of_an_auth_shaped_key_is_masked(stub):
    env, _ = stub                                      # core says bind=100.64.0.1 from the environment
    rec = json.loads(run("get", "node:bind", "--json", env=env).stdout)["key"]
    assert rec["source"] == "env" and rec["value"] == "********"
    assert "100.64.0.1" not in run("explain", "node:bind", env=env).stdout


def test_a_secret_with_no_value_is_not_reported_as_set(hive):
    h = hive
    code, out = h.json("get", "x-alpha:token")
    assert out["key"]["set"] is True and out["key"]["value"] == "********"
    assert out["key"]["secret"] is True and out["key"]["visibility"] == "operator"


# ── templates and instances ───────────────────────────────────────────────────────────────────────

def test_a_template_is_listed_once_and_its_instances_follow(hive):
    _, out = hive.json("list", "x-alpha:seat.")
    rows = out["keys"]
    assert [r["id"] for r in rows] == ["x-alpha:seat.{role}.enabled", "x-alpha:seat.dev.enabled"]
    tpl, inst = rows
    assert tpl["template"] is True and tpl["set"] is False and tpl["value"] is None and "instance_of" not in tpl
    assert inst["instance_of"] == tpl["id"] and inst["value"] == "true" and inst["source"] == "fleet-node"
    assert "template" not in inst and inst["write_via"].endswith("--fleet-node <device>")
    assert "seat.dev.enabled" in inst["write_via"]


def test_instances_match_one_segment_only_and_an_exact_key_wins(stub):
    env, _ = stub
    ids = [r["id"] for r in json.loads(run("list", "x-alpha:seat.", "--json", env=env).stdout)["keys"]]
    assert ids == ["x-alpha:seat.{role}.enabled", "x-alpha:seat.dev.enabled", "x-alpha:seat.ops.enabled"]


# ── visibility and who writes ─────────────────────────────────────────────────────────────────────

TABLE = {                                  # id: (class, scope, visibility, writable_by)
    "node:port": ("node", "node", "operator", "operator"),
    "node:sync_auth": ("node", "node", "operator", "operator"),
    "node:dashboard_viewers": ("node", "node", "operator", "operator"),
    "core:cap_self": ("governed", "fleet", "viewer", "owner"),
    "core:quorum_m": ("governed", "fleet", "viewer", "owner"),
    "x-alpha:greeting": ("module", "node", "operator", "operator"),
    "x-alpha:token": ("module", "node", "operator", "operator"),           # a secret is never a viewer's
    "x-alpha:limit": ("governed", "fleet", "viewer", "owner"),
    "x-alpha:operator.name": ("governed", "fleet", "viewer", "owner"),     # identifying: see the next test
    "x-alpha:seat.{role}.enabled": ("governed", "fleet-node", "viewer", "owner"),
    "x-beta:mode": ("module", "node", "operator", "operator"),
}


def test_class_scope_visibility_and_writer_per_key(hive):
    _, out = hive.json("list")
    by_id = {r["id"]: r for r in out["keys"]}
    for id_, want in TABLE.items():
        r = by_id[id_]
        assert (r["class"], r["scope"], r["visibility"], r["writable_by"]) == want, id_
    assert all(r["visibility"] == "operator" for r in out["keys"] if r["class"] in ("node", "module"))
    assert all(r["visibility"] in ("operator", "viewer") and r["writable_by"] in ("operator", "owner")
               for r in out["keys"])


def test_write_via_names_the_core_command_that_writes_it(hive):
    _, out = hive.json("list")
    by_id = {r["id"]: r for r in out["keys"]}
    assert by_id["node:port"]["write_via"] == "hv config node set port <value>"
    assert by_id["core:cap_self"]["write_via"] == "hive-mind config set cap_self <value>"
    assert by_id["x-alpha:greeting"]["write_via"] == "hive-mind module config alpha set greeting <value>"
    assert by_id["x-alpha:token"]["write_via"] == "hive-mind module config alpha set token --stdin"   # never on argv
    assert by_id["x-alpha:limit"]["write_via"] == "hive-mind module config alpha set limit <value> --fleet"


def test_an_identifying_key_carries_a_warning_and_a_node_key_does_not(hive):
    _, out = hive.json("list")
    by_id = {r["id"]: r for r in out["keys"]}
    assert by_id["x-alpha:operator.name"]["identifying"] is True
    assert any("journaled" in w for w in by_id["x-alpha:operator.name"]["warnings"])
    assert all(r["warnings"] == [] for i, r in by_id.items() if i != "x-alpha:operator.name")
    assert "warning:" in hive.hconfig("get", "x-alpha:operator.name").stdout


def test_set_by_comes_from_the_core_and_only_for_governed_core_keys(hive):
    _, out = hive.json("list")
    by_id = {r["id"]: r for r in out["keys"]}
    sb = by_id["core:cap_self"]["set_by"]
    assert set(sb) == {"device_id", "ts", "sid"} and sb["sid"].startswith("h:") and by_id["core:cap_self"]["value"] == 0.5
    assert by_id["core:cap_self"]["source"] == "fleet" and by_id["core:quorum_m"]["set_by"] is None
    assert all(r["set_by"] is None for i, r in by_id.items() if not i.startswith("core:"))


# ── the envelope ──────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("verb", VERBS + (("get", "core:nope"), ("get", "x-gamma:k"), ("explain", "node:nope")))
def test_json_is_one_object_with_schema_one(hive, verb):
    r = hive.hconfig(*verb, "--json")
    out = json.loads(r.stdout)                       # the whole of stdout is one object
    assert isinstance(out, dict) and out["schema"] == 1 and out["ok"] is (r.returncode == 0)
    assert r.stdout.count("\n{") == 0 and r.stderr == ""
    if r.returncode:
        assert set(out["error"]) == {"code", "id", "message", "hint"}


def test_refusals_are_typed_exit_one_and_say_nothing_changed(hive):
    for verb, code in ((("get", "core:nope"), "unknown-key"), (("get", "x-alpha:nope"), "unknown-key"),
                       (("explain", "x-gamma:k"), "not-installed"), (("get", "node:nope"), "unknown-key")):
        r = hive.hconfig(*verb, "--json")
        assert r.returncode == 1 and json.loads(r.stdout)["error"]["code"] == code, verb
    r = hive.hconfig("get", "core:nope")                # text mode: the message goes to stderr
    assert r.returncode == 1 and r.stdout == "" and "no such key" in r.stderr


def test_usage_errors_exit_two(hive):
    for argv in ((), ("bogus",), ("get",), ("list", "--class", "nope"), ("set",), ("unset",)):
        assert hive.hconfig(*argv).returncode == 2, argv


def test_list_class_and_prefix_filters(hive):
    _, out = hive.json("list", "--class", "governed")
    assert {r["class"] for r in out["keys"]} == {"governed"} and "x-alpha:limit" in [r["id"] for r in out["keys"]]
    _, out = hive.json("list", "--class", "node")
    assert [r["id"] for r in out["keys"]][0] == "node:port"
    assert {r["id"].split(":")[0] for r in hive.json("list", "core:")[1]["keys"]} == {"core"}
    assert [r["id"] for r in hive.json("list", "x-beta:")[1]["keys"]] == ["x-beta:mode"]
    assert hive.json("list", "x-gamma:")[1]["keys"] == []


def test_get_asks_only_for_the_source_the_id_names(stub):
    env, calls = stub
    run("get", "x-alpha:greeting", env=env)
    assert {c["argv"][0] for c in calls()} == {"hive-mind"} and not any("show" in c["argv"] for c in calls())
    run("get", "node:port", env=env)
    assert not any(c["argv"][:3] == ["hive-mind", "module", "list"] for c in calls()[-2:])


# ── explain ───────────────────────────────────────────────────────────────────────────────────────

def test_explain_derives_lines_from_config_show_alone(hive, stub):
    _, out = hive.json("explain", "core:cap_self")
    assert [d["code"] for d in out["derived"]] == ["single-principal"] and "binds on every corroborated fact" in out["derived"][0]["text"]
    _, out = hive.json("explain", "core:quorum_m")
    assert [d["code"] for d in out["derived"]] == ["elections-off"]
    env, calls = stub
    out = json.loads(run("explain", "core:dead_man_days", "--json", env=env).stdout)
    assert [d["code"] for d in out["derived"]] == ["dead-man-inert"]
    out = json.loads(run("explain", "node:bind", "--json", env=env).stdout)
    assert {d["code"] for d in out["derived"]} == {"env-override", "restart"}
    assert "governance" not in json.dumps(out) and "k1:" not in json.dumps(out)    # the operator-only block stays inside
    assert "derived:" in run("explain", "core:quorum_m", env=env).stdout


def test_a_hive_with_two_principals_has_no_single_principal_line(stub, tmp_path):
    answers = stubcore.standard_answers()
    gov = dict(stubcore.GOVERNANCE, admitted=[{"device_id": "k1:0123456789abcdef", "principal": "a"},
                                              {"device_id": "k1:fedcba9876543210", "principal": "b"}])
    answers["hv config show --json"] = {"out": {"schema": 1, "governed": stubcore.SHOW_ROWS, "governance": gov}}
    env, _ = stub_with(tmp_path, "x", answers)
    assert json.loads(run("explain", "core:cap_self", "--json", env=env).stdout)["derived"] == []


# ── what hconfig does to the core and to the machine ──────────────────────────────────────────────

def test_the_owner_variables_never_reach_a_core_program_and_the_key_dir_does(stub):
    env, calls = stub
    assert all(v in env for v in OWNER_VARS)
    run("list", env=env)
    seen = calls()
    assert seen
    for c in seen:
        assert not set(OWNER_VARS) & set(c["env"]), c["argv"]
        assert "HIVE_KEY_DIR" in c["env"]              # a path, not a secret: the child must resolve the same key dir
        assert c["stdin"] == ""                        # stdin is closed: a core verb can never read what a caller piped


def test_a_failing_core_is_core_failed_and_its_message_carries_no_value(stub, tmp_path):
    answers = stubcore.standard_answers()
    answers["hv config node list --json"] = {"rc": 3, "err": "bad value for token=hunter2\nnext line\n"}
    env, _ = stub_with(tmp_path, "f", answers)
    r = run("list", "--json", env=env)
    out = json.loads(r.stdout)
    assert r.returncode == 1 and out["error"]["code"] == "core-failed" and "exited 3" in out["error"]["message"]
    assert "hunter2" not in r.stdout + r.stderr
    answers["hv config node list --json"] = {"rc": 3, "err": "the daemon is not running\n"}
    env, _ = stub_with(tmp_path, "g", answers)
    assert "the daemon is not running" in json.loads(run("list", "--json", env=env).stdout)["error"]["message"]


def test_a_core_that_prints_something_else_or_is_missing_is_core_failed(stub, tmp_path):
    answers = stubcore.standard_answers()
    answers["hv config schema --json"] = {"out": "not json"}
    env, _ = stub_with(tmp_path, "h", answers)
    assert json.loads(run("list", "--json", env=env).stdout)["error"]["code"] == "core-failed"
    env = {"PATH": "/nonexistent", "HOME": str(tmp_path)}
    r = run("list", "--json", env=env)
    assert r.returncode == 1 and json.loads(r.stdout)["error"]["code"] == "core-failed"


def test_a_module_whose_schema_cannot_be_read_is_named_and_skipped(stub, tmp_path):
    answers = stubcore.standard_answers()
    answers["hive-mind module config alpha schema --json"] = {"rc": 1, "err": "schema unavailable\n"}
    env, _ = stub_with(tmp_path, "i", answers)
    out = json.loads(run("list", "--json", env=env).stdout)
    assert [w["module"] for w in out["warnings"]] == ["alpha"] and not any(r["id"].startswith("x-alpha") for r in out["keys"])
    assert any(r["id"] == "core:cap_self" for r in out["keys"])


def test_hconfig_writes_nothing_to_the_hive(hive):
    def snap():
        return {str(p): (p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(hive.home.rglob("*")) if p.is_file()}
    before = snap()
    for verb in VERBS + (("list", "--class", "node"), ("get", "core:cap_self"), ("explain", "core:quorum_m")):
        hive.hconfig(*verb)
    assert snap() == before


def test_the_program_has_no_listen_or_prompt_path():
    src = HCONFIG.read_text()
    for needle in ("import socket", "getpass", "HIVE_OWNER_KEY_PASSPHRASE\"]", "input("):
        assert needle not in src, needle
    assert run("version", env={"PATH": os.environ["PATH"]}).stdout.strip() == "hconfig 0.1.0-dev"


def test_text_output_is_for_people(hive):
    out = hive.hconfig("list", "x-alpha:").stdout.splitlines()
    assert out[0].split()[0] == "x-alpha:greeting" and "(node)" in out[0] and out[0].split()[1] == "yo"
    assert any(l.startswith("x-alpha:token") and "********" in l for l in out)
    schema = hive.hconfig("schema", "x-beta:").stdout
    assert "x-beta:mode" in schema and "module/node" in schema
    get = hive.hconfig("get", "x-alpha:limit").stdout
    assert "range: 1 .. 10" in get and "written by: owner" in get and "via: hive-mind module config alpha set limit" in get
    enum = hive.hconfig("get", "x-beta:mode").stdout
    assert "one of: a, b" in enum


def test_a_core_without_the_4_0_verbs_is_refused_with_a_hint(tmp_path):
    answers = stubcore.standard_answers()
    answers["hv config schema --json"] = {"rc": 2, "err": "hv config: error: argument config_action: invalid choice\n"}
    env, _ = stub_with(tmp_path, "old", answers)
    out = json.loads(run("list", "--json", env=env).stdout)
    assert out["error"]["code"] == "core-failed" and "4.0" in out["error"]["hint"]


# ── the contract's status and doctor answers ──────────────────────────────────────────────────────

def test_status_and_doctor_answer_in_the_contract_shapes(stub):
    env, _ = stub
    st = json.loads(run("status", "--json", env=env).stdout)
    assert st == {"schema": 1, "state": "ok", "version": "0.1.0-dev", "problems": [], "detail": {}}
    d = json.loads(run("doctor", "--json", env=env).stdout)
    assert d["schema"] == 1 and [r["level"] for r in d["results"]] == ["ok"]


def test_status_and_doctor_with_no_core_are_degraded_and_warn_but_exit_zero(tmp_path):
    env = {"PATH": "/nonexistent", "HOME": str(tmp_path)}
    r = run("status", "--json", env=env)
    st = json.loads(r.stdout)
    assert r.returncode == 0 and st["state"] == "degraded" and st["problems"]
    r = run("doctor", "--json", env=env)
    res = json.loads(r.stdout)["results"]
    assert r.returncode == 0 and [x["level"] for x in res] == ["warn"] and res[0]["fix_hint"]
