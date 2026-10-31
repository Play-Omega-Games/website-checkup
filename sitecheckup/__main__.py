"""Website Checkup command line.

    python3 -m sitecheckup check --url example.com --out /workspace/website-checkup/state
          [--business business.json] [--max-pages 25] [--max-links 150] [--no-observatory]
    python3 -m sitecheckup report --results state/results-2026-11-17.json [--previous state/results-2026-11-10.json]
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import checks, report


def main(argv=None):
    p = argparse.ArgumentParser(prog='sitecheckup')
    sub = p.add_subparsers(dest='cmd', required=True)
    c = sub.add_parser('check')
    c.add_argument('--url', required=True); c.add_argument('--out', required=True)
    c.add_argument('--business')
    c.add_argument('--max-pages', type=int, default=25); c.add_argument('--max-links', type=int, default=150)
    c.add_argument('--no-observatory', action='store_true')
    r = sub.add_parser('report')
    r.add_argument('--results', required=True); r.add_argument('--previous')
    a = p.parse_args(argv)
    if a.cmd == 'check':
        biz = json.loads(Path(a.business).read_text()) if a.business else {}
        res = checks.run_all(a.url, a.max_pages, a.max_links, biz, not a.no_observatory)
        items = report.issues(res)
        out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
        stamp = date.today().isoformat()
        (out / f'results-{stamp}.json').write_text(json.dumps(res, indent=1, default=str))
        (out / f'fixes-{stamp}.json').write_text(json.dumps(items, indent=1))
        prev = sorted(x for x in out.glob('fixes-*.json') if x.name != f'fixes-{stamp}.json')
        prev_items = json.loads(prev[-1].read_text()) if prev else None
        print(report.summary(res, items, prev_items))
    else:
        res = json.loads(Path(a.results).read_text())
        items = report.issues(res)
        prev_items = report.issues(json.loads(Path(a.previous).read_text())) if a.previous else None
        print(report.summary(res, items, prev_items))
        print(json.dumps(items, indent=1))


if __name__ == '__main__':
    sys.exit(main())
