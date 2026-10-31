"""Write data/fixes.json from scripts/fixes_src.py."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import fixes_src as F  # noqa: E402

fixes = {k: {'priority': p, 'title': t, 'why': w, 'how': h, 'learn_more': l} for k, (p, t, w, h, l) in F.FIXES.items()}
out = {'priorities': {'1': 'Critical: fix today', '2': 'High: this week', '3': 'Medium: this month', '4': 'Low: when you can'},
       'fixes': fixes, 'gbp_reminders': [{'topic': a, 'text': b} for a, b in F.GBP], 'gbp_help': F.GBP_HELP}
(ROOT / 'data' / 'fixes.json').write_text(json.dumps(out, ensure_ascii=False, indent=1) + '\n')
print(len(fixes), 'fixes,', len(F.GBP), 'reminders')
