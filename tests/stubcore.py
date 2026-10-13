"""A stand-in for the core's two programs, so a test can feed hconfig any answer and see exactly what it ran.

`install(tmp_path, answers)` writes a stub program and returns the environment that makes hconfig use it. `answers`
maps the argv after the program name (`"hv config schema --json"`) to `{"out": obj_or_str, "rc": 0, "err": ""}`. Every
call is appended to a log: its argv, the names of the environment variables it saw, and what was on its stdin."""

import json
import os
import sys
from pathlib import Path

STUB = '''#!/usr/bin/env python3
import json, os, sys
prog, args = sys.argv[1], sys.argv[2:]
answers = json.load(open(os.environ["STUB_ANSWERS"]))
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps({"argv": [prog] + args, "env": sorted(os.environ), "stdin": sys.stdin.read()}) + "\\n")
a = answers.get(" ".join([prog] + args))
if a is None:
    sys.stderr.write("stub: no answer for " + " ".join([prog] + args) + "\\n")
    sys.exit(97)
out = a.get("out", "")
sys.stdout.write(out if isinstance(out, str) else json.dumps(out))
sys.stderr.write(a.get("err", ""))
sys.exit(a.get("rc", 0))
'''


def install(tmp_path, answers, base_env=None, **extra):
    d = Path(tmp_path)
    (d / "stub.py").write_text(STUB)
    (d / "answers.json").write_text(json.dumps(answers))
    log = d / "calls.jsonl"
    log.write_text("")
    env = {"PATH": os.environ["PATH"], "HOME": str(d), "STUB_ANSWERS": str(d / "answers.json"), "STUB_LOG": str(log),
           "HCONFIG_HV": f"{sys.executable} {d / 'stub.py'} hv",
           "HCONFIG_HIVE_MIND": f"{sys.executable} {d / 'stub.py'} hive-mind", **(base_env or {}), **extra}
    return env, lambda: [json.loads(l) for l in log.read_text().splitlines()]


def spec(id_, type_="str", default=None, description="d", **more):
    return {"id": id_, "type": type_, "default": default, "description": description, **more}


NODE_SCHEMA = [
    spec("port", "int", 9876, min=1, max=65535),
    spec("bind", "str", None),
    spec("sync_auth", "str", "permissive", choices=["off", "permissive", "enforce"]),
    spec("dashboard_share", "str", "off", choices=["off", "on"]),
]
GOVERNED_SCHEMA = [
    spec("cap_self", "float", 0.7),
    spec("quorum_m", "int", 0, min=0),
    spec("dead_man_days", "float", 30.0),
    spec("capsule_putters", "str", "owner", choices=["owner", "fertile"]),
]
NODE_ROWS = [
    {"id": "port", "value": 9876, "source": "default", "set": False, "env": None},
    {"id": "bind", "value": "100.64.0.1", "source": "env", "set": False, "env": "HIVE_BIND"},
    {"id": "sync_auth", "value": "enforce", "source": "node", "set": True, "env": "HIVE_SYNC_AUTH"},
    {"id": "dashboard_share", "value": "off", "source": "default", "set": False, "env": None},
]
SET_BY = {"device_id": "k1:0123456789abcdef", "ts": "2026-10-13T00:00:00.000+00:00", "sid": "h:aaaaaaaaaa"}
SHOW_ROWS = [
    {"id": "cap_self", "value": 0.7, "default": 0.7, "source": "default", "set_by": None},
    {"id": "quorum_m", "value": 0, "default": 0, "source": "default", "set_by": None},
    {"id": "dead_man_days", "value": 45.0, "default": 30.0, "source": "fleet", "set_by": SET_BY},
    {"id": "capsule_putters", "value": "owner", "default": "owner", "source": "default", "set_by": None},
]
GOVERNANCE = {"owner_id": "o1:0123456789abcdef", "quorum_m": 0,
              "admitted": [{"device_id": "k1:0123456789abcdef", "principal": "op"},
                           {"device_id": "k1:fedcba9876543210", "principal": "op"}]}
MODULES = [{"name": "alpha", "version": "1.0.0", "config_schema": True},
           {"name": "plain", "version": "1.0.0", "config_schema": False}]
ALPHA_SCHEMA = {
    "greeting": {"type": "string", "scope": "node", "description": "hello", "default": "hi"},
    "token": {"type": "string", "scope": "node", "description": "a token", "secret": True},
    "limit": {"type": "int", "scope": "fleet", "description": "limit", "default": "5", "min": 1, "max": 10},
    "seat.{role}.enabled": {"type": "bool", "scope": "fleet-node", "description": "seat on"},
    "operator.name": {"type": "string", "scope": "fleet", "description": "name", "identifying": True},
    "mode": {"type": "enum", "scope": "node", "description": "mode", "values": ["a", "b"], "default": "a"},
}
CLEAR = "clear-text-secret-value"       # a core that forgot to mask: hconfig must not pass it on
ALPHA_ROWS = [
    {"key": "greeting", "value": "yo", "source": "node", "secret": False, "set": True},
    {"key": "limit", "value": "5", "source": "default", "secret": False, "set": True},
    {"key": "mode", "value": "a", "source": "default", "secret": False, "set": True},
    {"key": "operator.name", "value": None, "source": None, "secret": False, "set": False},
    {"key": "seat.dev.enabled", "value": "true", "source": "fleet-node", "secret": False, "set": True},
    {"key": "seat.ops.enabled", "value": "false", "source": "fleet-node", "secret": False, "set": True},
    {"key": "token", "value": CLEAR, "source": "node", "secret": True, "set": True},
]


def standard_answers():
    return {
        "hv config schema --json": {"out": {"schema": 1, "governed": GOVERNED_SCHEMA, "node": NODE_SCHEMA}},
        "hv config node list --json": {"out": {"schema": 1, "keys": NODE_ROWS}},
        "hv config show --json": {"out": {"schema": 1, "governed": SHOW_ROWS, "governance": GOVERNANCE}},
        "hive-mind module list --json": {"out": {"schema": 1, "modules": MODULES}},
        "hive-mind module config alpha schema --json": {"out": ALPHA_SCHEMA},
        "hive-mind module config alpha list --json": {"out": ALPHA_ROWS},
    }
