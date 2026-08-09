"""Seed a demonstrable tenancy: two tenants, four users, documents per department.

Creates the exact shape needed to *see* RBAC working:

  Acme (tenant A)                       Globex (tenant B)
    alice  user   [hr]                    carol  user   [hr]
    bob    user   [finance]
    admin  admin  [hr, finance]

  Documents: hr/handbook, finance/controls, legal/nda  (Acme)
             hr/handbook                                (Globex)

Alice must never see finance or legal. Bob must never see hr. Carol must never
see anything of Acme's. Admin sees Acme's departments but never Globex.

Idempotent. Passwords are generated, printed once, and never stored.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import boto3
from botocore.exceptions import ClientError

TENANTS = [
    ("t-acme", "Acme Corporation"),
    ("t-globex", "Globex Industries"),
]

# email, tenant, role, departments
USERS = [
    ("alice@acme.test", "t-acme", "user", ["hr"]),
    ("bob@acme.test", "t-acme", "user", ["finance"]),
    ("admin@acme.test", "t-acme", "admin", ["hr", "finance", "legal"]),
    ("carol@globex.test", "t-globex", "user", ["hr"]),
]

DOCUMENTS: list[tuple[str, str, str, bytes]] = [
    (
        "t-acme",
        "hr",
        "acme-hr-handbook.md",
        b"""# Acme HR Handbook

## Parental leave
Employees with 12 months of service receive 18 weeks of parental leave at full pay.
An additional 8 weeks of unpaid leave may be requested.

## Annual leave
Annual leave accrues at 2 days per calendar month, to a maximum of 24 days per year.
Unused days may be carried over up to a maximum of 5 days.

## Probation
New joiners serve a probation period of 3 months.
""",
    ),
    (
        "t-acme",
        "finance",
        "acme-finance-controls.md",
        b"""# Acme Finance Controls

## Purchase approvals
Purchases above 10000 USD require CFO sign-off and two written quotations.

## Travel expenses
Travel expenses above 500 USD require written approval from a department head.
Receipts must be submitted within 30 days of the expense being incurred.

## Payment terms
Supplier invoices are settled on net 45 day terms.
""",
    ),
    (
        "t-acme",
        "legal",
        "acme-nda-policy.md",
        b"""# Acme NDA Policy

## Mutual NDAs
All mutual non-disclosure agreements run for a term of 5 years from signature.

## Confidentiality breaches
A confirmed breach is escalated to the General Counsel within 24 hours.
""",
    ),
    (
        "t-globex",
        "hr",
        "globex-hr-handbook.md",
        b"""# Globex HR Handbook

## Parental leave
Globex employees receive 26 weeks of parental leave at full pay from day one.

## Annual leave
Globex grants a flat 30 days of annual leave per year.
""",
    ),
]


def strong_password() -> str:
    alphabet = string.ascii_letters + string.digits
    body = "".join(secrets.choice(alphabet) for _ in range(16))
    return f"Ekba!{body}9"


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-id", required=True)
    ap.add_argument("--region", default="ap-south-1")
    ap.add_argument("--skip-ingest", action="store_true")
    args = ap.parse_args()

    from app.clients.euri import EuriClient
    from app.clients.vectorstore import VectorStore
    from app.core.config import get_settings
    from app.core.logging import configure_logging
    from app.db import session as db
    from app.db.models import Document, Tenant, User
    from app.ingestion.pipeline import IngestionPipeline
    from sqlalchemy import select

    configure_logging("WARNING")
    settings = get_settings()
    cog = boto3.client("cognito-idp", region_name=args.region)

    # ---------------------------------------------------------------- Cognito
    print("Cognito users")
    credentials: list[tuple[str, str, str]] = []
    subs: dict[str, str] = {}
    for email, _tenant, role, _depts in USERS:
        password = strong_password()
        try:
            resp = cog.admin_create_user(
                UserPoolId=args.pool_id,
                Username=email,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "email_verified", "Value": "true"},
                ],
                MessageAction="SUPPRESS",
            )
            sub = next(
                a["Value"] for a in resp["User"]["Attributes"] if a["Name"] == "sub"
            )
            created = True
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "UsernameExistsException":
                raise
            got = cog.admin_get_user(UserPoolId=args.pool_id, Username=email)
            sub = next(a["Value"] for a in got["UserAttributes"] if a["Name"] == "sub")
            created = False

        # Always set a fresh permanent password so the account is immediately usable.
        cog.admin_set_user_password(
            UserPoolId=args.pool_id, Username=email, Password=password, Permanent=True
        )
        try:
            cog.admin_add_user_to_group(
                UserPoolId=args.pool_id, Username=email, GroupName=role
            )
        except ClientError:
            pass  # group membership is advisory; the database is authoritative

        subs[email] = sub
        credentials.append((email, password, role))
        print(f"  {'created' if created else 'exists '}  {email:<22} sub={sub[:8]}…")

    # ---------------------------------------------------------------- database
    print("\nDatabase rows")
    async with db.get_sessionmaker()() as s:
        for tid, name in TENANTS:
            if not (
                await s.execute(select(Tenant).where(Tenant.id == tid))
            ).scalar_one_or_none():
                s.add(Tenant(id=tid, name=name))
                print(f"  tenant  {tid}")
        await s.commit()

    async with db.get_sessionmaker()() as s:
        for email, tenant, role, depts in USERS:
            sub = subs[email]
            existing = (
                await s.execute(select(User).where(User.id == sub))
            ).scalar_one_or_none()
            if existing:
                existing.tenant_id, existing.role, existing.departments = (
                    tenant,
                    role,
                    depts,
                )
                existing.email = email
            else:
                s.add(
                    User(
                        id=sub,
                        tenant_id=tenant,
                        email=email,
                        role=role,
                        departments=depts,
                        status="active",
                    )
                )
            print(f"  user    {email:<22} {tenant:<10} {role:<6} {depts}")
        await s.commit()

    # ---------------------------------------------------------------- documents
    if args.skip_ingest:
        print("\n(skipping ingestion)")
    else:
        print("\nDocuments")
        euri = EuriClient(settings)
        vectors = VectorStore(settings)
        await vectors.ensure_collection()
        pipeline = IngestionPipeline(settings, euri, vectors)
        owner = subs["admin@acme.test"]

        for tenant, dept, name, body in DOCUMENTS:
            import hashlib

            checksum = hashlib.sha256(body).hexdigest()
            async with db.get_sessionmaker()() as s:
                dup = (
                    await s.execute(
                        select(Document).where(
                            Document.tenant_id == tenant,
                            Document.department == dept,
                            Document.checksum == checksum,
                        )
                    )
                ).scalar_one_or_none()
                if dup:
                    print(f"  exists  {tenant}/{dept}/{name}")
                    continue

            result = await pipeline.ingest(
                data=body,
                filename=name,
                mime="text/markdown",
                tenant_id=tenant,
                department=dept,
                owner_id=owner,
            )
            async with db.get_sessionmaker()() as s:
                s.add(
                    Document(
                        id=result.document_id,
                        tenant_id=tenant,
                        department=dept,
                        name=name,
                        s3_uri=f"s3://{settings.s3_bucket_documents}/{tenant}/{dept}/{name}",
                        mime_type="text/markdown",
                        size_bytes=len(body),
                        checksum=result.checksum,
                        owner_id=owner,
                        page_count=result.pages,
                    )
                )
                await s.commit()
            print(f"  ingest  {tenant}/{dept}/{name}  {result.chunks_written} chunks")
        await euri.aclose()

    print("\n" + "=" * 62)
    print("CREDENTIALS — shown once, not stored anywhere")
    print("=" * 62)
    for email, password, role in credentials:
        print(f"  {email:<22} {password:<24} ({role})")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
