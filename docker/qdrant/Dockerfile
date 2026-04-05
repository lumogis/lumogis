# Official qdrant/qdrant image does not ship curl or wget; Docker Compose
# healthchecks run inside the container, so we add curl only (~few MB).
FROM qdrant/qdrant:v1.14.1
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
USER 0:0
