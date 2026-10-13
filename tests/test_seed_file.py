"""`scripts/sign_manifest.py --seed-file PATH`: the seed from a 0600 file, so it never goes through the environment."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
ENV = "HCONFIG_PUBLISHER_SEED"
SEED = "0b" * 32


@pytest.fixture
def core():
    for var in ("HCONFIG_CORE", "HIVE_HOME"):
        d = os.environ.get(var)
        if d and (Path(d) / "ed25519.py").is_file():
            return Path(d)
    pytest.skip("no core checkout: set HCONFIG_CORE to a hive-mind checkout")


@pytest.fixture
def tree(tmp_path):
    """A one-file git tree holding this repository's signing script, with a manifest to rewrite."""
    root = tmp_path / "tree"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "sign_manifest.py", root / "scripts" / "sign_manifest.py")
    (root / "a.txt").write_text("a")
    (root / "module.json").write_text((REPO / "module.json").read_text())
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    return root


@pytest.fixture
def seed_file(tmp_path):
    p = tmp_path / "seed"
    p.write_text(SEED + "\n")
    p.chmod(0o600)
    return p


def sign(tree, core, *args, env_seed=None):
    env = {k: v for k, v in os.environ.items() if k != ENV}
    if env_seed is not None:
        env[ENV] = env_seed
    return subprocess.run([sys.executable, "-I", str(tree / "scripts" / "sign_manifest.py"), "--core", str(core),
                           "--root", str(tree), *args], capture_output=True, text=True, env=env)


def refused(r, *words):
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert all(w in r.stderr for w in words), r.stderr
    assert SEED not in r.stdout + r.stderr and SEED[:16] not in r.stdout + r.stderr


def test_a_good_file_signs_like_the_environment_does(tree, core, seed_file, tmp_path):
    r = sign(tree, core, "--seed-file", seed_file)
    assert r.returncode == 0, r.stderr
    assert SEED not in r.stdout + r.stderr
    by_file = (tree / "module.json").read_bytes(), (tree / "module.json.sig").read_bytes()
    assert sign(tree, core, "--check").returncode == 0
    (tree / "module.json.sig").unlink()
    assert sign(tree, core, env_seed=SEED).returncode == 0
    assert by_file == ((tree / "module.json").read_bytes(), (tree / "module.json.sig").read_bytes())


@pytest.mark.parametrize("mode", [0o640, 0o604, 0o660, 0o644, 0o700, 0o777])
def test_a_file_group_or_other_can_read_or_run_is_refused(tree, core, seed_file, mode):
    seed_file.chmod(mode)
    r = sign(tree, core, "--seed-file", seed_file)
    refused(r, "0600")
    assert not (tree / "module.json.sig").exists()


def test_a_stricter_mode_is_accepted(tree, core, seed_file):
    seed_file.chmod(0o400)
    assert sign(tree, core, "--seed-file", seed_file).returncode == 0


def test_a_symlink_is_refused_even_to_a_good_file(tree, core, seed_file, tmp_path):
    link = tmp_path / "link"
    link.symlink_to(seed_file)
    r = sign(tree, core, "--seed-file", link)
    refused(r, "symlink")
    assert not (tree / "module.json.sig").exists()


def test_a_directory_and_a_missing_path_are_refused(tree, core, tmp_path):
    refused(sign(tree, core, "--seed-file", tmp_path), "--seed-file")
    refused(sign(tree, core, "--seed-file", tmp_path / "nope"), "--seed-file")


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() != 0, reason="needs root to give a file to another user")
def test_a_file_owned_by_someone_else_is_refused(tree, core, seed_file):
    os.chown(seed_file, 12345, -1)
    refused(sign(tree, core, "--seed-file", seed_file), "owned")


@pytest.mark.parametrize("text", ["", "zz" * 32, "0b" * 31, "0b" * 33, "not hex at all"])
def test_a_file_that_is_not_64_hex_characters_is_refused(tree, core, seed_file, text):
    seed_file.write_text(text)
    r = sign(tree, core, "--seed-file", seed_file)
    refused(r, "64 hex characters")
    assert text not in r.stderr or text == ""
    assert not (tree / "module.json.sig").exists()


def test_the_message_for_bad_hex_matches_the_environment_path(tree, core, seed_file):
    seed_file.write_text("zz")
    a = sign(tree, core, "--seed-file", seed_file).stderr
    b = sign(tree, core, env_seed="zz").stderr
    assert "must be 64 hex characters (a 32-byte seed)" in a and "must be 64 hex characters (a 32-byte seed)" in b


def test_both_sources_are_refused_and_nothing_is_chosen(tree, core, seed_file):
    r = sign(tree, core, "--seed-file", seed_file, env_seed=SEED)
    refused(r, "not both")
    assert not (tree / "module.json.sig").exists()
    r = sign(tree, core, "--seed-file", seed_file, env_seed="")      # set but empty still counts as a second source
    refused(r, "not both")


def test_seed_file_with_check_is_refused(tree, core, seed_file):
    refused(sign(tree, core, "--check", "--seed-file", seed_file), "--seed-file")
