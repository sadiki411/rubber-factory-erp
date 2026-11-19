"""Extract original browser trace filmstrip; keep recorded timing, no fake UI.

This is a browser filmstrip recording (not Android / native screen recording).
Trace also contains unrelated Chromium internals; only Screenshot events are
included in the review artifact. All source pages use new synthetic data.
"""
import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
run = sys.argv[2] if len(sys.argv) > 2 else ''
if run not in ('', 'verified', 'recording'):
    raise SystemExit('Unsupported isolated recording run')
OUT = ROOT / 'output/manual-reship-20261119' / run
sys.path.insert(0, str(ROOT / '.codex_tmp/recording-runtime'))
import imageio_ffmpeg

tag = sys.argv[1]
source = OUT / f'{tag}-browser-trace.json'
frames_dir = OUT / f'{tag}-recorded-frames'
frames_dir.mkdir(exist_ok=True)
frames = []
for line in source.open(encoding='utf-8'):
    if '"Screenshot"' not in line or '"snapshot"' not in line:
        continue
    raw = line.strip().rstrip(',')
    if raw.startswith('{"traceEvents":['):
        raw = raw[len('{"traceEvents":['):]
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        continue
    if event.get('name') != 'Screenshot':
        continue
    image = event.get('args', {}).get('snapshot')
    if not image:
        continue
    frame = frames_dir / f'{len(frames):06}.jpg'
    frame.write_bytes(base64.b64decode(image))
    frames.append((event['ts'], frame))
assert frames, 'No usable screenshot events found'
frames.sort(key=lambda item: item[0])
concat = []
for i, (ts, frame) in enumerate(frames):
    concat += [f"file '{frame.as_posix()}'", f"duration {max(.001,(frames[i+1][0]-ts)/1e6) if i+1<len(frames) else 2:.6f}"]
concat += [f"file '{frames[-1][1].as_posix()}'"]
manifest = OUT / f'{tag}-frames.ffconcat'
manifest.write_text('\n'.join(concat), encoding='utf-8')
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'concat', '-safe', '0', '-i', str(manifest),
    '-vf', 'scale=1280:-2,pad=ceil(iw/2)*2:ceil(ih/2)*2', '-r', '10', '-c:v', 'libx264', '-preset', 'fast',
    '-crf', '25', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(OUT / f'{tag}.mp4')], check=True,
    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
info = {'recording_type': 'Chromium tracing filmstrip, original timestamps retained',
        'not_android_or_native_screen': True, 'frame_count': len(frames), 'duration_s': (frames[-1][0]-frames[0][0])/1e6,
        'first_ts': frames[0][0], 'last_ts': frames[-1][0], 'source_bytes': source.stat().st_size}
(OUT / f'{tag}-recording-info.json').write_text(json.dumps(info, indent=2), encoding='utf-8')
print(json.dumps(info))
