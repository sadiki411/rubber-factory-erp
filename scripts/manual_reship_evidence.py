"""Assemble an allowlisted, credential-free synthetic review package.

Reads saved UI traces/snapshots only; never connects to an ERP database/server.
The videos are explicitly a compilation of separate browser filmstrip clips.
"""
import hashlib
import html
import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = '86b2698092eaa7c83904eb9973277d3ead14bee3'
SOURCE = ROOT / 'output/manual-reship-20261119'
DEST = ROOT / 'output/review/ERP_无扫码整批退货原单再发_隔离验收包_20261119'
DEST.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / '.codex_tmp/recording-runtime'))
import imageio_ffmpeg

report = ROOT / 'docs/flowcards-20261119/无扫码整批退货原单再发补修验收.md'
shutil.copy2(report, DEST / '验收报告.md')
results = []
video_index = []
for tag, run, success in (
    ('SCAN', 'recording', 'scan-15-original-reship-success.jpg'),
    ('MANUAL', 'verified', 'manual-reship-success.jpg'),
):
    src = SOURCE / run
    target = DEST / tag.lower()
    target.mkdir(exist_ok=True)
    stages = {}
    for stage in ('initial', 'first-100', 'returned-0', 'reshipped-100'):
        path = src / f'{tag.lower()}-{stage}.json'
        stages[stage] = json.loads(path.read_text(encoding='utf-8'))
        shutil.copy2(path, target / path.name)
    assert [stages[s]['net_total'] for s in stages] == [0, 100, 0, 100]
    assert not stages['initial']['shipment_allocations'] and not stages['initial']['return_cases']
    assert len(stages['first-100']['shipment_allocations']) == 2
    assert not stages['first-100']['return_allocations']
    returned, final = stages['returned-0'], stages['reshipped-100']
    assert returned['return_allocations'] == final['return_allocations']
    assert len(final['return_cases']) == 1
    case = final['return_cases'][0]
    assert case['status'] == 'RESHIPPED'
    assert case['shipment_batch_id'] != case['reshipment_batch_id']
    assert len({a['shipment_no'] for a in final['shipment_allocations']}) == 2
    assert sorted(final['net_by_order'].values()) == [40, 60]
    assert sum(r['piece_quantity'] for r in final['return_allocations']) == 100
    assert returned['return_cases'][0]['reshipment_batch_id'] is None
    if tag == 'MANUAL':
        assert returned['return_cases'][0]['process_card_id'] is None
        assert returned['return_cases'][0]['return_round'] is None
    seed = json.loads((ROOT / f'.codex_tmp/manual-reship-20261119-{run}.seed.json').read_text(encoding='utf-8'))
    assert seed['ordinary_employee'] == {'is_staff': False, 'is_superuser': False}
    assert seed['initial_shipments'] == seed['initial_returns'] == 0
    (target / 'synthetic-seed-no-credentials.json').write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding='utf-8')
    result = {'tag': tag, 'isolated_run': run, 'net_stages': [100, 0, 100],
        'net_by_order': final['net_by_order'], 'case': case,
        'return_allocations_unchanged': True, 'return_allocation_sha256': final['return_allocation_sha256'],
        'return_count': 1, 'confirmed_outbound_occurrences': 2,
        'ordinary_employee': seed['ordinary_employee']}
    results.append(result)
    for path in (src / success, src / f'{tag.lower()}-returned-0.jpg'):
        shutil.copy2(path, target / path.name)
    if tag == 'SCAN':
        stills = ('scan-05-first-balances.jpg', 'scan-17-final-order-balances.jpg', 'scan-09-preview.jpg')
    else:
        stills = ('manual-first-100.jpg', 'manual-reshipped-100.jpg', 'manual-05-return-source.jpg')
    for name in stills:
        shutil.copy2(src / name, target / name)
    clips = []
    segment_info = []
    for trace in sorted(src.glob(f'{tag.lower()}-[0-9][0-9]-*-browser-trace.json')):
        name = trace.name.removesuffix('-browser-trace.json')
        subprocess.run([sys.executable, str(ROOT / 'scripts/manual_reship_video.py'), name, run], check=True, stdout=subprocess.DEVNULL)
        info_path = src / f'{name}-recording-info.json'
        info = json.loads(info_path.read_text(encoding='utf-8'))
        assert info['frame_count'] < 450, f'Filmstrip cap reached: {name}'
        segment_info.append({'name': name, **info})
        for path in (trace, src / f'{name}.mp4', info_path):
            shutil.copy2(path, target / path.name)
        clips.append(target / f'{name}.mp4')
    assert len(clips) == (17 if tag == 'SCAN' else 13)
    manifest = target / 'segments.ffconcat'
    manifest.write_text('\n'.join(f"file '{p.name}'" for p in clips), encoding='utf-8')
    compilation = target / f'{tag.lower()}-100-0-100-segment-compilation.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'concat', '-safe', '0', '-i', str(manifest),
        '-c', 'copy', '-movflags', '+faststart', str(compilation)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    (target / 'segments.json').write_text(json.dumps({'type': 'separate browser filmstrip clips, concatenated; not continuous native video',
        'between_segments': 'Review pauses and database read-only verification omitted. All business writes were performed through the employee UI.',
        'segments': segment_info}, ensure_ascii=False, indent=2), encoding='utf-8')
    video_index.append((tag, compilation.relative_to(DEST).as_posix(), [p.name for p in clips]))

(DEST / '数量核对.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
test_sources = ('manual-reship-backend.log', 'manual-reship-regression.log', 'manual-reship-ui-final.log')
test_summary = []
for name in test_sources:
    text = (ROOT / 'tmp' / name).read_text(encoding='utf-8', errors='replace')
    lines = [line for line in text.splitlines() if re.search(r'^Ran \d+ tests|^OK$|^Found \d+ test|Test Files\s|Tests\s+\d+ passed|^CONCURRENCY path=/api/quality/rework-cases/1/reship/|^CONCURRENCY statuses=\[201, 201\]', line)]
    test_summary += [f'## {name}', *lines, '']
test_summary += ['TypeScript tsc -b: exit 0 (empty final log).',
    'These are local synthetic tests, not production latency or Android acceptance evidence.']
(DEST / '测试结果_脱敏摘要.txt').write_text('\n'.join(test_summary), encoding='utf-8')

index = ['<!doctype html><meta charset="utf-8"><title>原退货单再次出货 · 隔离验收</title>',
    '<style>body{font:16px/1.7 system-ui;max-width:1000px;margin:30px auto;padding:0 20px}video{width:100%;background:#111}img{max-width:100%}h2{margin-top:40px}</style>',
    '<h1>原退货单再次出货 · 隔离验收</h1>',
    '<p>仅合成数据，未部署、未改正式账。普通员工页面完成两个入口的 100→0→100；原回冲明细完全未变。</p>',
    '<p><strong>录像说明：</strong>下方为按操作分别录制的浏览器影片汇编，不是一镜到底的桌面录像。片段间省略核对等待；低分辨率影片用于看过程，原尺寸截图和各阶段 JSON 用于看清数值。扫码入口手动输入完整流程卡号，未验证安卓相机。</p>',
    '<p><a href="验收报告.md">完整报告</a> · <a href="数量核对.json">数量核对</a> · <a href="测试结果_脱敏摘要.txt">测试摘要</a></p>']
for tag, movie, clips in video_index:
    index += [f'<h2>{"扫码退货入口" if tag == "SCAN" else "无扫码整批退货入口"}</h2>',
        f'<video controls preload="metadata" src="{html.escape(movie)}"></video>',
        f'<p><a href="{html.escape(movie)}">下载该路径汇编视频</a></p>', '<details><summary>各操作原片段</summary><ol>']
    index += [f'<li><a href="{tag.lower()}/{html.escape(name)}">{html.escape(name)}</a></li>' for name in clips]
    index += ['</ol></details>']
(DEST / '开始查看.html').write_text('\n'.join(index), encoding='utf-8')

source_paths = ['backend/quality/services.py', 'backend/quality/views.py', 'backend/quality/tests/test_manual_return_reship.py',
    'frontend/src/api/client.ts', 'frontend/src/components/QualityReturnReworkDrawer.tsx',
    'frontend/src/components/QualityReturnReworkDrawer.test.tsx',
    'scripts/manual_reship_review_seed.py', 'scripts/manual_reship_snapshot.py', 'scripts/manual_reship_video.py',
    'scripts/manual_reship_evidence.py', 'docs/flowcards-20261119/无扫码整批退货原单再发补修验收.md']
for rel in source_paths:
    target = DEST / 'source' / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / rel, target)
tracked = subprocess.run(['git', 'diff', BASE, '--', *source_paths], cwd=ROOT, capture_output=True, check=True).stdout
(DEST / 'tracked-source.patch').write_bytes(tracked)
head = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
(DEST / 'source-base.txt').write_text(f'Review base: {BASE}\nAssembly HEAD: {head}\nBranch: codex/flow-cards-20261118\nSource directory includes all review files; patch also includes staged/committed added files.\nNot pushed or deployed.\n', encoding='utf-8')

hashes = {p.relative_to(DEST).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(DEST.rglob('*'))
    if p.is_file() and p.name != 'SHA256.json'}
assert not any('credentials' in p.lower() and not p.endswith('no-credentials.json') for p in hashes)
assert not any(p.endswith(('.sqlite3', '.env', '.db')) for p in hashes)
(DEST / 'SHA256.json').write_text(json.dumps(hashes, ensure_ascii=False, indent=2), encoding='utf-8')
archive = DEST.with_suffix('.zip')
with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
    for p in sorted(DEST.rglob('*')):
        if p.is_file():
            zf.write(p, p.relative_to(DEST))
print(json.dumps({'archive': str(archive), 'bytes': archive.stat().st_size, 'checks': results}, ensure_ascii=False))
