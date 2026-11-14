# The live paperless-ngx fixture (LUM-309)

A throwaway paperless-ngx server that the connector lane runs against, so the
paperless adapter is exercised by a real REST API instead of by fakes.

```bash
make compose-test-paperless
```

That is the whole interface. It brings the fixture up, waits for health, runs
both live modules, and always tears down — including on a failing assertion.

---

## Why it exists

Every paperless test in the repository monkeypatches
`PaperlessPoller.fetch_documents_page` wholesale. The adapter's request layer —
its URL, its params, its auth header, its envelope parsing, its terminal
condition — had therefore **never made a real request** to anything.

That is not a theoretical gap. Planning this lane surfaced **LUM-747** before a
line of code was written: the fakes modelled the end of a page walk as an empty
page, the real server returns a 404, and the adapter retried that 404 through
its full backoff ladder — ~31 seconds of sleeping and an ERROR log on *every
healthy poll tick*. A fake that models the server wrongly does not fail; it
agrees with you.

## What it covers

| Lane | Module | Proves |
|---|---|---|
| Adapter | `orchestrator/tests/integration/test_paperless_live_adapter.py` | The HTTP contract: content round-trip, real 401 handling, strict `added__gt`, ascending `added`, pagination to the terminal 404, the `Token` scheme, blank-content skipping |
| Pipeline | `orchestrator/tests/integration/test_paperless_live_pipeline.py` | Poll → ingest into real Postgres `external_documents` and real Qdrant points, watermark advance, dedup, `user_id` isolation, the ADR-062 page-boundary case |

## What it does **not** cover

Read this before treating a green run as broader assurance than it is.

- **Retrieval quality.** The pipeline lane injects a hash-based embedder and
  retrieves points by *computed id*, never by search. The vectors are
  meaningless. Any assertion here about relevance or ranking would be invalid.
- **OCR.** `PAPERLESS_OCR_MODE=skip_noarchive`, and seeds are plain `.txt`. The
  connector reads the `content` field and does not care how it was produced.
- **Correspondents and tags** — not fetched by the adapter today (LUM-283).
- **Writing to paperless** as a product feature (LUM-725). The seeding helper
  uses `post_document`, and LUM-725 inherits that call's shape, but nothing here
  hardens it as a client.
- **Deletion reconciliation** (LUM-733).

`make test` **collects** this lane (the `paperless_live` marker is for **`make compose-test-paperless`** to select with — **ADR 194** / **LUM-753** removed the old `-m "not paperless_live"` deselection). Without the compose fixture the tests **skip** and the LUM-718 terminal summary reports how many integration items validated nothing. A green `make test` on a laptop is therefore *not* evidence that any of this works. The gate is the dedicated make target.

## Cost

~**75 seconds** cold on a developer laptop, of which ~55s is the paperless boot.
Step 0 measured a 62.9s warm boot; the tests themselves are ~15s (adapter) and
~25s (pipeline).

The timeouts are deliberately **not** tuned to those numbers. `start_period:
180s`, `retries: 12` and `--wait-timeout 420` all stay generous, because a
GitHub runner is materially slower than the laptop this was measured on, and a
healthcheck tuned to a warm local boot is exactly how a gate becomes flaky in CI
only. Treat >4 minutes as a regression worth investigating.

## Version floor and pinning

The image is pinned **by digest** in `docker-compose.paperless-test.yml`:

```
ghcr.io/paperless-ngx/paperless-ngx@sha256:3838b9a4260d23acc5bb63aed407138435e70b56e5806f4baa350ca184e57582
```

paperless-ngx **3.0.4**, captured by the Step 0 spike on 2026-07-30.

**Operator floor: ≥ 2.16.2.** LUM-309's original description claimed ≥2.16.1
"for the `added__gt` timezone fix". That attribution is wrong, and the changelog
says so: [v2.16.1](https://github.com/paperless-ngx/paperless-ngx/releases/tag/v2.16.1)
ships PR #9976, which fixes **`created`** date filtering broken in 2.16.0 (a
`gte` typo), not an `added__gt` timezone fix.
[v2.16.2](https://github.com/paperless-ngx/paperless-ngx/releases/tag/v2.16.2)
fixes `created` datetime coercion (#10026, #10285) — again `created`. No
changelog entry names `added__gt` and timezones at any 2.16.x release. 2.16.2 is
recommended as the floor because of the MariaDB skip warning affecting
2.16.0–2.16.1, not because of `added__gt`.

## Live-shape facts worth knowing

Recorded here rather than only in the plan, because LUM-725 and LUM-733 both
need them.

- **A page past the last returns HTTP 404**, body `{"detail":"Invalid page."}` —
  not an empty 200. This is DRF's `PageNumberPagination`. It is the whole of
  LUM-747.
- **An *empty* first page returns 200 with `count: 0`**, because DRF's
  `allow_empty_first_page` defaults True. So a 404 on page 1 never means "the
  archive is empty" — it means the endpoint is wrong. The adapter warns on that
  case and logs the past-the-end case at INFO.
- **`added` is read-only over the REST API.** `PATCH {"added": ...}` returns
  **200** and changes nothing — DRF serialises the object having silently
  ignored the read-only field. The server's own OpenAPI schema
  (`/api/schema/?format=json`) lists the writable document fields and `added` is
  not among them; **`created` is**. A spike that checks only the status code
  will conclude the opposite, and LUM-309's Step 0 Obs 7 did exactly that.
  Manufacturing an equal-`added` precondition therefore needs a write to
  paperless's own database — see `force_equal_added()` in `_paperless_seed.py`.
- **A `.txt` upload populates `content`** without any OCR pass, so no text-layer
  PDF is needed for seeding.
- **The root path returns 302**, and `urllib.request.urlopen` *follows* it and
  reports 200. `curl` without `-L` sees the 302. The healthcheck accepts both;
  tightening it to `== 302` on the reasonable-looking grounds that "the root
  redirects" makes the container never report healthy, and the failure reads as
  a slow boot rather than a config error.

## Safety: the teardown is scoped, and that is load-bearing

The make target runs under `COMPOSE_PROJECT_NAME=lumogis-paperless-test` and
brings Postgres and Qdrant up from `docker-compose.paperless-test.yml` itself,
never from the production `docker-compose.yml`.

The teardown trap runs `docker compose down -v`, **which deletes volumes**.
Scoped to the fixture's project that is exactly right. Under the default project
name it would delete a developer's real dev-stack volumes — their household
data — the first time they ran the target. Do not remove the export, and do not
copy this teardown shape without it.

Verified on 2026-07-30 by running the target with the full dev stack live: all
ten services and all eight named `lumogis_*` volumes survived.

## Troubleshooting

**"paperless fixture is unreachable" and the run fails rather than skips.** That
is `LUMOGIS_REQUIRE_PAPERLESS_FIXTURE=1` doing its job — the make target and CI
both set it, so a fixture that did not come up is a hard failure. A skip there
would report green while validating nothing (LUM-718). On a laptop with no
fixture running, the same tests skip.

**"No module named pytest".** `$(PYTHON)` is not your venv. The target checks
this *before* booting the container, so it fails in a second rather than after a
minute:

```bash
make compose-test-paperless PYTHON=$(pwd)/.venv/bin/python
```

**Assertions failing on empty result sets when the container is already up.**
Should not happen — tags carry a per-process `RUN_ID` precisely so a re-run
seeds genuinely new content. If it does, suspect that nonce: paperless dedupes
by checksum, so re-seeding identical bytes creates no new document, and the
surviving one predates the run's watermark.

**A failed boot.** The trap dumps `docker compose logs paperless` *before*
`down -v`, because `down -v` destroys the container and logs gathered after it
are gone.
