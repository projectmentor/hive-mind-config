# hconfig

The configuration module for HiveMind. One registry of every setting a node
has, in three classes, each with its schema, its merged value, where the value came from, and who may change it and how.

hconfig 0.1 is a command-line tool with a JSON protocol, not a service. It has no listener, no socket and no daemon, holds
no key and reads no passphrase. It writes only through the core's own verbs. A dashboard runs it as a subprocess.

Needs HiveMind core 4.0 or newer (`hv config schema|show|node` and `hive-mind module list|config … --json`). The
manifest's `min_core` is `3.1` because the core compares it with its contract version, which the 4.0 line still reports
as `3.1`; on a core without those verbs hconfig refuses with `core-failed` and says so.

## Install

```
hive-mind module add hconfig --from <this repository's URL> --publisher <key>
```

The release is not signed yet: no publisher key exists for this repository, so the core refuses the tree until one does
(see "Signing" below).

## Use

```
hconfig schema [PREFIX] [--json]                          every known key and its schema, without values
hconfig list [PREFIX] [--class node|module|governed] [--json]
hconfig get ID [--json]                                   one record
hconfig explain ID [--json]                               the record, and what its value means for this hive
hconfig set ID [VALUE] [--stdin] [--non-interactive] [--json]
hconfig unset ID [--json]
hconfig version
hconfig status | doctor --json                              the module contract's answers; each says whether the core's verbs answer
```

A key is spelled the way the journal spells it:

| Class | Spelling | Source | Written by |
|---|---|---|---|
| node | `node:<key>` | `hv config schema` (the node files) | the operator, `hv config node set` |
| module | `x-<module>:<key>`, scope `node` | the module's `config_schema` | the operator, `hive-mind module config <module> set` |
| governed | `core:<key>`, or `x-<module>:<key>` with scope `fleet` / `fleet-node` | the core's governed table, or the module's schema | the owner, signed |

Every verb takes `--json` and prints one object: `{"schema": 1, "ok": true, …}`, or on a refusal
`{"schema": 1, "ok": false, "error": {"code", "id", "message", "hint"}}`. Exit codes: `0` ok, `1` refused with nothing
changed, `2` usage.

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

`hconfig set` and `unset` change what the operator could already change with no owner key: `node:` keys (through
`hv config node set`) and node-scope module keys (through `hive-mind module config <module> set|unset`). Nothing signed
is written in this release: a `core:` key, a module's `fleet` or `fleet-node` key and `node:owner_quota` are refused as
`owner-only`, with the exact command to run by hand in `hint`, and no write verb is started.

- The value is checked (type, enum, bounds) before any write verb starts, so a bad value never reaches the core. The
  core checks the rest.
- A secret (`secret: true`) is never taken on a command line: `hconfig set <id> VALUE` is refused as `secret-on-argv`.
  Give it with `--stdin` (one line); it reaches the core on stdin too, so it is in no process's argument list.
- A write compares the layer it writes: a `node:` or module-node value equal to the current *node-layer* value is a
  no-op (`changed: false`, nothing runs); a value equal only to the default or to another layer is still written.
- Refusals, each exit 1 with nothing changed: `unknown-key`, `not-installed`, `validation`, `secret-on-argv`,
  `read-only` (a `node:` key cannot be unset: the core has no verb for it), `env-overridden` (the key is shadowed by an
  environment variable; hconfig never writes an environment), `owner-only`, `core-failed` (the core verb's exit and one
  safe line of its message). No message carries a value.
- Writes serialize on one `flock` under `$HIVE_HOME/hconfig/` (a directory of mode 0700), and every write appends one
  line to `$HIVE_HOME/hconfig/audit.jsonl` (0600): `{ts, id, class, action, source, uid, pid, old, new, ok, error}`.
  `source` is `HCONFIG_SOURCE` (a short name such as `hdash`; anything else is `other`). `old` and `new` are left out for
  a secret. The file is a local convenience record, not evidence: any process of the same user can edit it. It is not
  synced. A change made directly with a core verb is not in it.

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
