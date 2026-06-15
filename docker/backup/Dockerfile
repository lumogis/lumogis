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
  && rm -rf /var/lib/apt/lists/*

# Pin supercronic v0.2.33 (official release checksum from GitHub).
ENV SUPERCRONIC_URL=https://github.com/aptible/supercronic/releases/download/v0.2.33/supercronic-linux-amd64 \
    SUPERCRONIC_SHA1SUM=71b0d58cc53f6bd72cf2f293e09e294b79c666d8 \
    SUPERCRONIC=supercronic-linux-amd64

RUN curl -fsSLO "$SUPERCRONIC_URL" \
  && echo "${SUPERCRONIC_SHA1SUM}  ${SUPERCRONIC}" | sha1sum -c - \
  && chmod +x "$SUPERCRONIC" \
  && mv "$SUPERCRONIC" /usr/local/bin/supercronic

COPY --from=falkordb-donor / /falkordb-donor/
COPY docker/backup/install-redis-check-rdb-v13.sh /tmp/install-redis-check-rdb-v13.sh
RUN chmod +x /tmp/install-redis-check-rdb-v13.sh \
  && /tmp/install-redis-check-rdb-v13.sh /falkordb-donor \
  && rm -rf /falkordb-donor /tmp/install-redis-check-rdb-v13.sh \
  && /opt/falkordb-redis-check/ld-linux-x86-64.so.2 \
       --library-path /opt/falkordb-redis-check/lib \
       --list /usr/local/bin/redis-check-rdb-v13 \
  | tee /tmp/redis-check-rdb-v13.ldd \
  && ! grep -q 'not found' /tmp/redis-check-rdb-v13.ldd

COPY scripts/backup /scripts/backup
COPY docker/backup/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh /scripts/backup/*.sh /scripts/backup/lib/*.sh /scripts/backup/falkordb-dump-restore.sh

ENTRYPOINT ["/entrypoint.sh"]
