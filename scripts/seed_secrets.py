"""Seed a local .env into AWS Secrets Manager.

Creates on first run, adds a version thereafter. There is NO delete code path in
this file, and tests/security/test_no_delete_paths.py asserts that.

    python scripts/seed_secrets.py --env dev --file .env --dry-run
    python scripts/seed_secrets.py --env dev --file .env

Never prints a secret value. The dry run prints key names and a verdict only.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Keys worth seeding. Anything else in .env is local-only config and is skipped.
SEEDABLE = {
    "EURI_API_KEY": "euri-api-key",
    "DATABASE_URL": "database-url",
    "REDIS_URL": "redis-url",
    "QDRANT_API_KEY": "qdrant-api-key",
    "QDRANT_URL": "qdrant-url",
    "COGNITO_CLIENT_SECRET": "cognito-client-secret",
    "LANGSMITH_API_KEY": "langsmith-api-key",
}

PROJECT = "ekba"


def parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise SystemExit(f"{path} not found")
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip().strip('"').strip("'")
        # Skip anything still carrying a placeholder — seeding one would look like
        # success while leaving the workload with an unusable credential.
        placeholder = (
            not value
            or value.startswith(("replace-me", "YOUR-"))
            or any(marker in value for marker in ("PLACEHOLDER", "SET_FROM_TERRAFORM"))
        )
        if not placeholder:
            out[key.strip()] = value
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True, choices=["dev", "prod"])
    ap.add_argument("--file", default=".env")
    ap.add_argument("--region", default="ap-south-1")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    values = parse_env(Path(args.file))
    planned = {SEEDABLE[k]: v for k, v in values.items() if k in SEEDABLE}
    missing = [k for k in SEEDABLE if k not in values]

    if not planned:
        print("Nothing to seed — no seedable keys had real values.")
        return 1

    print(f"Environment : {args.env}")
    print(f"Region      : {args.region}")
    print(f"Seedable    : {len(planned)} of {len(SEEDABLE)}")
    if missing:
        print(f"Absent      : {', '.join(missing)}")
    print()

    if args.dry_run:
        for name in sorted(planned):
            print(f"  would seed  {PROJECT}/{args.env}/{name}  (value hidden)")
        print("\nDry run only. Nothing was written.")
        return 0

    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError:
        raise SystemExit("boto3 is required: pip install '.[aws]'") from None

    client = boto3.client("secretsmanager", region_name=args.region)
    created = updated = 0

    for name, value in sorted(planned.items()):
        secret_id = f"{PROJECT}/{args.env}/{name}"
        try:
            client.describe_secret(SecretId=secret_id)
            exists = True
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                raise
            exists = False

        if exists:
            # Add a new version. Never delete, never recreate.
            client.put_secret_value(SecretId=secret_id, SecretString=value)
            updated += 1
            print(f"  updated  {secret_id}  (new version)")
        else:
            client.create_secret(
                Name=secret_id,
                SecretString=value,
                Tags=[
                    {"Key": "Project", "Value": PROJECT},
                    {"Key": "Environment", "Value": args.env},
                    {"Key": "ManagedBy", "Value": "seed_secrets"},
                ],
            )
            created += 1
            print(f"  created  {secret_id}")

    print(f"\nDone. created={created} updated={updated} deleted=0")
    print(
        "This script has no delete path. Rotation adds a version; it never removes one."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
