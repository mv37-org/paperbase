#!/usr/bin/env bash

set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  print_cloud_sql_public_url.sh INSTANCE_NAME DB_NAME [DB_USER] [PROJECT_ID]

Arguments:
  INSTANCE_NAME  Cloud SQL instance name
  DB_NAME        Database name
  DB_USER        Database user (default: postgres)
  PROJECT_ID     GCP project id (default: gcloud configured project)

Environment:
  DB_PASSWORD    Optional password to embed in the printed URL.
                 If unset, the script uses the placeholder <PASSWORD>.

Example:
  ./scripts/print_cloud_sql_public_url.sh paperbase-pg paperbase postgres sudo-476714
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 2 || $# -gt 4 ]]; then
  usage >&2
  exit 1
fi

instance_name="$1"
db_name="$2"
db_user="${3:-postgres}"
project_id="${4:-$(gcloud config get-value project 2>/dev/null)}"

if [[ -z "$project_id" || "$project_id" == "(unset)" ]]; then
  echo "error: project id is required; pass it as the 4th argument or run 'gcloud config set project PROJECT_ID'" >&2
  exit 1
fi

if ! command -v gcloud >/dev/null 2>&1; then
  echo "error: gcloud CLI is required" >&2
  exit 1
fi

public_ip="$(
  gcloud sql instances describe "$instance_name" \
    --project="$project_id" \
    --flatten='ipAddresses[]' \
    --format='csv[no-heading](ipAddresses.type,ipAddresses.ipAddress)' \
  | awk -F, '$1 == "PRIMARY" { print $2; exit }'
)"

if [[ -z "$public_ip" ]]; then
  echo "error: no public PRIMARY IP found for instance '$instance_name' in project '$project_id'" >&2
  exit 1
fi

password="${DB_PASSWORD:-<PASSWORD>}"
database_url="postgresql+psycopg://${db_user}:${password}@${public_ip}:5432/${db_name}?sslmode=require"

echo "$database_url"
