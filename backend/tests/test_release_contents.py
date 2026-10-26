"""Keep credentials and obsolete assets out of the deliverable."""
from pathlib import Path
import shutil
import zipfile
from uuid import uuid4
import pytest
from scripts.build_release import ROOT, build, release_files


@pytest.fixture
def source_copy(tmp_path):
    target = tmp_path / "backend"
    shutil.copytree(ROOT, target, ignore=shutil.ignore_patterns(
        ".env", ".venv", "logs", "__pycache__", ".ruff_cache", ".pytest_cache"))
    return target


def test_archive_contains_current_sources_and_no_secret_file(source_copy, tmp_path):
    credential = uuid4().hex + uuid4().hex
    (source_copy / ".env").write_text("PANDASCORE_TOKEN=" + credential, encoding="utf-8")
    archive = tmp_path / "release.zip"
    build(archive, root=source_copy)
    with zipfile.ZipFile(archive) as z:
        assert "runtime/app/db/schema.py" in z.namelist()
        assert "runtime/.env" not in z.namelist()
        assert z.testzip() is None
        assert all(credential.encode() not in z.read(name) for name in z.namelist())


@pytest.mark.parametrize("relative", ["docs/openapi-v0.9.0.json", "app/db/schema_v6.py",
                                      "docs/backup.txt", "backups/old.py"])
def test_historical_or_unapproved_files_block_packaging(source_copy, relative):
    p = source_copy / Path(relative)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("obsolete", encoding="utf-8")
    with pytest.raises(ValueError):
        release_files(source_copy)


def test_runtime_excludes_maintenance_and_repeat_build_has_valid_manifest(source_copy, tmp_path):
    import hashlib
    import json
    archive = tmp_path / "release.zip"
    for _ in range(2):
        build(archive, root=source_copy)
        with zipfile.ZipFile(archive) as z:
            names = z.namelist()
            assert "maintenance/backend/app/db/schema.sql" in names
            assert "maintenance/backend/app/db/ddl.py" in names
            assert not any(n.startswith("runtime/") and (
                n.endswith(".sql") or n.endswith("/ddl.py") or n.endswith("/seed.py")
                or "/docs/" in n or "/tests/" in n or "/migrations/" in n) for n in names)
            rows = json.loads(z.read("MANIFEST.json"))
            assert {r["file"] for r in rows} == set(names) - {"MANIFEST.json"}
            for row in rows:
                body = z.read(row["file"])
                assert row["bytes"] == len(body)
                assert row["sha256"] == hashlib.sha256(body).hexdigest()
