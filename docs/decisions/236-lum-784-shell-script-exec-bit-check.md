# ADR-236: Committed shell scripts must be 100755 in the git index (LUM-784)
**Status:** Finalised
**Created:** 2026-11-07
**Last updated:** 2026-11-07
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-07 (claude-opus-5-5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/shell_script_exec_bit_check_retro.md`
**Draft mirror:** `.cursor/adrs/shell_script_exec_bit_check.md`
**Linear:** LUM-784

## Context

This repo runs with `core.fileMode=false`, so git ignores permission bits and a script committed from such a checkout lands as 100644. Nothing warns, and the failure only appears on a clean checkout: `docker/egress-proxy/entrypoint.sh` (LUM-758) failed with "permission denied" at container start in three compose overlays. One file was fixed; the class was not. An audit found 31 scripts in the scoped trees that were 100644.

## Decision

`scripts/check-exec-bits.sh` reads modes from the git index (`git ls-files -s`) and fails when a tracked `*.sh` under `docker/`, `scripts/`, `.github/scripts/`, `stack-control/` or `tests/` is not 100755.

- Exemptions live in `scripts/exec-bit-allowlist.txt`, one path and a reason per line. Today: `scripts/backup/lib/common.sh` (sourced library).
- Scripts run only through `bash` still get the bit, so the rule needs no judgement about how a script is invoked.
- `make exec-bit-check` runs it, and so does the `lint-and-test` CI job.
- 30 scripts were set to 100755 in one mode-only commit.

## Alternatives considered

- **`.gitattributes`.** Not chosen: it cannot set file modes.
- **Invoke every entrypoint through an interpreter.** Not chosen: it fixes call sites one at a time and leaves direct `./script` calls (e.g. `make release-doc-sync-check`) exposed.
- **Unset `core.fileMode` locally.** Not chosen: it fixes one machine, not the class.
- **A pre-commit hook.** Not chosen now: CI on `dev` is the net (LUM-788).

## Consequences

- A non-executable script in scope turns `dev` CI red instead of failing at container start.
- A new sourced-only library needs an allowlist line.
- Trees outside the scope (`clients/`, `deprecated/`, and others) are not checked.

## Revisit conditions

- A script outside the scope fails with "permission denied" on a clean checkout: widen the scope after auditing that tree.
- The allowlist grows past a handful of entries: adopt a naming convention for libraries instead.

## Linear linkage (Product OS)

- **Existing issue:** LUM-784
- **New issue needed:** no
- **Historical evidence only:** no

## Testing retrospective

The check was red with 30 offenders before the mode fix and green after; `tests/test_check_exec_bits_script.py` covers green, red, allowlist and scope. The CI step has not run on a runner yet.

## Status history

- 2026-11-07: Finalised by /record-retro (retrospective).
