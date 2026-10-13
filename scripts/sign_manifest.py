#!/usr/bin/env python3
"""Regenerate and sign `module.json`, or check it.

    HCONFIG_PUBLISHER_SEED=<64 hex> python3 -I scripts/sign_manifest.py [--core DIR]
    python3 -I scripts/sign_manifest.py --seed-file PATH [--core DIR]     # PATH: a 0600 file of 64 hex characters
    python3 -I scripts/sign_manifest.py --files          # refresh `files`, unsigned (no key is provisioned yet)
    python3 -I scripts/sign_manifest.py --check [--allow-unsigned] [--core DIR]

Signing rewrites `files` (every `git ls-files` path except the manifest and its signature, with its sha256),
takes `publisher` from the seed, keeps every other field of the committed manifest, and writes `module.json`
and `module.json.sig` (Ed25519 over the exact manifest bytes). The seed comes from the environment or from `--seed-file PATH` (never from
a file in the tree, never on the command line), and is never printed. The file must be a regular file you own, not a
symlink, with no group or other permission bits (0600 or stricter); both sources at once are refused.

`--files` is the stub for the signing step while the publisher key does not exist: it rewrites `files`, keeps the
placeholder `publisher` (a point encoding the core refuses to decode), and removes any signature, so the core refuses the tree until it is really signed.

`--check` needs no seed. It verifies the committed manifest the way `hive-mind module add` does: well-formed,
signature valid under the manifest's own publisher, every tracked file listed with a matching digest, nothing
listed that is missing. With `--allow-unsigned` a missing signature is reported and tolerated (digests are still
checked); a present but invalid signature always fails. Exit 0 if the tree is as asked, 1 with the reason if not.

The Ed25519 code is the core's (`h:a2588f7edc`): `ed25519.py` from the core checkout named by `--core`, else
`HCONFIG_CORE`, else `HIVE_HOME`.
"""

import argparse
import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST, SIGNATURE = "module.json", "module.json.sig"
# y = q: a non-canonical point encoding the core's ed25519 refuses to decode, so verify() is False for every signature.
# (All zero bytes would decode to a small-order point that some forged signatures verify under.)
PLACEHOLDER_PUBLISHER = base64.b64encode(bytes([0xED]) + b"\xff" * 30 + b"\x7f").decode()


ENV_NAME = "HCONFIG_PUBLISHER_SEED"

DEFAULTS = {
    "name": "hconfig",
    "version": "0.1.0-dev",
    # The core compares this with its CONTRACT_VERSION (4.0 from the contract bump on release/4.0); hconfig needs the
    # 4.0 verbs, so a 3.x core refuses the install.
    "min_core": "4.0",
    "contract": 1,
    "trust": "confined",
    "cli": {"name": "hconfig", "command": ["hconfig"]},
    "status": ["hconfig", "status", "--json"],
    "doctor": ["hconfig", "doctor", "--json"],
    "reads": [],
    "quota": {"per_hour": 0, "per_day": 0},
    "config_schema": {},
}


def die(msg):
    print(f"sign_manifest: {msg}", file=sys.stderr)
    sys.exit(1)


def load_ed25519(core):
    core = core or os.environ.get("HCONFIG_CORE") or os.environ.get("HIVE_HOME")
    if not core or not (Path(core) / "ed25519.py").is_file():
        die("no core checkout: pass --core DIR (or set HCONFIG_CORE or HIVE_HOME) to a directory holding ed25519.py")
    sys.path.insert(0, str(core))
    import ed25519
    return ed25519


def tracked_files(root):
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True).stdout
    return sorted(n.decode() for n in out.split(b"\x00") if n and n.decode() not in (MANIFEST, SIGNATURE))


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def render(manifest):
    return (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode()


def build(root, publisher):
    manifest = {**DEFAULTS, "publisher": publisher}
    manifest["files"] = {rel: sha256_file(root / rel) for rel in tracked_files(root)}
    return manifest


def read_seed_file(path):
    """The seed hex from PATH, or die. Opens without following a symlink and checks the open file, not the name."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError as e:
        die(f"--seed-file cannot be opened (a symlink is refused): {e.strerror}")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            die("--seed-file is not a regular file")
        if st.st_uid != os.geteuid():
            die("--seed-file is not owned by the current user")
        if st.st_mode & 0o177:
            die("--seed-file must be mode 0600 or stricter (no group or other access, no execute)")
        return os.read(fd, 4096).decode("ascii", "replace")
    finally:
        os.close(fd)


def read_seed(seed_file):
    """The 32-byte seed from --seed-file or HCONFIG_PUBLISHER_SEED; both at once is refused."""
    if seed_file is not None and ENV_NAME in os.environ:
        die("pass the seed with --seed-file or HCONFIG_PUBLISHER_SEED, not both")
    seed_hex, source = (read_seed_file(seed_file), "the --seed-file contents") if seed_file is not None else (
        os.environ.get(ENV_NAME, ""), ENV_NAME)
    try:
        seed = bytes.fromhex(seed_hex.strip())
        assert len(seed) == 32
    except (ValueError, AssertionError):
        die(f"{source} must be 64 hex characters (a 32-byte seed)")
    return seed


def sign(root, core, seed_file=None):
    ed25519 = load_ed25519(core)
    seed = read_seed(seed_file)
    manifest = build(root, base64.b64encode(ed25519.pub_from_seed(seed)).decode())
    raw = render(manifest)
    (root / MANIFEST).write_bytes(raw)
    (root / SIGNATURE).write_text(base64.b64encode(ed25519.sign(raw, seed)).decode() + "\n")
    print(f"signed {MANIFEST}: {len(manifest['files'])} files, publisher {manifest['publisher']}")


def refresh_files(root):
    manifest = build(root, PLACEHOLDER_PUBLISHER)
    (root / MANIFEST).write_bytes(render(manifest))
    if (root / SIGNATURE).exists():
        (root / SIGNATURE).unlink()
    print(f"{MANIFEST}: {len(manifest['files'])} files listed, UNSIGNED (no publisher key is provisioned)")


def check(root, core, allow_unsigned=False):
    ed25519 = load_ed25519(core)
    try:
        raw = (root / MANIFEST).read_bytes()
        m = json.loads(raw)
        pub = base64.b64decode(m["publisher"], validate=True)
        files = m["files"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        die(f"{MANIFEST} is missing or malformed: {e}")
    unsigned = not (root / SIGNATURE).exists()
    if unsigned and not allow_unsigned:
        die(f"{SIGNATURE} is missing: the tree is unsigned, so the core would refuse it")
    if not unsigned:
        try:
            sig = base64.b64decode("".join((root / SIGNATURE).read_text().split()), validate=True)
        except (OSError, ValueError) as e:
            die(f"{SIGNATURE} is malformed: {e}")
        if not ed25519.verify(raw, sig, pub):
            die("the manifest signature is not valid under its publisher key")
    tracked = set(tracked_files(root))
    if tracked - set(files):
        die(f"tracked files the manifest does not list: {', '.join(sorted(tracked - set(files))[:5])}")
    if set(files) - tracked:
        die(f"manifest lists files that are not tracked: {', '.join(sorted(set(files) - tracked)[:5])}")
    for rel, digest in sorted(files.items()):
        if sha256_file(root / rel) != digest:
            die(f"{rel!r} does not match its manifest digest")
    print(f"{MANIFEST} ok: {len(files)} files, " + ("UNSIGNED (tolerated)" if unsigned else "signature valid"))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="verify the committed manifest; needs no seed")
    ap.add_argument("--allow-unsigned", action="store_true", help="with --check: tolerate a missing signature")
    ap.add_argument("--files", action="store_true", help="refresh `files` without signing (the stub while no key exists)")
    ap.add_argument("--core", help="core checkout holding ed25519.py (default HCONFIG_CORE, then HIVE_HOME)")
    ap.add_argument("--seed-file", metavar="PATH", help=f"read the publisher seed from this 0600 file instead of {ENV_NAME}")
    ap.add_argument("--root", default=str(ROOT), help="the module tree (default: this repository)")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if args.seed_file is not None and (args.check or getattr(args, "files", False)):
        die("--seed-file is for signing; --check and --files need no seed")
    if args.check:
        check(root, args.core, args.allow_unsigned)
    elif args.files:
        refresh_files(root)
    else:
        sign(root, args.core, args.seed_file)


if __name__ == "__main__":
    main()
