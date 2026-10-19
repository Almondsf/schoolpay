#!/bin/sh
# Restore an encrypted backup into a database. Practise this monthly on a
# throwaway database: a backup that has never been restored isn't a backup.
#
#   BACKUP_AGE_IDENTITY=/secure/age-key.txt \          # the private key (kept off the server)
#   RESTORE_DATABASE_URL=postgresql://schoolpay@host:5432/schoolpay_restore_test \
#   deploy/restore.sh /var/backups/schoolpay/schoolpay-20261019T020000Z.dump.age
#
# Restores into an EMPTY database, as its owner. Refuses a database that
# already has tables, so it can't overwrite live data by mistake. Afterwards,
# start the API against it with the same FIELD_ENCRYPTION_KEYS as production
# and check /api/v1/health/ready, a school's billing summary and a receipt.
set -eu

backup="${1:?usage: deploy/restore.sh <backup.dump.age>}"
: "${BACKUP_AGE_IDENTITY:?set BACKUP_AGE_IDENTITY (the age private key file)}"
: "${RESTORE_DATABASE_URL:?set RESTORE_DATABASE_URL}"

if [ -f "$backup.sha256" ]; then
  sha256sum -c "$backup.sha256" || { echo "Checksum doesn't match: the file is damaged" >&2; exit 1; }
fi

tables="$(psql "$RESTORE_DATABASE_URL" -Atc "select count(*) from information_schema.tables where table_schema = 'public'")"
if [ "$tables" != "0" ]; then
  echo "The target database already has $tables tables. Restore into an empty database." >&2
  exit 1
fi

age --decrypt --identity "$BACKUP_AGE_IDENTITY" "$backup" \
  | pg_restore --dbname "$RESTORE_DATABASE_URL" --no-owner --exit-on-error

echo "Restored. Rows per key table:"
psql "$RESTORE_DATABASE_URL" -c "select 'schools' as t, count(*) from schools union all
  select 'students', count(*) from students union all select 'payments', count(*) from payments union all
  select 'receipts', count(*) from receipts union all select 'audit_log', count(*) from audit_log"
