FROM python:3.13-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir mcp==1.30.0

WORKDIR /app
COPY server.py smoke_client.py ./
USER 65534:65534
EXPOSE 8765

CMD ["python", "server.py"]
