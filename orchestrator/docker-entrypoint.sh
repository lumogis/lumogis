#!/bin/bash
# Lumogis orchestrator entrypoint.
# Do NOT use set -e — pull failures must be non-fatal (degraded-mode startup).
# Init runs as root; the app then runs as the checkout owner (LUM-887, see the
# privdrop section at the end).

# LibreChat bind-mount target — generated at runtime; gitignored in the repo.
# Seed from the tracked cold-start template so fresh clones and single-file mounts work.
_LIBRECHAT_CFG="/project/config/librechat.yaml"
_LIBRECHAT_TEMPLATE="/project/config/librechat.coldstart.yaml"
# An install from the two release files (LUM-980) has no config/ folder and no
# LibreChat, so there is nothing to seed and nothing to warn about.
if [ -d /project/config ]; then
    # Docker can create `librechat.yaml` as an empty directory when the bind-mount
    # source path was missing on first boot; LibreChat then logs EISDIR / invalid YAML.
    if [ -d "$_LIBRECHAT_CFG" ]; then
        rm -rf "$_LIBRECHAT_CFG"
        echo "[entrypoint] Removed directory at ${_LIBRECHAT_CFG}; expected a regular file — re-seeding below"
    fi
    if [ ! -f "$_LIBRECHAT_CFG" ] || [ ! -s "$_LIBRECHAT_CFG" ]; then
        if [ -f "$_LIBRECHAT_TEMPLATE" ]; then
            cp "$_LIBRECHAT_TEMPLATE" "$_LIBRECHAT_CFG"
            echo "[entrypoint] Seeded librechat.yaml from librechat.coldstart.yaml"
        else
            echo "[entrypoint] WARNING: missing $_LIBRECHAT_TEMPLATE — LibreChat may fail until config exists" >&2
        fi
    fi
fi

OLLAMA_URL="${OLLAMA_URL:-http://ollama:11434}"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-nomic-embed-text}"
# Comma-separated chat LLM(s) to pull after the embedder (default: small Llama 3.2).
# Use OLLAMA_EXTRA_MODELS= to disable (empty).
OLLAMA_EXTRA_MODELS="${OLLAMA_EXTRA_MODELS-llama3.2:3b}"
HOST_ENV="/project/.env"

# >>> secrets-section
# ── Auto-generate secrets ────────────────────────────────────────────────────
# On first run, .env has placeholder values from .env.example.
# Generate real secrets and write them to the bind-mounted .env so they
# persist and take effect for ALL services on the next docker compose up -d.
# The current run uses the original placeholders (consistent across services),
# EXCEPT the refresh secret (LUM-885): the block after this loop exports the
# value from .env for this run, so a first boot never runs on the placeholder.
if [ -f "$HOST_ENV" ]; then
    _gen() { python3 -c "import secrets; print(secrets.token_hex(32))"; }
    _changed=0
    for pair in \
        "JWT_SECRET=change-me-in-production" \
        "JWT_REFRESH_SECRET=change-me-in-production" \
        "RESTART_SECRET=change-me-to-a-random-secret"; do
        key="${pair%%=*}"
        if grep -q "^${pair}$" "$HOST_ENV"; then
            sed -i "s|^${pair}$|${key}=$(_gen)|" "$HOST_ENV"
            echo "[entrypoint] Generated ${key}"
            _changed=1
        fi
    done
    [ "$_changed" -eq 1 ] && echo "[entrypoint] Secrets saved to .env — active on next restart."
fi

# ── Refresh secret (LUM-885, security audit #2 AUTH-002) ─────────────────────
# The orchestrator refuses to boot with AUTH_ENABLED=true when its refresh
# secret is missing, a placeholder, or equal to AUTH_SECRET (main.py). This
# block makes the default compose install satisfy that, without ever editing an
# operator-written .env:
#
#   1. LUMOGIS_JWT_REFRESH_SECRET non-empty  -> do nothing (python resolves it
#      first, even when it is a placeholder, and its gate then judges it: the
#      same "first non-empty wins" rule as auth._refresh_secret()).
#   2. process JWT_REFRESH_SECRET is real    -> do nothing.
#   3. .env holds a real JWT_REFRESH_SECRET  -> export it for THIS run (covers the
#      first boot after the loop above rewrote the placeholder, and a
#      `docker compose restart` that keeps the creation-time env).
#   4. .env key missing/empty/placeholder AND AUTH_ENABLED=true -> generate one,
#      write it to .env, verify the write, then export it. A secret that could
#      not be persisted is never exported (the python gate refuses boot).
#
# LibreChat keeps reading the same JWT_REFRESH_SECRET from .env at `compose up`;
# that is inherited on purpose (typ + the auth_sessions hash binding contain it).
# Set LUMOGIS_JWT_REFRESH_SECRET to split the two products.
_lum_gen() { python3 -c "import secrets; print(secrets.token_hex(32))"; }
_lum_trim() {
    local v="$1"
    v="${v#"${v%%[![:space:]]*}"}"
    v="${v%"${v##*[![:space:]]}"}"
    printf '%s' "$v"
}
_lum_is_placeholder() {
    case "$1" in
        ""|"change-me-in-production"|"__GENERATE_ME__") return 0 ;;
    esac
    return 1
}
# Normalise a .env value the way compose's env_file would: drop a trailing CR,
# trim, strip ONE matching pair of surrounding quotes.
_lum_env_value() {
    local v="${1%$'\r'}"
    v="$(_lum_trim "$v")"
    case "$v" in
        \"*\") v="${v#\"}"; v="${v%\"}" ;;
        \'*\') v="${v#\'}"; v="${v%\'}" ;;
    esac
    printf '%s' "$v"
}
_lum_refresh_secret_section() {
    local auth_on lum_secret proc_secret line has_key value new reread
    auth_on="$(_lum_trim "${AUTH_ENABLED:-false}")"
    auth_on="$(printf '%s' "$auth_on" | tr '[:upper:]' '[:lower:]')"

    lum_secret="$(_lum_trim "${LUMOGIS_JWT_REFRESH_SECRET:-}")"
    [ -n "$lum_secret" ] && return 0

    proc_secret="$(_lum_trim "${JWT_REFRESH_SECRET:-}")"
    _lum_is_placeholder "$proc_secret" || return 0

    [ -f "$HOST_ENV" ] || return 0

    if grep -Eq '^[[:space:]]*export[[:space:]]+JWT_REFRESH_SECRET=' "$HOST_ENV"; then
        echo "[entrypoint] WARNING: JWT_REFRESH_SECRET in .env has a form the entrypoint does not parse; leaving it to compose" >&2
        return 0
    fi

    line="$(grep '^JWT_REFRESH_SECRET=' "$HOST_ENV" | tail -n 1)"
    has_key=0
    [ -n "$line" ] && has_key=1
    value="$(_lum_env_value "${line#*=}")"
    case "$value" in
        *[[:space:]]*|*"#"*)
            echo "[entrypoint] WARNING: JWT_REFRESH_SECRET in .env has a form the entrypoint does not parse; leaving it to compose" >&2
            return 0
            ;;
    esac

    if _lum_is_placeholder "$value"; then
        [ "$auth_on" = "true" ] || return 0
        new="$(_lum_gen)"
        if [ "$has_key" -eq 1 ]; then
            sed -i "s|^JWT_REFRESH_SECRET=.*$|JWT_REFRESH_SECRET=${new}|" "$HOST_ENV" 2>/dev/null
        else
            {
                [ -s "$HOST_ENV" ] && [ -n "$(tail -c 1 "$HOST_ENV")" ] && printf '\n'
                printf 'JWT_REFRESH_SECRET=%s\n' "$new"
            } >> "$HOST_ENV" 2>/dev/null
        fi
        reread="$(grep '^JWT_REFRESH_SECRET=' "$HOST_ENV" 2>/dev/null | tail -n 1)"
        if [ -n "$new" ] && [ "$(_lum_env_value "${reread#*=}")" = "$new" ]; then
            echo "[entrypoint] Generated JWT_REFRESH_SECRET"
            value="$new"
        else
            echo "[entrypoint] WARNING: could not write JWT_REFRESH_SECRET to .env" >&2
            return 0
        fi
    fi

    export JWT_REFRESH_SECRET="$value"
    echo "[entrypoint] Using JWT_REFRESH_SECRET from .env for this run"
}
_lum_refresh_secret_section
# <<< secrets-section

# Warn if /data is not mounted (misconfigured FILESYSTEM_ROOT)
if [ ! -d "/data" ]; then
    echo "[entrypoint] WARNING: /data is not mounted. Check FILESYSTEM_ROOT in .env." >&2
fi

# ── AUTH_SECRET hardening (post-/verify-plan follow-up) ─────────────────────
# Refuse to boot when AUTH_ENABLED=true but AUTH_SECRET is unset or a known
# placeholder. The entrypoint auto-rotates JWT_SECRET / JWT_REFRESH_SECRET /
# RESTART_SECRET above, but intentionally does NOT auto-rotate AUTH_SECRET —
# operators flip family-LAN mode on deliberately and must own that secret.
# A duplicate Python-side guard lives in main._enforce_auth_consistency for
# the case where the entrypoint is bypassed (e.g. local dev `uvicorn main:app`).
_AUTH_ENABLED_NORM="$(echo "${AUTH_ENABLED:-false}" | tr '[:upper:]' '[:lower:]' | xargs)"
if [ "$_AUTH_ENABLED_NORM" = "true" ]; then
    _AUTH_SECRET_NORM="$(echo "${AUTH_SECRET:-}" | xargs)"
    case "$_AUTH_SECRET_NORM" in
        ""|"change-me-in-production"|"__GENERATE_ME__")
            echo "[entrypoint] FATAL: AUTH_ENABLED=true but AUTH_SECRET is unset or a placeholder." >&2
            echo "[entrypoint]        Generate a real secret with: openssl rand -hex 32" >&2
            echo "[entrypoint]        Then set AUTH_SECRET in .env and restart." >&2
            echo "[entrypoint]        AUTH_SECRET is intentionally NOT auto-rotated — see" >&2
            echo "[entrypoint]        .cursor/plans/family_lan_multi_user.plan.md follow-ups." >&2
            exit 1
            ;;
    esac
fi

# ── LUMOGIS_CREDENTIAL_KEY[S] hardening ─────────────────────────────────────
# Refuse to boot when AUTH_ENABLED=true but no usable Fernet key is configured
# for the per-user connector credential subsystem (services/connector_credentials.py).
# Without a key, every credential PUT/GET/resolve would raise at request time
# and the container would otherwise come up "healthy". A duplicate Python-side
# guard lives in main._enforce_auth_consistency for the case where the
# entrypoint is bypassed (e.g. local dev `uvicorn main:app`).
#
# Honour LUMOGIS_CREDENTIAL_KEYS (CSV, newest first) over LUMOGIS_CREDENTIAL_KEY
# when set — same precedence as services.connector_credentials._load_keys().
#
# Critical: NOT auto-rotated by the loop above. Auto-generating this key would
# silently destroy every existing encrypted credential on the next boot if
# .env is ever re-bootstrapped. Operators must own this key.
if [ "$_AUTH_ENABLED_NORM" = "true" ]; then
    _CRED_KEYS_NORM="$(echo "${LUMOGIS_CREDENTIAL_KEYS:-${LUMOGIS_CREDENTIAL_KEY:-}}" | xargs)"
    case "$_CRED_KEYS_NORM" in
        ""|"change-me-in-production"|"__GENERATE_ME__")
            echo "[entrypoint] FATAL: AUTH_ENABLED=true but LUMOGIS_CREDENTIAL_KEY[S] is unset or a placeholder." >&2
            echo "[entrypoint]        Generate with: python3 -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\"" >&2
            echo "[entrypoint]        Then set LUMOGIS_CREDENTIAL_KEY in .env (or LUMOGIS_CREDENTIAL_KEYS for a CSV during rotation) and restart." >&2
            echo "[entrypoint]        Key is intentionally NOT auto-rotated — losing it makes every per-user connector credential unrecoverable." >&2
            exit 1
            ;;
    esac
fi

# ── Apply Postgres migrations ────────────────────────────────────────────────
# In a git checkout, Postgres runs postgres/init.sql itself on the first init of
# an empty data volume (initdb mount). An install from the two release files has
# no such mount, so on an empty database the runner applies init.sql from the
# image first (LUM-980). Schema changes live in postgres/migrations/*.sql; the
# runner is idempotent and tracks applied files in a schema_migrations table.
# An ordinary migration failure warns and continues (ADR 059); exit 3 means no
# schema is possible (empty database and no usable baseline, or no migrations at
# all), and starting would only serve an empty database, so stop.
if [ -f "/app/db_migrations.py" ]; then
    echo "[entrypoint] Running Postgres migrations..."
    _mig_rc=0
    python3 /app/db_migrations.py || _mig_rc=$?
    if [ "$_mig_rc" -eq 3 ]; then
        echo "[entrypoint] FATAL: no database schema — see the migration log above." >&2
        exit 3
    elif [ "$_mig_rc" -ne 0 ]; then
        echo "[entrypoint] WARNING: migration runner exited non-zero — see logs above." >&2
        echo "[entrypoint] Continuing startup; orchestrator may run in degraded mode." >&2
    fi
fi

# ── Legacy `user_id='default'` remap (post-013 scope model) ──────────────────
# The 013-memory-scopes.sql migration introduces a first-class `scope` column
# whose `personal` arm gates visibility on `user_id`. Any leftover rows from
# the pre-multi-user dev period that still carry `user_id='default'` would be
# stranded under nobody's account once `AUTH_ENABLED=true` is flipped on.
# The remap script is idempotent (subsequent boots are no-ops) and is the only
# mutating operation that touches existing data — kept separate from the
# migration runner so the SQL file stays pure-SQL and operationally debuggable.
if [ -f "/app/db_default_user_remap.py" ]; then
    echo "[entrypoint] Running legacy default-user remap..."
    if ! python3 /app/db_default_user_remap.py; then
        echo "[entrypoint] WARNING: default-user remap exited non-zero — see WARN line above." >&2
        echo "[entrypoint] Continuing startup; legacy 'default'-user rows remain in place." >&2
    fi
fi

# OLLAMA_SKIP_WAIT=true makes the orchestrator bind immediately in degraded mode,
# skipping the up-to-120s readiness wait and the (potentially multi-minute) embedding
# model pull. Intended for environments that do not need on-boot embeddings — e.g. the
# restart_e2e RC gate, where a fast, deterministic --force-recreate matters and the proof
# (file_index_count) does not depend on Ollama. Models can still be pulled later via the
# dashboard. Unset/any-other value preserves the default wait-and-pull behaviour.
if [ "${OLLAMA_SKIP_WAIT:-}" = "true" ]; then
    echo "[entrypoint] OLLAMA_SKIP_WAIT=true — skipping Ollama readiness wait and model pulls; starting in degraded mode."
else
echo "[entrypoint] Waiting for Ollama at $OLLAMA_URL ..."
for i in $(seq 1 60); do
    if curl -sf "$OLLAMA_URL/api/tags" > /dev/null 2>&1; then
        echo "[entrypoint] Ollama ready."
        break
    fi
    echo "[entrypoint] Waiting... ($i/60)"
    sleep 2
done

# Check if embedding model is already present using /api/show (returns 200 if present, 404 if not).
# More reliable than grepping /api/tags which includes :tag suffixes.
if curl -sf -X POST "$OLLAMA_URL/api/show" \
       -H "Content-Type: application/json" \
       -d "{\"name\": \"$EMBEDDING_MODEL\"}" > /dev/null 2>&1; then
    echo "[entrypoint] $EMBEDDING_MODEL already present."
else
    echo "[entrypoint] Pulling $EMBEDDING_MODEL (~300 MB, may take several minutes)..."
    if curl -sf -X POST "$OLLAMA_URL/api/pull" \
           -H "Content-Type: application/json" \
           -d "{\"name\": \"$EMBEDDING_MODEL\", \"stream\": false}" \
           > /dev/null 2>&1; then
        echo "[entrypoint] $EMBEDDING_MODEL pulled successfully."
    else
        echo "[entrypoint] WARNING: Failed to pull $EMBEDDING_MODEL." >&2
        echo "[entrypoint] Search and ingest will be unavailable until the model is pulled." >&2
        echo "[entrypoint] Use the dashboard (Settings → Models) to pull it manually." >&2
        # Do NOT exit — let the orchestrator start in degraded mode.
    fi
fi

# Optional chat LLM(s) pulled after the embedder (see OLLAMA_EXTRA_MODELS).
_ensure_ollama_model() {
    _name="$1"
    [ -z "$_name" ] && return 0
    if curl -sf -X POST "$OLLAMA_URL/api/show" \
           -H "Content-Type: application/json" \
           -d "{\"name\": \"$_name\"}" > /dev/null 2>&1; then
        echo "[entrypoint] $_name already present."
        return 0
    fi
    echo "[entrypoint] Pulling $_name (may take several minutes on first start)..."
    if curl -sf -X POST "$OLLAMA_URL/api/pull" \
           -H "Content-Type: application/json" \
           -d "{\"name\": \"$_name\", \"stream\": false}" \
           > /dev/null 2>&1; then
        echo "[entrypoint] $_name pulled successfully."
    else
        echo "[entrypoint] WARNING: Failed to pull $_name." >&2
        echo "[entrypoint] Pull it from the dashboard (Settings → Models) or: docker compose exec ollama ollama pull $_name" >&2
    fi
}

if [ -n "$OLLAMA_EXTRA_MODELS" ]; then
    _old_ifs="$IFS"
    IFS=','
    for _raw in $OLLAMA_EXTRA_MODELS; do
        _m="$(echo "$_raw" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        [ -n "$_m" ] && _ensure_ollama_model "$_m"
    done
    IFS="$_old_ifs"
fi
fi  # end OLLAMA_SKIP_WAIT guard

# >>> privdrop-section
# ── Drop root, run the app as the checkout owner (LUM-887, audit INFRA-003) ──
# Everything above needed root (librechat seed, .env secret rewrite, migrations).
# Here we repair ownership of the paths the app writes, make sure the target UID
# has a passwd entry, and exec the app through `setpriv`.
#
# MAINTAINERS: any new path the app writes must be added to the repair list
# below, or it will not be writable after the drop. For compose mounts
# test_every_writable_orchestrator_mount_is_repaired enforces this; for a
# path an env var can move out of those mounts (CAPTURE_MEDIA_ROOT,
# STOP_ENTITIES_PATH, ...) test_movable_write_paths_are_repaired does. Owned vs
# shared: a named volume or image-internal dir only Lumogis writes is OWNED
# (anything not U:G is re-owned); anything a person can see on the host is
# SHARED (only root-owned entries are re-owned, so a person's own files and a
# group-shared workspace are never rewritten).
_LUM_PROJECT_DIR="${_LUM_PROJECT_DIR:-/project}"
_LUM_HOME_DIR="${_LUM_HOME_DIR:-/var/lib/lumogis/home}"
_LUM_APP_CONFIG_DIR="${_LUM_APP_CONFIG_DIR:-/app/config}"
_LUM_ROOTFS_DIR="${_LUM_ROOTFS_DIR:-/}"
_LUM_UID_MAP_FILE="${_LUM_UID_MAP_FILE:-/proc/self/uid_map}"
_LUM_PASSWD_FILE="${_LUM_PASSWD_FILE:-/etc/passwd}"
_LUM_GROUP_FILE="${_LUM_GROUP_FILE:-/etc/group}"
_LUM_DATA_GLOB="${_LUM_DATA_GLOB:-/data /data-[0-9]*}"
_LUM_DEFAULT_HF_HOME="${_LUM_DEFAULT_HF_HOME:-/var/lib/lumogis/hf-cache}"
_LUM_DEFAULT_STAGING="${_LUM_DEFAULT_STAGING:-/var/lib/lumogis/email-staging}"
_LUM_DEFAULT_DATA_DIR="${_LUM_DEFAULT_DATA_DIR:-/opt/lumogis/data}"
_LUM_DEFAULT_UID=10001

_lum_privdrop_section() {
    local U G d f ws path _lum_trim_uid _lum_trim_gid map name
    _LUM_DROP=()

    # Already non-root (compose `user:`): nothing to drop, nothing to repair.
    if [ "$(id -u)" != "0" ]; then
        echo "[entrypoint] Running as uid $(id -u); no privilege drop"
        return 0
    fi

    # User namespace guard: under rootless Docker, Podman or userns-remap,
    # container root already maps to the host user (or an unprivileged
    # sub-UID). Dropping to 10001 would hand the user's workspace to a sub-UID
    # the host user cannot open. An unreadable map counts as identity.
    map="$(tr -s '[:space:]' ' ' < "$_LUM_UID_MAP_FILE" 2>/dev/null | sed 's/^ //;s/ $//')"
    if [ -n "$map" ] && [ "$map" != "0 0 4294967295" ]; then
        echo "[entrypoint] user namespace detected (rootless Docker, Podman or userns-remap); container root is not host root, so no privilege drop and no ownership repair"
        return 0
    fi

    # Explicit override. Trim spaces, tabs and CR (a CRLF .env leaves a trailing \r).
    _lum_trim_uid="${LUMOGIS_RUN_UID:-}"
    _lum_trim_gid="${LUMOGIS_RUN_GID:-}"
    _lum_trim_uid="$(printf '%s' "$_lum_trim_uid" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    _lum_trim_gid="$(printf '%s' "$_lum_trim_gid" | tr -d '\r' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    if [ -n "$_lum_trim_uid" ] && ! [[ "$_lum_trim_uid" =~ ^[0-9]+$ ]]; then
        echo "[entrypoint] FATAL: LUMOGIS_RUN_UID must be a number" >&2
        exit 1
    fi
    if [ -n "$_lum_trim_gid" ] && ! [[ "$_lum_trim_gid" =~ ^[0-9]+$ ]]; then
        echo "[entrypoint] FATAL: LUMOGIS_RUN_GID must be a number" >&2
        exit 1
    fi
    if [ "$_lum_trim_uid" = "0" ]; then
        echo "[entrypoint] WARNING: LUMOGIS_RUN_UID=0 — the orchestrator runs as root (not recommended)" >&2
        return 0
    fi

    if [ -n "$_lum_trim_uid" ]; then
        U="$_lum_trim_uid"
        G="${_lum_trim_gid:-$_lum_trim_uid}"
    else
        # Detection: the owner of the /project directory. It is the one thing
        # no container ever creates or rewrites.
        U="$_LUM_DEFAULT_UID"
        G="$_LUM_DEFAULT_UID"
        if [ -d "$_LUM_PROJECT_DIR" ]; then
            local owner
            owner="$(stat -c '%u %g' "$_LUM_PROJECT_DIR" 2>/dev/null)"
            if [[ "$owner" =~ ^[0-9]+\ [0-9]+$ ]]; then
                if [ "${owner%% *}" = "0" ]; then
                    echo "[entrypoint] ${_LUM_PROJECT_DIR} is owned by root; running as uid ${_LUM_DEFAULT_UID}. The admin \"add ingest folder\" step will show a compose snippet instead of writing docker-compose.override.yml. Set LUMOGIS_RUN_UID=0 to keep root."
                else
                    U="${owner%% *}"
                    G="${owner##* }"
                fi
            else
                echo "[entrypoint] WARNING: could not read the owner of ${_LUM_PROJECT_DIR}; running as uid ${_LUM_DEFAULT_UID}. Set LUMOGIS_RUN_UID to choose." >&2
            fi
        fi
        # LUMOGIS_RUN_GID alone replaces the detected group.
        [ -n "$_lum_trim_gid" ] && G="$_lum_trim_gid"
    fi

    # ── Ownership repair ────────────────────────────────────────────────────
    local _lum_t0=$SECONDS _lum_total=0
    # _lum_own_tree <owned|shared|shared-one> <path> [<prune>...]
    #   One find pass. -P never follows symlinks, `chown -h` changes a link and
    #   never its target, -execdir chowns ./name from the entry's own
    #   directory (a path component swapped for a symlink cannot redirect it),
    #   and multiply-linked non-directories are never chowned (a hard link in an
    #   image-internal dir would otherwise hand its target, e.g. under /etc, to
    #   $U). Never fatal: Docker Desktop and NFS root_squash can refuse.
    _lum_own_tree() {
        local mode="$1" tpath="$2" skip selected rc n p
        shift 2
        [ -e "$tpath" ] || [ -L "$tpath" ] || return 0
        local -a fargs=(-P "$tpath") sel
        [ "$mode" = "shared-one" ] && fargs+=(-maxdepth 0)
        for p in "$@"; do fargs+=(-path "$p" -prune -o); done
        if [ "$mode" = "owned" ]; then
            sel=(\( ! -user "$U" -o ! -group "$G" \))
        else
            sel=(-user 0)
        fi
        skip="$(mktemp 2>/dev/null || echo /dev/null)"
        selected="$(mktemp 2>/dev/null || echo /dev/null)"
        # Only entries the rule selects are considered at all, so a person's own
        # hard links in a shared tree are neither chowned nor reported as skipped.
        find "${fargs[@]}" \
            \( "${sel[@]}" \) \
            \( \( ! -type d -links +1 -fprint "$skip" \) -o \
               \( -fprint "$selected" -execdir chown -h "$U:$G" {} + \) \) 2>/dev/null
        rc=$?
        n=$(wc -l < "$skip" 2>/dev/null | tr -d ' ')
        if [ "${n:-0}" -gt 0 ] 2>/dev/null; then
            echo "[entrypoint] WARNING: skipped ${n} hard-linked file(s) under ${tpath}; fix their owner by hand" >&2
        fi
        n=$(wc -l < "$selected" 2>/dev/null | tr -d ' ')
        _lum_total=$(( _lum_total + ${n:-0} ))
        if [ "$rc" -ne 0 ]; then
            echo "[entrypoint] WARNING: could not repair ownership of ${tpath} (uid ${U})" >&2
        fi
        [ "$skip" = /dev/null ] || rm -f "$skip"
        [ "$selected" = /dev/null ] || rm -f "$selected"
        return 0
    }

    # Owned trees: only Lumogis writes them, only containers see them. Each is
    # owned only at its image default path; if the operator moved it with the
    # env var, it is treated as shared below.
    local hf="${HF_HOME:-$_LUM_DEFAULT_HF_HOME}"
    local stg="${EMAIL_ATTACHMENT_STAGING_ROOT:-$_LUM_DEFAULT_STAGING}"
    local dat="${LUMOGIS_DATA_DIR:-$_LUM_DEFAULT_DATA_DIR}"
    _lum_own_tree owned "$_LUM_HOME_DIR"
    local -a _lum_moved=()
    if [ "$hf" = "$_LUM_DEFAULT_HF_HOME" ]; then _lum_own_tree owned "$hf"; else _lum_moved+=("$hf"); fi
    if [ "$stg" = "$_LUM_DEFAULT_STAGING" ]; then _lum_own_tree owned "$stg"; else _lum_moved+=("$stg"); fi
    if [ "$dat" = "$_LUM_DEFAULT_DATA_DIR" ]; then _lum_own_tree owned "$dat"; else _lum_moved+=("$dat"); fi

    # Shared trees: host-visible binds or operator-chosen paths. Only
    # root-owned entries become U:G (leftovers from the old root image or from
    # `docker compose exec`).
    # /workspace/backups is a separate bind (BACKUP_HOST_DIR): only the directory
    # itself, backup_*.zip and users/ are repaired; snapshots/ belongs to the root
    # backup service and is never touched.
    ws="${WORKSPACE_PATH:-/workspace}"
    _lum_own_tree shared "$ws" "$ws/backups"
    _lum_own_tree shared-one "$ws/backups"
    _lum_own_tree shared "${USER_EXPORT_DIR:-$ws/backups/users}"
    for f in "$ws"/backups/backup_*.zip; do
        [ -e "$f" ] && _lum_own_tree shared-one "$f"
    done
    for path in "${_lum_moved[@]}" "${CAPTURE_MEDIA_ROOT:-}" "${LUMOGIS_IMAP_EGRESS_ALLOW_DIR:-}"; do
        [ -n "$path" ] && _lum_own_tree shared "$path"
    done
    # The admin's stop-entities list (LUM-922): $ws/config/stop_entities.txt,
    # which POST /kg/stop-entities rewrites with temp file + os.replace. The
    # default sits in the workspace tree repaired above; this line names it and
    # covers a STOP_ENTITIES_PATH moved elsewhere. Only the file itself: a moved
    # path's folder may be a system directory, and is the operator's to own.
    _lum_own_tree shared-one "${STOP_ENTITIES_PATH:-$ws/config/stop_entities.txt}"
    # /app/config holds the shipped stop_entities.txt, which is also where it
    # lived before LUM-922; an install that pins STOP_ENTITIES_PATH there still
    # writes it (temp file + os.replace needs directory write). Only when it is
    # image-internal: under the dev overlay (./orchestrator:/app) it is a
    # tracked checkout dir on another device and already the checkout owner's.
    if [ -d "$_LUM_APP_CONFIG_DIR" ] \
       && [ "$(stat -c %d "$_LUM_APP_CONFIG_DIR" 2>/dev/null)" = "$(stat -c %d "$_LUM_ROOTFS_DIR" 2>/dev/null)" ]; then
        _lum_own_tree shared "$_LUM_APP_CONFIG_DIR"
    fi
    for f in "$_LUM_PROJECT_DIR/.env" "$_LUM_PROJECT_DIR/config/librechat.yaml" "$_LUM_PROJECT_DIR/docker-compose.override.yml"; do
        _lum_own_tree shared-one "$f"
    done
    echo "[entrypoint] ownership repair: ${_lum_total} entries re-owned to ${U}:${G} in $(( SECONDS - _lum_t0 ))s"

    # ── passwd / group entry for the target UID ────────────────────────────
    # The checkout owner (usually 1000) has no passwd line in the image;
    # getpass, git, psutil and torch look the user up. Idempotent.
    if ! awk -F: -v u="$U" '$3==u{f=1} END{exit !f}' "$_LUM_PASSWD_FILE" 2>/dev/null; then
        if ! printf 'lumogis-%s:x:%s:%s:Lumogis runtime user:%s:/usr/sbin/nologin\n' "$U" "$U" "$G" "$_LUM_HOME_DIR" >> "$_LUM_PASSWD_FILE" 2>/dev/null; then
            echo "[entrypoint] WARNING: could not add a passwd entry for uid $U; tools that look the user up may fail" >&2
        fi
    fi
    if ! awk -F: -v g="$G" '$3==g{f=1} END{exit !f}' "$_LUM_GROUP_FILE" 2>/dev/null; then
        printf 'lumogis-%s:x:%s:\n' "$G" "$G" >> "$_LUM_GROUP_FILE" 2>/dev/null || true
    fi
    name="$(awk -F: -v u="$U" '$3==u{print $1; exit}' "$_LUM_PASSWD_FILE" 2>/dev/null)"
    if [ -n "$name" ]; then
        export USER="$name" LOGNAME="$name"
    fi

    # ── Readability warnings (never fatal) ────────────────────────────────
    for d in $_LUM_DATA_GLOB; do
        [ -d "$d" ] || continue
        if ! setpriv --reuid="$U" --regid="$G" --clear-groups -- test -r "$d" -a -x "$d" 2>/dev/null; then
            echo "[entrypoint] WARNING: uid $U cannot read $d — documents there will not be ingested. Fix host permissions or set LUMOGIS_RUN_UID." >&2
        fi
    done
    f="${LUMOGIS_EGRESS_ACCESS_LOG:-}"
    if [ -n "$f" ] && [ -e "$f" ]; then
        if ! setpriv --reuid="$U" --regid="$G" --clear-groups -- test -r "$f" 2>/dev/null; then
            echo "[entrypoint] WARNING: uid $U cannot read $f — egress deny notices will be missing. Run once: docker compose exec egress-proxy chmod 0644 /var/log/squid/access.log" >&2
        fi
    fi

    export HOME="$_LUM_HOME_DIR"
    _LUM_DROP=(setpriv --reuid="$U" --regid="$G" --clear-groups --)
    echo "[entrypoint] Dropping privileges to uid $U gid $G"
    return 0
}
_lum_privdrop_section
# <<< privdrop-section

exec "${_LUM_DROP[@]}" "$@"
