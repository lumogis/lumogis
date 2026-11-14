# ADR-233: navigator prepare reports existing artefacts through /find-artefacts (LUM-866)
**Status:** Finalised
**Created:** 2026-11-07
**Last updated:** 2026-11-07
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-07 (claude-opus-5-5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/navigator_prepare_find_artefacts_retro.md`
**Draft mirror:** `.cursor/adrs/navigator_prepare_find_artefacts.md`
**Linear:** LUM-866

## Context

ADR-203 (LUM-300) retired the Linear evidence index and its scripts. `/find-artefacts` (LUM-801) replaced it. The navigator skill's `/navigator prepare` step was never updated: it told the agent to run an empty bash block and grep a register that no longer exists. Every prepare run had to work out that the step was dead. The LUM-852 run hit it twice.

## Decision

`/navigator prepare LUM-###` reports what the repos already hold for the issue by running the `/find-artefacts` procedure, read-only, and listing the results under `## Existing artefacts`.

- The section is informational. It may change the routing (an existing ready plan, an existing exploration), but it writes and links nothing.
- The run-workflow prepare prompt (`runNavigatorPreparePhase`) and its prompt template ask for the same section.
- The `NAVIGATOR_PREPARE_RESULT` block is unchanged.

## Alternatives considered

- **Delete the section outright.** Not chosen: knowing an issue already has a plan or exploration is the most useful routing input prepare can have, and `/find-artefacts` provides it without a register.
- **Add artefact paths to `NAVIGATOR_PREPARE_RESULT`.** Not chosen now: nothing parses them. Revisit when something does.

## Consequences

- Every step in `/navigator prepare` can be executed.
- One `/find-artefacts` pass per prepare run.
- A register-backed lookup must not come back without a new ADR superseding ADR-203.

## Revisit conditions

- `/find-artefacts` changes its output or is retired.
- run-workflow needs artefact paths in machine-readable form.

## Linear linkage (Product OS)

- **Existing issue:** LUM-866
- **New issue needed:** no
- **Historical evidence only:** no

## Testing retrospective

No tests added (skill prose and one prompt string). `drift_check` exit 0 with 0 errors; the run-workflow script syntax check exit 0. No live prepare run yet.

## Status history

- 2026-11-07: Finalised by /record-retro (retrospective).
