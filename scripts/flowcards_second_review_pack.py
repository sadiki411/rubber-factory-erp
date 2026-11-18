"""Allowlisted second-review artifacts; no database, credentials or deployment.

Prepare evidence with --prepare, commit the report/evidence, then package with
--runtime-commit SHA. Reads the synthetic SQLite file using mode=ro only.
"""
import argparse
import hashlib
import json
import sqlite3
import subprocess
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "06a31e4e1394b4acd889a1ba5a03885d26e9bddf"
DOC = "docs/flowcards-20261118/second-review"
PDFS = [f"output/pdf/工序流转卡_二轮_{tag}.pdf" for tag in ("常规O型圈_仅线径上限", "客户例外_线径加内径")]
LOGS = {
    "backend-388.log": "second-review-backend-delivery.log",
    "flowcards-63.log": "second-review-flow-delivery.log",
    "frontend-28.log": "second-review-frontend-final2.log",
    "measurement-ui-3.log": "second-review-measurement-final.log",
    "build.log": "second-review-build-final2.log",
}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def prepare():
    target = ROOT / DOC
    target.mkdir(parents=True, exist_ok=True)
    for name, log in LOGS.items():
        raw = (ROOT / "tmp" / log).read_bytes()
        text = raw.decode("utf-8-sig" if not raw.startswith(b"\xff\xfe") else "utf-16", errors="strict")
        # Management-command tests may print their synthetic fixture password.
        lines = [line for line in text.splitlines() if "账号密码" not in line]
        (target / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
    sample = json.loads((ROOT / ".codex_tmp/flow-second-review.json").read_text(encoding="utf-8"))
    sample.pop("database", None)
    for row in sample["samples"]:
        row["pdf"] = next(p for p in PDFS if Path(p).name == Path(row["pdf"]).name)
        assert sha((ROOT / row["pdf"]).read_bytes()) == row["sha256"]
    (target / "samples.json").write_text(json.dumps(sample, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tables = ["flowcards_internalcard", "flowcards_cardgroup", "flowcards_standardrevision", "flowcards_printjob", "flowcards_cardevent", "flowcards_templatepublication", "quality_qualityshipmentbatch", "production_productionrun", "production_productiondailylog", "inventory_inventorycontainer", "inventory_inventorytransaction"]
    db = ROOT / ".codex_tmp/flow-second-review.sqlite3"
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
        counts = {table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}
        events = []
        for row in conn.execute("SELECT id, kind, employee_name, business_at, replaces_id, \"values\" FROM flowcards_cardevent ORDER BY created_at, id"):
            values = json.loads(row[5])
            events.append({"id": str(uuid.UUID(row[0])), "kind": row[1], "employee_name": row[2], "business_at_utc": row[3], "replaces_id": str(uuid.UUID(row[4])) if row[4] else None, "state": values.get("state"), "actual": values.get("actual"), "judgment": values.get("judgment"), "message": values.get("measurement_message"), "bake_event_id": values.get("bake_event_id")})
    assert [counts[k] for k in tables[:6]] == [2, 2, 2, 2, 5, 0]
    assert all(counts[k] == 0 for k in tables[6:])
    (target / "synthetic-counts.json").write_text(json.dumps({"synthetic_only": True, "counts": counts, "events": events}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"prepared": DOC, "counts": counts}, ensure_ascii=False))


def package(runtime):
    head = git("rev-parse", "HEAD").decode().strip()
    subprocess.run(["git", "merge-base", "--is-ancestor", runtime, head], cwd=ROOT, check=True)
    subprocess.run(["git", "diff", "--exit-code", runtime, head, "--", "backend", "frontend", "scripts"], cwd=ROOT, check=True)
    assert not git("diff", "HEAD", "--name-only").strip(), "Commit tracked changes first"
    assert not git("diff", "--cached", "--name-only").strip(), "Commit staged changes first"
    payloads = {}
    for name in filter(None, git("diff", "--name-only", "-z", BASE, head).decode().split("\0")):
        path = Path(name)
        assert not path.is_absolute() and ".." not in path.parts
        assert not path.name.startswith(".env") and path.suffix.lower() not in {".sqlite3", ".db", ".pem", ".key", ".keystore", ".xlsx", ".xls"}
        payloads[f"source/{name}"] = git("show", f"{head}:{name}")
    for name in PDFS:
        payloads[name] = (ROOT / name).read_bytes()
    payloads["changes.patch"] = git("diff", "--binary", BASE, head)
    payloads["README.md"] = f"""# 工序流转卡二轮补修审阅包

未推送、未部署、未发布模板、未迁移正式库；不是现场验收全部通过。
运行代码：{runtime}；证据提交：{head}；增量基线：{BASE}。
先读 source/docs/flowcards-20261118/二轮补修交付与待审阅.md。
两张合成PDF在 output/pdf；截图、日志和样本映射在 source/{DOC}。
参数仅隔离测试，不是任何客户公差。没有正式库、环境密钥、账号凭据或整库导出。
changes.patch为原分支增量，需要接收方已有基线。未完成项见报告，不代替Android实扫、实印或纸材安全许可。
manifest.json列SHA256，排除自身以避免自引用。
""".encode("utf-8")
    manifest = {"baseline": BASE, "runtime_commit": runtime, "evidence_commit": head, "synthetic_only": True, "deployed": False, "pushed": False, "files": [{"path": name, "bytes": len(raw), "sha256": sha(raw)} for name, raw in sorted(payloads.items())]}
    payloads["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    target = ROOT / "output/review/ERP_工序流转卡_二轮补修审阅包_20261118.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(payloads.items()):
            archive.writestr(name, raw)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        for row in manifest["files"]:
            assert sha(archive.read(row["path"])) == row["sha256"]
    print(json.dumps({"zip": str(target), "bytes": target.stat().st_size, "sha256": sha(target.read_bytes()), "files": len(payloads), "runtime_commit": runtime, "evidence_commit": head}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--runtime-commit")
    args = parser.parse_args()
    if args.prepare:
        prepare()
    elif args.runtime_commit:
        package(args.runtime_commit)
    else:
        parser.error("Use --prepare or --runtime-commit SHA")
