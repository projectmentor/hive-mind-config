# hconfig command line and JSON protocol

This is the reference a dashboard (hdash) is written against: every verb, the envelope it prints, the error codes and the exit
codes. The README says what hconfig is; this says exactly what it prints. `tests/test_doc_sync.py` fails when a verb
here does not exist, or a verb that exists is not here.

hconfig 0.1 is a subprocess, not a service. A caller starts `hconfig <verb> --json`, reads standard output to the end, and
reads the exit code. Nothing is cached between calls and nothing stays running.

## Calling it

- **Always pass `--json`.** Output is then exactly one JSON object on standard output (`status` and `doctor` print theirs
  on one line). Without it the output is text for a person and is not a protocol.
- **Standard input is closed** unless the verb is `set --stdin`, which reads one line.
- **Set `HCONFIG_SOURCE`** (a short name, such as `hdash`) so the audit line says who asked. Any other value is recorded as
  `other`.
- **Environment.** hconfig runs `hv` and `hive-mind` from `PATH` (`HCONFIG_HV`, `HCONFIG_HIVE_MIND` name others) and uses
  `HIVE_HOME` to find the hive. Pass `HIVE_KEY_DIR` through if the caller has it; `hconfig doctor` warns when it differs
  from the hive's `.key-dir`. The owner variables (`HIVE_OWNER_PASSPHRASE`, `HIVE_OWNER_KEY_PASSPHRASE`,
  `HIVE_OWNER_SEED`) are removed from the environment of every program hconfig starts, so do not pass them on.
- **Time.** A core verb that does not answer in 30 seconds is `core-failed`.

## Envelope

Every verb but `status`, `doctor` and `version` prints:

```json
{"schema": 1, "ok": true, "...": "the verb's fields"}
```

and on a refusal:

```json
{"schema": 1, "ok": false, "error": {"code": "validation", "id": "node:port", "message": "…", "hint": "…"}}
```

`error.id` is the key the refusal is about, or `null`. `message` and `hint` are text for a person; branch on `code`. **No
message carries a value**, so a page can show them as they are.

`schema` is the protocol version. A caller that sees a number it does not know should stop and say so. New fields may be
added within `schema: 1`; ignore fields you do not know.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | ok (including a write that changed nothing: `changed: false`) |
| `1` | refused, nothing changed (every error code below except `owner-locked`); also `validate` with an error in it |
| `2` | usage (a bad flag or a missing argument); the message is on standard error, not JSON |
| `5` | the owner key is locked: `owner-locked` |

### Error codes

| `error.code` | When | Exit |
|---|---|---|
| `unknown-key` | the id is in no source | 1 |
| `validation` | the value fails the key's type, enum or bounds, before any write verb starts | 1 |
| `secret-on-argv` | a secret was given on the command line; give it with `--stdin` | 1 |
| `read-only` | the key cannot be written this way (a `node:` key cannot be unset; a fleet value cannot be removed) | 1 |
| `env-overridden` | an environment variable shadows the key; hconfig never writes an environment | 1 |
| `owner-only` | the key is signed by the owner outside the agent's allowlist (`core:` keys, `node:owner_quota`); `hint` has the exact command to run by hand | 1 |
| `owner-locked` | a signed write needed the owner key and the core refused to prompt (agent locked, or up but not signing this act); `hint` names the module to unlock for and the directory the agent's socket was looked for in | 5 |
| `not-installed` | the module named is not installed | 1 |
| `core-failed` | a core verb exited non-zero, did not run, or did not print JSON; `message` carries its exit and at most one safe line of its message | 1 |

## The record

`schema`, `list`, `get`, `explain` and the write verbs return records. A key is spelled the way the journal spells it:
`node:<key>`, `x-<module>:<key>`, `core:<key>`.

| Field | Meaning |
|---|---|
| `id` | the spelling above |
| `class` | `node`, `module` or `governed` |
| `scope` | `node`, `fleet` or `fleet-node` |
| `type` | `string`, `int`, `float`, `bool`, `enum` or `list` |
| `default` | the schema default, or `null`; `********` for a secret that has one |
| `values` / `min` / `max` | the choices of an enum; the bounds of a number, where there are any |
| `description` | text |
| `secret` | the value is never printed, never taken on argv |
| `identifying` | a fleet value of this key names a person or a place and is visible to every admitted device for good |
| `visibility` | `operator` or `viewer`: whether hdash may show it to an allowlisted tailnet login. A hint for hdash's viewer listener, which enforces it; hconfig does not |
| `writable_by` | `operator` or `owner` |
| `write_via` | the core command that writes the key (what to run by hand for an `owner-only` key) |
| `warnings` | text lines; an `identifying` fleet key carries one |
| `set` | `true` when a value is stored in some layer |
| `source` | `default`, `node`, `fleet-node`, `fleet`, `env` or `null` |
| `value` | the merged value the core reports (`********` for a secret), or `null` |
| `set_by` | `{device_id, ts, sid}` of the last signed change for a `core:` key; `null` otherwise |
| `env` | node keys only: the variable that would override the key |
| `template`, `instance_of` | a template key such as `x-m:seat.{role}.enabled` is listed once with `template: true`; each concrete key set under it follows with `instance_of` naming it |

`schema` returns records without values (`set`, `source`, `value`, `set_by` are absent).

A caller that serves viewers (allowlisted tailnet logins, not admitted devices) serves a record only when `visibility` is
`viewer`, and then without `set_by`, `write_via` or any audit line. A `node:` key, a secret and an `identifying` value must
never reach a viewer. hconfig labels; the caller enforces. **Known gap in 0.1:** a governed key with `identifying: true` is
labelled `visibility: viewer` (the golden registry shows it). Until that is fixed, a caller must also test `identifying`
itself and withhold the value.

## Verbs

### hconfig schema

`hconfig schema [PREFIX] --json` → `{schema, ok, keys: [record…], warnings: [text…]}`. Every known key and its schema, without
values. `PREFIX` keeps ids that start with it (`node:`, `x-alpha:`, `core:cap`). A module whose schema could not be read is
named in `warnings` and skipped; the rest is still returned.

### hconfig list

`hconfig list [PREFIX] [--class node|module|governed] --json` → the same shape with values. Values are the core's merged answer,
never recomputed here.

### hconfig get

`hconfig get ID --json` → `{schema, ok, key: record}`. `unknown-key` if there is no such id.

### hconfig explain

`hconfig explain ID --json` → `{schema, ok, key: record, derived: [{code, text}…]}`. `derived` are the consequence lines the
value has for this hive, from the core's `config show` alone (for example, a single-principal hive with `core:cap_self`
set, or an environment variable that shadows a file value). `code` is stable; `text` is for a person.

### hconfig set

`hconfig set ID [VALUE] [--stdin] [--device ID] [--non-interactive] --json` → `{schema, ok, id, action: "set", changed, key:
record, warnings, note?}`.

- The value is the argument, or one line of standard input with `--stdin`; both, or neither, is a usage error (exit 2). A
  secret is accepted only with `--stdin`.
- Type, enum and bounds are checked before any write verb starts.
- `changed` is `false`, and nothing ran, when the value equals what the layer being written already holds (the node file's own
  entry, or the journal's entry for the full fleet key). A value equal only to the default or to another layer's is still
  written.
- `--device k1:<16 hex>` names the device of a `fleet-node` key; the default is this device.
- Signed writes (`fleet`, `fleet-node`) go through the core with `--non-interactive`: the owner-unlock agent signs if it holds the
  key, else the verb exits 5 and the answer is `owner-locked` (exit 5). Nothing is ever read from standard input to unlock.
  `--non-interactive` is accepted for callers; hconfig never prompts.
- `core:` keys and `node:owner_quota` are `owner-only` with `write_via` in `hint`; no write verb is started.

### hconfig unset

`hconfig unset ID --json` → the same shape with `action: "unset"`. Removes a node-scope module key's value. `node:` keys and
fleet values cannot be removed (`read-only`).

### hconfig validate

`hconfig validate [MODULE] --json` → `{schema, ok, errors: [{id, message}…], warnings: [{id, message}…]}`. The core's own
validator for every installed module with a schema (or the one named; `not-installed` if there is none), and, with no module
named, the stored node keys against the core's schema. `ok` is `false`, and the exit code `1`, when `errors` is not empty.
Messages name a key and a rule, never a value.

### hconfig doctor

`hconfig doctor --json` → `{schema: 1, results: [{level, check, message, fix_hint?}…]}` on one line; always exit 0. `level` is
`ok` or `warn`. The checks, by `check` name: `core-verbs` (the core's config verbs answer; when they do not, it is the only
row), `config` / `config-invalid` / `config-unknown-key` / `config-warning`, `env-override`, `governed-range`, `key-dir`,
`agent-socket-dir`, `audit-file`, `node-file-audit-gap`, `peers-reread`.

`key-dir` and `agent-socket-dir` are the two that explain an `owner-locked` the operator did not expect: `HIVE_KEY_DIR` differs from
the hive's `.key-dir`, or the owner agent's socket is in the runtime directory this process does not look in.

### hconfig status

`hconfig status --json` → `{schema: 1, state: "ok"|"degraded", version, problems: [text…], detail: {}}` on one line. The module
contract's status answer: `ok` when the core's config verbs answer.

### hconfig version

`hconfig version` prints `hconfig <version>`. Text only.

## Not in 0.1

There is no `audit` verb: the audit file (`$HIVE_HOME/hconfig/audit.jsonl`, 0600, local, never synced) is written by every
`set` and `unset` and is read by hand. There is no listener, no socket and no cache; a caller that wants a change must call
again.
