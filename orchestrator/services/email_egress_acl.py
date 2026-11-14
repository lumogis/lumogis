# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Layer 2 of LUM-625 — the email-IMAP Squid egress **allow-file** generator.

Layer 1 (``egress_guard.imap_egress_scope``) is the real, per-connection,
cross-persona guarantee. Layer 2 is a **Docker-only cooperative audit /
consistency layer**: it emits the household-union list of declared IMAP hosts to
``docker/egress-proxy/allow/lumogis-email-imap.txt`` so the LUM-618 Squid proxy
can allow a ``CONNECT`` only to those hosts (by SNI) and log every denial. It is
**not** a hard post-compromise wall — Core is dual-homed and could bypass the
proxy — so nothing here claims that (see the plan / draft ADR).

A credential change-listener rebuilds the file. **Since LUM-758 the proxy applies
it itself** — its entrypoint watches the allow directory and reloads Squid on a
real content change — so adding a mailbox is no longer a two-party operation that
silently fails to sync while looking finished. The trigger is the *file*, not a
message from Core: a notification can be lost, a file cannot.

Note this module is still what *authors* the list; the watcher only decides when
it takes effect. Widening is therefore reported rather than gated — see
``_notify_widened``. One gate remains: the in-process ceiling
(``tethered.activate(locked=True)``) bakes its allowlist at boot and still needs a
Core restart (LUM-636), which setup warns about up front.

**Where the file lands (LUM-673):** in a bare checkout / the host-side CLI the
allow dir is the repo-tree ``docker/egress-proxy/allow`` (see ``_default_allow_dir``).
But the orchestrator Dockerfile flattens ``orchestrator/`` to ``/app``, so that
relative computation resolves to ``/docker/egress-proxy/allow`` **inside the
container** — a path nothing mounts and Squid never reads. So in a Docker deploy
the allow dir MUST be pinned via the ``LUMOGIS_IMAP_EGRESS_ALLOW_DIR`` env var to
a directory bind-mounted into BOTH this container and the Squid proxy; only then
does an in-container regeneration actually reach the proxy.

Mailbox hosts are **arbitrary user credential data**, so a host the strict
manifest validator can't express (IDN, IPv6-literal, a nonstandard port) is
**skipped and logged** — never a hard failure, and Layer 1 still contains it.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from connectors.registry import IMAP
from models.capability import normalise_external_endpoints

from services import connector_credentials as ccs
from services import email_credentials

_log = logging.getLogger(__name__)

ACL_ID = "lumogis-email-imap"
ALLOW_FILE_NAME = f"{ACL_ID}.txt"
# LUM-673: in a Docker deploy the repo-tree fallback below resolves to a
# container-local path Squid never reads (the Dockerfile flattens orchestrator/
# to /app). Set this env var to a directory bind-mounted identically into both the
# orchestrator and the Squid proxy so an in-container regen reaches the proxy.
ALLOW_DIR_ENV = "LUMOGIS_IMAP_EGRESS_ALLOW_DIR"
# Standard implicit-TLS IMAPS. A mailbox on any other port is Layer-1-contained
# but not expressible in the Layer-2 (SNI) allow file — reported as skipped.
STANDARD_IMAPS_PORT = 993
# Never-matching RFC-2606 sentinel so the generated ACL file is never empty (an
# empty Squid ACL can FATAL at startup on the shared proxy). ``.invalid`` never
# resolves and can never equal a real mailbox SNI.
_EMPTY_SENTINEL = "placeholder.email-imap.invalid"

# Serialise concurrent regenerations (from concurrent credential writes) so the
# atomic file swap never interleaves.
_REGEN_LOCK = threading.Lock()


def _default_allow_dir() -> Path:
    # LUM-673: prefer the explicitly-mounted shared dir when set (the only path
    # that reaches Squid from inside the flattened orchestrator container).
    override = os.environ.get(ALLOW_DIR_ENV)
    if override:
        return Path(override)
    # Fallback (bare checkout / host-side CLI): services/ -> orchestrator/ -> repo root.
    return Path(__file__).resolve().parents[2] / "docker" / "egress-proxy" / "allow"


def compute_allow_hosts(
    hosts: list[tuple[str, int]],
) -> tuple[list[str], list[tuple[str, str]]]:
    """Split ``(host, port)`` pairs into ``(kept_hosts, skipped)``.

    ``kept_hosts`` are de-duplicated, order-preserved, normalised hostnames the
    Squid SNI ACL can express. ``skipped`` is ``[(host, reason)]`` for hosts the
    validator rejects (IDN/IPv6/etc.) or on a nonstandard port — Layer 1 still
    contains these; they are logged so the operator knows Layer 2 is partial.
    """
    kept: list[str] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for host, port in hosts:
        if port != STANDARD_IMAPS_PORT:
            skipped.append((host, f"nonstandard port {port} not expressible in the SNI ACL"))
            continue
        try:
            normalised = normalise_external_endpoints([host])
        except (ValueError, TypeError) as exc:
            skipped.append((host, str(exc)))
            continue
        h = normalised[0]
        if h not in seen:
            seen.add(h)
            kept.append(h)
    return kept, skipped


def proxy_eligible(host: str, port: int) -> bool:
    """True iff ``(host, port)`` is expressible in the Layer-2 SNI allow file.

    Single source of truth for the proxy-routing decision (LUM-638): a host the
    allow-file generator skips (IDN / IPv6 / nonstandard port) cannot be
    allowlisted at the proxy, so it takes the **direct** path. Both the real
    dialer (``imap_transport._proxy_eligible``) and the Layer-1 scope-target
    predictor (``egress_guard.imap_egress_target_host``) must agree via this
    helper, or the scope allows a host the transport never dials (self-block).
    """
    kept, _ = compute_allow_hosts([(host, port)])
    return bool(kept)


def household_imap_hosts() -> list[tuple[str, int]]:
    """Union of every household member's declared IMAP ``(host, port)`` pairs."""
    from services import users

    hosts: list[tuple[str, int]] = []
    for user in users.list_users():
        try:
            hosts.extend(email_credentials.declared_imap_hosts(user.id))
        except Exception:  # noqa: BLE001 - one user's bad store must not sink the file
            _log.warning("imap.egress.acl.enumerate_failed user_id=%s", user.id)
    return hosts


def write_allow_file(
    hosts: list[tuple[str, int]], *, allow_dir: Path | None = None
) -> tuple[Path, list[str], list[tuple[str, str]]]:
    """Write the household-union allow file; return ``(path, kept, skipped)``.

    **Atomic + genuinely fail-safe:** the new content is written to a temp file in
    the same directory, fsync'd, then ``os.replace``'d over the target (atomic on
    POSIX). So a mid-write failure (disk full, OOM-kill) leaves the previous file
    fully intact — never a truncated/partial allow file — and a concurrent
    ``squid -k reconfigure`` never reads a half-written file. Skipped hosts are
    logged (Layer 2 is partial for them; Layer 1 still contains them).

    A never-matching sentinel is always written when there are no kept hosts so
    the Squid ACL file is never *empty* (some Squid versions FATAL on an empty
    ACL, which — on the shared proxy — would take down community containment too).
    """
    allow_dir = allow_dir or _default_allow_dir()
    kept, skipped = compute_allow_hosts(hosts)
    for host, reason in skipped:
        _log.warning("imap.egress.acl.skip host=%s reason=%s", host, reason)

    allow_dir.mkdir(parents=True, exist_ok=True)
    out = allow_dir / ALLOW_FILE_NAME
    body = kept or [_EMPTY_SENTINEL]
    lines = [
        f"# LUM-625 Layer-2 email-IMAP egress allowlist (ACL id: {ACL_ID}).",
        "# Household union of declared IMAP hosts. One bare host per line (SNI match).",
        "# Generated — do not hand-edit; regenerate on mailbox add/remove.",
        *body,
        "",
    ]
    tmp = allow_dir / f"{ALLOW_FILE_NAME}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, out)  # atomic; leaves the old file intact on any earlier failure
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass
    return out, kept, skipped


def read_allow_hosts(path: Path) -> list[str]:
    """Hosts currently listed in ``path``; ``[]`` if it does not exist.

    Skips the comment header and the never-matching sentinel, so "no mailboxes"
    reads back as an empty list rather than as one phantom host.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return [
        line.strip()
        for line in raw.splitlines()
        if line.strip() and not line.startswith("#") and line.strip() != _EMPTY_SENTINEL
    ]


def _should_notify(allow_dir: Path | None) -> bool:
    """True only when writing the *canonical, proxied* allow directory.

    Two ways this would otherwise misfire, both of which notify admins about an
    egress surface nobody is enforcing:

    * ``register()`` is gated on the **Layer-1** flag alone (``main.py``), which is
      independent of whether a proxy exists. With ``ALLOW_DIR_ENV`` unset the file
      lands in a container-local path nothing mounts, so the previous-file baseline
      is destroyed on every container recreate — the startup seed would then compute
      "every host is new" and alert every admin, on every recreate, about a proxy
      that isn't deployed.
    * ``scripts/gen_capability_egress_acl.py`` passes an explicit ``allow_dir``; an
      operator pointing it at a scratch directory must not page the household.
    """
    return allow_dir is None and bool(os.environ.get(ALLOW_DIR_ENV))


def regenerate(*, allow_dir: Path | None = None, actor_user_id: str | None = None) -> Path:
    """Rebuild the allow file from the current credential store (static regen).

    Serialised by ``_REGEN_LOCK`` so concurrent triggers can't interleave the
    atomic swap. Enumeration reads decrypted payloads (no DNS — see
    ``declared_imap_hosts``), so this is CPU/DB-bound and bounded, not network-bound.

    **Lock ordering is load-bearing.** The previous-state read happens *inside*
    ``_REGEN_LOCK`` — two mailboxes added at nearly the same moment would otherwise
    both read the same stale baseline and report a delta that never matches disk.
    Logging and notification happen *outside* it: ``dispatcher.emit`` does per-user
    preference loads and channel ``deliver()`` **network** calls, and holding the
    regen lock across those would serialise every later regeneration behind a slow
    ntfy or web-push delivery, breaking this module's "not network-bound" contract.
    """
    with _REGEN_LOCK:
        target = (allow_dir or _default_allow_dir()) / ALLOW_FILE_NAME
        previous = set(read_allow_hosts(target))
        path, kept, skipped = write_allow_file(household_imap_hosts(), allow_dir=allow_dir)
        added = sorted(set(kept) - previous)
        removed = sorted(previous - set(kept))

    # The delta, not today's bare counts: "kept=3" cannot answer "what changed?".
    _log.info(
        "imap.egress.acl.regenerated path=%s kept=%d skipped=%d added=%s removed=%s",
        path,
        len(kept),
        len(skipped),
        added or "-",
        removed or "-",
    )
    if added and _should_notify(allow_dir):
        _notify_widened(added=added, removed=removed, kept=kept, actor_user_id=actor_user_id)
    return path


def _notify_widened(
    *, added: list[str], removed: list[str], kept: list[str], actor_user_id: str | None
) -> None:
    """Tell admins the egress surface grew. Informed, never asked to approve.

    Widening only — a removal narrows the surface and needs no alarm. Admins only,
    so a member cannot aim this at the rest of the household.

    **The sole-admin case is deliberate.** Recipients are the active admins minus
    the actor, because telling someone what they just did is noise. But a household
    with exactly one active admin is the bootstrap default and a very plausible
    steady state, and there that rule excludes the only recipient — the notification
    would reach *nobody*, quietly forfeiting the "a human notices the surface widen"
    property this whole design rests on. So when the actor is the only active admin
    they are notified anyway, as a **receipt** rather than an approval gate.
    """
    try:
        from models.notifications import NotificationType
        from models.notifications import TypedNotification
        from services.notifications import dispatcher

        from services import users

        admins = users.list_admin_ids()
        recipients = [a for a in admins if a != actor_user_id] or admins
    except Exception:  # noqa: BLE001 - an audit notification must never break regen
        _log.exception("imap.egress.acl.notify_setup_failed")
        return

    for admin_id in recipients:
        # Per-recipient, NOT one try/except around the loop: a single failing
        # channel would otherwise silently drop every admin enumerated after it.
        try:
            dispatcher.emit(
                TypedNotification(
                    user_id=admin_id,
                    notification_type=NotificationType.EGRESS_ALLOWLIST_WIDENED,
                    title="A new mail host was allowed through the egress proxy",
                    body=(
                        f"{', '.join(added)} — added when a household member connected a mailbox."
                    ),
                    metadata={"added": added, "removed": removed, "kept_total": len(kept)},
                )
            )
        except Exception:  # noqa: BLE001
            _log.exception("imap.egress.acl.notify_failed user_id=%s", admin_id)


def _regenerate_background(reason: str, *, actor_user_id: str | None = None) -> None:
    """Run ``regenerate`` on a daemon thread so a credential write / startup never
    blocks on it. Never raises.

    ``actor_user_id`` is carried through purely so the widening notification can
    skip the person who triggered it. Before LUM-758 the listener received the
    acting user's id and dropped it here, which made "don't notify the actor"
    unimplementable further down.
    """

    def _run() -> None:
        try:
            regenerate(actor_user_id=actor_user_id)
        except Exception:  # noqa: BLE001 - regen failure must never break the caller
            _log.exception("imap.egress.acl.regen_failed reason=%s", reason)

    threading.Thread(target=_run, name="imap-egress-acl-regen", daemon=True).start()


def _on_credential_change(*, user_id: str, connector: str, action: str, instance: str = "") -> None:
    """Change-listener: regenerate when an IMAP mailbox is added/removed/updated.

    Regenerates on **every** IMAP action (put + delete) so a removed mailbox
    shrinks the file. Runs **off the credential-write thread** (background) so a
    slow enumeration can never stall the write.

    LUM-758: the proxy now applies the new file itself (it watches the allow
    directory), so this no longer leaves a member waiting on an operator.
    """
    if connector != IMAP:
        return
    _regenerate_background(f"credential_change:{action}", actor_user_id=user_id)


def register() -> None:
    """Register the regeneration listener AND seed the allow file from the current
    store (call once at startup). The seed runs in the background so startup is
    never blocked, and it closes the gap where a boot with the flag already on and
    mailboxes already configured would otherwise leave the committed deny-all file
    stale until the next credential change (LUM-625 review)."""
    ccs.register_change_listener(_on_credential_change)
    _regenerate_background("startup_seed")
