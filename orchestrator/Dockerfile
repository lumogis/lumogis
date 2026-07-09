FROM python:3.12-slim
# jq (LUM-337): required by scripts/doctor/run.sh in --json mode so an operator
# running `make doctor ARGS="--json"` inside the orchestrator container works
# (run.sh exits "DOCTOR_FATAL: jq not found" without it). The pytest suite stubs
# jq, so this is the operator-path fix rather than a test-pass fix.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr poppler-utils curl git jq nodejs && rm -rf /var/lib/apt/lists/* \
    && git config --global --add safe.directory '*'
WORKDIR /app
COPY orchestrator/requirements-core.txt orchestrator/requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt
COPY orchestrator/ .
COPY config/ /opt/lumogis/config/
COPY orchestrator/docker-entrypoint.sh /docker-entrypoint.sh
# Strip CR so Windows CRLF checkouts do not break shebang (exec: no such file)
RUN sed -i 's/\r$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
