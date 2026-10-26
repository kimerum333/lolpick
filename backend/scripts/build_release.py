"""Build the current release only; historical or unknown assets stop the build."""
from pathlib import Path
import argparse
import re
import hashlib
import json
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DOCS = {
    "ANALYTICS.md", "API_SECURITY.html", "API_SECURITY.md", "AUTH_INTEGRATION.md",
    "DATA_MODEL.md", "PANDASCORE_INTEGRATION.md", "POINT_HISTORY.md",
    "USER_ACTIONS.md", "user-actions-upgrade.sql", "POINT_PREFLIGHT.sql", "SUPABASE_SETUP.md", "capacity.sql", "openapi.json",
    "runtime-grants.template.sql", "schema.sql",
    "concurrent-seasons-upgrade.sql", "CONCURRENT_SEASONS.md",
    "API_GUIDE.html", "API_TESTER.html", "FRONTEND_INTEGRATION.md", "frontend-api.ts",
}
TOP = {
    ".dockerignore", ".env.example", ".gitignore", "alembic.ini", "Dockerfile",
    "pyproject.toml", "README.md", "requirements-dev.txt", "requirements.txt", "verify.ps1",
}
IGNORED = {".git", ".venv", "venv", "logs", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache"}
DEPLOY = {
    "journald-lolpick.conf", "lolpick-api.service.example", "lolpick-pandascore.service.example",
    "lolpick-pandascore.timer.example", "lolpick-session-expiry.service.example",
    "lolpick-session-expiry.timer.example", "nginx.conf.example", "runtime-role-check.sql",
    "lolpick-reconcile.service.example", "lolpick-reconcile.timer.example",
}
MIGRATIONS = {"env.py", "script.py.mako", "versions/0008_native_session_lookup.py", "versions/0009_user_actions.py", "versions/0010_concurrent_seasons.py"}
SCRIPTS = {"build_release.py", "frontend-api.test.mjs", "verify_postgres.py", "build_api_docs.py", "verify_http_contract.py", "templates/frontend-client.ts.txt", "templates/api-guide.html", "templates/guide-font.woff2", "templates/OFL-font.txt"}


def release_files(root=ROOT):
    root = Path(root).resolve()
    files = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in IGNORED for part in rel.parts) or p.suffix in {".pyc", ".pyo"}:
            continue
        if p.is_symlink():
            raise ValueError(f"Symlinks are not release assets: {rel}")
        if not p.is_file():
            continue
        if p.name.startswith(".env") and p.name != ".env.example":
            continue
        if any(part.lower() in {"archive", "archives", "backup", "backups"} for part in rel.parts):
            raise ValueError(f"Backup directory inside release: {rel}")
        if re.search(r"(?:schema|ddl)_v\d|v\d+\.\d|\.bak$|\.old$|(?:^|[-_.])(?:old|backup|archive)(?:[-_.]|$)", p.name, re.I):
            raise ValueError(f"Historical file inside release: {rel}")
        sub = Path(*rel.parts[1:]).as_posix() if len(rel.parts) > 1 else ""
        folder = rel.parts[0]
        allowed = (
            (len(rel.parts) == 1 and p.name in TOP)
            or (folder == "app" and p.suffix in {".py", ".sql"})
            or (folder == "tests" and (
                (len(rel.parts) == 2 and p.suffix == ".py")
                or sub == "fixtures/api-examples.json"
            ))
            or (folder == "docs" and sub in DOCS)
            or (folder == "deploy" and sub in DEPLOY)
            or (folder == "migrations" and sub in MIGRATIONS)
            or (folder == "scripts" and sub in SCRIPTS)
        )
        if not allowed:
            raise ValueError(f"Unapproved release asset: {rel}")
        files.append(p)
    required = {"app/main.py", "app/db/schema.py", "app/db/domain.py", "app/db/schema.sql",
                "docs/API_SECURITY.html", "docs/schema.sql", "docs/openapi.json",
                "migrations/versions/0008_native_session_lookup.py"}
    missing = required - {p.relative_to(root).as_posix() for p in files}
    if missing:
        raise ValueError(f"Missing required release assets: {sorted(missing)}")
    return files


def release_payloads(root=ROOT):
    root = Path(root).resolve()
    payloads = {}
    for p in release_files(root):
        rel = p.relative_to(root).as_posix()
        payloads["maintenance/backend/" + rel] = p.read_bytes()
        operational = (
            (rel.startswith("app/") and p.suffix == ".py" and rel != "app/db/ddl.py")
            or rel in {"Dockerfile", ".dockerignore", ".env.example", "requirements.txt"}
            or (rel.startswith("deploy/") and p.suffix != ".sql")
        )
        if operational:
            body = p.read_bytes()
            if rel == "requirements.txt":
                body = "\n".join(line for line in body.decode().splitlines()
                    if not line.startswith("alembic")).encode() + b"\n"
            payloads["runtime/" + rel] = body
    payloads["README.md"] = (
        "# LoLPICK backend v0.14.1\n\n"
        "runtime/: 실행 코드·실행 의존성·배포 예시. 기존 운영 폴더와 DB에 자동 적용하지 않습니다.\n"
        "maintenance/backend/: 전체 소스·테스트·마이그레이션·문서.\n"
        "프론트 연결 안내: maintenance/backend/docs/API_GUIDE.html 및 frontend-api.ts.\n"
        "DB head: 0010_concurrent_seasons. v0.13에서 새 DB 마이그레이션은 없습니다.\n"
        "기존 DB에 schema.sql을 재실행하지 않습니다. 비밀값·운영 로그·가상환경은 포함하지 않습니다.\n"
        "검증 증거가 있으면 maintenance/verification/ 아래에 포함됩니다.\n"
    ).encode()
    return payloads


def build(destination, *, root=ROOT, verification=None):
    destination = Path(destination).resolve()
    root = Path(root).resolve()
    if destination.is_relative_to(root):
        raise ValueError("Save release output outside the backend source directory.")
    payloads = release_payloads(root)
    if verification is not None:
        verification = Path(verification).resolve()
        for name in ("results.json", "unit.xml", "security-results.json", "api-test-results.json", "http-results.json", "client-results.json", "client.xml", "api-examples.json", "ruff.log", "final-docs-packaging.log"):
            path = verification / name
            if path.is_file():
                payloads["maintenance/verification/" + name] = path.read_bytes()
        for folder in ("security-fix", "virtual-api-check"):
            for path in sorted((verification / "harness" / folder).glob("*.py")):
                payloads[f"maintenance/verification/harness/{folder}/{path.name}"] = path.read_bytes()
    # Generate from current bytes, never an earlier output directory or this file.
    manifest = [{"file": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
                for name, body in sorted(payloads.items())]
    payloads["MANIFEST.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".building")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, body in sorted(payloads.items()):
                archive.writestr(name, body)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("ZIP integrity check failed.")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return len(payloads)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verification", type=Path)
    args = parser.parse_args()
    count = build(args.output, verification=args.verification)
    print(f"Current release: {count} files")


if __name__ == "__main__":
    main()
