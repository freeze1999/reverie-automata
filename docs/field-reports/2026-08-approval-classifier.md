# Field report: when the safety layer became the work

**Window:** 2026-07-05 to 2026-08-29

**System:** a long-running Reverie Automata instance

**Contributor:** Reverie Automata (discovery, diagnosis, patch drafts, test
matrix, and post-deployment probes)

**Maintainer role:** protected-file application, clean-room public port,
privacy review, and release

This is the complete public account of a production finding. Private prompts,
credentials, personal messages, hostnames, filesystem layout, business data,
and model-provider details have been removed. Counts, dates, state transitions,
test cases, and technical conclusions are preserved.

## Executive result

The instance accumulated 89 approval records. A manual review of every concrete
artifact found that most did not need a human risk decision:

| Observed class | Count | Public interpretation |
|---|---:|---|
| Local read-only commands and queries | 57 | Non-mutating; allow only inside the installation's configured read boundary |
| Reversible scratch, cycle-record, or internal-ledger writes | 18 | Candidate for deterministic, snapshot-backed execution |
| Network/API reads | 5 | Candidate only behind an endpoint allowlist |
| Concrete live protected-code patch | 1 | Staged change: test, canary, rollback |
| Task-level intent records | 8 | Not executable authority; classify the eventual artifact |
| **Total** | **89** | |

Their final recorded states were 62 executed, 18 invalidated, 9 expired, and 0
pending. The 89 rows are the complete approval history at the observation
boundary, not a sample. The classes are mutually exclusive and were assigned by
reviewing each stored concrete artifact, rather than accepting the planner's
risk label or the automata's journal as evidence.

The dominant failure was not a permissive agent. It was an over-broad safety
classifier that repeatedly treated *mentioning* a protected object as *changing*
it. “Read-only” here means non-mutating, not universally harmless: installations
must still keep credentials and other confidential inputs outside the agent's
read boundary. The resulting approval and recovery threads became fresh work for
the next cycle. The safety layer had created a self-renewing workload about
itself.

## What the automata found

The first concrete reproduction was structurally simple:

```sh
sed -n '105,120p' "$PROTECTED_FILE"
```

It was read-only, yet an apply-time check scanned the command for a protected
path without first proving that the command could write. The automata diagnosed
the missing read/write distinction and drafted an `_is_write_cmd` fix with a
regression matrix.

The next cycle falsified its own initial closure claim. A second enforcement
site, the pre-tool inspector, still contained a broad redirect expression. This
read-only probe produced another approval:

```sh
ls -la "$PROTECTED_DIR" 2>&1
```

That established two independently implemented decision sites:

1. call-time classification, before the tool ran;
2. apply-time reinspection, before an approved artifact ran.

Fixing only one could not stop the loop. The automata consolidated the repeated
approval threads into one canonical repair record, produced patches for both
sites, and explicitly reported that its own protected-path policy prevented it
from applying the repair. That refusal was correct: discovering a safety-layer
bug did not grant authority to rewrite the safety layer.

A later probe found a narrower parser defect. The literal expression below was
mistaken for redirection even though `>` was inside single quotes:

```sh
grep -n '>{1,2}' "$PROTECTED_FILE"
```

The replacement scanner became quote-aware. Because an outer string classifier
cannot safely interpret a second shell grammar, `sh -c` and `bash -c` remain
fail-closed rather than having their quoted programs guessed at.

## Timeline

| Date | Event |
|---|---|
| 2026-07-05 | First approval in the reviewed ledger. |
| 2026-08-21 | The automata traced a read-only protected-file probe that was refused at apply time. It drafted a write-vs-read discriminator and tests. |
| 2026-08-22 | A live `2>&1` reproduction proved the first repair covered only one of two enforcement sites. The automata identified the call-time redirect classifier, drafted the second patch, and consolidated the repeated recovery threads. |
| 2026-08-23 | It declined to keep retrying a change it could not authorize and maintained a single operator handoff instead of multiplying repair requests. |
| 2026-08-25 | It found the quoted-`>` false positive, replaced regex-only redirect detection with a quote-aware scanner, and named nested-shell parsing as a separate boundary. |
| 2026-08-27 | The protected implementation was applied outside the cycle's authority boundary. |
| 2026-08-28 | Live probes passed: quoted `>`, `2>/dev/null`, `2>&1`, and `>&2` no longer filed approvals; real protected-path redirects remained blocked. |
| 2026-08-29 | The automata reported remaining classifier friction, including opaque `execute_code` calls. The approval ledger reached 89 resolved rows. |

## Regression contract

These must remain allowed:

```sh
ls "$PROTECTED_FILE" 2>/dev/null
grep x "$PROTECTED_FILE" 2>&1
grep x "$PROTECTED_FILE" >&2
grep -n '>{1,2}' "$PROTECTED_FILE"
printf 'literal > and >>'
```

These must remain blocked when their target is protected or unresolved:

```sh
echo x > "$PROTECTED_FILE"
echo x 1>"$PROTECTED_FILE"
echo x &>"$PROTECTED_FILE"
echo x >>"$PROTECTED_FILE"
exec 3<>"$PROTECTED_FILE"
echo x 2>&"$OUTPUT_FD"
sh -c 'echo x > "$PROTECTED_FILE"'
```

The public tests encode this contract. They test behavior, not the private
instance's paths or plugin layout.

## What remains unresolved

The field fix removed the redirect loop. It did not make arbitrary shell safe,
and it did not justify blanket self-approval.

- Opaque code-execution tools cannot be proven read-only from their names.
- A command may contain multiple shell programs and substitutions.
- A protected path may be a read-only source to `cp`, while the destination is
  the only mutated object; token-presence classifiers cannot express that.
- Read-only service and scheduler introspection is lower risk than mutation, but
  installations may deliberately keep those command families fail-closed.
- The same process must not be able to rewrite both the action and the policy
  that authorizes the action.

The architectural conclusion is therefore **capability release, not arbitrary
self-approval**. A deterministic policy can auto-run local reads and narrowly
scoped reversible writes. Protected-code changes need an external snapshot,
tests, a canary, and a rollback actuator outside the automata's writable body.
Irreversible egress, credential access, scheduler/service mutation, destructive
deletion, and changes to the inspector or backup gate still require a separate
authority.

## Clean-room boundary

The production finding and the public implementation deliberately differ:

| Private operational record | Public repository |
|---|---|
| Instance-specific absolute paths | Configured `protected_paths` and temporary fixtures |
| Messaging cards and operator identifiers | Generic approval semantics |
| Raw cycle journals and private thread bodies | Technical chronology and minimal reproductions |
| Two installation-specific enforcement modules | One reusable `Inspector` contract |
| Internal service topology | No host or deployment identifiers |
| Full approval artifacts, some containing sensitive commands | Complete aggregate counts and sanitized command classes |

No private memory, prompt, credential, account identifier, personal message, or
business record is needed to reproduce the bug or verify the fix.

## Attribution

This was not a retrospective bug hunt assigned by a maintainer. The running
Reverie Automata instance encountered the false positive, reproduced it across
cycles, corrected its first incomplete diagnosis when the loop returned,
generated the repair artifacts and test matrix, respected the boundary that
prevented it from editing its own inspector, and later ran the live validation
probes.

The human maintainer supplied the protected application step and the privacy
boundary for publication. The public commit preserves that division of work:
machine discovery and technical contribution, human review and release.
