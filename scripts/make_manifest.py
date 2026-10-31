"""Write MANIFEST.txt: path, size in bytes and SHA-256 for every tracked file. Run from the repo root."""
import hashlib
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parent.parent
files = sorted(f for f in subprocess.run(['git', 'ls-files'], cwd=root, capture_output=True, text=True).stdout.split()
               if f != 'MANIFEST.txt')
lines = []
for f in files:
    b = (root / f).read_bytes()
    lines.append(f'{f} {len(b)} {hashlib.sha256(b).hexdigest()}')
(root / 'MANIFEST.txt').write_text('\n'.join(lines) + '\n')
print(len(lines), 'files')
