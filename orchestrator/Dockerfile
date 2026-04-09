FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr poppler-utils curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY orchestrator/requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt
COPY orchestrator/ .
COPY config/ /opt/lumogis/config/
COPY orchestrator/docker-entrypoint.sh /docker-entrypoint.sh
# Strip CR so Windows CRLF checkouts do not break shebang (exec: no such file)
RUN sed -i 's/\r$//' /docker-entrypoint.sh && chmod +x /docker-entrypoint.sh
ENTRYPOINT ["/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
