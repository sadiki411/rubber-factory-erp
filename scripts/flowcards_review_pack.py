"""Build an allowlisted, local review ZIP; never includes databases or .env files.

Run after committing review evidence. Does not push, deploy or modify Git refs.
The incremental bundle requires the baseline commit already in the recipient repo.
"""
import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "baced3ad26519b934a8c7f3b9d0830d45540df0d"
RUNTIME = "a6ec357387fab213f1ae4117157da736985cfb4b"
DOCS = "docs/flowcards-20261118"
PDFS = [f"output/pdf/工序流转卡_RV_{name}.pdf" for name in [
    "完整要求_需整模", "完整要求_不需整模", "缺要求_待确认", "长字段_续页"]]


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


head = git("rev-parse", "HEAD").decode().strip()
subprocess.run(["git", "merge-base", "--is-ancestor", RUNTIME, head], cwd=ROOT, check=True)
subprocess.run(["git", "diff", "--exit-code", RUNTIME, head, "--", "backend", "frontend", "deploy", "compose.yaml", ".github"], cwd=ROOT, check=True)
subprocess.run(["git", "diff", "--exit-code", "HEAD", "--", "."], cwd=ROOT, check=True)
assert not git("diff", "--cached", "--name-only").strip(), "Commit staged changes first"

target = ROOT / "output/review/ERP_工序流转卡_补修审阅包_20261118.zip"
if target.exists():
    raise SystemExit("Review ZIP already exists; preserve it and choose a new release name")
target.parent.mkdir(parents=True, exist_ok=True)
temp = ROOT / ".codex_tmp"
temp.mkdir(exist_ok=True)
bundle = temp / f"flowcards-review-{head[:12]}.bundle"
subprocess.run(["git", "bundle", "create", str(bundle), f"{BASE}..HEAD"], cwd=ROOT, check=True)
subprocess.run(["git", "bundle", "verify", str(bundle)], cwd=ROOT, check=True)

paths = git("diff", "--name-only", "-z", BASE, head).decode().split("\0")
payloads = {}
for name in filter(None, paths):
    path = Path(name)
    assert not path.is_absolute() and ".." not in path.parts
    assert not path.name.startswith(".env") and path.suffix.lower() not in {".sqlite3", ".db", ".pem", ".key", ".keystore", ".xlsx", ".xls"}
    payloads[f"source/{name}"] = git("show", f"{head}:{name}")
for name in PDFS:
    payloads[name] = (ROOT / name).read_bytes()
payloads["changes.patch"] = git("diff", "--binary", BASE, head)
payloads["flowcards-review.bundle"] = bundle.read_bytes()
payloads["README.md"] = f"""# 工序流转卡补修审阅包

仅审阅，未推送、未部署、未迁移正式库。运行代码 {RUNTIME}；证据提交 {head}。
先读 source/{DOCS}/补修交付与复核.md，再看 evidence-RV.json、现场验收补充操作单.md 和 rv-evidence。
PDF在 output/pdf；全部隔离合成草稿，不是客户标准或实印通过证明。
source/包含基线后的变更文件，changes.patch为可读/可应用增量；bundle要求接收仓库已有基线 {BASE}。
本包无正式库、环境密钥或正式账号凭据；测试脚本中的合成本机固定账号仅属一次性fixture，不能用于正式站。
manifest.json列出SHA256（不自哈希）。未验项详见报告，不以软件测试替代真机、实印、材料适用性或正式全账验收。
""".encode("utf-8")
manifest = {
    "runtime_commit": RUNTIME, "evidence_commit": head, "baseline": BASE,
    "synthetic_only": True, "deployed": False, "pushed": False,
    "files": [{"path": name, "bytes": len(raw), "sha256": sha(raw)} for name, raw in sorted(payloads.items())],
    "manifest_self_hash": "excluded to avoid self-reference",
}
payloads["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
with zipfile.ZipFile(target, "x", compression=zipfile.ZIP_DEFLATED) as archive:
    for name, raw in sorted(payloads.items()):
        archive.writestr(name, raw)
with zipfile.ZipFile(target) as archive:
    assert archive.testzip() is None
    for item in manifest["files"]:
        assert sha(archive.read(item["path"])) == item["sha256"]
print(json.dumps({"zip": str(target), "bytes": target.stat().st_size, "sha256": sha(target.read_bytes()),
    "runtime_commit": RUNTIME, "evidence_commit": head, "files": len(payloads)}, ensure_ascii=False, indent=2))
