"""Finish local synthetic evidence. Never connect to an external/production DB.

PDF inspection uses the bundled Python with pypdf. An old-release subprocess
uses the project Python and opens the restored synthetic clone read-only.
The frame player is explicitly NOT a continuous recording.
"""
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "e6a469acaab33dde3665ecccbbba397f2513524a"
OUT = ROOT / "output/flow-pilot-20261119"
DOCS = ROOT / "docs/flowcards-20261119"
PROJECT_PYTHON = "D:/develop/venvs/erp/Scripts/python.exe"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def old_release_probe(archive_root, database, private_root):
    # No settings/module from the current checkout may leak into this process.
    archive_root, database, private_root = map(Path, (archive_root, database, private_root))
    assert archive_root.is_relative_to(ROOT / ".codex_tmp")
    assert database == ROOT / ".codex_tmp/flow-pilot-20261119-verify.restored.sqlite3"
    assert database.is_file()
    before = digest(database)
    os.environ.update(SQLITE_PATH=str(database), DJANGO_SETTINGS_MODULE="erp.settings",
                      FLOW_CARDS_WRITE_ENABLED="0", FLOW_CARDS_PRIVATE_ROOT=str(private_root))
    sys.path.insert(0, str(archive_root / "backend"))
    import django
    django.setup()
    from django.conf import settings
    settings.DATABASES["default"]["NAME"] = database.as_uri() + "?mode=ro"
    settings.DATABASES["default"]["OPTIONS"]["uri"] = True
    from django.db import connection
    from django.contrib.auth import get_user_model
    from rest_framework.test import APIClient
    from flowcards.models import InternalCard, PrintJob, TemplatePublication
    from quality.models import QualityOrder
    assert QualityOrder.objects.exclude(order_no__startswith="SYNTH-").count() == 0
    assert TemplatePublication.objects.count() == 0
    client = APIClient(HTTP_HOST="127.0.0.1")
    client.force_authenticate(get_user_model().objects.get(username="pilot-worker"))
    card = InternalCard.objects.first()
    job = PrintJob.objects.get(request_key="SYNTH-OLD-FROZEN")
    queries = []

    def only_read(execute, sql, params, many, context):
        assert sql.lstrip().upper().startswith(("SELECT", "PRAGMA", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE")), sql
        queries.append(sql.split()[0].upper())
        return execute(sql, params, many, context)

    with connection.execute_wrapper(only_read):
        detail = client.get(f"/api/flow-cards/cards/{card.pk}/")
        events = client.get(f"/api/flow-cards/cards/{card.pk}/events/")
        pdf = client.get(f"/api/flow-cards/print-jobs/{job.pk}/pdf/")
        raw = b"".join(pdf.streaming_content)
        new_route = client.post(f"/api/flow-cards/cards/{card.pk}/record/", {}, format="json")
        blocked = client.post("/api/flow-cards/events/", {}, format="json")
    assert detail.status_code == events.status_code == pdf.status_code == 200
    assert new_route.status_code == 404  # The scoped new route is absent on the old release.
    assert blocked.status_code == 403
    assert hashlib.sha256(raw).hexdigest() == job.pdf_sha256
    connection.close()
    assert digest(database) == before
    print(json.dumps({"old_release": BASE, "read_only_database": True,
        "GET_card": detail.status_code, "GET_events": events.status_code,
        "GET_frozen_v3_pdf": pdf.status_code, "frozen_pdf_sha256": hashlib.sha256(raw).hexdigest(),
        "new_route_write_status_on_old_release": new_route.status_code,
        "old_generic_write_status_with_switch_off": blocked.status_code, "database_unchanged": True,
        "observed_queries": len(queries), "scope": "local old-code GET compatibility only, not server rollback"}))


def finish():
    from pypdf import PdfReader
    DOCS.mkdir(parents=True, exist_ok=True)
    pdfs = ROOT / "output/pdf/flow-pilot-20261119"
    rows = []
    expected_pages = [1, 1, 1, 2, 1]
    source = json.loads((pdfs / "mixed-pdf-evidence.json").read_text(encoding="utf-8"))
    for item, pages in zip(source["samples"], expected_pages, strict=True):
        path = ROOT / item["file"]
        reader = PdfReader(path)
        assert len(reader.pages) == pages
        assert digest(path) == item["sha256"]
        text = "\n".join(p.extract_text() for p in reader.pages)
        sizes = [[round(float(p.mediabox.width) * 25.4 / 72, 3),
                  round(float(p.mediabox.height) * 25.4 / 72, 3)] for p in reader.pages]
        assert all(abs(w - 190) < 0.01 and abs(h - 130) < 0.01 for w, h in sizes)
        counts = {name: text.count(name) for name in item["expected_dimensions"]}
        assert all(n == 1 for n in counts.values())
        assert not any(name in text for name in ("内径不要求测", "高度不测", "线径不测"))
        if not counts:
            assert "关键尺寸" not in text
        rows.append({"file": path.name, "pages": pages, "sizes_mm": sizes,
                     "dimension_text_counts": counts, "no_none_fields": True, "sha256": digest(path)})
    write_json(OUT / "pdf-structure-verification.json", {"passed": True, "samples": rows,
        "note": "Text/geometry assertions supplement six rendered page PNG inspections; not actual paper printing."})

    # Later duplicate index entries supersede earlier entries because files were overwritten.
    recording = OUT / "recording"
    index = json.loads((recording / "index.json").read_text(encoding="utf-8"))
    unique = {r["name"]: r for r in index["index"] if (recording / r["name"]).is_file()}
    frames = sorted(unique.values(), key=lambda r: (r["timestamp"], r["name"]))
    write_json(recording / "playback-index.json", {"kind": "screen-change-frames-not-continuous-video",
        "original_index_rows": len(index["index"]), "available_unique_frames": len(frames), "frames": frames,
        "limitations": ["Not a continuous screen recording", "Duplicate early names were overwritten",
                        "Inter-tool waits and every keystroke cannot be reconstructed", "Login frame(s) excluded below if needed"]})
    template = (DOCS / "屏幕变化帧回放_模板.html").read_text(encoding="utf-8")
    (recording / "回放.html").write_text(template.replace("__FRAMES__", json.dumps(frames)), encoding="utf-8")

    # Keep only relevant, sanitised logs. Do not distribute account/password output.
    log_dir = OUT / "logs"
    log_dir.mkdir(exist_ok=True)
    for name in ("third-backend-final.log", "third-p0-chain.log", "third-frontend.log", "third-lint.log", "third-build.log"):
        raw = (ROOT / "tmp" / name).read_bytes()
        encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8"
        text = raw.decode(encoding, errors="replace")
        text = re.sub(r"\x1b\[[0-9;]*m", "", text)
        lines = [line for line in text.splitlines() if not re.search(r"password|密码|erp-shared|gpt-audit", line, re.I)]
        (log_dir / name.replace(".log", ".txt")).write_text("\n".join(lines) + "\n", encoding="utf-8")

    old_root = ROOT / ".codex_tmp/flow-pilot-20261119-old-release"
    old_root.mkdir(exist_ok=True)
    archived = subprocess.check_output(["git", "archive", "--format=zip", BASE, "backend"], cwd=ROOT)
    with zipfile.ZipFile(io.BytesIO(archived)) as archive:
        for name in archive.namelist():
            assert not Path(name).is_absolute() and ".." not in Path(name).parts
        archive.extractall(old_root)
    restored = ROOT / ".codex_tmp/flow-pilot-20261119-verify.restored.sqlite3"
    private = ROOT / ".codex_tmp/flow-pilot-20261119-verify.restored.private"
    result = subprocess.run([PROJECT_PYTHON, str(Path(__file__).resolve()), "--old-release-probe",
        str(old_root), str(restored), str(private)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise RuntimeError(result.stderr)
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    write_json(OUT / "old-release-readonly-probe.json", payload)
    checks = []
    env = {**os.environ, "PYTHONUTF8": "1", "SQLITE_PATH": str(restored)}
    commands = [
        ([PROJECT_PYTHON, "backend/manage.py", "check"], ROOT),
        ([PROJECT_PYTHON, "backend/manage.py", "makemigrations", "--check", "--dry-run"], ROOT),
        (["D:/develop/node22/node.exe", "node_modules/typescript/bin/tsc", "-b"], ROOT / "frontend"),
        ([PROJECT_PYTHON, "scripts/test_flowcards_pilot_field_metrics.py", "-v"], ROOT),
        (["git", "diff", "--check"], ROOT),
    ]
    for command, cwd in commands:
        run = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8")
        checks.append({"command": command, "cwd": str(cwd.relative_to(ROOT)), "exit_code": run.returncode,
                       "stdout": run.stdout, "stderr": run.stderr})
        assert run.returncode == 0, checks[-1]
    write_json(OUT / "software-checks.json", {"passed": True, "checks": checks})
    metrics = subprocess.run([PROJECT_PYTHON, "scripts/flowcards_pilot_field_metrics.py", str(OUT / "field-metrics.csv")],
                             cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", check=True)
    write_json(OUT / "field-metrics-unmeasured.json", json.loads(metrics.stdout))
    quantity = json.loads((OUT / "quantity-permission-recovery.json").read_text(encoding="utf-8"))
    write_json(DOCS / "evidence-summary.json", {"synthetic_only": True, "base": BASE,
        "runtime_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip(),
        "formal_publications": quantity["formal_publications"],
        "net_totals": {s["tag"]: s["net_total"] for s in quantity["suites"]},
        "manual_loop_passed": False, "continuous_recording_passed": False,
        "android_tested": False, "production_tested": False, "deployed": False, "pushed": False,
        "evidence": [{"path": str(p.relative_to(ROOT)).replace("\\", "/"), "sha256": digest(p)}
                     for p in sorted(OUT.glob("*.json"))]})
    print(json.dumps({"pdf_checks": len(rows), "frames": len(frames), "local_old_release_get_passed": True}))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--old-release-probe":
        old_release_probe(*sys.argv[2:])
    else:
        finish()
