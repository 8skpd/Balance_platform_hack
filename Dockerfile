# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/tsconfig.json frontend/vite.config.ts ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BALANCE_DATA_DIR=/app/var \
    BALANCE_FRONTEND_DIST=/app/frontend/dist
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends libseccomp2 libstdc++6 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.lock /app/backend/requirements.lock
RUN pip install --no-cache-dir -r /app/backend/requirements.lock
COPY backend/app /app/backend/app
COPY --from=frontend /build/dist /app/frontend/dist
COPY scripts/create_admin.py scripts/backup.py /app/scripts/
COPY docker/runtime_check.py docker/healthcheck.py docker/smoke_test.py /app/docker/
COPY --chmod=755 docker/entrypoint.sh /app/docker/entrypoint.sh
RUN groupadd --gid 10001 balance \
    && useradd --uid 10001 --gid balance --create-home balance \
    && mkdir -p /app/var \
    && chown balance:balance /app/var
USER 10001:10001
WORKDIR /app/backend
EXPOSE 8000
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
