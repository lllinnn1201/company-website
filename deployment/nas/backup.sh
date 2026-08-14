#!/usr/bin/env sh
# Run this from Synology Task Scheduler once per day.
# Usage: /volume1/docker/company-website/deployment/nas/backup.sh /volume1/backups/company-portal
set -eu

backup_dir="${1:?Pass an absolute backup directory as the first argument}"
container="company-portal-mysql"
database="${MYSQL_DATABASE:-ai_news}"

mkdir -p "$backup_dir"
timestamp="$(date '+%Y-%m-%d_%H-%M-%S')"
destination="$backup_dir/${database}_${timestamp}.sql.gz"

docker exec "$container" sh -c \
  'exec mysqldump --single-transaction --routines --triggers -uroot -p"$MYSQL_ROOT_PASSWORD" "$MYSQL_DATABASE"' \
  | gzip > "$destination"

find "$backup_dir" -type f -name "${database}_*.sql.gz" -mtime +30 -delete
