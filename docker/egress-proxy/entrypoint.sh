#!/bin/bash
# LUM-758 — egress-proxy entrypoint: prepare, start the allow-file watcher, run Squid.
#
# WHY THIS FILE EXISTS
# Core regenerates the Layer-2 IMAP allow file the moment a household member adds
# a mailbox, but Squid keeps serving its last-loaded copy until something tells it
# otherwise. That made adding a mailbox a two-party operation with nothing saying
# so: the setup flow looked finished and the mailbox silently never synced. The
# watcher below closes that gap — the proxy applies its own allow file.
#
# The trigger is the FILE, not a clock and not a message from Core. A notification
# can be lost (a poke that arrives while the proxy is restarting leaves the list
# silently unapplied); a file cannot. The file *is* the state, so the guard
# converges regardless of what was missed. See
# .cursor/adrs/LUM-758-imap-egress-allow-reload.md.
#
# THREE THINGS ABOUT SQUID THAT ARE NOT OBVIOUS (all verified against the pinned
# image jacobalberty/squid@sha256:559a8dd2… / Squid 4.15 on 2026-07-31):
#
#   1. `squid -k reconfigure` DOES NOT WORK HERE. Squid reads its pid file and
#      refuses to signal a "unreasonably small PID value: 1" — and because the
#      entrypoint ends in `exec squid`, Squid *is* PID 1. It fails with
#      `FATAL: Bad PID file`. `kill -HUP` is the working mechanism.
#   2. `squid -k parse` ALWAYS EXITS 0 — even with an allow file missing. Gating a
#      reload on its exit code would be a check that reports nothing wrong while
#      checking nothing. We grep its OUTPUT for ERROR instead.
#   3. `squid -k parse` does not validate allow-file CONTENT at all: a line of
#      `!!! garbage !!!` parses clean. Content validity is Core's job
#      (compute_allow_hosts / revalidate_imap_host), not this script's.
#
# Proof the mechanism works end to end: with allow=hostA, a request to hostB is
# refused 403; after rewriting the file to hostB and sending HUP, the same request
# reaches 503 (ACL now permits it; DNS then fails). That 403->503 flip is the ACL
# being re-read with no restart and no operator.

set -uo pipefail   # NOT -e: see watch_loop — a transient failure must not kill the watcher.

ALLOW_DIR="${ALLOW_DIR:-/etc/squid/allow}"
SQUID_CONF="${SQUID_CONF:-/etc/squid/squid.conf}"
HEARTBEAT="${HEARTBEAT:-/var/run/lumogis-allow-watch.alive}"
PROC_ROOT="${PROC_ROOT:-/proc}"   # injectable so the PID-safety guard is testable
# Consecutive failed reloads before the heartbeat stops and the container goes
# unhealthy. Small enough to surface fast, large enough to ride out a blip.
MAX_RELOAD_FAILURES="${MAX_RELOAD_FAILURES:-5}"
POLL_SECONDS="${LUMOGIS_EGRESS_ALLOW_POLL_SECONDS:-2}"
WATCH_ENABLED="${LUMOGIS_EGRESS_ALLOW_WATCH:-1}"

log() { printf '%s lumogis.egress.watch %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2; }

# The Squid master PID.
#
# NOT hardcoded to 1. `kill -HUP 1` is only correct because this entrypoint ends in
# `exec squid`. If anyone later adds `init: true` to the compose service, PID 1
# becomes tini and a hardcoded signal would go to the wrong process — a reload that
# reports success and does nothing. Lowest matching PID = the master (a daemonised
# Squid forks a kid with the same comm; under -N there is only one).
squid_master_pid() {
  local pid comm pids
  # Numeric sort, so the lowest PID wins; a bare glob would order 1, 10, 100, 2.
  pids="$(for d in "$PROC_ROOT"/[0-9]*; do printf '%s\n' "${d##*/}"; done | sort -n)"
  for pid in $pids; do
    read -r comm < "$PROC_ROOT/$pid/comm" 2>/dev/null || continue
    if [ "$comm" = "squid" ]; then
      printf '%s' "$pid"
      return 0
    fi
  done
  return 1
}

# Content digest of the allow directory.
#
# The glob is *.txt and that is LOAD-BEARING. write_allow_file() creates its temp
# file INSIDE this directory (`lumogis-email-imap.txt.tmp.<pid>`) before atomically
# renaming it into place. A `*` glob would hash that temp file, so every single
# write would produce two digest changes — temp appears, temp vanishes — and fire a
# reconfigure even when the resulting bytes are identical. Squid frees and recreates
# its SSL context on reconfigure (known bugs around spliced connections), so
# reloading only on real change is a correctness property, not an optimisation.
# It is also a TOCTOU: if the rename lands between glob expansion and sha256sum
# opening the file, the digest is non-deterministic.
allow_digest() {
  sha256sum "$ALLOW_DIR"/*.txt 2>/dev/null | sha256sum | cut -d' ' -f1
}

# True iff Squid can read every file its config references.
#
# Keyed on OUTPUT, never on exit status (see header note 2). Scope is deliberately
# narrow: this catches a missing or root-unreadable allow file — an out-of-band
# deletion or a bad mount — and nothing else. Core's own writer is atomic, never
# deletes, and always writes a sentinel rather than an empty file, so it cannot
# produce this condition. The real config parses with zero ERROR lines, so this
# gate cannot wedge reloads spuriously.
config_readable() {
  local out rc
  out="$(squid -k parse -f "$SQUID_CONF" 2>&1)"
  rc=$?
  # BOTH signals are required, because neither alone catches the other's case:
  #   * a MISSING allow file exits 0 and only prints "ERROR: Can not open file"
  #   * a MALFORMED directive exits 1 and never prints "ERROR" at all
  # An earlier version checked only the output, on the belief that parse "always
  # exits 0" — true for a missing allow file, false for a bad directive. Since a
  # mailbox add now reloads automatically, letting an unloadable config through
  # would take the whole shared proxy down (community containment included) on
  # the next member action rather than at a moment an operator chose.
  [ "$rc" -eq 0 ] || return 1
  ! printf '%s' "$out" | grep -qE '\| *ERROR'
}

# Reason for the most recent apply_reload outcome, so the caller decides logging
# (retrying every tick must not reprint the same line every tick).
RELOAD_REASON=""

apply_reload() {
  local pid
  if ! config_readable; then
    RELOAD_REASON="squid -k parse rejected the config; keeping last good config"
    return 1
  fi
  if ! pid="$(squid_master_pid)"; then
    RELOAD_REASON="could not locate the squid master process"
    return 1
  fi
  if kill -HUP "$pid" 2>/dev/null; then
    RELOAD_REASON="applied via SIGHUP to pid=$pid"
    return 0
  fi
  RELOAD_REASON="SIGHUP to pid=$pid was rejected"
  return 1
}

watch_loop() {
  local last current fails=0
  last="$(allow_digest)"
  log "watching $ALLOW_DIR (poll=${POLL_SECONDS}s) digest=${last:0:12}"
  while true; do
    sleep "$POLL_SECONDS"

    # Heartbeat only while reloads actually work. A watcher that is alive but
    # persistently unable to apply changes is NOT healthy: Core would go on
    # telling members no operator is needed while nothing reached Squid. Letting
    # the heartbeat go stale is what turns that into a red container instead of a
    # silence — and it is the only channel Core-side health can observe.
    if [ "$fails" -lt "$MAX_RELOAD_FAILURES" ]; then
      : > "$HEARTBEAT" 2>/dev/null || true
    fi

    current="$(allow_digest)"
    # Empty means the digest could not be computed at all (e.g. sha256sum missing
    # from PATH) — no information, so never treat it as a change. Note a file
    # vanishing mid-glob does NOT land here: sha256sum still emits the hash of
    # empty input, which reads as an ordinary content change and fails closed.
    [ -z "$current" ] && continue
    [ "$current" = "$last" ] && continue

    if apply_reload; then
      log "allow-dir change ${last:0:12} -> ${current:0:12} — $RELOAD_REASON"
      last="$current"
      fails=0
    else
      # Deliberately do NOT advance the digest: retry on every tick so a
      # transient failure self-heals, instead of sitting unapplied until some
      # unrelated mailbox change happens to trigger the next attempt. Log once
      # per failure run, not once per tick.
      fails=$((fails + 1))
      if [ "$fails" -eq 1 ]; then
        log "reload FAILED — $RELOAD_REASON; retrying every ${POLL_SECONDS}s"
      elif [ "$fails" -eq "$MAX_RELOAD_FAILURES" ]; then
        log "reload still failing after $fails attempts — heartbeat stopped, container will go unhealthy"
      fi
    fi
  done
}

# A dead watcher must never be silent. Squid stays reachable on 3128 whether or not
# this loop is alive, so the healthcheck cannot see it die — hence both a respawn
# and a heartbeat file the healthcheck asserts the freshness of.
supervise() {
  local backoff=1
  while true; do
    watch_loop
    log "watcher exited unexpectedly; respawning in ${backoff}s"
    sleep "$backoff"
    backoff=$(( backoff < 30 ? backoff * 2 : 30 ))
  done
}

main() {
  mkdir -p /var/log/squid /var/spool/squid
  /usr/local/bin/gen-bump-cert.sh /etc/squid/bump.pem
  chown -R proxy:proxy /var/log/squid /var/spool/squid /etc/squid/bump.pem

  if [ "$WATCH_ENABLED" != "0" ]; then
    : > "$HEARTBEAT" 2>/dev/null || true
    supervise &
  else
    log "watcher DISABLED (LUMOGIS_EGRESS_ALLOW_WATCH=0) — allow-file changes need an operator reload"
  fi

  # Backgrounded above so it survives this exec: the shell's image is replaced by
  # Squid, but the child process is untouched and simply reparents.
  exec squid -N -d1 -f "$SQUID_CONF"
}

# Allow the test suite to source this file and exercise the pure functions.
[ "${LUMOGIS_ENTRYPOINT_SOURCE_ONLY:-0}" = "1" ] || main "$@"
