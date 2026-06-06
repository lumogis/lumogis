# Debug test runners (LUM-377)

Summary-first wrappers around existing **`make`** targets. Full output is teed to
**`target/debug-logs/`** (gitignored); stdout shows a short summary.

## Quick commands

```bash
make test-list          # inventory table (all suites + stages)
make debug              # fast chain: unit → lint → web unit → rust (skips rust when absent)
./scripts/debug/unit.sh
./scripts/debug/logs.sh last
```

## Heavy / destructive suites

Integration, RC, graph-parity, restart-e2e, and web e2e require an explicit opt-in:

```bash
./scripts/debug/cli.sh integration graph-parity --heavy
LUMOGIS_DEBUG_HEAVY=1 ./scripts/debug/cli.sh integration rc
./scripts/debug/web.sh e2e --heavy
```

## Release gates

**`make verify-public-rc`** and **`make verify-public-rc-full`** are **not** wrapped
here — run them directly on the release line. They appear in **`make test-list`**
with `wrapper: none`.

## Environment

| Variable | Purpose |
| --- | --- |
| `LUMOGIS_DEBUG_COMPOSE` | Use `compose-test` / `compose-lint` instead of venv |
| `LUMOGIS_DEBUG_HEAVY` | Allow integration/web e2e without `--heavy` |
| `LUMOGIS_DEBUG_FAIL_FAST` | Unset = stop chain on first failure; `0` = run all fast stages |
| `LUMOGIS_DEBUG_LOG_DIR` | Override log directory (tests use tmpdir) |
| `LUMOGIS_DEBUG_RUST_BUNDLED` | `cargo test --features bundled` |

See [docs/testing/automated-test-strategy.md](../../docs/testing/automated-test-strategy.md).
