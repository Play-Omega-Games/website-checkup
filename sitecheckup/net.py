"""Polite, read-only HTTP and TLS helpers. Only GET and HEAD are ever sent. No logins, no forms, no cookies kept."""
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone

UA = 'OmegaWebsiteCheckup/1.0 (read-only weekly site check; +https://github.com/Play-Omega-Games/website-checkup)'
TIMEOUT = 20
GAP_SCALE = 1.0  # tests set this to 0
_last = {}


class _Redirects(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        self.chain = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append({'status': code, 'to': newurl})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _polite(host, gap):
    t = _last.get(host, 0)
    wait = gap * GAP_SCALE - (time.time() - t)
    if wait > 0:
        time.sleep(wait)
    _last[host] = time.time()


def fetch(url, method='GET', max_bytes=3_000_000, gap=1.0, headers=None):
    """Returns a dict: ok, status, final_url, chain, headers, body (str, GET only), elapsed_ms, ttfb_ms, bytes, error."""
    assert method in ('GET', 'HEAD')
    host = urllib.parse.urlparse(url).netloc
    _polite(host, gap)
    red = _Redirects()
    opener = urllib.request.build_opener(red)
    h = {'User-Agent': UA, 'Accept': 'text/html,application/xhtml+xml,*/*;q=0.8', 'Accept-Encoding': 'identity'}
    h.update(headers or {})
    req = urllib.request.Request(url, method=method, headers=h)
    t0 = time.time()
    out = {'url': url, 'ok': False, 'status': None, 'final_url': url, 'chain': red.chain, 'headers': {},
           'body': '', 'elapsed_ms': None, 'ttfb_ms': None, 'bytes': 0, 'error': None}
    try:
        with opener.open(req, timeout=TIMEOUT) as r:
            out['ttfb_ms'] = round((time.time() - t0) * 1000)
            out['status'] = r.status
            out['final_url'] = r.geturl()
            out['headers'] = {k.lower(): v for k, v in r.headers.items()}
            if method == 'GET':
                raw = r.read(max_bytes)
                out['bytes'] = len(raw)
                ctype = out['headers'].get('content-type', '')
                if any(k in ctype for k in ('html', 'xml', 'text', 'json')) or not ctype:
                    out['body'] = raw.decode(_charset(ctype), 'replace')
            out['ok'] = 200 <= r.status < 400
    except urllib.error.HTTPError as e:
        out['status'] = e.code
        out['final_url'] = e.geturl() or url
        out['headers'] = {k.lower(): v for k, v in (e.headers or {}).items()}
        out['error'] = f'HTTP {e.code}'
    except urllib.error.URLError as e:
        out['error'] = _reason(e.reason)
    except (socket.timeout, TimeoutError):
        out['error'] = 'timed out'
    except Exception as e:  # noqa: BLE001
        out['error'] = type(e).__name__ + ': ' + str(e)[:120]
    out['elapsed_ms'] = round((time.time() - t0) * 1000)
    return out


def _charset(ctype):
    if 'charset=' in ctype:
        return ctype.split('charset=')[-1].split(';')[0].strip() or 'utf-8'
    return 'utf-8'


def _reason(r):
    s = str(r)
    if isinstance(r, ssl.SSLCertVerificationError) or 'CERTIFICATE_VERIFY_FAILED' in s:
        msg = getattr(r, 'verify_message', None)
        if not msg:
            m = re.search(r'certificate verify failed: ([^(]+)', s)
            msg = m.group(1) if m else 'not trusted'
        return 'certificate problem: ' + msg.strip().rstrip('.')[:100]
    if isinstance(r, socket.gaierror) or 'Name or service not known' in s or 'nodename nor servname' in s:
        return 'domain not found (DNS)'
    if 'UNEXPECTED_EOF' in s or 'EOF occurred' in s:
        return 'the connection closed before the page loaded'
    if 'refused' in s.lower():
        return 'connection refused'
    if 'timed out' in s.lower():
        return 'timed out'
    return s[:120]


def tls_info(host, port=443):
    """Certificate facts for host. Verifies the chain and hostname like a browser would."""
    out = {'host': host, 'ok': False, 'valid': False, 'not_after': None, 'days_left': None,
           'issuer': None, 'protocol': None, 'error': None, 'names': []}
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as s:
                cert = s.getpeercert()
                out['protocol'] = s.version()
        out.update(_cert_facts(cert))
        out['valid'] = True
        out['ok'] = True
    except ssl.SSLCertVerificationError as e:
        out['error'] = 'certificate problem: ' + (e.verify_message or str(e))[:120].strip().rstrip('.')
        # Read the dates anyway, without trusting the chain, so we can say when it expired.
        try:
            raw = ssl.get_server_certificate((host, port), timeout=TIMEOUT)
            out.update(_pem_dates(raw))
        except Exception:  # noqa: BLE001
            pass
    except Exception as e:  # noqa: BLE001
        out['error'] = _reason(e)
    return out


def _cert_facts(cert):
    na = datetime.strptime(cert['notAfter'], '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
    issuer = dict(x[0] for x in cert.get('issuer', ()))
    names = [v for k, v in cert.get('subjectAltName', ()) if k == 'DNS']
    return {'not_after': na.isoformat(), 'days_left': (na - datetime.now(timezone.utc)).days,
            'issuer': issuer.get('organizationName') or issuer.get('commonName'), 'names': names[:20]}


def _pem_dates(pem):
    """Best-effort expiry from an unverified PEM, using the ssl module's test decoder if present."""
    import os
    import tempfile
    try:
        with tempfile.NamedTemporaryFile('w', suffix='.pem', delete=False) as f:
            f.write(pem)
            path = f.name
        cert = ssl._ssl._test_decode_cert(path)  # noqa: SLF001
        os.unlink(path)
        return _cert_facts(cert)
    except Exception:  # noqa: BLE001
        return {}


def robots(base):
    rp = urllib.robotparser.RobotFileParser()
    r = fetch(urllib.parse.urljoin(base, '/robots.txt'), gap=0.5)
    text = r['body'] if r['status'] == 200 else ''
    rp.parse(text.splitlines())
    sitemaps = [l.split(':', 1)[1].strip() for l in text.splitlines() if l.lower().startswith('sitemap:')]
    return rp, {'status': r['status'], 'exists': r['status'] == 200 and bool(text.strip()), 'sitemaps': sitemaps,
                'blocks_all': _blocks_all(text)}


def _blocks_all(text):
    agent_all = False
    for line in text.splitlines():
        line = line.split('#')[0].strip()
        if line.lower().startswith('user-agent:'):
            agent_all = line.split(':', 1)[1].strip() == '*'
        elif agent_all and line.lower().replace(' ', '') == 'disallow:/':
            return True
    return False
