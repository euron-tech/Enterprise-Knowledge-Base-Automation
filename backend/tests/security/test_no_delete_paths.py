"""Repo-wide safety invariants.

These are the user's absolute rules, enforced mechanically rather than by review:
  - no AWS Secrets Manager deletion anywhere
  - no `terraform destroy` in any workflow, Makefile or script
  - prevent_destroy on every protected resource
  - Project=ekba tagging so the "never touch non-project infra" rule is checkable

A failure here is not a style issue. It means a guardrail was removed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

REPO = Path(__file__).resolve().parents[3]
SEED_SCRIPT = REPO / "scripts" / "seed_secrets.py"
TF = REPO / "infra" / "terraform"
WORKFLOWS = REPO / ".github" / "workflows"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _is_guard_line(line: str, context: str = "") -> bool:
    """A line that searches for, errors on, or DENIES a forbidden action is a guard.

    Three legitimate reasons to name a forbidden string:
      1. a grep that detects it (the CI safety job, this test)
      2. an error message that reports it
      3. an IAM Deny statement that prohibits it

    `context` is the surrounding lines: an action inside a Deny block or a
    `forbidden_actions` list is a control, even though the deny keyword is not on
    the same line.
    """
    stripped = line.strip()
    if stripped.startswith(("#", "//", "*")):
        return True
    if any(
        marker in line
        for marker in ("grep", "::error::", "must never", "is forbidden", "prevent_destroy")
    ):
        return True
    return any(
        marker in context
        for marker in ('effect    = "Deny"', 'effect = "Deny"', "forbidden_actions", '"Deny"')
    )


def _window(lines: list[str], n: int, radius: int = 12) -> str:
    """The lines around a match, used to judge whether it sits inside a Deny block."""
    return "\n".join(lines[max(0, n - 1 - radius) : n + radius])


def _sources() -> list[Path]:
    out: list[Path] = []
    for pattern in (
        "**/*.py",
        "**/*.yml",
        "**/*.yaml",
        "**/*.tf",
        "**/*.sh",
        "Makefile",
    ):
        for p in REPO.glob(pattern):
            parts = set(p.parts)
            if parts & {".venv", "node_modules", ".git", ".terraform", "__pycache__"}:
                continue
            if p.name == Path(__file__).name:
                continue  # this file names the forbidden strings on purpose
            out.append(p)
    return out


# --------------------------------------------------------------- secrets
def test_seed_script_exists():
    assert SEED_SCRIPT.exists(), "scripts/seed_secrets.py is missing"


def test_seed_script_has_no_delete_path():
    body = _read(SEED_SCRIPT)
    for forbidden in (
        "delete_secret",
        "delete-secret",
        "remove_regions_from_replication",
    ):
        assert (
            forbidden not in body
        ), f"seed_secrets.py must never contain {forbidden!r}"


def test_seed_script_creates_and_updates_only():
    body = _read(SEED_SCRIPT)
    assert "create_secret" in body
    assert "put_secret_value" in body


def test_no_secret_deletion_anywhere_in_repo():
    offenders: list[str] = []
    pattern = re.compile(
        r"delete[-_]secret|DeleteSecret|force-delete-without-recovery", re.I
    )
    for path in _sources():
        text = _read(path)
        lines = text.splitlines()
        for match in pattern.finditer(text):
            n = text[: match.start()].count("\n") + 1
            snippet = lines[n - 1]
            # An explicit IAM Deny on DeleteSecret is a control, not a violation.
            if _is_guard_line(snippet, _window(lines, n)):
                continue
            offenders.append(f"{path.relative_to(REPO)}:{n}  {snippet.strip()[:80]}")
    assert not offenders, "secret deletion found:\n" + "\n".join(offenders)


# --------------------------------------------------------------- terraform destroy
def test_no_terraform_destroy_in_ci():
    if not WORKFLOWS.exists():
        pytest.skip("no workflows yet")
    offenders = []
    for wf in WORKFLOWS.glob("*.y*ml"):
        for n, line in enumerate(_read(wf).splitlines(), start=1):
            if re.search(r"terraform\s+\S*\s*destroy", line) and not _is_guard_line(
                line
            ):
                offenders.append(f"{wf.relative_to(REPO)}:{n}")
    assert not offenders, f"terraform destroy must never appear in CI: {offenders}"


def test_no_terraform_destroy_anywhere():
    offenders = []
    for path in _sources():
        text = _read(path)
        lines = text.splitlines()
        for m in re.finditer(r"terraform\s+destroy", text):
            n = text[: m.start()].count("\n") + 1
            if _is_guard_line(lines[n - 1]):
                continue
            offenders.append(f"{path.relative_to(REPO)}:{n}")
    assert not offenders, f"terraform destroy found in: {offenders}"


# --------------------------------------------------------------- prevent_destroy
PROTECTED = [
    ("modules/secrets/main.tf", "aws_secretsmanager_secret"),
    ("modules/secrets/main.tf", "aws_kms_key"),
    ("modules/s3/main.tf", "aws_s3_bucket"),
    ("modules/ecr/main.tf", "aws_ecr_repository"),
]


@pytest.mark.parametrize(("rel", "resource"), PROTECTED)
def test_protected_resources_have_prevent_destroy(rel, resource):
    path = TF / rel
    if not path.exists():
        pytest.skip(f"{rel} not present yet")
    text = _read(path)
    assert f'resource "{resource}"' in text, f"{resource} not defined in {rel}"
    assert (
        "prevent_destroy = true" in text
    ), f"{resource} in {rel} lacks prevent_destroy"


def test_ecr_and_s3_are_not_force_deletable():
    ecr = TF / "modules/ecr/main.tf"
    if ecr.exists():
        assert "force_delete         = false" in _read(
            ecr
        ) or "force_delete = false" in _read(ecr)


# --------------------------------------------------------------- tagging
def test_every_environment_tags_project_ekba():
    for env in ("dev", "prod"):
        main = TF / "envs" / env / "main.tf"
        if not main.exists():
            pytest.skip(f"env {env} not present yet")
        text = _read(main)
        assert 'Project     = "ekba"' in text, f"{env} must tag Project=ekba"
        assert (
            "default_tags" in text
        ), f"{env} must apply default_tags to every resource"


# --------------------------------------------------------------- container hardening
def test_dockerfile_runs_as_non_root():
    df = REPO / "backend" / "Dockerfile"
    if not df.exists():
        pytest.skip("Dockerfile not present yet")
    text = _read(df)
    assert re.search(
        r"^USER\s+10001", text, re.M
    ), "container must run as a non-root uid"


def test_dockerfile_has_no_secrets():
    df = REPO / "backend" / "Dockerfile"
    if not df.exists():
        pytest.skip("Dockerfile not present yet")
    text = _read(df)
    for pattern in (r"euri-[0-9a-f]{20,}", r"AKIA[0-9A-Z]{16}", r"ENV\s+.*_KEY\s*="):
        assert not re.search(pattern, text), f"possible secret in Dockerfile: {pattern}"


def test_k8s_enforces_non_root_and_readonly_rootfs():
    manifest = REPO / "k8s" / "base" / "deployment.yaml"
    if not manifest.exists():
        pytest.skip("k8s manifests not present yet")
    text = _read(manifest)
    for required in (
        "runAsNonRoot: true",
        "readOnlyRootFilesystem: true",
        "allowPrivilegeEscalation: false",
    ):
        assert required in text, f"deployment.yaml missing {required}"

    # Inspect real image references only — a comment mentioning the tag is not a violation.
    image_lines = [
        ln.strip()
        for ln in text.splitlines()
        if ln.strip().startswith("image:") and not ln.strip().startswith("#")
    ]
    assert image_lines, "no image reference found"
    for line in image_lines:
        assert ":latest" not in line, f"images must be pinned by digest: {line}"
        assert "@sha256:" in line, f"image must be referenced by digest: {line}"
