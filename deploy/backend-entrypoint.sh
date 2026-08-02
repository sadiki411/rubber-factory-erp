#!/bin/sh
set -eu

SQLITE_PATH="${SQLITE_PATH:-/app/data/db.sqlite3}"
MEDIA_ROOT="${MEDIA_ROOT:-/app/media}"
STATIC_ROOT="${STATIC_ROOT:-/app/staticfiles}"
BACKUP_DIR="${BACKUP_DIR:-/app/backups}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$(dirname "$SQLITE_PATH")" "$MEDIA_ROOT" "$STATIC_ROOT" "$BACKUP_DIR"
  chown -R erp:erp "$(dirname "$SQLITE_PATH")" "$MEDIA_ROOT" "$STATIC_ROOT" "$BACKUP_DIR"
  exec gosu erp "$0" "$@"
fi

if [ "${DJANGO_DEBUG:-0}" != "1" ]; then
  case "${DJANGO_SECRET_KEY:-}" in
    ""|change-this-to-a-long-random-string|dev-only-change-me)
      SECRET_KEY_FILE="$(dirname "$SQLITE_PATH")/.django-secret-key"
      if [ -s "$SECRET_KEY_FILE" ]; then
        DJANGO_SECRET_KEY="$(cat "$SECRET_KEY_FILE")"
      else
        umask 077
        DJANGO_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(64))')"
        printf '%s\n' "$DJANGO_SECRET_KEY" > "$SECRET_KEY_FILE"
        chmod 600 "$SECRET_KEY_FILE"
        echo "已生成并持久化Django安全密钥。"
      fi
      export DJANGO_SECRET_KEY
      ;;
  esac
fi

if [ "$#" -gt 0 ]; then
  exec "$@"
fi

if [ "${BACKUP_BEFORE_MIGRATE:-1}" = "1" ] && [ -s "$SQLITE_PATH" ]; then
  echo "检测到现有数据库，迁移前先创建一致性备份。"
  python manage.py backup_erp \
    --output "$BACKUP_DIR" \
    --retention-count "${BACKUP_RETENTION_COUNT:-30}"
fi

python manage.py migrate --noinput
python manage.py collectstatic --noinput --clear
python manage.py init_erp

exec gunicorn erp.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 1 \
  --threads "${GUNICORN_THREADS:-4}" \
  --timeout 120 \
  --graceful-timeout 30 \
  --max-requests 1000 \
  --max-requests-jitter 100 \
  --forwarded-allow-ips='*' \
  --access-logfile - \
  --error-logfile -
