"""Allowlisted synthetic review pack. No push, deploy, DB or credentials.

Run only after the source/evidence commits; generated screenshots and PDFs stay
outside Git and are integrity-checked in this local ZIP.
"""
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "e6a469acaab33dde3665ecccbbba397f2513524a"
RUNTIME = "7c4b1e33029bb4c63cf4d9516ff47f3945b6c281"
TARGET = ROOT / "output/review/ERP_工序流转卡_三项补修与隔离验收包_20261119.zip"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


head = git("rev-parse", "HEAD").decode().strip()
subprocess.run(["git", "merge-base", "--is-ancestor", RUNTIME, head], cwd=ROOT, check=True)
subprocess.run(["git", "diff", "--exit-code", RUNTIME, head, "--", "backend", "frontend", "deploy", "compose.yaml", ".github"], cwd=ROOT, check=True)
assert not git("diff", "HEAD", "--name-only").strip(), "Tracked source must be committed"
assert not git("diff", "--cached", "--name-only").strip()
assert not TARGET.exists(), "Do not overwrite a delivered review package"
payloads = {}
changed = git("diff", "--name-only", "-z", BASE, head).decode().split("\0")
for name in filter(None, changed):
    path = Path(name)
    assert name.startswith(("backend/flowcards/", "frontend/src/", "scripts/", "docs/flowcards-20261119/")), name
    assert not path.is_absolute() and ".." not in path.parts
    assert path.suffix in (".py", ".tsx", ".ts", ".md", ".html", ".json")
    payloads["source/" + name] = git("show", f"{head}:{name}")
payloads["changes.patch"] = git("diff", "--binary", BASE, head)

evidence = ROOT / "output/flow-pilot-20261119"
for path in sorted(evidence.rglob("*")):
    if not path.is_file():
        continue
    assert path.suffix.lower() in (".jpg", ".json", ".html", ".txt", ".csv"), path
    assert not any(word in path.name.lower() for word in ("credential", "password", "secret", ".env"))
    payloads[str(path.relative_to(ROOT)).replace("\\", "/")] = path.read_bytes()
for path in sorted((ROOT / "output/pdf/flow-pilot-20261119").iterdir()):
    assert path.is_file() and path.suffix in (".pdf", ".png", ".json"), path
    payloads[str(path.relative_to(ROOT)).replace("\\", "/")] = path.read_bytes()
payloads["tools/flowcards_readonly_benchmark.js"] = git("show", f"{head}:scripts/flowcards_readonly_benchmark.js")
payloads["README.md"] = f"""# 三项补修与隔离验收包

仅合成隔离审阅；未推送、未部署、未写正式账、未发布正式模板。
运行修复提交：{RUNTIME}；证据提交：{head}；基线：{BASE}。
先读 source/docs/flowcards-20261119/三项补修与隔离验收报告.md，
再读同目录实机待验与现场操作单.md。

三项P0软件补修完成；SCAN净交付100→0→100；MANUAL仅100→0，缺客户卡关联，再发未通过。
第三节整体验收未通过，不构成试点批准。
5份PDF/6页均为草稿合成预览，不是客户公差，也未签发真实身份。
页面截图证明对应合成操作，390px截图不是安卓实机。
recording/回放.html是1171张变化帧回放，不是连续录像；先完整解压再打开。
field-metrics.csv全空白未测，工具不会把空白算为0；不要把合成统计自检当成实测性能。
原始数据库、凭据、环境变量不在包内。source/仅为基线后的变更文件，changes.patch需原基线仓库。
最终是否补修第二入口、是否试点由用户决定，不因本包自动修改其他模块。
""".encode("utf-8")

# The readme/documentation may mention password handling, but no literal value
# from the old test management command should escape into logs or patches.
for name, raw in payloads.items():
    if name.endswith((".txt", ".patch", ".json", ".md", ".html", ".py", ".ts", ".tsx", ".js", ".csv")):
        assert (b"temporary" + b"-password") not in raw, name
        assert not re.search(rb"(?:BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY|[Aa]uthorization: Bearer )", raw), name
manifest = {"runtime_commit": RUNTIME, "evidence_commit": head, "baseline": BASE,
    "synthetic_only": True, "pushed": False, "deployed": False, "formal_template_published": False,
    "section3_passed": False, "manual_loop_passed": False, "continuous_recording": False, "android_tested": False,
    "files": [{"path": name, "bytes": len(raw), "sha256": sha(raw)} for name, raw in sorted(payloads.items())],
    "manifest_self_hash": "excluded to avoid self-reference"}
payloads["manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
TARGET.parent.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(TARGET, "x", compression=zipfile.ZIP_DEFLATED) as archive:
    for name, raw in sorted(payloads.items()):
        archive.writestr(name, raw)
with zipfile.ZipFile(TARGET) as archive:
    assert archive.testzip() is None
    for item in manifest["files"]:
        raw = archive.read(item["path"])
        assert len(raw) == item["bytes"] and sha(raw) == item["sha256"]
result = {"zip": str(TARGET), "bytes": TARGET.stat().st_size, "sha256": sha(TARGET.read_bytes()),
          "runtime_commit": RUNTIME, "evidence_commit": head, "files": len(payloads)}
TARGET.with_suffix(".sha256.txt").write_text(f"{result['sha256']}  {TARGET.name}\n", encoding="utf-8")
print(json.dumps(result, ensure_ascii=False, indent=2))
