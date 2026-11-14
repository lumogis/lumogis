# ADR-254: Export content gate for maintainer-only build paths (LUM-898, LUM-872)

**Status:** Finalised
**Created:** 2026-11-10
**Last updated:** 2026-11-10
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-10 (claude-opus-5-5)
**Plan:** none — shipped before formal plan / verify cycle for this chunk
**Exploration:** `.cursor/explorations/public_export_content_gate_retro.md`
**Draft mirror:** `.cursor/adrs/public_export_content_gate.md`
**Relates:** [ADR 081](081-lum-434-export-boundary-reconciliation.md) (public-export doc rule), [ADR 229](229-lum-855-kg-agpl-relicence.md) (the export gates in Python)

## Context

[ADR 081](081-lum-434-export-boundary-reconciliation.md) says public-shipping docs describe
the shipped clients on their own terms and do not name maintainer-only trees.
`scripts/check-public-export.sh` enforced that only for the public agent docs, the
beginners guide and `EVALUATION.md`. Everything else that ships, including every ADR,
had no content check.

The weekly code review found ADR 229 naming a maintainer-only build path
([LUM-898](https://linear.app/lumogis/issue/LUM-898)). The LUM-859 arbitration found the
same in ADR 097, along with a build-tool version pin
([LUM-872](https://linear.app/lumogis/issue/LUM-872)). An export-tree sweep then found
more exported files doing the same: 16 other ADRs, the reference manual,
`docs/capabilities.md`, `CHANGELOG.md`, two test files, a build-script comment, the root
`Makefile` and the export scripts themselves.

## Decision

1. **Reword, don't strip.** Each exported file that named a maintainer-only build path,
   or described one as withheld, is reworded neutrally ("the appliance", "the appliance
   build targets", "commercial components stay outside this licence"). The files keep
   shipping, so the next public snapshot replaces the old copies. No decision, status or
   date line changed. Each touched ADR has a `2026-11-10: public-boundary wording
   (LUM-898)` status-history line. With the option-2 amendment, the same rewording
   covers exported files that named other local-list paths (the build CI workflow, the
   bundled Core locks and input list, the fused-hub quarantine folder): ADRs 080, 082,
   094, 097, 100, 127, 153 and 223, the reference manual, the core test-coverage matrix,
   `orchestrator/requirements-core.txt` and `.github/workflows/web-e2e.yml`. The ADRs the
   owner accepted as they are (095, 101, 102, 116, 124, 149) were left alone. One ADR 097
   status-history line kept its date and lost only the lock path.
2. **Content and path gate.** `check-public-export.sh` gate 3 scans every exported file,
   as bytes and with any suffix, for a fixed list of maintainer-only path names. Any hit
   fails the export with `file:line`. Since 2026-11-10 it also fails any file, directory
   or symlink whose relative path contains a needle, reporting only the top-most match.
   It runs inside the existing Python gate block, so `make test-export-gates` exercises
   it the same way it exercises the token and runner gates.
3. **No allow-listed file (amended 2026-11-10, owner decision, option 2 "paths + neutral
   strip list").** Nothing that ships may name a listed path, the strip list included.
   The strip list is split in two:
   - `scripts/public-export-strip-list.txt` ships. Its entries and comments name nothing
     maintainer-only, and it lists the local list, so the local list never ships.
   - `scripts/public-export-strip-list.local.txt` is maintainer-only and has the same
     format. It holds every entry that names or exists only for the appliance: its tree
     and sub-entries, its make include, its build CI workflow, ADR 076, the bundled Core
     locks and requirements, and the deprecated fused-hub tree.

   `create-upstream-export-tree.sh` and `check-public-export.sh` each have one loader
   that reads the main list and then the local list, if present. Every strip-list read
   in both scripts goes through it. `check-rename-export-atomic.sh` and the capability
   doc test read both lists too. The former `CONTENT_ALLOW` exception is gone.
4. **The gate never carries its own needles.** The gate script, its shell tests and its
   pytest cases all ship in the public tree, so they build the names from parts at run
   time. A plain `grep` of the export tree for the literals, in paths or content, returns
   nothing.
5. **Fail closed without the local list.** If the source checkout holds the appliance
   tree or its make include (names built from parts), and the local list is missing,
   both scripts die before they write or check anything. The export script also checks
   `HEAD`, since it exports from `HEAD`. A public checkout has neither, so it runs on the
   main list alone.
6. **Generic local-list contract.** The LUM-492 contract used to hardcode the appliance
   build CI path and describe it. It is now `assert_local_strip_list_contract`: when the
   local list is present, every path on it, dangling symlinks included, is absent from
   the export tree. Its comments and messages name no path.
7. **Neutral export tooling prose.** The shipped export scripts and their tests do not
   describe the appliance, or its build CI, as private, proprietary, never public or
   stripped.
8. **Generic where possible.** The root `Makefile` now includes optional
   `Makefile.<name>.mk` extensions by wildcard instead of naming one.
   `scripts/check-rename-export-atomic.sh` checks every `apps/<name>/` tree rather than
   one named tree.

## Alternatives considered

- **Strip-list the offending ADRs.** Not chosen. Older copies are already public, and
  only an updated copy replaces them. Stripping would also hide decisions that are
  otherwise public.
- **Keep the strip list as the one allow-listed file** (the first version of this ADR).
  Superseded on 2026-11-10: the strip list ships, so it published the very paths the
  gate kept out of every other file.
- **Strip-list or rewrite the ADRs that make the appliance inferable** (ADR 095, 101,
  102, 116, 124, 149). Not chosen. The owner accepted that their decisions stay public
  as they are after the 2026-11-10 rewording.
- **Allow-list each offending file and fix them later.** Not chosen. An allow-list that
  starts at more than 20 entries would only ever grow.
- **Scan only `*.md`.** Not chosen. The leaks were also in a Rust comment, a TypeScript
  test regex, a Python docstring, a shell script and the `Makefile`.
- **Match the bare product name as well.** Not chosen. The product and binary name is a
  public product identity, used in shipped docs and in tests that assert UI copy does
  not contain it.

## Consequences

- **Easier:** an exported file that names a listed path fails the export before publish,
  in any file type.
- **Harder:** every new mention of a listed path in a shipping file fails the export.
  The fix is neutral wording, or a strip-list entry for a file that truly must not ship.
- **Harder:** a maintainer-only path goes on the local list, not the main list. Running
  either export script in a checkout that has the appliance tree but not the local list
  now fails.
- **Future work must:** keep the needles assembled from parts in any shipping file. Never
  add an allow-list entry without an ADR amendment. Add a needle when a new
  maintainer-only build path appears. Keep the main strip list's comments neutral.
- **Not covered:** prose that describes something as withheld without naming its path.
  That still relies on review and on the rule in `AGENTS.md`. Some public ADRs (095, 101,
  102, 116, 124, 149) still make the appliance's existence inferable; the owner accepted
  this on 2026-11-10.

## Revisit conditions

- A shipping file has a real need to name a listed path. Revisit the no-allow-list rule.
- The export moves from a deny-list to an allow-list ([ADR 102](102-repo-topology-cloud-services-boundary.md) pressure-relief valve 2). The gate may then become redundant.
- A maintainer-only build path is renamed. Update the needles in the same change.

## Linear linkage (Product OS)

- **Existing issues:** LUM-898 (primary), LUM-872 (ADR 097 line, folded in).
- **New issue needed:** see the session summary for the one remaining gap.
- **Historical evidence only:** no.

## Testing retrospective

- **Added:** eight appliance-path cases in `scripts/test-public-export-gates.sh` (five
  reject, three accept); `test_check_public_export_rejects_appliance_path_in_content`
  (two parameters) and `test_check_public_export_allows_appliance_path_in_strip_list_only`
  in `orchestrator/tests/test_check_public_export_script.py`.
- **Run:** `make test-export-gates` 31/31. Against the previous gate script, the five
  reject cases fail. Export pytest plus `test_default_user_remap_target.py`: 43 passed.
  The export tree built from the commit passes `check-public-export.sh`; a probe line
  added to an exported ADR fails it.
- **2026-11-10 amendment:** the "strip list may name it" shell case now rejects; new shell
  cases cover the local list, a nested tree path and the make include as a file name.
  New pytest cases: the strip list naming the path rejects, a file path naming it
  rejects (two parameters), the main list is neutral, both scripts fail closed without
  the local list (four parameters), a public-style checkout passes without it, any
  local-list path in the tree fails, the local list is on the main list and disjoint
  from the required public paths, and the built tree omits every local-list path. The
  LUM-492 tests that named the build CI path were replaced by these generic ones.
- **Gaps:** no automated check for withheld-without-a-path prose (see Consequences).

## Status history

- 2026-11-10: Finalised by /record-retro (retrospective).
- 2026-11-10: Amended for the owner's option-2 decision ("paths + neutral strip list"):
  strip list split into a neutral main list and a maintainer-only local list, fail
  closed without the local list, path check added to gate 3, `CONTENT_ALLOW` removed,
  LUM-492 contract made generic, 12 more exported files reworded (LUM-898).
