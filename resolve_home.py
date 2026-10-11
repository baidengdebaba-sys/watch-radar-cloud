from __future__ import annotations
import hashlib, ipaddress, json, os, urllib.request
from pathlib import Path

SSH_PORT = 40222
OUT_HOST = Path('/tmp/watchradar_home_host')
OUT_KNOWN = Path('/tmp/watchradar_known_hosts')


def clean(s: str) -> str:
    return (s or '').lstrip('\ufeff').replace('\r', '').strip()


def valid_public_ipv4(s: str) -> bool:
    try:
        ip = ipaddress.ip_address(clean(s))
        return ip.version == 4 and ip.is_global
    except Exception:
        return False


def key_material(known: str) -> str:
    for line in clean(known).splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[-2].startswith('ssh-'):
            return f'{parts[-2]} {parts[-1]}'
    return ''


def latest_ddns_host(material: str) -> str:
    if not material:
        return ''
    digest = hashlib.sha256(material.encode()).hexdigest()[:32]
    topic = f'watchradar-{digest}'
    url = f'https://ntfy.sh/{topic}/json?poll=1&since=latest'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'WatchRadar/1.0'})
        with urllib.request.urlopen(req, timeout=10) as r:
            rows = r.read().decode('utf-8', 'ignore').splitlines()
        latest = ''
        for line in rows:
            try:
                j = json.loads(line)
                msg = clean(str(j.get('message', '')))
                if j.get('event') == 'message' and valid_public_ipv4(msg):
                    latest = msg
            except Exception:
                pass
        return latest
    except Exception:
        return ''


def main():
    seed_known = clean(os.environ.get('HOME_SSH_KNOWN_HOSTS', ''))
    fallback = clean(os.environ.get('HOME_SSH_HOST', ''))
    material = key_material(seed_known)
    auto = latest_ddns_host(material)
    host = auto if valid_public_ipv4(auto) else fallback if valid_public_ipv4(fallback) else ''
    if not host or not material:
        raise SystemExit('HOME_RESOLVE_FAILED')

    OUT_HOST.write_text(host + '\n', encoding='utf-8')
    OUT_KNOWN.write_text(f'[{host}]:{SSH_PORT} {material}\n', encoding='utf-8')
    print('HOME_HOST_SOURCE=' + ('auto-ddns' if auto == host else 'fallback-secret'))


if __name__ == '__main__':
    main()
