#!/bin/sh
# Encrypted Postgres backup. Run daily (cron or a scheduled job), from the host
# running docker compose, or anywhere `pg_dump` can reach the database.
#
#   BACKUP_DIR=/var/backups/schoolpay \
#   BACKUP_AGE_RECIPIENT=age1...      \   # public key; the private key is kept OFF this server
#   BACKUP_KEEP_DAYS=30               \
#   BACKUP_UPLOAD_CMD='rclone copy "$1" offsite:schoolpay-backups/' \
#   deploy/backup.sh
#
# What it does:
#   1. pg_dump (custom format: compressed, restorable table by table) inside the db container
#   2. encrypts it with age to BACKUP_AGE_RECIPIENT, so a stolen backup is useless
#   3. checks the encrypted file isn't empty, writes a SHA-256 next to it
#   4. optionally copies it off the server (BACKUP_UPLOAD_CMD gets the file path as $1)
#   5. deletes local backups older than BACKUP_KEEP_DAYS
#
# The database holds encrypted fields (guardian phones and emails, MFA secrets):
# a restore also needs FIELD_ENCRYPTION_KEYS, which must be backed up separately
# (a password manager or a sealed envelope), never alongside these files.
set -eu

: "${BACKUP_DIR:?set BACKUP_DIR}"
: "${BACKUP_AGE_RECIPIENT:?set BACKUP_AGE_RECIPIENT (an age public key)}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-30}"
COMPOSE="${COMPOSE:-docker compose}"
DB_SERVICE="${DB_SERVICE:-db}"

command -v age >/dev/null || { echo "age is not installed (https://age-encryption.org)" >&2; exit 1; }
mkdir -p "$BACKUP_DIR"
umask 077

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
file="$BACKUP_DIR/schoolpay-$stamp.dump.age"

# The dump never touches the disk unencrypted
$COMPOSE exec -T "$DB_SERVICE" pg_dump -U schoolpay -d schoolpay --format=custom --no-owner \
  | age --encrypt --recipient "$BACKUP_AGE_RECIPIENT" --output "$file"

[ -s "$file" ] || { echo "Backup is empty: $file" >&2; rm -f "$file"; exit 1; }
sha256sum "$file" > "$file.sha256"
echo "Backup written: $file ($(du -h "$file" | cut -f1))"

if [ -n "${BACKUP_UPLOAD_CMD:-}" ]; then
  sh -c "$BACKUP_UPLOAD_CMD" upload "$file"
  sh -c "$BACKUP_UPLOAD_CMD" upload "$file.sha256"
  echo "Copied off the server"
fi

find "$BACKUP_DIR" -name 'schoolpay-*.dump.age*' -mtime +"$KEEP_DAYS" -delete
