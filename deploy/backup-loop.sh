#!/bin/sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-/app/backups}"
BACKUP_RETENTION_COUNT="${BACKUP_RETENTION_COUNT:-30}"

if [ "$(id -u)" = "0" ]; then
  mkdir -p "$BACKUP_DIR"
  chown -R erp:erp "$BACKUP_DIR"
  exec gosu erp "$0" "$@"
fi

run_backup() {
  cd /app/backend
  python manage.py backup_erp \
    --output "$BACKUP_DIR" \
    --retention-count "$BACKUP_RETENTION_COUNT"
}

# TZ由Compose固定为Asia/Shanghai，因此这里的02:00就是北京时间。
while true; do
  now_epoch="$(date +%s)"
  today_0200="$(date -d 'today 02:00' +%s)"
  if [ "$now_epoch" -ge "$today_0200" ]; then
    next_epoch="$(date -d 'tomorrow 02:00' +%s)"
  else
    next_epoch="$today_0200"
  fi
  sleep_seconds=$((next_epoch - now_epoch))
  echo "下一次备份将在 $(date -d "@$next_epoch" '+%Y-%m-%d %H:%M:%S %Z') 执行。"
  sleep "$sleep_seconds"

  success=0
  for attempt in 1 2 3; do
    if run_backup; then
      success=1
      break
    fi
    echo "备份失败（第${attempt}次），5分钟后重试。" >&2
    sleep 300
  done
  if [ "$success" -ne 1 ]; then
    echo "连续三次备份失败，退出并交由Compose重启服务。" >&2
    exit 1
  fi
done
