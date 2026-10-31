"""Turn check results into a prioritized fix list and a short chat summary."""
import json
from datetime import date
from pathlib import Path

from . import checks

DATA = Path(__file__).resolve().parent.parent / 'data'


def library():
    return json.loads((DATA / 'fixes.json').read_text())


def issues(res):
    lib = library()['fixes']
    out = []

    def add(key, evidence, where=None):
        f = lib[key]
        out.append({'id': key, 'priority': f['priority'], 'title': f['title'], 'why': f['why'], 'how': f['how'],
                    'learn_more': f['learn_more'], 'evidence': evidence, 'where': where or []})

    up = res['uptime']
    dom = res.get('domain') or {}
    tls = res.get('tls') or {}
    cert_err = bool(tls.get('error') and 'certificate' in tls['error'])
    if not up['up']:
        err = up.get('error') or f"HTTP {up.get('status')}"
        if not (cert_err and 'certificate' in (err or '')):  # the certificate item already covers it
            if dom.get('dns_error') == 'no such domain' or (err and 'DNS' in err):
                add('domain_not_found', f"{dom.get('host', 'Your domain')} has no DNS records.")
            else:
                add('site_down', f'Homepage check: {err}.')
    if dom.get('days_left') is not None:
        if dom['days_left'] < 30:
            add('domain_expiring_30', f"Domain registration ends {dom['expires']} ({dom['days_left']} days).")
        elif dom['days_left'] < 60:
            add('domain_expiring_60', f"Domain registration ends {dom['expires']} ({dom['days_left']} days).")
    if res['site'].startswith('https://') or tls:
        if tls.get('error') and 'certificate' in tls['error']:
            extra = f" It expired on {tls['not_after'][:10]}." if tls.get('days_left') is not None and tls['days_left'] < 0 else ''
            detail = tls['error'].replace('certificate problem: ', '')
            add('cert_invalid', f"{detail[:1].upper() + detail[1:]}.{extra}")
        elif tls.get('days_left') is not None:
            if tls['days_left'] < 14:
                add('cert_expiring_14', f"Certificate expires {tls['not_after'][:10]} ({tls['days_left']} days).")
            elif tls['days_left'] < 30:
                add('cert_expiring_30', f"Certificate expires {tls['not_after'][:10]} ({tls['days_left']} days).")
    if up.get('up') and up.get('http_redirects_to_https') is False:
        add('no_https_redirect', f"http:// returned {up.get('http_status') or up.get('http_error')} without moving to https://.")
    if up.get('up') and not up.get('alt_host_ok'):
        add('alt_host_broken', f"{up['alt_host']}: {up.get('alt_host_error') or 'did not load'}.")
    if 'skipped' in res:
        return sort(out)

    seo = res['seo']
    pages = seo['pages']
    home = pages[0] if pages else None
    if seo['robots_blocks_all']:
        add('robots_blocks_all', 'robots.txt has "Disallow: /" for all crawlers.')
    if home and 'noindex' in home['issues']:
        add('noindex_home', 'The homepage has a noindex robots setting.', [home['url']])
    ni = [p['url'] for p in pages[1:] if 'noindex' in p['issues']]
    if ni:
        add('noindex_page', f'{len(ni)} page(s) set to noindex.', ni)

    probs = res['links']['problems']
    bi = [p for p in probs if p['kind'] == 'internal' and p['verdict'] == 'broken']
    be = [p for p in probs if p['kind'] == 'external' and p['verdict'] == 'broken']
    if bi:
        add('broken_internal', f'{len(bi)} broken link(s) to your own pages.',
            [f"{p['url']} ({p['status'] or p['error']}), found on {p['found_on'][0]}" for p in bi[:15]])
    if be:
        add('broken_external', f'{len(be)} broken link(s) to other sites.',
            [f"{p['url']} ({p['status'] or p['error']}), found on {p['found_on'][0]}" for p in be[:15]])

    def pages_with(tag):
        return [p['url'] for p in pages if tag in p['issues']]
    for tag, key in (('missing_title', 'missing_title'), ('missing_description', 'missing_description'),
                     ('missing_h1', 'missing_h1'), ('multiple_h1', 'multiple_h1'), ('missing_viewport', 'missing_viewport'),
                     ('missing_lang', 'missing_lang'), ('mixed_content', 'mixed_content')):
        w = pages_with(tag)
        if w:
            add(key, f'{len(w)} of {len(pages)} pages checked.', w[:15])
    tl = pages_with('long_title') + pages_with('short_title')
    if tl:
        add('title_length', f'{len(tl)} page(s).', tl[:15])
    dl = pages_with('long_description') + pages_with('short_description')
    if dl:
        add('description_length', f'{len(dl)} page(s).', dl[:15])
    alt = [p for p in pages if p['images_missing_alt']]
    if alt:
        n = sum(len(p['images_missing_alt']) for p in alt)
        add('images_missing_alt', f'At least {n} image(s) on {len(alt)} page(s).', [p['url'] for p in alt[:15]])
    if seo['duplicate_titles']:
        add('duplicate_titles', f"{len(seo['duplicate_titles'])} title(s) used on more than one page.",
            [f'"{t}": {len(u)} pages' for t, u in list(seo['duplicate_titles'].items())[:10]])
    if not seo['sitemap']:
        add('no_sitemap', 'No sitemap at /sitemap.xml or in robots.txt.')
    if not seo['robots_txt']:
        add('no_robots', 'No robots.txt at the site root.')
    if not seo['has_favicon']:
        add('no_favicon', 'No icon link on the homepage.')
    if not seo['og_image']:
        add('no_og_image', 'No og:image tag on the homepage.')
    if not seo['local_business_schema']:
        add('no_local_schema', 'No local business structured data found on the homepage.')

    sp = res['speed']
    b = sp.get('basic') or {}
    if b.get('ttfb_ms') and b['ttfb_ms'] > checks.SLOW_FIRST_BYTE_MS:
        add('slow_ttfb', f"First byte took {b['ttfb_ms']} ms.")
    if b.get('load_ms') and b['load_ms'] > checks.SLOW_LOAD_MS:
        add('slow_load', f"Your homepage and its {_files(b['files_loaded'])} took {b['load_ms'] / 1000:.1f} s to download, fetched like a browser does.")
    if b.get('page_kb') and b['page_kb'] > checks.HEAVY_PAGE_KB:
        add('heavy_page', f"Your homepage and its files weigh {_size(b['page_kb'])}.")
    if b and not b.get('compressed'):
        add('not_compressed', 'Homepage was sent without compression.')
    if b.get('large_files'):
        add('large_files', f"{len(b['large_files'])} file(s) over {checks.LARGE_FILE_KB} KB.",
            [f"{x['url']} ({x['kb']} KB)" for x in b['large_files'][:10]])

    sec = res['security']
    missing = [h for h, ok in sec['headers'].items() if not ok]
    if len(missing) >= 3:
        g = f" Free outside scan grade: {sec['observatory']['grade']}." if sec.get('observatory') else ''
        add('security_headers', f"Missing: {', '.join(missing)}.{g}")

    fr = res['freshness']
    if fr['days_since_newest'] is not None and fr['days_since_newest'] > 180:
        add('stale_content', f"Newest date found on the site: {fr['newest_signal']} ({fr['days_since_newest']} days ago).")
    if fr['copyright_year'] and fr['copyright_year'] < fr['current_year']:
        add('copyright_old', f"Footer shows {fr['copyright_year']}.")
    c = res.get('contact', {})
    if c.get('phone_on_home') is False:
        add('phone_not_on_home', 'The phone number you gave wasn\'t found in the homepage text.')
    if c.get('street_on_home') is False:
        add('street_not_on_home', 'The street address you gave wasn\'t found in the homepage text.')
    return sort(out)


def sort(items):
    return sorted(items, key=lambda x: (x['priority'], x['id']))


def gbp_reminder(today=None):
    lib = library()
    today = today or date.today()
    r = lib['gbp_reminders'][today.isocalendar()[1] % len(lib['gbp_reminders'])]
    return {'topic': r['topic'], 'text': r['text'], 'help': lib['gbp_help']}


def compare(now_issues, prev_issues):
    a = {i['id'] for i in now_issues}
    b = {i['id'] for i in (prev_issues or [])}
    return {'new': sorted(a - b), 'fixed': sorted(b - a), 'still_open': sorted(a & b)}


def status(items):
    if any(i['priority'] == 1 for i in items):
        return 'Urgent'
    if any(i['priority'] == 2 for i in items):
        return 'Needs attention'
    return 'Looking good'


def _files(n):
    return '1 file' if n == 1 else f'{n} files'


def _size(kb):
    return f'{kb} KB' if kb < 1024 else f'{kb / 1024:.1f} MB'


def summary(res, items, prev_items=None, today=None):
    lib = library()
    pr = lib['priorities']
    L = [f"Website Checkup for {res['site']}: {status(items)}"]
    up = res['uptime']
    t = res.get('tls') or {}
    dm = res.get('domain') or {}
    if dm.get('dns_error') == 'no such domain':
        L.append("Up: no, the domain doesn't point anywhere")
        t = {}
    elif up['up']:
        L.append(f"Up: yes, responded in {up['response_ms']} ms")
    elif 'certificate' in (up.get('error') or ''):
        L.append('Up: the server answers, but browsers show visitors a security warning instead of the site')
    else:
        L.append(f"Up: no ({up.get('error') or up.get('status')})")
    if t.get('days_left') is not None:
        if t['days_left'] < 0:
            L.append(f"Certificate: expired on {t['not_after'][:10]}")
        else:
            L.append(f"Certificate: expires {t['not_after'][:10]} ({t['days_left']} days)")
    if t.get('error'):
        L.append('Certificate: ' + t['error'].replace('certificate problem: ', 'problem, '))
    if dm.get('expires'):
        L.append(f"Domain: registered until {dm['expires']}")
    if 'crawl' in res:
        L.append(f"Pages checked: {res['crawl']['pages_checked']}; links checked: {res['links']['checked']} of {res['links']['total_found']}")
        b = res['speed']['basic']
        L.append(f"Speed: homepage and its {_files(b['files_loaded'])} downloaded in {b['load_ms'] / 1000:.1f} s "
                 f"({_size(b['page_kb'])}), first byte in {b['ttfb_ms']} ms")
    if prev_items is not None:
        c = compare(items, prev_items)
        L.append(f"Since last week: {len(c['fixed'])} fixed, {len(c['new'])} new, {len(c['still_open'])} still open")
    counts = {p: sum(1 for i in items if str(i['priority']) == p) for p in pr}
    L.append('Fix list: ' + ', '.join(f"{counts[p]} {pr[p].split(':')[0]}" for p in pr))
    for i in items[:5]:
        L.append(f"{pr[str(i['priority'])].split(':')[0]}: {i['title']}. {i['evidence']}")
    skipped = []
    if dm.get('rdap_error') and not dm.get('dns_error'):
        skipped.append('domain renewal date (' + dm['rdap_error'] + ')')
    if up.get('http_note'):
        skipped.append('http to https redirect (' + up['http_note'] + ')')
    if 'crawl' in res and res['speed']['basic'].get('files_failed'):
        skipped.append(f"{res['speed']['basic']['files_failed']} homepage files that didn't download for the speed check")
    if 'crawl' in res and res['links']['total_found'] > res['links']['checked']:
        skipped.append(f"{res['links']['total_found'] - res['links']['checked']} links over this week's limit")
    unchecked = [p for p in res.get('links', {}).get('problems', []) if p['verdict'] == 'unchecked']
    ext = [p for p in unchecked if p['kind'] == 'external']
    own = [p for p in unchecked if p['kind'] == 'internal']
    if ext:
        skipped.append(f'{len(ext)} outside links that block automated checks')
    if own:
        skipped.append(f"{len(own)} of your own pages that dropped the connection this time ({', '.join(p['url'] for p in own[:3])})")
    if skipped:
        L.append("Couldn't check this week: " + '; '.join(skipped))
    g = gbp_reminder(today)
    L.append(f"Google Business Profile reminder ({g['topic']}): {g['text']}")
    return '\n'.join(L)
