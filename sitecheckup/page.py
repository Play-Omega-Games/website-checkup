"""HTML facts from one page."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urldefrag

YEAR_RE = re.compile(r'(?:\u00a9|&copy;|\(c\)|copyright)\s*(?:\d{4}\s*[-\u2013]\s*)?(\d{4})', re.I)


class _P(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base = base
        self.title = None
        self._in = None
        self.meta = {}
        self.links = []
        self.imgs = []
        self.scripts = []
        self.styles = []
        self.h1 = []
        self.lang = None
        self.canonical = None
        self.icons = []
        self.jsonld = []
        self.text = []
        self._skip = 0
        self._buf = ''

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or '') for k, v in attrs}
        if tag == 'html':
            self.lang = a.get('lang') or None
        elif tag == 'title' and self.title is None:
            self._in, self._buf = 'title', ''
        elif tag == 'h1':
            self._in, self._buf = 'h1', ''
        elif tag == 'meta':
            key = (a.get('name') or a.get('property') or a.get('http-equiv') or '').lower()
            if key:
                self.meta[key] = a.get('content', '')
        elif tag == 'a' and a.get('href'):
            self.links.append(a['href'].strip())
        elif tag == 'img':
            self.imgs.append({'src': a.get('src', ''), 'alt': a.get('alt')})
        elif tag == 'script':
            if a.get('src'):
                self.scripts.append(a['src'])
            if a.get('type', '').lower() == 'application/ld+json':
                self._in, self._buf = 'jsonld', ''
            self._skip += 1
        elif tag == 'style':
            self._skip += 1
        elif tag == 'link':
            rel = a.get('rel', '').lower()
            if 'stylesheet' in rel and a.get('href'):
                self.styles.append(a['href'])
            if rel == 'canonical':
                self.canonical = a.get('href')
            if 'icon' in rel:
                self.icons.append(a.get('href'))

    def handle_endtag(self, tag):
        if tag == 'title' and self._in == 'title':
            self.title = ' '.join(self._buf.split())
            self._in = None
        elif tag == 'h1' and self._in == 'h1':
            self.h1.append(' '.join(self._buf.split()))
            self._in = None
        elif tag == 'script':
            if self._in == 'jsonld':
                self.jsonld.append(self._buf)
                self._in = None
            self._skip = max(0, self._skip - 1)
        elif tag == 'style':
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._in:
            self._buf += data
        if not self._skip:
            self.text.append(data)


def parse(html, base):
    p = _P(base)
    try:
        p.feed(html)
    except Exception:  # noqa: BLE001
        pass
    text = ' '.join(' '.join(p.text).split())
    links = []
    for h in p.links:
        if h.startswith(('mailto:', 'tel:', 'javascript:', '#', 'sms:', 'data:')):
            continue
        links.append(urldefrag(urljoin(base, h))[0])
    years = [int(y) for y in YEAR_RE.findall(html)]
    jsonld_types = re.findall(r'"@type"\s*:\s*"([^"]+)"', ' '.join(p.jsonld))
    return {
        'title': p.title, 'meta_description': p.meta.get('description'), 'robots_meta': p.meta.get('robots', ''),
        'viewport': p.meta.get('viewport'), 'og_title': p.meta.get('og:title'), 'og_image': p.meta.get('og:image'),
        'lang': p.lang, 'canonical': urljoin(base, p.canonical) if p.canonical else None, 'h1': p.h1,
        'links': links, 'imgs': [dict(i, src=urljoin(base, i['src'])) for i in p.imgs if i['src']],
        'scripts': [urljoin(base, s) for s in p.scripts], 'styles': [urljoin(base, s) for s in p.styles],
        'icons': [urljoin(base, i) for i in p.icons if i], 'jsonld_types': jsonld_types,
        'copyright_years': years, 'text': text[:200000], 'words': len(text.split()),
    }
