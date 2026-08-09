"""Generate realistic dev activity so the admin console has something to show.

Runs a spread of real questions as each seeded user against the deployed API:
answerable ones, out-of-scope ones that must refuse, and a couple that exercise
the agent's multi-step path. Every request writes a genuine request_usage row, so
the operations page reflects real latency, tokens and cost rather than fixtures.

    python scripts/seed_activity.py --url https://... --creds creds.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REFUSAL = "I could not find enough evidence in the approved documents to answer this question."

# (actor, question, expectation) — expectation is only for the console summary.
TRAFFIC: list[tuple[str, str, str]] = [
    ("alice@acme.test", "How many weeks of parental leave do employees get?", "answer"),
    ("alice@acme.test", "How does annual leave accrue?", "answer"),
    ("alice@acme.test", "How many unused leave days can be carried over?", "answer"),
    ("alice@acme.test", "How long is the probation period?", "answer"),
    ("alice@acme.test", "What is the CFO sign-off threshold?", "refusal (no finance)"),
    ("alice@acme.test", "How long do mutual NDAs run for?", "refusal (no legal)"),
    (
        "alice@acme.test",
        "What is the remote working allowance?",
        "refusal (not in corpus)",
    ),
    ("bob@acme.test", "What is the travel expense approval limit?", "answer"),
    ("bob@acme.test", "Within how many days must receipts be submitted?", "answer"),
    ("bob@acme.test", "What are the supplier payment terms?", "answer"),
    ("bob@acme.test", "How many quotations are needed for a large purchase?", "answer"),
    ("bob@acme.test", "How many weeks of parental leave?", "refusal (no hr)"),
    ("admin@acme.test", "What is the CFO sign-off threshold?", "answer"),
    ("admin@acme.test", "How long do mutual NDAs run for?", "answer"),
    ("admin@acme.test", "How many weeks of parental leave?", "answer"),
    (
        "admin@acme.test",
        "If an employee takes the full paid and unpaid parental leave, how many weeks is that in total?",
        "answer (multi-step)",
    ),
    ("admin@acme.test", "When is a confidentiality breach escalated?", "answer"),
    ("carol@globex.test", "How many weeks of parental leave?", "answer (Globex)"),
    ("carol@globex.test", "How many days of annual leave?", "answer (Globex)"),
    (
        "carol@globex.test",
        "What is the CFO sign-off threshold?",
        "refusal (other tenant)",
    ),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--creds", required=True)
    args = ap.parse_args()

    base = args.url.rstrip("/")
    creds: dict[str, str] = json.loads(Path(args.creds).read_text(encoding="utf-8"))
    http = httpx.Client(timeout=90)

    tokens: dict[str, str] = {}
    for email, password in creds.items():
        r = http.post(f"{base}/auth/login", json={"email": email, "password": password})
        if r.status_code == 200:
            tokens[email] = r.json()["id_token"]
        else:
            print(f"  login failed: {email} ({r.status_code})")

    answered = refused = blocked = failed = 0
    total_cost = 0.0
    print(f"\nGenerating {len(TRAFFIC)} requests\n")

    for actor, question, expectation in TRAFFIC:
        if actor not in tokens:
            continue
        r = http.post(
            f"{base}/chat",
            json={"question": question},
            headers={"Authorization": f"Bearer {tokens[actor]}"},
        )
        if r.status_code == 400:
            blocked += 1
            print(f"  blocked   {actor:<20} {question[:52]}")
            continue
        if r.status_code != 200:
            failed += 1
            print(f"  HTTP {r.status_code}  {actor:<20} {question[:52]}")
            continue

        body = r.json()
        total_cost += float(body.get("estimated_cost") or 0)
        is_refusal = body["answer"] == REFUSAL
        if is_refusal:
            refused += 1
        else:
            answered += 1
        mark = "refused " if is_refusal else "answered"
        print(
            f"  {mark}  {actor:<20} {body['latency_ms']:>6}ms  "
            f"{len(body['citations'])} cite  [{expectation}]"
        )

    print(
        f"\n  answered={answered}  refused={refused}  blocked={blocked}  failed={failed}"
    )
    print(f"  estimated cost of this run: ${total_cost:.6f}")
    print("\nThe admin Operations page now reflects this traffic.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
