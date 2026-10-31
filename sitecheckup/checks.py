"""The weekly checks. Every network call is a GET or HEAD. Nothing is ever posted to the site."""
import json
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

from . import net
from .page import parse

OBS = 'https://observatory-api.mdn.mozilla.net/api/v2/scan'
SEC_HEADERS = ['strict-transport-security', 'content-security-policy', 'x-content-type-options',
               'x-frame-options', 'referrer-policy']
DATE_RE = re.compile(r'\b(20\d\d)-(\d\d)-(\d\d)\b')


def norm_site(url):
    url = url.strip()
    if not re.match(r'^https?://', url):
        url = 'https://' + url
    p = urllib.parse.urlparse(url)
    return f'{p.scheme}://{p.netloc}/'


def same_site(a, b):
    ha, hb = urllib.parse.urlparse(a).netloc.lower(), urllib.parse.urlparse(b).netloc.lower()
    strip = lambda h: h[4:] if h.startswith('www.') else h  # noqa: E731
    return strip(ha) == strip(hb)


# ------------------------------------------------------------------ uptime and https
def check_uptime(site, tries=3, wait=10):
    attempts = []
    for i in range(tries):
        home = net.fetch(site, gap=0)
        attempts.append(home['status'] or home['error'])
        if home['ok']:
            break
        if i < tries - 1:
            time.sleep(wait)
    host = urllib.parse.urlparse(site).netloc
    http = net.fetch('http://' + host + '/', method='HEAD', gap=0.5)
    if http['status'] in (405, 501):
        http = net.fetch('http://' + host + '/', gap=0.5, max_bytes=1000)
    redirects_to_https = http['final_url'].startswith('https://') if (http['status'] or http['chain']) else None
    http_note = None
    if not redirects_to_https and (http['error'] or (http['status'] or 0) >= 500):
        # Some networks block or proxy plain http. Test a control site before blaming the owner's site.
        ctrl = net.fetch('http://example.com/', method='HEAD', gap=0)
        if not ctrl['ok']:
            redirects_to_https, http_note = None, 'plain http is blocked on this computer, so this check was skipped'
    alt_host = host[4:] if host.startswith('www.') else 'www.' + host
    for i in range(3):
        alt = net.fetch('https://' + alt_host + '/', method='HEAD', gap=0.5)
        if alt['status'] in (405, 501):
            alt = net.fetch('https://' + alt_host + '/', gap=0.5, max_bytes=1000)
        if alt['ok'] or alt['status'] or 'DNS' in (alt['error'] or ''):
            break
        time.sleep(5)
    return {
        'up': home['ok'], 'status': home['status'], 'error': home['error'], 'final_url': home['final_url'],
        'response_ms': home['elapsed_ms'], 'ttfb_ms': home['ttfb_ms'], 'redirects': home['chain'],
        'http_redirects_to_https': redirects_to_https, 'http_status': http['status'], 'http_error': http['error'], 'http_note': http_note,
        'alt_host': alt_host, 'alt_host_ok': alt['ok'], 'alt_host_final': alt['final_url'], 'alt_host_error': alt['error'],
        'attempts': attempts, '_home': home,
    }


def check_tls(site):
    host = urllib.parse.urlparse(site).netloc.split(':')[0]
    return net.tls_info(host)


# ------------------------------------------------------------------ domain (DNS over HTTPS and RDAP, both free with no key)
DOH = 'https://dns.google/resolve'
RDAP = 'https://rdap.org/domain/'


def registrable(host):
    parts = host.lower().split(':')[0].split('.')
    if parts[0] == 'www':
        parts = parts[1:]
    two_level = {'co.uk', 'org.uk', 'ac.uk', 'com.au', 'net.au', 'org.au', 'co.nz', 'co.za', 'com.br', 'co.jp', 'com.mx', 'co.in'}
    n = 3 if '.'.join(parts[-2:]) in two_level else 2
    return '.'.join(parts[-n:])


def check_domain(site, today=None):
    host = urllib.parse.urlparse(site).netloc.split(':')[0]
    out = {'host': host, 'resolves': None, 'dns_error': None, 'expires': None, 'days_left': None, 'rdap_error': None}
    r = net.fetch(DOH + '?' + urllib.parse.urlencode({'name': host, 'type': 'A'}), gap=0, headers={'Accept': 'application/dns-json'})
    try:
        j = json.loads(r['body'])
        out['resolves'] = j.get('Status') == 0 and any(a.get('type') in (1, 5) for a in j.get('Answer', []))
        if j.get('Status') == 3:
            out['dns_error'] = 'no such domain'
    except Exception:  # noqa: BLE001
        out['dns_error'] = 'lookup unavailable'
    dom = registrable(host)
    for i in range(3):  # the free registry lookup sometimes drops a connection; try again before giving up
        r = net.fetch(RDAP + dom, gap=0, headers={'Accept': 'application/rdap+json'})
        if r['status'] == 200 or (r['status'] and r['status'] < 500 and r['status'] != 429):
            break
        time.sleep(3 * (i + 1))
    try:
        j = json.loads(r['body'])
        for e in j.get('events', []):
            if e.get('eventAction') == 'expiration':
                exp = datetime.fromisoformat(e['eventDate'].replace('Z', '+00:00'))
                out['expires'] = exp.date().isoformat()
                out['days_left'] = (exp.date() - (today or date.today())).days
        if not out['expires']:
            out['rdap_error'] = 'no expiry date published for this domain'
    except Exception:  # noqa: BLE001
        out['rdap_error'] = f"registry lookup unavailable ({r['status'] or r['error']})"
    return out


# ------------------------------------------------------------------ crawl
def sitemap_urls(site, robots_info, limit=500):
    cands = robots_info.get('sitemaps') or [urllib.parse.urljoin(site, '/sitemap.xml')]
    urls, lastmods, found, seen = [], [], None, set()
    queue = list(cands)
    while queue and len(seen) < 6:
        sm = queue.pop(0)
        if sm in seen:
            continue
        seen.add(sm)
        r = net.fetch(sm, gap=0.5)
        if r['status'] != 200 or not r['body'].strip().startswith(('<?xml', '<urlset', '<sitemapindex')):
            continue
        found = found or sm
        try:
            root = ET.fromstring(r['body'].encode())
        except ET.ParseError:
            continue
        ns = '{http://www.sitemaps.org/schemas/sitemap/0.9}'
        for sub in root.iter(ns + 'sitemap'):
            loc = sub.find(ns + 'loc')
            if loc is not None and loc.text:
                queue.append(loc.text.strip())
        for u in root.iter(ns + 'url'):
            loc, lm = u.find(ns + 'loc'), u.find(ns + 'lastmod')
            if loc is not None and loc.text and len(urls) < limit:
                urls.append(loc.text.strip())
            if lm is not None and lm.text:
                lastmods.append(lm.text.strip()[:10])
    # Some sites stamp every page with the same build date; that says nothing about real updates.
    uniform = len(lastmods) > 3 and len(set(lastmods)) == 1
    return {'found': found, 'urls': urls, 'count': len(urls), 'lastmod_uniform': uniform,
            'newest_lastmod': (max(lastmods) if lastmods and not uniform else None)}


def crawl(site, home, rp, sm, max_pages=25):
    pages, queue, seen = {}, [home['final_url']], set()
    for u in sm['urls'][:max_pages]:
        queue.append(u)
    while queue and len(pages) < max_pages:
        u = queue.pop(0)
        if u in seen or not same_site(u, site):
            continue
        seen.add(u)
        if not rp.can_fetch(net.UA, u):
            continue
        if re.search(r'\.(pdf|jpe?g|png|gif|webp|svg|zip|mp4|mp3|docx?|xlsx?)$', u, re.I):
            continue
        r = home if u == home['final_url'] else net.fetch(u)
        if not r['body'] or 'html' not in r['headers'].get('content-type', 'text/html'):
            pages[u] = {'status': r['status'], 'error': r['error'], 'facts': None, 'headers': r['headers']}
            continue
        f = parse(r['body'], r['final_url'])
        pages[u] = {'status': r['status'], 'error': r['error'], 'facts': f, 'headers': r['headers'],
                    'bytes': r['bytes'], 'final_url': r['final_url']}
        for l in f['links']:
            if same_site(l, site) and l not in seen:
                queue.append(l)
    return pages


# ------------------------------------------------------------------ links
def check_links(site, pages, rp, max_links=150):
    targets = {}
    for src, p in pages.items():
        if not p['facts']:
            continue
        for l in p['facts']['links'] + [i['src'] for i in p['facts']['imgs']]:
            if l.startswith(('http://', 'https://')):
                targets.setdefault(l, set()).add(src)
    internal = [u for u in targets if same_site(u, site)]
    external = [u for u in targets if not same_site(u, site)]
    order = internal + external
    results, checked = [], 0
    for u in order[:max_links]:
        if u in pages and pages[u]['status'] is not None:
            st, err = pages[u]['status'], pages[u]['error']
        else:
            if same_site(u, site) and not rp.can_fetch(net.UA, u):
                continue
            r = net.fetch(u, method='HEAD', gap=1.0)
            if r['status'] in (403, 405, 429, 501) or (r['status'] and r['status'] >= 500) or r['error']:
                r = net.fetch(u, gap=1.0, max_bytes=20000)
            tries = 0
            while r['status'] is None and tries < 2:  # connection-level errors are often momentary
                time.sleep(5)
                r = net.fetch(u, gap=1.0, max_bytes=20000)
                tries += 1
            st, err = r['status'], r['error']
        checked += 1
        kind = 'internal' if same_site(u, site) else 'external'
        if kind == 'external' and (st in (401, 403, 429, 999) or (st is None and 'DNS' not in (err or ''))):
            verdict = 'unchecked'
        elif kind == 'internal' and st is None:
            verdict = 'unchecked'  # the site itself answered; a dropped connection here is not proof of a broken page
        elif st and st < 400:
            verdict = 'ok'
        else:
            verdict = 'broken'
        if verdict != 'ok':
            results.append({'url': u, 'status': st, 'error': err, 'kind': kind, 'verdict': verdict,
                            'found_on': sorted(targets[u])[:5]})
    return {'total_found': len(order), 'checked': checked, 'cap': max_links,
            'internal_found': len(internal), 'external_found': len(external), 'problems': results}


# ------------------------------------------------------------------ seo
def check_seo(site, pages, robots_info, sm):
    per, titles, canon_seen = [], {}, set()
    for u, p in pages.items():
        f = p['facts']
        if not f:
            continue
        # A page whose canonical address was already checked is a copy (for example ?template= variants).
        canon = f['canonical'] or u
        if canon != u and canon in canon_seen:
            continue
        canon_seen.add(canon)
        canon_seen.add(u)
        issues = []
        t = f['title'] or ''
        if not t:
            issues.append('missing_title')
        elif len(t) > 65:
            issues.append('long_title')
        elif len(t) < 10:
            issues.append('short_title')
        d = f['meta_description'] or ''
        if not d:
            issues.append('missing_description')
        elif len(d) > 170:
            issues.append('long_description')
        elif len(d) < 50:
            issues.append('short_description')
        if len(f['h1']) == 0:
            issues.append('missing_h1')
        elif len(f['h1']) > 1:
            issues.append('multiple_h1')
        if not f['viewport']:
            issues.append('missing_viewport')
        if not f['lang']:
            issues.append('missing_lang')
        if 'noindex' in (f['robots_meta'] or '').lower() or 'noindex' in p['headers'].get('x-robots-tag', '').lower():
            issues.append('noindex')
        no_alt = [i['src'] for i in f['imgs'] if i['alt'] is None]
        if no_alt:
            issues.append('images_missing_alt')
        if u.startswith('https://'):
            mixed = [s for s in f['scripts'] + f['styles'] + [i['src'] for i in f['imgs']] if s.startswith('http://')]
            if mixed:
                issues.append('mixed_content')
        else:
            mixed = []
        if t:
            # Pages that name the same canonical address (or differ only by ?query) count as one page.
            key = f['canonical'] or u.split('?')[0]
            titles.setdefault(t, set()).add(key)
        per.append({'url': u, 'title': t, 'description_len': len(d), 'h1_count': len(f['h1']), 'issues': issues,
                    'images_missing_alt': no_alt[:10], 'images_total': len(f['imgs']), 'mixed': mixed[:10],
                    'words': f['words']})
    dup = {t: sorted(us) for t, us in titles.items() if len(us) > 1}
    home = next((p for p in per), None)
    homef = next((p['facts'] for p in pages.values() if p['facts']), None) or {}
    return {
        'pages': per, 'duplicate_titles': dup, 'robots_txt': robots_info['exists'],
        'robots_blocks_all': robots_info['blocks_all'], 'sitemap': sm['found'], 'sitemap_urls': sm['count'],
        'home': home, 'has_favicon': bool(homef.get('icons')), 'og_image': bool(homef.get('og_image')),
        'jsonld_types': homef.get('jsonld_types', []),
        'local_business_schema': any('business' in t.lower() or t in ('Restaurant', 'Store', 'Dentist', 'Bakery')
                                     for t in homef.get('jsonld_types', [])),
    }


# ------------------------------------------------------------------ speed
SLOW_FIRST_BYTE_MS = 1500   # server took this long to start answering
SLOW_LOAD_MS = 3000         # homepage and its files took this long to download in total
HEAVY_PAGE_KB = 3000        # homepage and its files weigh more than this
LARGE_FILE_KB = 300
MAX_FILE_BYTES = 5_000_000


PARALLEL = 6                # files downloaded at once, like one visitor's browser


def check_speed(site, home, pages):
    """Basic load-speed check, measured directly, the way one visitor's browser fetches the page: download
    the homepage, then the files it needs (scripts, styles and images, up to 40), up to 6 at a time,
    accepting compression. Records time to first byte, the homepage download time, the total time until
    the homepage and all its files are downloaded, and the total weight. It does not run the page's
    scripts or measure on-screen rendering on a phone."""
    from concurrent.futures import ThreadPoolExecutor
    f = pages.get(home['final_url'], {}).get('facts') or {}
    assets = list(dict.fromkeys(f.get('scripts', []) + f.get('styles', []) + [i['src'] for i in f.get('imgs', [])]))[:40]
    enc = {'Accept-Encoding': 'gzip, br'}
    page = net.fetch(home['final_url'], gap=0, headers=enc, max_bytes=MAX_FILE_BYTES)
    if not page['ok']:
        page = home
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=PARALLEL) as pool:
        got = list(pool.map(lambda a: net.fetch(a, gap=0, headers=dict(enc, Accept='*/*'), max_bytes=MAX_FILE_BYTES), assets))
    files_wall_ms = round((time.time() - t0) * 1000) if assets else 0
    files, big = [], []
    for a, r in zip(assets, got):
        if r['ok']:
            files.append({'url': a, 'bytes': r['bytes'], 'ms': r['elapsed_ms']})
            if r['bytes'] > LARGE_FILE_KB * 1024:
                big.append({'url': a, 'kb': round(r['bytes'] / 1024)})
    total_bytes = page['bytes'] + sum(x['bytes'] for x in files)
    return {
        'source': 'basic',
        'basic': {
            'ttfb_ms': page['ttfb_ms'], 'home_ms': page['elapsed_ms'], 'html_kb': round(page['bytes'] / 1024),
            'files_found': len(assets), 'files_loaded': len(files), 'files_failed': len(assets) - len(files),
            'files_ms': files_wall_ms, 'parallel': PARALLEL,
            'slowest_file': max(files, key=lambda x: x['ms']) if files else None,
            'load_ms': page['elapsed_ms'] + files_wall_ms,
            'page_kb': round(total_bytes / 1024),
            'large_files': big,
            'compressed': page['headers'].get('content-encoding') in ('gzip', 'br', 'zstd', 'deflate'),
            'cache_control': page['headers'].get('cache-control'),
        },
    }


# ------------------------------------------------------------------ security headers
def check_security(site, home, use_observatory=True):
    present = {h: (h in home['headers']) for h in SEC_HEADERS}
    csp = home['headers'].get('content-security-policy', '')
    if not present['x-frame-options'] and 'frame-ancestors' in csp:
        present['x-frame-options'] = True
    out = {'headers': present, 'observatory': None, 'observatory_error': None}
    if use_observatory:
        host = urllib.parse.urlparse(site).netloc
        req = urllib.request.Request(OBS + '?host=' + urllib.parse.quote(host), method='POST',
                                     headers={'User-Agent': net.UA})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                j = json.loads(r.read())
            if j.get('grade'):
                out['observatory'] = {'grade': j['grade'], 'score': j.get('score'),
                                      'tests_failed': j.get('tests_failed'), 'tests_quantity': j.get('tests_quantity')}
            else:
                out['observatory_error'] = j.get('error') or 'no grade'
        except Exception as e:  # noqa: BLE001
            out['observatory_error'] = type(e).__name__
    return out


# ------------------------------------------------------------------ freshness and contact
def check_freshness(pages, sm, today=None):
    today = today or date.today()
    years, lm_headers, page_dates = [], [], []
    for u, p in pages.items():
        lm = p['headers'].get('last-modified')
        if lm:
            try:
                lm_headers.append(datetime.strptime(lm, '%a, %d %b %Y %H:%M:%S GMT').date().isoformat())
            except ValueError:
                pass
        f = p['facts']
        if f:
            years += [y for y in f['copyright_years'] if 1990 <= y <= today.year + 1]
            for y, m, d in DATE_RE.findall(f['text'][:50000]):
                try:
                    dd = date(int(y), int(m), int(d))
                    if dd <= today:
                        page_dates.append(dd.isoformat())
                except ValueError:
                    pass
    # Last-Modified headers are kept for reference only: many servers set them to the time of the request.
    newest = max([x for x in [sm.get('newest_lastmod'), max(page_dates) if page_dates else None] if x] or [None])
    days = (today - date.fromisoformat(newest[:10])).days if newest else None
    return {'copyright_year': max(years) if years else None, 'sitemap_newest': sm.get('newest_lastmod'),
            'header_newest': max(lm_headers) if lm_headers else None,
            'page_date_newest': max(page_dates) if page_dates else None,
            'newest_signal': newest, 'days_since_newest': days, 'current_year': today.year}


def check_contact(pages, home_url, business):
    """Is the owner's phone and address visible on the homepage? Only uses what the owner told us."""
    f = (pages.get(home_url) or {}).get('facts') or {}
    text = f.get('text', '')
    digits = re.sub(r'\D', '', text)
    out = {}
    phone = re.sub(r'\D', '', business.get('phone') or '')
    if phone:
        out['phone_on_home'] = phone[-10:] in digits
    if business.get('street'):
        out['street_on_home'] = business['street'].lower().split(',')[0].strip() in text.lower()
    return out


def run_all(url, max_pages=25, max_links=150, business=None, observatory=True, today=None):
    site = norm_site(url)
    t0 = time.time()
    up = check_uptime(site)
    home = up.pop('_home')
    res = {'site': site, 'checked_at': datetime.now(timezone.utc).isoformat(timespec='seconds'), 'uptime': up}
    res['domain'] = check_domain(site, today)
    res['tls'] = check_tls(site) if site.startswith('https://') else {'ok': False, 'error': 'site is not on https'}
    if not home['ok']:
        res['skipped'] = 'Site did not load, so page checks were skipped this week.'
        res['elapsed_s'] = round(time.time() - t0)
        return res
    rp, rinfo = net.robots(site)
    sm = sitemap_urls(site, rinfo)
    pages = crawl(site, home, rp, sm, max_pages)
    res['crawl'] = {'pages_checked': len(pages), 'cap': max_pages, 'sitemap_urls': sm['count']}
    res['links'] = check_links(site, pages, rp, max_links)
    res['seo'] = check_seo(site, pages, rinfo, sm)
    res['speed'] = check_speed(site, home, pages)
    res['security'] = check_security(site, home, observatory)
    res['freshness'] = check_freshness(pages, sm, today)
    res['contact'] = check_contact(pages, home['final_url'], business or {})
    res['elapsed_s'] = round(time.time() - t0)
    return res
