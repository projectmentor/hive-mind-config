"""docs/CLI.md is hdash's reference: every verb it documents must exist, and every verb that exists must be documented
there and listed in the README's usage block. Error codes and exit codes the doc names must be the ones the program uses."""

import importlib.machinery
import importlib.util
import re

from conftest import HCONFIG, REPO

DOC = (REPO / "docs" / "CLI.md").read_text()
README = (REPO / "README.md").read_text()


def load():
    loader = importlib.machinery.SourceFileLoader("hconfig_cli", str(HCONFIG))
    spec = importlib.util.spec_from_loader("hconfig_cli", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def verbs():
    sub = next(a for a in load().parser()._actions if a.dest == "verb")
    return set(sub.choices)


def test_every_documented_verb_exists():
    documented = set(re.findall(r"^### hconfig ([a-z]+)\s*$", DOC, re.M))
    assert documented, "docs/CLI.md has no verb headings"
    assert documented <= verbs(), f"documented but not implemented: {documented - verbs()}"


def test_every_verb_is_documented():
    documented = set(re.findall(r"^### hconfig ([a-z]+)\s*$", DOC, re.M))
    assert verbs() <= documented, f"implemented but not in docs/CLI.md: {verbs() - documented}"
    for v in verbs():
        assert re.search(rf"^hconfig {v}\b", README, re.M), f"the README's usage block lacks {v}"


def test_every_command_line_in_the_doc_names_a_verb():
    for v in re.findall(r"`hconfig ([a-z]+)[ `]", DOC):
        assert v in verbs(), f"docs/CLI.md shows `hconfig {v}`, which is not a verb"


def test_error_codes_match_the_program():
    src = HCONFIG.read_text()
    used = set(re.findall(r'Refused\("([a-z-]+)"', src))
    table = DOC.split("### Error codes", 1)[1].split("## The record", 1)[0]
    documented = set(re.findall(r"^\| `([a-z-]+)` \|", table, re.M))
    assert used == documented, f"code only in the program: {used - documented}; only in the doc: {documented - used}"
