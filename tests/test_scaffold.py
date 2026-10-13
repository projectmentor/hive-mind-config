"""H1: the repository is an installable contract-1 module. The manifest is checked by the core's own `check_tree` and
`module check --run`; install runs against a decoy hive with an ephemeral signing key."""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import HCONFIG, REPO, Hive, git

SIGN = REPO / "scripts" / "sign_manifest.py"
LINUX_ONLY = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="module management is Linux-only")


def run(*argv, env=None, cwd=None, isolated=True):
    return subprocess.run([sys.executable, *(["-I"] if isolated else []), *map(str, argv)], capture_output=True,
                          text=True, env=env, cwd=cwd)


def sign_py(root, core, *args, seed=None):
    env = {k: v for k, v in os.environ.items() if k != "HCONFIG_PUBLISHER_SEED"}
    if seed:
        env["HCONFIG_PUBLISHER_SEED"] = seed
    return run(root / "scripts" / "sign_manifest.py", "--core", core, "--root", root, *args, env=env)


def manifest():
    return json.loads((REPO / "module.json").read_text())


# ── the manifest says what the design (hconfig_4x.md §6, H1) says ─────────────────────────────────

def test_the_manifest_asks_for_what_the_design_says():
    m = manifest()
    assert (m["name"], m["min_core"], m["contract"]) == ("hconfig", "3.1", 1)   # see sign_manifest.py: the 4.0 line still reports contract 3.1
    assert m["cli"] == {"name": "hconfig", "command": ["hconfig"]}
    assert m["reads"] == [] and m["config_schema"] == {}
    assert m["quota"] == {"per_hour": 0, "per_day": 0}
    assert m["status"] == ["hconfig", "status", "--json"] and m["doctor"] == ["hconfig", "doctor", "--json"]
    for absent in ("service", "hooks", "runtimes", "config", "ui"):
        assert absent not in m, absent                  # no listener, no hooks, no runtime in 0.1


def test_the_signing_script_and_the_manifest_agree():
    sys.path.insert(0, str(REPO / "scripts"))
    try:
        import sign_manifest
    finally:
        sys.path.remove(str(REPO / "scripts"))
    m = manifest()
    assert {k: v for k, v in m.items() if k not in ("files", "publisher")} == sign_manifest.DEFAULTS


def test_the_licence_is_kept_and_the_readme_states_it():
    assert (REPO / "LICENSE").read_text().startswith("MIT License") and "LICENSE" in manifest()["files"]
    readme = (REPO / "README.md").read_text()
    assert "## Licence" in readme and "MIT" in readme.split("## Licence", 1)[1]


def test_every_verb_the_readme_documents_exists_and_nothing_else():
    readme = (REPO / "README.md").read_text()
    verbs = {"schema", "list", "get", "explain", "version", "status", "doctor", "set", "unset", "validate"}
    assert all(f"hconfig {v}" in readme or v in ("status", "doctor") for v in verbs)
    r = run(HCONFIG, "--help")
    assert r.returncode == 0 and all(v in r.stdout for v in verbs)
    assert not {"audit", "import", "export"} & set(r.stdout.replace(",", " ").replace("{", " ").split())


# ── the core's own conformance check ──────────────────────────────────────────────────────────────

def test_module_check_run_passes_and_the_lint_is_clean(core_dir):
    r = run(core_dir / "hivemind_ctl.py", "module", "check", REPO, "--run", "--json", isolated=False)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out["ok"], out
    assert [w for w in out["warnings"] if "unsigned" not in w] == []     # the identifying-data lint is clean


def test_check_tolerates_only_the_unsigned_state_the_readme_describes(core_dir):
    assert sign_py(REPO, core_dir, "--check", "--allow-unsigned").returncode == 0
    r = sign_py(REPO, core_dir, "--check")
    assert r.returncode == 1 and "unsigned" in r.stderr
    assert not (REPO / "module.json.sig").exists()
    assert "no publisher key" in (REPO / "README.md").read_text() or "No publisher key" in (REPO / "README.md").read_text()


def test_one_changed_byte_fails_check(core_dir, module_copy):
    p = module_copy / "hconfig"
    p.write_bytes(p.read_bytes() + b"#")
    r = sign_py(module_copy, core_dir, "--check", "--allow-unsigned")
    assert r.returncode == 1 and "digest" in r.stderr


def test_a_tracked_file_missing_from_files_fails_check(core_dir, module_copy):
    (module_copy / "stray.txt").write_text("x")
    git(module_copy, "add", "stray.txt")
    r = sign_py(module_copy, core_dir, "--check", "--allow-unsigned")
    assert r.returncode == 1 and "stray.txt" in r.stderr


def test_signing_needs_the_seed_in_the_environment(core_dir, module_copy):
    r = sign_py(module_copy, core_dir)
    assert r.returncode == 1 and "HCONFIG_PUBLISHER_SEED" in r.stderr
    text = SIGN.read_text()
    assert 'os.environ.get("HCONFIG_PUBLISHER_SEED"' in text and "--seed" not in text


def test_a_bad_signature_fails_even_when_unsigned_is_tolerated(core_dir, module_copy):
    assert sign_py(module_copy, core_dir, seed="07" * 32).returncode == 0
    (module_copy / "module.json.sig").write_text("A" * 86 + "==\n")
    r = sign_py(module_copy, core_dir, "--check", "--allow-unsigned")
    assert r.returncode == 1 and "signature" in r.stderr


def test_the_placeholder_publisher_verifies_no_signature(core_dir):
    """A small-order key (such as all zero bytes) accepts forged signatures; the placeholder must not decode at all."""
    sys.path.insert(0, str(core_dir))
    try:
        import ed25519
    finally:
        sys.path.remove(str(core_dir))
    pub = base64.b64decode(manifest()["publisher"])
    for seed in (b"\x00" * 32, bytes(range(32))):
        sig = ed25519.sign(b"anything", seed)
        assert not ed25519.verify(b"anything", sig, pub)
    assert not ed25519.verify(b"anything", b"\x00" * 64, pub)


# ── install: the signed tree installs, runs, and removes cleanly ──────────────────────────────────

@LINUX_ONLY
def test_a_signed_copy_installs_runs_and_removes(core_dir, module_copy, tmp_path):
    seed = os.urandom(32).hex()                  # ephemeral and test-only: this repository has no publisher key yet
    assert sign_py(module_copy, core_dir, seed=seed).returncode == 0
    git(module_copy, "add", "-A")
    git(module_copy, "commit", "-q", "-m", "signed copy")
    pub = json.loads((module_copy / "module.json").read_text())["publisher"]
    h = Hive(core_dir, tmp_path / "decoy")
    h.core_run("config", "identity", "init")
    h.core_run("owner", "init")
    device = next(l.split()[1] for l in h.core_run("whoami").stdout.splitlines() if l.startswith("device:"))
    h.core_run("group", "admit", device, "--principal", "op")
    out = h.core_run("module", "install", "hconfig", "--from", str(module_copy), "--publisher", pub,
                     "--non-interactive", "--json")
    assert out.returncode == 0, out.stderr
    launcher = h.home / "user" / ".local" / "bin" / "hconfig"
    assert launcher.is_file()
    assert json.loads((h.home / "hive" / ".modules.json").read_text())["hconfig"]["trust"] == "confined"
    ran = h.core_run("module", "run", "hconfig", "--", "version")
    assert ran.stdout.strip() == "hconfig 0.1.0-dev"
    h.core_run("module", "remove", "hconfig")
    assert not launcher.exists() and not (h.home / "modules" / "hconfig").exists()
