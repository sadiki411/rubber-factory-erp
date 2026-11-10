"""Read-only onsite HTTP timing tool. No shipment/return/inventory writes.

python scripts/probe_scan_network.py --cards CARD1 CARD2 --samples 100
Use the dedicated read-only account. Password is prompted and never saved.
Measures HTTP lookup, not camera recognition, rendering or save performance.
"""
import argparse
import getpass
import http.cookiejar
import json
import math
import statistics
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='https://erp.qfylagent.org')
    parser.add_argument('--cards', nargs='+', required=True)
    parser.add_argument('--samples', type=int, default=20)
    parser.add_argument('--timeout', type=float, default=10)
    parser.add_argument('--include-history', action='store_true')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    site = args.url.rstrip('/')
    parsed = urlsplit(site)
    if (parsed.hostname not in {'erp.qfylagent.org', 'localhost', '127.0.0.1'}
            or parsed.path or parsed.username or parsed.query or parsed.fragment
            or parsed.scheme not in {'http', 'https'}
            or (parsed.hostname == 'erp.qfylagent.org' and parsed.scheme != 'https')
            or not 1 <= args.samples <= 1000):
        parser.error('Only the ERP or an isolated localhost server is allowed; samples 1..1000')
    cookies = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookies))

    def request(path, body=None):
        headers = {'Referer': site + '/', 'Accept': 'application/json'}
        if body is not None:
            headers['Content-Type'] = 'application/json'
            headers['X-CSRFToken'] = next((cookie.value for cookie in cookies if cookie.name == 'csrftoken'), '')
        req = Request(site + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        with opener.open(req, timeout=args.timeout) as response:
            if response.status == 204:
                return {}
            return json.load(response)

    request('/api/auth/session/')
    identity = request('/api/auth/login/', {'username': input('Read-only username: '), 'password': getpass.getpass('Password (not saved): ')})
    rows = []
    try:
        if not identity.get('user', {}).get('read_only'):
            raise RuntimeError('Refusing: use the dedicated read-only account, not an administrator')
        for index in range(args.samples):
            code = args.cards[index % len(args.cards)]
            paths = [('scan', '/api/quality/process-cards/scan/?' + urlencode({'code': code}))]
            for phase, path in paths:
                started = time.perf_counter()
                error, payload = None, {}
                try:
                    payload = request(path)
                except (HTTPError, URLError, TimeoutError) as exc:
                    error = type(exc).__name__ + (f': HTTP {exc.code}' if isinstance(exc, HTTPError) else '')
                elapsed = (time.perf_counter() - started) * 1000
                rows.append({'sample': index + 1, 'phase': phase, 'ms': round(elapsed, 2), 'error': error, 'found': payload.get('found')})
                if phase == 'scan' and not error and args.include_history and (payload.get('active_card') or {}).get('id'):
                    paths.append(('history', f"/api/quality/process-cards/{int(payload['active_card']['id'])}/shipment-trace/"))
                print(f'{index + 1}/{args.samples} {phase}: {elapsed:.0f}ms ' + (error or 'OK'))
        summaries = {}
        for phase in ['scan', 'history']:
            measured = [row for row in rows if row['phase'] == phase]
            values = sorted(row['ms'] for row in measured if not row['error'])
            if measured:
                summaries[phase] = {'requests': len(measured), 'errors': sum(bool(row['error']) for row in measured),
                                    'first_ms': measured[0]['ms'], 'p50_ms': statistics.median(values) if values else None,
                                    'p95_ms': values[math.ceil(len(values) * .95) - 1] if values else None}
        result = {'scope': 'Read-only HTTP including current network; no camera/UI/save/cold-OS claim', 'server': site, 'summary': summaries, 'samples': rows}
        if args.output:
            args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
        print(json.dumps(summaries, indent=2, ensure_ascii=False))
    finally:
        request('/api/auth/logout/', {})


if __name__ == '__main__':
    main()
