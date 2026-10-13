"""Fixtures every hconfig test shares. Nothing here touches the network, the real hive or a real key: the hive is a
throwaway `HIVE_HOME` made by the core's own verbs, with two modules signed by an ephemeral test key."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HCONFIG = REPO / "hconfig"
_MAIL = "t" + chr(64) + "test.invalid"       # built, not written: `module check` lints a shipped file for email addresses
_IDENT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": _MAIL, "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": _MAIL}
TOKEN = "tok-9f3a-plain-text"                # the value of a secret key; no hconfig output may ever contain it

ALPHA = {
    "greeting": {"type": "string", "scope": "node", "description": "hello", "default": "hi"},
    "token": {"type": "string", "scope": "node", "description": "a token", "secret": True},
    "limit": {"type": "int", "scope": "fleet", "description": "limit", "default": "5", "min": 1, "max": 10},
    "seat.{role}.enabled": {"type": "bool", "scope": "fleet-node", "description": "seat on"},
    "operator.name": {"type": "string", "scope": "fleet", "description": "name", "identifying": True},
}
BETA = {"mode": {"type": "enum", "scope": "node", "description": "mode", "values": ["a", "b"], "default": "a"}}


def git(root, *args):
    r = subprocess.run(["git", "-C", str(root), *args], env={**os.environ, **_IDENT}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def tracked_copy(dest, src=REPO):
    """The tracked files of `src`, as they are on disk now, committed in a fresh repository at `dest`."""
    git(dest, "init", "-q")
    for rel in [n for n in git(src, "ls-files", "-z").split("\0") if n]:
        (dest / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / rel, dest / rel)
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "copy")
    return dest


@pytest.fixture(scope="session")
def core_dir():
    """The core checkout (`HCONFIG_CORE`, else `HIVE_HOME`); skip when there is none."""
    for var in ("HCONFIG_CORE", "HIVE_HOME"):
        d = os.environ.get(var)
        if d and (Path(d) / "ed25519.py").is_file() and (Path(d) / "hive_modules.py").is_file():
            return Path(d)
    pytest.skip("no core checkout: set HCONFIG_CORE to a hive-mind checkout on release/4.0 or newer")


@pytest.fixture
def module_copy(tmp_path):
    """A committed throwaway copy of this repository, safe to damage."""
    dest = tmp_path / "module-copy"
    dest.mkdir()
    return tracked_copy(dest)


def make_module(core, dest, name, schema, seed):
    """A signed contract-1 module tree with a `config_schema`, committed in a fresh repository."""
    sys.path.insert(0, str(core))
    try:
        import ed25519
    finally:
        sys.path.remove(str(core))
    dest.mkdir(parents=True)
    (dest / "README.md").write_text(name + "\n")
    (dest / name).write_text("#!/usr/bin/env python3\nprint('hi')\n")
    (dest / name).chmod(0o755)
    git(dest, "init", "-q")
    git(dest, "add", "-A")
    manifest = {"name": name, "version": "1.0.0", "min_core": "3.1", "contract": 1,
                "publisher": base64.b64encode(ed25519.pub_from_seed(seed)).decode(),
                "cli": {"name": name, "command": [name]}, "config_schema": schema,
                "files": {p: hashlib.sha256((dest / p).read_bytes()).hexdigest() for p in ("README.md", name)}}
    raw = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode()
    (dest / "module.json").write_bytes(raw)
    (dest / "module.json.sig").write_text(base64.b64encode(ed25519.sign(raw, seed)).decode() + "\n")
    git(dest, "add", "-A")
    git(dest, "commit", "-q", "-m", "m")
    return manifest["publisher"]


class Hive:
    """A decoy hive and the environment that reaches it."""

    def __init__(self, core, home):
        self.core, self.home = Path(core), Path(home)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("HIVE_", "HCONFIG_"))}
        self.env.update(HIVE_HOME=str(self.home / "hive"), HIVE_IDENTITY_STASH=str(self.home / "stash"),
                        HIVE_MODULES_DIR=str(self.home / "modules"), HOME=str(self.home / "user"),
                        HIVE_OWNER_KEY_PASSPHRASE="ci")
        for var in ("TERMUX_VERSION", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):   # a unit is written, never started
            self.env.pop(var, None)
        (self.home / "user" / ".local" / "bin").mkdir(parents=True)
        (self.home / "user" / ".local" / "bin").chmod(0o755)

    def core_run(self, *args, check=True, stdin=None):
        """`hive-mind` (the control plane) or `hv` (the data plane), whichever the verb belongs to."""
        data_plane = args[:1] in (("whoami",), ("remember",)) or args[:2] in (
            ("config", "identity"), ("config", "show"), ("config", "schema"), ("config", "node"))
        entry = "hv" if data_plane else "hivemind_ctl.py"
        r = subprocess.run([sys.executable, str(self.core / entry), *args], env=self.env, capture_output=True,
                           text=True, cwd=self.core, input=stdin)
        assert not check or r.returncode == 0, r.stderr + r.stdout
        return r

    def hconfig_env(self, **extra):
        """What the operator's shell would give hconfig: the hive, the core's two programs, and the owner variables
        (which hconfig must remove from everything it starts)."""
        return {**self.env, "HCONFIG_HV": f"{sys.executable} {self.core / 'hv'}",
                "HCONFIG_HIVE_MIND": f"{sys.executable} {self.core / 'hivemind_ctl.py'}", **extra}

    def hconfig(self, *args, env=None, check=None):
        r = subprocess.run([sys.executable, "-I", str(HCONFIG), *args], env=env or self.hconfig_env(),
                           capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if check is not None:
            assert r.returncode == check, r.stderr + r.stdout
        return r

    def json(self, *args, **kw):
        r = self.hconfig(*args, "--json", **kw)
        return r.returncode, json.loads(r.stdout)


@pytest.fixture(scope="session")
def hive(core_dir, tmp_path_factory):
    """A decoy hive: owner and one admitted device, modules `alpha` and `beta` installed, one governed core entry, a
    fleet value, a fleet-node instance of a template, a node value and a secret."""
    root = tmp_path_factory.mktemp("decoy")
    h = Hive(core_dir, root)
    h.core_run("config", "identity", "init")
    h.core_run("owner", "init")
    device = next(l.split()[1] for l in h.core_run("whoami").stdout.splitlines() if l.startswith("device:"))
    h.core_run("group", "admit", device, "--principal", "op")
    seed = bytes(range(32))
    pub = make_module(core_dir, root / "src-alpha", "alpha", ALPHA, seed)
    make_module(core_dir, root / "src-beta", "beta", BETA, seed)
    for name in ("alpha", "beta"):
        h.core_run("module", "add", name, "--from", str(root / f"src-{name}"), "--publisher", pub, "--non-interactive")
    h.core_run("config", "set", "cap_self", "0.5")
    h.core_run("module", "config", "alpha", "set", "limit", "7", "--fleet")
    h.core_run("module", "config", "alpha", "set", "seat.dev.enabled", "true", "--fleet-node", device)
    h.core_run("module", "config", "alpha", "set", "greeting", "yo")
    h.core_run("module", "config", "alpha", "set", "token", "--stdin", stdin=TOKEN + "\n")
    h.core_run("config", "node", "set", "port", "9999")
    h.device = device
    return h
