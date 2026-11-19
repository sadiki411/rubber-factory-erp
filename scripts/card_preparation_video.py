"""Convert original CDP screencast JPEG frames, preserving recorded timing."""
import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "card-preparation-20261119"
sys.path.insert(0, "D:/workspace/erp/.codex_tmp/recording-runtime")
import imageio_ffmpeg

for tag in sys.argv[1:]:
    if tag not in ("A-v5-recording", "C-screen-recording", "C-completion-recording"):
        raise SystemExit("Unknown review recording")
    source = OUT / f"{tag}.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    assert not payload.get("errors"), "Recording contains failed UI actions"
    frames = sorted(payload["frames"], key=lambda f: f["metadata"]["timestamp"])
    assert frames
    folder = ROOT / ".codex_tmp" / tag
    folder.mkdir(exist_ok=True)
    concat = []
    for index, frame in enumerate(frames):
        file = folder / f"{index:05}.jpg"
        file.write_bytes(base64.b64decode(frame["data"]))
        duration = max(.01, frames[index + 1]["metadata"]["timestamp"] - frame["metadata"]["timestamp"]) if index + 1 < len(frames) else 1
        concat.extend([f"file '{file.as_posix()}'", f"duration {duration:.6f}"])
    concat.append(f"file '{file.as_posix()}'")
    manifest = folder / "frames.ffconcat"
    manifest.write_text("\n".join(concat), encoding="utf-8")
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", str(manifest),
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2", "-r", "20", "-c:v", "libx264", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(OUT / f"{tag}.mp4")], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    info = {"type": "original CDP screencast, not Android hardware", "frames": len(frames), "duration_seconds": frames[-1]["metadata"]["timestamp"] - frames[0]["metadata"]["timestamp"], "source": source.name}
    (OUT / f"{tag}-info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(info))
