from __future__ import annotations
import json
from pathlib import Path

LATEST=Path('data/latest.json')
PREVIOUS=Path('/tmp/watchradar_previous.json')


def preserve():
    if LATEST.exists():
        PREVIOUS.write_bytes(LATEST.read_bytes())
    else:
        PREVIOUS.write_text('{"items":[]}',encoding='utf-8')


def restore_if_needed():
    if not LATEST.exists() or not PREVIOUS.exists():return
    try:
        now=json.loads(LATEST.read_text(encoding='utf-8'))
        prev=json.loads(PREVIOUS.read_text(encoding='utf-8'))
        if isinstance(prev.get('items'),list):now['items']=prev['items']
        now['goofish_status']='home_exit_unavailable'
        now.setdefault('errors',[]).append('goofish: HOME_EXIT_UNAVAILABLE')
        LATEST.write_text(json.dumps(now,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception:
        pass

if __name__=='__main__':
    import sys
    restore_if_needed() if len(sys.argv)>1 and sys.argv[1]=='restore' else preserve()
