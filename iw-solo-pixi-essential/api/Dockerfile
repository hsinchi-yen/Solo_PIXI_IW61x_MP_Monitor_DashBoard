# Build context: iw-solo-pixi-essential/  (docker-compose: build.context ., dockerfile api/Dockerfile)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app/api

COPY api/requirements.txt /app/api/requirements.txt
RUN pip install --no-cache-dir -r /app/api/requirements.txt

# Shared with the desktop tools: the IW611 parser and splitter live one directory above api/.
COPY schema.sql /app/schema.sql
COPY parsers/ /app/parsers/
COPY ingestion/ /app/ingestion/
COPY api/ /app/api/
# The dashboard is also served by the API itself, so it opens on the API port (8003) as it always has,
# not only through nginx (8004).
COPY web/ /app/web/

RUN useradd --system --uid 1000 --home /app iw && mkdir -p /tmp/iw_uploads && chown iw /tmp/iw_uploads
USER iw

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "2", "--timeout-keep-alive", "30"]
