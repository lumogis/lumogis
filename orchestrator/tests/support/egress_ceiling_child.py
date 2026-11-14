# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
"""Subprocess helper for the LUM-571 locked-ceiling isolation tests.

Run as ``python egress_ceiling_child.py <mode>`` in a **throwaway** interpreter so
the permanent, locked ``tethered.activate()`` audit hook never poisons the pytest
process. Prints a single JSON verdict line to stdout. Deliberately imports no app
code — it exercises ``tethered`` directly.

Modes (all fully offline):
- ``allow``            explicit ``127.0.0.1`` allow (allow_localhost=False) → connect succeeds
- ``block_loopback``   same allow → connect ``127.0.0.2`` → EgressBlocked (no 127/8 auto-exempt)
- ``block_prod``       production posture (allow_localhost=True) → ``203.0.113.1:993`` blocked
- ``locked_no_token``  ``deactivate()`` with no token → TetheredLocked
- ``locked_with_token````deactivate(lock_token=<same object>)`` → succeeds
- ``fc_normal``        LUM-637 ceiling posture (fail_closed=True): loopback connect
                       succeeds, an off-list host is still an ordinary block
- ``fc_policy_error``  same, with ``tethered``'s policy check made to raise → EgressBlocked
- ``fo_policy_error``  control: fail_closed=False + same raise → connect succeeds, which
                       proves the injected error really reaches the fail_closed branch
- ``fc_tamper_helper`` LUM-903 / LUM-637 F2: ceiling posture, then a ``_policy`` helper
                       patched to return True *after* the lock → the guardian treats it
                       as tampering and an off-list host stays blocked
- ``bytes_host``       LUM-903 / LUM-637 F1: ceiling posture; ``bytes`` / ``bytearray``
                       hosts (connect and getaddrinfo) to an off-list host → EgressBlocked
- ``bytes_host_loopback`` same F1 check on a live loopback listener with
                       allow_localhost=False: the ``str`` and ``bytes`` forms are both
                       blocked (``tethered==0.5.1`` let the ``bytes`` form connect)
"""

from __future__ import annotations

import json
import socket
import sys
import threading


def _start_listener() -> int:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _accept_loop() -> None:
        try:
            while True:
                conn, _ = srv.accept()
                conn.close()
        except OSError:
            pass

    threading.Thread(target=_accept_loop, daemon=True).start()
    return port


def _connect(host: str, port: int, timeout: float = 1.0) -> None:
    sock = socket.create_connection((host, port), timeout=timeout)
    sock.close()


def _connect_raw(host: str | bytes | bytearray, port: int, timeout: float = 1.0) -> None:
    """``socket.connect`` with the host exactly as given (``create_connection``
    would resolve it to a ``str`` first, hiding the ``bytes`` form)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
    finally:
        sock.close()


def _activate_ceiling_posture(tethered, *, fail_closed: bool) -> None:
    """The kwargs ``egress_guard.activate_ceiling()`` passes (LUM-571 + LUM-637)."""
    tethered.activate(
        allow=["allowed.example"],
        allow_localhost=True,
        locked=True,
        lock_token=object(),
        fail_closed=fail_closed,
        external_subprocess_policy="warn",
    )


def _make_policy_check_raise() -> None:
    """Simulate a ``tethered`` bug inside ``AllowPolicy.is_allowed()``.

    ``_policy._is_localhost`` is called by ``is_allowed`` whenever
    ``allow_localhost=True``. Since ``tethered`` 1.0 the locked-mode integrity
    snapshot pins every ``_policy`` module global, so patching it *after*
    ``activate(locked=True)`` trips the C guardian's tamper path instead (see
    ``fc_tamper_helper``). Call this *before* activating: the snapshot then
    records the raising helper as the baseline, and the error reaches the
    ``fail_closed`` branch. ``fo_policy_error`` is the control that proves it.
    """
    import tethered._policy as policy

    def _boom(_host: str) -> bool:
        raise ValueError("simulated tethered policy-check bug")

    policy._is_localhost = _boom


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "<none>"
    try:
        import tethered
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"skip": True, "reason": f"tethered import failed: {exc}"}))
        return

    result: dict[str, object] = {"mode": mode}
    try:
        if mode == "allow":
            port = _start_listener()
            tethered.activate(
                allow=["127.0.0.1"], allow_localhost=False, locked=True, lock_token=object()
            )
            _connect("127.0.0.1", port)
            result["ok"] = True
        elif mode == "block_loopback":
            tethered.activate(
                allow=["127.0.0.1"], allow_localhost=False, locked=True, lock_token=object()
            )
            try:
                _connect("127.0.0.2", 9)
                result["ok"] = False
                result["detail"] = "connect to 127.0.0.2 unexpectedly succeeded"
            except tethered.EgressBlocked:
                result["ok"] = True
        elif mode == "block_prod":
            tethered.activate(
                allow=["allowed.example"], allow_localhost=True, locked=True, lock_token=object()
            )
            try:
                _connect("203.0.113.1", 993)
                result["ok"] = False
                result["detail"] = "connect to 203.0.113.1 unexpectedly succeeded"
            except tethered.EgressBlocked:
                result["ok"] = True
        elif mode == "locked_no_token":
            tethered.activate(allow=["allowed.example"], locked=True, lock_token=object())
            try:
                tethered.deactivate()
                result["ok"] = False
                result["detail"] = "deactivate without token unexpectedly succeeded"
            except tethered.TetheredLocked:
                result["ok"] = True
        elif mode == "locked_with_token":
            token = object()
            tethered.activate(allow=["allowed.example"], locked=True, lock_token=token)
            tethered.deactivate(lock_token=token)
            result["ok"] = True
        elif mode == "fc_normal":
            port = _start_listener()
            _activate_ceiling_posture(tethered, fail_closed=True)
            _connect("127.0.0.1", port)
            try:
                _connect("203.0.113.1", 993)
                result["ok"] = False
                result["detail"] = "connect to 203.0.113.1 unexpectedly succeeded"
            except tethered.EgressBlocked:
                result["ok"] = True
        elif mode in ("fc_policy_error", "fo_policy_error"):
            port = _start_listener()
            _make_policy_check_raise()  # before the lock: see the docstring
            _activate_ceiling_posture(tethered, fail_closed=(mode == "fc_policy_error"))
            try:
                _connect("127.0.0.1", port)
                outcome = "connected"
            except tethered.EgressBlocked as exc:
                outcome = f"blocked: {exc}"
            want = "connected" if mode == "fo_policy_error" else "blocked"
            result["ok"] = outcome.startswith(want)
            if not result["ok"]:
                result["detail"] = f"expected {want}, got {outcome}"
        elif mode == "fc_tamper_helper":
            _activate_ceiling_posture(tethered, fail_closed=True)
            import tethered._policy as policy

            policy._is_localhost = lambda _host: True
            try:
                _connect("203.0.113.1", 993)
                result["ok"] = False
                result["detail"] = "connect to 203.0.113.1 succeeded after the helper patch"
            except tethered.EgressBlocked:
                result["ok"] = True
        elif mode == "bytes_host":
            _activate_ceiling_posture(tethered, fail_closed=True)
            failures = []
            hosts = (("bytes", b"203.0.113.1"), ("bytearray", bytearray(b"203.0.113.1")))
            for label, host in hosts:
                try:
                    _connect_raw(host, 993)
                    failures.append(f"connect {label} host succeeded")
                except tethered.EgressBlocked:
                    pass
                except OSError as exc:
                    failures.append(f"connect {label} host was not checked ({exc!r})")
            try:
                socket.getaddrinfo(b"not-listed.invalid", 443)
                failures.append("getaddrinfo bytes host resolved")
            except tethered.EgressBlocked:
                pass
            except OSError as exc:
                failures.append(f"getaddrinfo bytes host was not checked ({exc!r})")
            result["ok"] = not failures
            if failures:
                result["detail"] = "; ".join(failures)
        elif mode == "bytes_host_loopback":
            port = _start_listener()
            tethered.activate(
                allow=["allowed.example"],
                allow_localhost=False,
                locked=True,
                lock_token=object(),
                fail_closed=True,
            )
            outcomes = {}
            for label, host in (("str", "127.0.0.1"), ("bytes", b"127.0.0.1")):
                try:
                    _connect_raw(host, port)
                    outcomes[label] = "connected"
                except tethered.EgressBlocked:
                    outcomes[label] = "blocked"
            result["ok"] = outcomes == {"str": "blocked", "bytes": "blocked"}
            if not result["ok"]:
                result["detail"] = f"expected both blocked, got {outcomes}"
        else:
            result["ok"] = False
            result["detail"] = f"unknown mode {mode!r}"
    except Exception as exc:  # noqa: BLE001
        result["ok"] = False
        result["detail"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps(result))


if __name__ == "__main__":
    main()
