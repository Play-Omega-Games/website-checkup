"""Offline tests for Website Checkup, against a tiny local fixture site. Run: python3 tests/test_sitecheckup.py"""
import functools
import http.server
import json
import re
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from sitecheckup import checks, net, report  # noqa: E402
from sitecheckup.page import parse  # noqa: E402

net.GAP_SCALE = 0
DASHES = ('\u2014', '\u2013')


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve():
    tmp = Path(tempfile.mkdtemp())
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(tmp)))
    port = srv.server_address[1]
    for f in (ROOT / 'tests' / 'fixture').iterdir():
        data = f.read_bytes().replace(b'PORT', str(port).encode())
        (tmp / f.name).write_bytes(data)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f'http://127.0.0.1:{port}/', tmp


class Fixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv, cls.site, cls.tmp = serve()
        home = net.fetch(cls.site, gap=0)
        cls.rp, cls.rinfo = net.robots(cls.site)
        cls.sm = checks.sitemap_urls(cls.site, cls.rinfo)
        cls.pages = checks.crawl(cls.site, home, cls.rp, cls.sm, 10)
        cls.links = checks.check_links(cls.site, cls.pages, cls.rp, 50)
        cls.seo = checks.check_seo(cls.site, cls.pages, cls.rinfo, cls.sm)
        cls.fresh = checks.check_freshness(cls.pages, cls.sm, date(2026, 11, 17))
        cls.contact = checks.check_contact(cls.pages, home['final_url'], {'phone': '555-123-4567', 'street': '12 Elm St'})
        cls.res = {'site': cls.site, 'uptime': {'up': True, 'status': 200, 'response_ms': 5, 'http_redirects_to_https': None,
                                                 'alt_host_ok': True, 'alt_host': 'x'},
                   'tls': {}, 'crawl': {'pages_checked': len(cls.pages), 'cap': 10, 'sitemap_urls': cls.sm['count']},
                   'links': cls.links, 'seo': cls.seo,
                   'speed': checks.check_speed(cls.site, home, cls.pages),
                   'security': {'headers': {h: False for h in checks.SEC_HEADERS}, 'observatory': None},
                   'freshness': cls.fresh, 'contact': cls.contact}
        cls.items = report.issues(cls.res)
        cls.ids = {i['id'] for i in cls.items}

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp)

    def test_sitemap(self):
        self.assertEqual(self.sm['count'], 2)
        self.assertEqual(self.sm['newest_lastmod'], '2024-02-01')

    def test_crawl(self):
        self.assertIn(self.site + 'about.html', self.pages)
        self.assertIn(self.site + 'hidden.html', self.pages)

    def test_broken_internal(self):
        bad = [p['url'] for p in self.links['problems'] if p['verdict'] == 'broken']
        self.assertEqual(bad, [self.site + 'missing.html'])
        self.assertIn('broken_internal', self.ids)

    def test_seo(self):
        home = self.seo['pages'][0]
        for tag in ('short_title', 'missing_description', 'multiple_h1', 'missing_lang', 'images_missing_alt'):
            self.assertIn(tag, home['issues'])
        self.assertEqual(home['images_missing_alt'], [self.site + 'cake.jpg'])
        about = next(p for p in self.seo['pages'] if p['url'].endswith('about.html'))
        self.assertEqual(about['issues'], [])
        self.assertIn('Hi', self.seo['duplicate_titles'])
        self.assertTrue(self.seo['robots_txt'])
        self.assertFalse(self.seo['robots_blocks_all'])
        self.assertTrue(self.seo['has_favicon'])

    def test_noindex_page_not_home(self):
        self.assertIn('noindex_page', self.ids)
        self.assertNotIn('noindex_home', self.ids)

    def test_freshness(self):
        self.assertEqual(self.fresh['copyright_year'], 2023)
        self.assertEqual(self.fresh['newest_signal'], '2024-03-02')
        self.assertIn('stale_content', self.ids)
        self.assertIn('copyright_old', self.ids)

    def test_contact(self):
        self.assertTrue(self.contact['phone_on_home'])
        self.assertFalse(self.contact['street_on_home'])
        self.assertIn('street_not_on_home', self.ids)
        self.assertNotIn('phone_not_on_home', self.ids)

    def test_sorted_and_complete(self):
        pr = [i['priority'] for i in self.items]
        self.assertEqual(pr, sorted(pr))
        for i in self.items:
            self.assertTrue(i['title'] and i['why'] and i['how'] and i['evidence'])

    def test_speed_measured(self):
        b = self.res['speed']['basic']
        self.assertEqual(self.res['speed']['source'], 'basic')
        self.assertGreaterEqual(b['files_found'], 2)
        self.assertEqual(b['files_loaded'] + b['files_failed'], b['files_found'])
        self.assertGreater(b['page_kb'] * 1024 + 1024, b['html_kb'] * 1024)
        self.assertGreaterEqual(b['load_ms'], b['home_ms'])
        self.assertIsNotNone(b['ttfb_ms'])
        self.assertNotIn('slow_load', self.ids)

    def test_speed_rules(self):
        def ids_for(**kw):
            b = {'ttfb_ms': 200, 'home_ms': 300, 'load_ms': 900, 'page_kb': 800, 'files_loaded': 5,
                 'compressed': True, 'large_files': []}
            b.update(kw)
            res = dict(self.res, speed={'source': 'basic', 'basic': b})
            return {i['id'] for i in report.issues(res)}
        self.assertFalse(ids_for() & {'slow_load', 'heavy_page', 'slow_ttfb', 'not_compressed'})
        self.assertIn('slow_load', ids_for(load_ms=checks.SLOW_LOAD_MS + 1))
        self.assertIn('heavy_page', ids_for(page_kb=checks.HEAVY_PAGE_KB + 1))
        self.assertIn('slow_ttfb', ids_for(ttfb_ms=checks.SLOW_FIRST_BYTE_MS + 1))
        self.assertIn('not_compressed', ids_for(compressed=False))
        s = report.summary(dict(self.res, speed={'source': 'basic', 'basic': dict(
            ttfb_ms=200, home_ms=300, load_ms=4200, page_kb=2048, files_loaded=7, files_failed=0,
            compressed=True, large_files=[])}), [], None, date(2026, 11, 17))
        self.assertIn('Speed: homepage and its 7 files downloaded in 4.2 s (2.0 MB), first byte in 200 ms', s)
        self.assertNotIn("Couldn't check this week", s)
        s = report.summary(dict(self.res, speed={'source': 'basic', 'basic': dict(
            ttfb_ms=90, home_ms=100, load_ms=150, page_kb=12, files_loaded=1, files_failed=0,
            compressed=True, large_files=[])}), [], None, date(2026, 11, 17))
        self.assertIn('Speed: homepage and its 1 file downloaded in 0.1 s (12 KB), first byte in 90 ms', s)
        s = report.summary(dict(self.res, speed={'source': 'basic', 'basic': dict(
            ttfb_ms=200, home_ms=300, load_ms=900, page_kb=500, files_loaded=5, files_failed=2,
            compressed=True, large_files=[])}), [], None, date(2026, 11, 17))
        self.assertIn("Couldn't check this week: 2 homepage files that didn't download for the speed check", s)

    def test_summary(self):
        s = report.summary(self.res, self.items, [], date(2026, 11, 17))
        self.assertIn('Needs attention', s)
        self.assertIn('Google Business Profile reminder', s)
        self.assertTrue(s.startswith('Website Checkup for '))
        self.assertNotIn('speed ' + 'score', s.lower())
        for d in DASHES:
            self.assertNotIn(d, s)


class Units(unittest.TestCase):
    def test_blocks_all(self):
        self.assertTrue(net._blocks_all('User-agent: *\nDisallow: /\n'))
        self.assertFalse(net._blocks_all('User-agent: *\nDisallow: /admin\n'))
        self.assertFalse(net._blocks_all('User-agent: BadBot\nDisallow: /\n'))

    def test_parse(self):
        f = parse('<html lang="en"><head><title> A  B </title><meta name="description" content="d">'
                  '<script type="application/ld+json">{"@type": "Bakery"}</script></head>'
                  '<body><a href="/x#top">x</a><a href="tel:1">t</a><p>&copy; 2019-2025 Co</p></body></html>', 'https://s.test/')
        self.assertEqual(f['title'], 'A B')
        self.assertEqual(f['links'], ['https://s.test/x'])
        self.assertEqual(f['copyright_years'], [2025])
        self.assertEqual(f['jsonld_types'], ['Bakery'])

    def test_down_site(self):
        res = {'site': 'https://down.test/', 'uptime': {'up': False, 'error': 'domain not found (DNS)'}, 'tls': {}, 'skipped': 'x'}
        items = report.issues(res)
        self.assertEqual([i['id'] for i in items], ['domain_not_found'])
        self.assertEqual(report.status(items), 'Urgent')

    def test_cert_rules(self):
        base = {'site': 'https://s.test/', 'uptime': {'up': False, 'error': 'x'}, 'skipped': 'x'}
        for days, key in ((5, 'cert_expiring_14'), (20, 'cert_expiring_30')):
            ids = {i['id'] for i in report.issues(dict(base, tls={'days_left': days, 'not_after': '2026-12-01T00:00:00'}))}
            self.assertIn(key, ids)
        ids = {i['id'] for i in report.issues(dict(base, tls={'days_left': 60, 'not_after': '2027-01-01T00:00:00'}))}
        self.assertFalse(ids & {'cert_expiring_14', 'cert_expiring_30'})
        ids = {i['id'] for i in report.issues(dict(base, tls={'error': 'certificate problem: certificate has expired', 'days_left': -3, 'not_after': '2026-11-01T00:00:00'}))}
        self.assertIn('cert_invalid', ids)

    def test_registrable(self):
        self.assertEqual(checks.registrable('www.rosasbakery.com'), 'rosasbakery.com')
        self.assertEqual(checks.registrable('shop.rosas.co.uk'), 'rosas.co.uk')

    def test_domain_rules(self):
        base = {'site': 'https://s.test/', 'uptime': {'up': False, 'error': 'x'}, 'skipped': 'x', 'tls': {}}
        ids = {i['id'] for i in report.issues(dict(base, domain={'days_left': 12, 'expires': '2026-11-29'}))}
        self.assertIn('domain_expiring_30', ids)
        ids = {i['id'] for i in report.issues(dict(base, domain={'days_left': 45, 'expires': '2026-12-31'}))}
        self.assertIn('domain_expiring_60', ids)
        ids = {i['id'] for i in report.issues(dict(base, domain={'dns_error': 'no such domain', 'host': 's.test'}))}
        self.assertIn('domain_not_found', ids)
        self.assertNotIn('site_down', ids)

    def test_internal_connection_drop_is_unchecked(self):
        import types
        calls = []
        def fake(url, method='GET', **kw):
            calls.append(url)
            return {'status': None, 'error': 'the connection closed before the page loaded', 'ok': False}
        orig, orig_sleep = net.fetch, checks.time.sleep
        net.fetch, checks.time.sleep = fake, (lambda s: None)
        try:
            pages = {'https://s.test/': {'status': 200, 'error': None, 'facts': {'links': ['https://s.test/a'], 'imgs': []}},
                     'https://s.test/a': {'status': None, 'error': 'the connection closed before the page loaded', 'facts': None}}
            rp = types.SimpleNamespace(can_fetch=lambda ua, u: True)
            res = checks.check_links('https://s.test/', pages, rp, 10)
        finally:
            net.fetch, checks.time.sleep = orig, orig_sleep
        self.assertGreaterEqual(len(calls), 3)  # retried, not trusted from the crawl
        self.assertEqual([p['verdict'] for p in res['problems']], ['unchecked'])

    def test_compare(self):
        c = report.compare([{'id': 'a'}, {'id': 'b'}], [{'id': 'b'}, {'id': 'c'}])
        self.assertEqual(c, {'new': ['a'], 'fixed': ['c'], 'still_open': ['b']})

    def test_gbp_rotates(self):
        from datetime import timedelta
        seen = {report.gbp_reminder(date(2026, 11, 2) + timedelta(weeks=i))['topic'] for i in range(6)}
        self.assertEqual(len(seen), 6)


class Library(unittest.TestCase):
    def test_no_outside_speed_api(self):
        for f in ROOT.rglob('*'):
            if f.is_file() and '.git' not in f.parts and f.suffix in ('.py', '.md', '.json', '.txt'):
                low = f.read_text(errors='ignore').lower()
                for word in ('page' + 'speed', 'google' + 'apis', 'psi' + '_', 'psi' + '-key', 'light' + 'house', 'speed ' + 'score'):
                    self.assertNotIn(word, low, f)

    def test_name_and_slug(self):
        readme = (ROOT / 'README.md').read_text()
        self.assertIn('Website Checkup', readme)
        self.assertIn('Play-Omega-Games/website-checkup', net.UA)
        for f in ROOT.rglob('*'):
            if f.is_file() and '.git' not in f.parts and f.suffix in ('.py', '.md', '.json', '.txt'):
                low = f.read_text(errors='ignore').lower()
                for old in ('website' + ' care', 'website' + '-care', 'site' + 'care'):
                    self.assertNotIn(old, low, f)

    def test_fix_library(self):
        lib = json.loads((ROOT / 'data' / 'fixes.json').read_text())
        blob = json.dumps(lib, ensure_ascii=False)
        for d in DASHES:
            self.assertNotIn(d, blob)
        for k, f in lib['fixes'].items():
            self.assertIn(f['priority'], (1, 2, 3, 4), k)
            self.assertTrue(f['how'], k)
            if f['learn_more']:
                self.assertTrue(f['learn_more'].startswith('https://'), k)

    def test_every_issue_has_fix(self):
        src = (ROOT / 'sitecheckup' / 'report.py').read_text()
        lib = json.loads((ROOT / 'data' / 'fixes.json').read_text())['fixes']
        used = set(re.findall(r"add\('([a-z_0-9]+)'", src)) | set(re.findall(r"\('[a-z_0-9]+', '([a-z_0-9]+)'\)", src))
        used |= {'missing_title', 'missing_description', 'missing_h1', 'multiple_h1', 'missing_viewport', 'missing_lang', 'mixed_content'}
        self.assertTrue(used <= set(lib), used - set(lib))

    def test_read_only(self):
        """Only GET and HEAD reach the owner's site. The single POST goes to the free outside header scan."""
        for f in (ROOT / 'sitecheckup').glob('*.py'):
            src = f.read_text()
            for m in re.finditer(r"method='([A-Z]+)'", src):
                if m.group(1) == 'POST':
                    self.assertIn('OBS', src[max(0, m.start() - 200):m.start()], f.name)
                else:
                    self.assertIn(m.group(1), ('GET', 'HEAD'), f.name)
            self.assertNotIn('Cookie', src)
            self.assertNotIn('Authorization', src)
            self.assertNotIn('PUT', re.sub(r'OUTPUT|INPUT', '', src))


class CopyrightYear(unittest.TestCase):
    def test_year_found_with_react_comments(self):
        from sitecheckup import page
        html = '<html><body><div>\u00a9 <!-- -->2026<!-- --> Omega Games. All rights reserved.</div></body></html>'
        self.assertEqual(page.parse(html, 'https://example.com/')['copyright_years'], [2026])

    def test_homepage_link_without_slash_is_one_page(self):
        from sitecheckup import page
        html = '<a href="https://example.com">Home</a><a href="https://example.com/">Home</a><a href="/about">About</a>'
        links = page.parse(html, 'https://example.com/about')['links']
        self.assertEqual(sorted(set(links)), ['https://example.com/', 'https://example.com/about'])


if __name__ == '__main__':
    unittest.main(verbosity=1)
