FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu tzdata \
    && groupadd --gid 10001 erp \
    && useradd --uid 10001 --gid erp --create-home --shell /usr/sbin/nologin erp \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt

COPY backend/manage.py /app/backend/manage.py
COPY backend/erp/*.py /app/backend/erp/
COPY backend/molds/*.py /app/backend/molds/
COPY backend/molds/migrations/*.py /app/backend/molds/migrations/
COPY backend/molds/management/*.py /app/backend/molds/management/
COPY backend/molds/management/commands/*.py /app/backend/molds/management/commands/
COPY backend/orders/*.py /app/backend/orders/
COPY backend/orders/migrations/*.py /app/backend/orders/migrations/
COPY backend/production/*.py /app/backend/production/
COPY backend/production/migrations/*.py /app/backend/production/migrations/
COPY backend/quality/*.py /app/backend/quality/
COPY backend/quality/migrations/*.py /app/backend/quality/migrations/
COPY backend/analytics/*.py /app/backend/analytics/
COPY backend/analytics/migrations/*.py /app/backend/analytics/migrations/
COPY deploy/ /app/deploy/

RUN chmod +x /app/deploy/backend-entrypoint.sh /app/deploy/backup-loop.sh

WORKDIR /app/backend
EXPOSE 8000
ENTRYPOINT ["/app/deploy/backend-entrypoint.sh"]
