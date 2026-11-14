# ADR-245: Caddy closes idle Core connections before uvicorn does (LUM-875)
**Status:** Finalised
**Created:** 2026-11-08
**Last updated:** 2026-11-08
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-11-08 (claude-opus-5-5)
**Plan:** none — shipped as a targeted `exec:claude` fix without a plan / verify cycle
**Exploration:** `.cursor/explorations/caddy_upstream_keepalive_retro.md`
**Draft mirror:** `.cursor/adrs/caddy_upstream_keepalive.md`
**Linear:** LUM-875

## Context

Now and then, `POST /api/v1/auth/refresh` got a 502 from Caddy without reaching Core, and Lumogis Web treats a failed refresh as signed out. It was seen twice in about 60 Playwright tests on lumogis-dev on 2026-11-06. Caddy's error log had `"msg":"EOF"`, and Core logged nothing.

Core ran `uvicorn main:app` with uvicorn's default `--timeout-keep-alive` of 5 s. Caddy's `reverse_proxy` pools upstream connections and keeps an idle one for 2 minutes by default. When uvicorn closes an idle connection at the moment Caddy reuses it, the request gets EOF or connection reset. Go's transport, which Caddy uses, retries only replayable (idempotent) requests on a reused connection, so a POST fails and the client sees 502.

On an idle host the race window is under a millisecond: 288 parallel attempts at 4.990–5.010 s gaps through the real Caddyfile gave no 502. It opens up when Core's event loop is busy across the deadline. uvloop runs due timers before it polls for I/O, so after a stall the keep-alive timer closes the connection before uvicorn reads a request that arrived during the stall. That matches the trace, where the failing refresh came 5.2 s after the previous response, not 5.0 s.

## Decision

The proxy's idle timeout must be shorter than the upstream's keep-alive, so Caddy is always the side that closes an idle connection.

- **uvicorn keeps idle connections for 75 s:** `--timeout-keep-alive 75` in `orchestrator/Dockerfile`'s `CMD` and in `docker-compose.dev.yml`'s orchestrator `command:`, which replaces the CMD.
- **Caddy keeps idle Core connections for 60 s:** every orchestrator route in `docker/caddy/Caddyfile` uses `import to_orchestrator`, a snippet that sets `transport http { keepalive 60s }`. The `/events*` block keeps its own `reverse_proxy` and sets the same `keepalive 60s` in its transport. That covers all 27 orchestrator `reverse_proxy` handlers (checked with `caddy adapt`: every `orchestrator:8000` handler has `idle_timeout` 60 s).
- **The invariant is tested** in `orchestrator/tests/test_uvicorn_keepalive_vs_caddy.py`. Every `reverse_proxy` to `orchestrator:8000` must set a keepalive below uvicorn's `--timeout-keep-alive`. A bare one counts as Caddy's 2-minute default. The dev overlay must use the same value as the Dockerfile.
- **The live race is reproducible:** `scripts/check-caddy-upstream-keepalive.sh` (`make caddy-keepalive-check`) runs throwaway containers and never touches the running stack.

### As-implemented surface

| Item | Value |
| --- | --- |
| uvicorn flag | `--timeout-keep-alive 75` (Dockerfile CMD, dev overlay) |
| Caddy snippet | `(to_orchestrator)`: `reverse_proxy orchestrator:8000 { transport http { keepalive 60s } }` |
| SSE block | `transport http { … keepalive 60s }` |
| Static guard | `orchestrator/tests/test_uvicorn_keepalive_vs_caddy.py` (5 tests) |
| Live check | `make caddy-keepalive-check` (`ARGS="--around 75"`, `--uvicorn-args`, `--caddyfile`, `--routes`) |
| Unchanged | SPA upstream `lumogis-web:80` (Caddy default against nginx's 75 s; static GETs, which Go retries); native Core launcher (loopback, no pooling proxy; code note in place) |

## Alternatives considered

- **Raise uvicorn's keep-alive only, as the issue suggested (`75`).** Not chosen alone: Caddy's default idle timeout is 2 minutes, so the race just moves to 75 s. Measured with the live check (`--around 75` with the pre-fix Caddyfile): 4 of 5 probes got 502.
- **Raise uvicorn above Caddy's default (for example 130 s) and leave the Caddyfile alone.** Not chosen: it depends on an implicit default of a floating `caddy:2-alpine` tag.
- **Cap Caddy's keepalive below 5 s and keep uvicorn's default.** Not chosen: it gives up most connection reuse, and it still relies on Caddy noticing uvicorn's close in time.
- **Enable Caddy retries (`lb_retries` / `lb_try_duration`) for POSTs.** Rejected: replaying a non-idempotent request is unsafe in general (refresh-token rotation, chat, ingest).
- **Set `UVICORN_TIMEOUT_KEEP_ALIVE` as an image `ENV`.** Not chosen: an explicit flag on the command is visible where the launch is defined, and the static test can read it.

## Consequences

- An orchestrator route added to the Caddyfile must use `import to_orchestrator`, or set its own `keepalive` below 75 s. A bare `reverse_proxy orchestrator:8000` fails the static test.
- Changing either number means changing the other. The test asserts that Caddy's keepalive is less than uvicorn's.
- Any new launch path that runs Core's uvicorn behind a pooling proxy needs `--timeout-keep-alive` above that proxy's idle timeout. The native Core launcher has a code note for the day LAN exposure puts a proxy in front of it.
- Core holds idle upstream connections for up to 60 s (Caddy closes them first). Each costs little on uvicorn's event loop.
- LUM-887 (deployment hardening, run-workflow in progress on 2026-11-08) plans a `(to_orchestrator)` snippet with `header_up X-Forwarded-For {client_ip}` over the same one-liners. The snippet name was chosen to match, so the `import to_orchestrator` lines merge cleanly. The snippet body and the `/events*` transport block need a manual merge that keeps both `header_up` and `keepalive 60s`. The static test fails if `keepalive` is dropped.

## Revisit conditions

- Caddy's `reverse_proxy` gains automatic retries for non-idempotent requests on a reused connection, or a Caddy release changes how `keepalive` is configured.
- Core moves off uvicorn, adds workers, or runs behind another proxy (for example LAN exposure for native Core, or a Tailscale/Cloudflare front proxy that pools connections to Caddy).
- `lumogis-web` starts serving non-idempotent routes through its nginx, which then needs the same cap.
- Any new 502 with upstream `EOF` or `connection reset` in Caddy's error log.

## Linear linkage (Product OS)

- **Existing issue:** [LUM-875](https://linear.app/lumogis/issue/LUM-875/intermittent-502-on-authrefresh-logs-the-user-out-caddy-reuses-a-keep)
- **New issue needed:** no
- **Historical evidence only:** no. LUM-875 is active; suggested status In Review until the fix is on the dev stack.
- **Follow-ups:** the dev-stack confirmation (no upstream `EOF` in Caddy's error log over a Playwright lane after the rebuild) is P2, recorded in the LUM-875 closing comment. The SPA upstream and the native Core launcher are in-code notes. No new issues.

## Testing retrospective

- **Added:** `orchestrator/tests/test_uvicorn_keepalive_vs_caddy.py` (5 tests); `scripts/check-caddy-upstream-keepalive.sh` + `make caddy-keepalive-check`.
- **Fails without the fix:** the static test against the pre-fix Dockerfile, dev overlay and Caddyfile gave 2 failed, 3 passed. With the fix: 5 passed.
- **Live, before and after:** before, uvicorn's default with the pre-fix Caddyfile, 29/30 probes got 502 (Caddy: `read: connection reset by peer`) over six route classes (`/api/*`, `/v1/*`, `/mcp/*`, `/events`, `/ingest`, `/settings`). After, 0/30. With uvicorn at 75 s and the pre-fix Caddyfile at a 75 s gap, 4/5 got 502. With the full fix at a 75 s gap, 0/5.
- **Gates:** `make lint` 0; `make exec-bit-check` 0; `make changelog-check` 0; `make compose-policy-check` 0 (temporary `.env` from `.env.example`); `docker compose -f docker-compose.yml -f docker-compose.dev.yml config` 0; `caddy validate` 0; `shellcheck` 0.
- **Pre-existing failure, not this change:** `tests/test_single_worker_boot_gate.py::test_lifespan_refuses_before_touching_any_backend` fails on the untouched baseline `3395a2d2` as well.
- **Not run:** the Playwright lane on the dev stack, which was not rebuilt (shared stack; overnight run rule). `tests/integration/test_caddy_security_headers.py` was also not run; the header block is unchanged.
- **`docs/testing/automated-test-strategy.md`:** no update needed. **Release skills:** no change.

## Status history

- 2026-11-08: Finalised by /record-retro (retrospective).
