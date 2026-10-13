# hconfig

The configuration module for HiveMind. One registry of every setting a node
has, in three classes, each with its schema, its merged value, where the value came from, and who may change it and how.

hconfig 0.1 is a command-line tool with a JSON protocol, not a service. It has no listener, no socket and no daemon, holds
no key and reads no passphrase. It writes only through the core's own verbs. A dashboard runs it as a subprocess.

Needs HiveMind core 4.0 or newer (`hv config schema|show|node` and `hive-mind module list|config … --json`). The
manifest's `min_core` is `4.0`, so an older core refuses to install it; on a core without those verbs hconfig itself
refuses with `core-failed` and says so.

## Install

```
hive-mind module add hconfig --from <this repository's URL> --publisher <key>
```

The release is not signed yet: no publisher key exists for this repository, so the core refuses the tree until one does
(see "Signing" below).

## Use

The full command and JSON reference, the one a dashboard is written against, is [docs/CLI.md](docs/CLI.md).

```
hconfig schema [PREFIX] [--json]                          every known key and its schema, without values
hconfig list [PREFIX] [--class node|module|governed] [--json]
hconfig get ID [--json]                                   one record
hconfig explain ID [--json]                               the record, and what its value means for this hive
hconfig set ID [VALUE] [--stdin] [--device ID] [--non-interactive] [--json]
hconfig unset ID [--json]
hconfig validate [MODULE] [--json]
hconfig version
hconfig status --json                                       the module contract's status answer
hconfig doctor --json                                       the module contract's doctor answer, with the configuration checks below
```

A key is spelled the way the journal spells it:

| Class | Spelling | Source | Written by |
|---|---|---|---|
| node | `node:<key>` | `hv config schema` (the node files) | the operator, `hv config node set` |
| module | `x-<module>:<key>`, scope `node` | the module's `config_schema` | the operator, `hive-mind module config <module> set` |
| governed | `core:<key>`, or `x-<module>:<key>` with scope `fleet` / `fleet-node` | the core's governed table, or the module's schema | the owner, signed |

Every verb takes `--json` and prints one object: `{"schema": 1, "ok": true, …}`, or on a refusal
`{"schema": 1, "ok": false, "error": {"code", "id", "message", "hint"}}`. Exit codes: `0` ok, `1` refused with nothing
changed, `2` usage, `5` the owner key is locked (`owner-locked`).

A record (`keys[]` of `schema`/`list`, `key` of `get`/`explain`) has: `id`, `class`, `scope`, `type` (`string`, `int`,
`float`, `bool`, `enum`, `list`), `default`, `values` or `min`/`max` where they apply, `description`, `secret`,
`identifying`, `visibility` (`operator` or `viewer`), `writable_by` (`operator` or `owner`), `write_via` (the core command
that writes it), `warnings`, and, in `list`/`get`/`explain` only, `set`, `source` (`default`, `node`, `fleet-node`,
`fleet`, `env`, or `null`), `value` and `set_by` (`{device_id, ts, sid}` for a `core:` key, else `null`). A node key adds
`env`, the variable that would override it. A template (`x-m:seat.{role}.enabled`) is listed once with `template: true`,
and each concrete key set under it follows with `instance_of`.

Values are the core's merged answer, never recomputed here. A secret prints as `********` and is never shown in any
verb. No error message carries a value.

`schema` and `list` print a `warnings` list too: a module whose schema or values could not be read is named there and
skipped; the rest of the registry is still returned.

### Writing

`hconfig set` and `unset` write through the core's own verbs and nothing else: `node:` keys (`hv config node set`),
node-scope module keys (`hive-mind module config <module> set|unset`) and, signed, a module's `fleet` and `fleet-node`
keys (`hive-mind module config <module> set --fleet|--fleet-node <device> --non-interactive`). hconfig has no signing
code and never sees a passphrase: the core signs through the owner-unlock agent if it holds the owner key for that
module, and otherwise **refuses with exit 5**, which hconfig reports as `owner-locked` (exit 5) with the module to unlock
for (`hive-mind owner unlock --module <name>`, typed on your own terminal) and the directory it looked for the agent's
socket in. That covers a locked agent and an agent that is up but will not sign this act; nothing is ever read from
stdin to unlock. `core:` keys and `node:owner_quota` are `owner-only`: the exact command to run by hand is in `hint`, and
no write verb is started. A fleet value cannot be removed, only set again (`unset` is `read-only`).

A fleet-node key is written for this device unless `--device k1:<16 hex>` names another. An `identifying` key's result
carries a `warnings` line: a fleet value is journaled and visible to every admitted device for good.

- The value is checked (type, enum, bounds) before any write verb starts, so a bad value never reaches the core. The
  core checks the rest.
- A secret (`secret: true`) is never taken on a command line: `hconfig set <id> VALUE` is refused as `secret-on-argv`.
  Give it with `--stdin` (one line); it reaches the core on stdin too, so it is in no process's argument list.
- A write compares the layer it writes, never the merged value: a `node:` or module-node value equal to the node
  file's own entry is a no-op (`changed: false`, nothing runs); a fleet value equal to the journal's entry for that key is
  too; a value equal only to the default, or to another device's, is still written (signed). For another device's
  fleet-node entry hconfig cannot read the journal, so it runs the core verb, which compares the full key and signs
  nothing if it is equal (`changed: false`).
- A node-scope module key set to its own schema default is not stored: the core compares the merged node layer and
  reports it as already set, so `changed` is `false` and nothing is audited (the same for a secret submitted again with the
  value it holds). Persisting an explicit pin equal to the default needs a core change.
- Refusals, each exit 1 with nothing changed: `unknown-key`, `not-installed`, `validation`, `secret-on-argv`,
  `read-only` (a `node:` key cannot be unset: the core has no verb for it), `env-overridden` (the key is shadowed by an
  environment variable; hconfig never writes an environment), `owner-only`, `core-failed` (the core verb's exit and one
  safe line of its message). No message carries a value.
- Writes serialize on one `flock` under `$HIVE_HOME/hconfig/` (a directory of mode 0700), and every write appends one
  line to `$HIVE_HOME/hconfig/audit.jsonl` (0600): `{ts, id, class, action, source, uid, pid, old, new, ok, error}`.
  `source` is `HCONFIG_SOURCE` (a short name such as `hdash`; anything else is `other`). `old` and `new` are left out for
  a secret. The file is a local convenience record, not evidence: any process of the same user can edit it. It is not
  synced. A change made directly with a core verb is not in it.

### Validate and doctor

`hconfig validate [MODULE]` runs the core's own validator (`module config <name> validate --json`) for every installed
module with a schema (or the one named) and checks the node keys' stored values against the core's schema. It prints
`{ok, errors, warnings}` (each item `{id, message}`, naming a key and a rule, never a value) and exits 1 when there is an
error.

`hconfig doctor --json` is the contract's doctor answer (always exit 0) with one row per check: the core's verbs answer;
module config and node values validate (invalid, unknown and required-but-unset keys); a node key shadowed by an
environment variable; a governed value outside the core's current table; **key-dir mismatch** (`HIVE_KEY_DIR` is set and
differs from the hive's `.key-dir`, or the hive has none, so a child without the variable would resolve another key
directory); **agent-socket-dir mismatch** (the owner agent's socket is in the one of the two runtime directories this
process does not look in, which makes every governed write say `owner-locked` while the agent is unlocked); the audit
directory and file are private; the node file changed after hconfig's last audit line; and the sync daemon (the
`hive-sync` user unit, when `systemctl` can say) has not restarted since the node file changed.

### Where the core is found

hconfig runs `hv` and `hive-mind` from `PATH`. `HCONFIG_HV` and `HCONFIG_HIVE_MIND` name another program (split into
arguments, never run through a shell); the tests use them. The three owner variables (`HIVE_OWNER_PASSPHRASE`,
`HIVE_OWNER_KEY_PASSPHRASE`, `HIVE_OWNER_SEED`) are removed from the environment of every program hconfig starts.

## Security

hconfig is a front for the core's own configuration verbs. It adds no listener, stores no credential and has no signing
code. Anything it can do, the account running it could already do with the core's verbs. It is as unconfined as its
caller; the manifest's `trust: confined` describes the unit hconfig does not have.

## Signing

`scripts/sign_manifest.py` signs `module.json` from `HCONFIG_PUBLISHER_SEED` (environment only). No publisher key is
provisioned for this repository, so the manifest carries a placeholder `publisher` the core refuses to decode and there
is no `module.json.sig`. `--files` refreshes the digests meanwhile; CI runs `--check --allow-unsigned`.

## Tests

```
HCONFIG_CORE=/path/to/hive-mind-checkout python3 -m pytest
```

`HCONFIG_CORE` is a checkout of the core on `release/4.0` or newer. Without it the tests that need a core are skipped.

## Licence

MIT. See [LICENSE](LICENSE). Every file in this repository is under that licence.
