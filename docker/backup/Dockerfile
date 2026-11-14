# SPDX-License-Identifier: AGPL-3.0-only
# Lumogis DR backup sidecar (LUM-185) — postgres client tools + supercronic.
#
# FALKORDB_IMAGE must stay in sync with docker-compose.falkordb.yml (see .env.example).
ARG FALKORDB_IMAGE=falkordb/falkordb:v4.18.1

FROM ${FALKORDB_IMAGE} AS falkordb-donor

FROM postgres:16.8

ARG FALKORDB_IMAGE

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    jq \
    redis-tools \
    rsync \
  && rm -rf /var/lib/apt/lists/*

# Pin supercronic v0.2.33 (official release checksums from the GitHub release
# notes). TARGETARCH picks the binary: an amd64 binary in an arm64 image (Apple
# Silicon, Raspberry Pi, ARM servers) fails at start with "exec format error"
# (LUM-310).
ARG TARGETARCH
RUN case "${TARGETARCH:-amd64}" in \
      amd64) SUPERCRONIC=supercronic-linux-amd64 SUPERCRONIC_SHA1SUM=71b0d58cc53f6bd72cf2f293e09e294b79c666d8 ;; \
      arm64) SUPERCRONIC=supercronic-linux-arm64 SUPERCRONIC_SHA1SUM=e0f0c06ebc5627e43b25475711e694450489ab00 ;; \
      *) echo "backup image: unsupported TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac \
  && curl -fsSLO "https://github.com/aptible/supercronic/releases/download/v0.2.33/${SUPERCRONIC}" \
  && echo "${SUPERCRONIC_SHA1SUM}  ${SUPERCRONIC}" | sha1sum -c - \
  && chmod +x "$SUPERCRONIC" \
  && mv "$SUPERCRONIC" /usr/local/bin/supercronic

COPY --from=falkordb-donor / /falkordb-donor/
COPY docker/backup/install-redis-check-rdb-v13.sh /tmp/install-redis-check-rdb-v13.sh
RUN chmod +x /tmp/install-redis-check-rdb-v13.sh \
  && /tmp/install-redis-check-rdb-v13.sh /falkordb-donor \
  && rm -rf /falkordb-donor /tmp/install-redis-check-rdb-v13.sh \
  && /opt/falkordb-redis-check/ld.so \
       --library-path /opt/falkordb-redis-check/lib \
       --list /usr/local/bin/redis-check-rdb-v13 \
  | tee /tmp/redis-check-rdb-v13.ldd \
  && ! grep -q 'not found' /tmp/redis-check-rdb-v13.ldd

COPY scripts/backup /scripts/backup
COPY docker/backup/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh /scripts/backup/*.sh /scripts/backup/lib/*.sh /scripts/backup/falkordb-dump-restore.sh

ENTRYPOINT ["/entrypoint.sh"]
