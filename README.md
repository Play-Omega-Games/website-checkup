# Website Checkup

A strictly read-only weekly checkup for a small business website, for the 🛠️ Website Checkup Grok Bot template by Omega Games. It looks at the public site the way a visitor or search engine would. It never logs in, submits forms or changes anything.

## What it checks

- Up or down, response time, and whether http:// moves to https://
- Security certificate: valid, and days until it expires
- Domain: still resolves, and its registration end date (public registry data)
- Broken links, inside the site and out
- Search basics: titles, descriptions, headings, phone-friendly setup, language, image alt text, noindex, sitemap and robots.txt
- Speed: a basic load check it measures itself, with no key or outside service: time to first byte, how long the homepage and the files it needs (scripts, styles and images) take to download, total page weight, compression and very large files
- Security settings (common response headers, plus a free outside scan grade)
- Freshness: newest date found and the footer year
- Optional: the owner's phone and street address on the homepage

Every finding becomes a prioritized fix (Critical, High, Medium, Low) with plain-English steps from `data/fixes.json`.

## Quick start

```
python3 -m sitecheckup check --url example.com --out ./state
python3 -m sitecheckup check --url example.com --out ./state --business business.json
python3 tests/test_sitecheckup.py
```

Only GET and HEAD requests go to the site, about one per second, identified as `OmegaWebsiteCheckup/1.0`. It honors robots.txt and checks at most 25 pages and 150 links by default.

## Files

- `sitecheckup/`: the checker (standard library only).
- `data/fixes.json`: the fix guides, built from `scripts/fixes_src.py` by `scripts/build_data.py`.
- `tests/`: offline tests against a small local sample site.

## License

Content is CC0 1.0. Code is MIT. See `LICENSE`.

Questions or ideas? Contact Omega Games at john@playomega.games.
