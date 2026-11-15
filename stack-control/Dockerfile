# Pinned Debian release so the Docker CLI pins below keep resolving (LUM-980).
FROM python:3.12-slim-trixie

# stack-control is a published, long-lived image (LUM-980): pin the Docker CLI
# and Compose plugin to a minor line (patch releases float) and check both at
# each release (docs/RELEASE-MANUAL-CHECKLIST.md).
ARG DOCKER_CLI_VERSION=5:29.9.*
ARG DOCKER_COMPOSE_VERSION=5.7.*

# Install Docker CLI (for `docker compose restart`), gosu (privilege drop), and bash (entrypoint)
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    gosu \
    gnupg && \
    install -m 0755 -d /etc/apt/keyrings && \
    curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg && \
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/debian $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
      > /etc/apt/sources.list.d/docker.list && \
    apt-get update && apt-get install -y --no-install-recommends "docker-ce-cli=${DOCKER_CLI_VERSION}" "docker-compose-plugin=${DOCKER_COMPOSE_VERSION}" && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .
COPY entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh

# Root at start — entrypoint.sh maps docker.sock GID then gosu → appuser (uid 1001)
EXPOSE 9000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]

# LUM-980: the version this image was built as: dev for a local build, X.Y.Z for a
# release, the short commit for main. Last in the file so a new version rebuilds
# no other layer. Backups record it, and checks use it to tell pulled from built.
ARG LUMOGIS_IMAGE_VERSION=dev
ENV LUMOGIS_IMAGE_VERSION=$LUMOGIS_IMAGE_VERSION
