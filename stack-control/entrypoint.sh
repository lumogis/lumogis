#!/usr/bin/env bash
# Align app user with the *mounted* Docker socket group at runtime (GID differs
# per host: Ubuntu, Fedora, Docker Desktop VM, etc.). Build-time DOCKER_GID is not used.
set -euo pipefail

if ! id appuser &>/dev/null; then
  useradd --system --uid 1001 --create-home appuser
fi

if [ -S /var/run/docker.sock ]; then
  sock_gid=$(stat -c '%g' /var/run/docker.sock)
  # Group 0 = root group; socket is effectively root-only for non-root users
  if [ "${sock_gid}" = "0" ]; then
    exec "$@"
  fi
  group_name=$(getent group "${sock_gid}" | cut -d: -f1 || true)
  if [ -z "${group_name}" ]; then
    group_name="lumogis-dockersock"
    if ! groupadd -g "${sock_gid}" "${group_name}" 2>/dev/null; then
      group_name=$(getent group "${sock_gid}" | cut -d: -f1 || true)
    fi
  fi
  if [ -n "${group_name}" ]; then
    usermod -aG "${group_name}" appuser || true
  else
    echo "[entrypoint] WARN: could not map docker.sock GID ${sock_gid}; running stack-control as root" >&2
    exec "$@"
  fi
fi

exec gosu appuser "$@"
