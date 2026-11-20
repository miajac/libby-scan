#!/usr/bin/env python3
"""
libby_scan.py - check your Goodreads "Want to Read" shelf against an
OverDrive/Libby library (default: Alaska Digital Library, adl.overdrive.com).

Usage
-----
1. Export your Goodreads library:
     Goodreads -> My Books -> Import and export (left sidebar)
     -> "Export Library", then download goodreads_library_export.csv
2. Run:
     python3 libby_scan.py goodreads_library_export.csv

   Options:
     --library adl          OverDrive library key(s); repeat for several
                            (e.g. --library adl --library anchorage)
     --shelf to-read        Goodreads shelf to scan (default: to-read)
     --format ebook         only ebooks / only audiobooks (default: both)
     --out results          output basename -> results.csv + results.html
     --workers 4            parallel requests (be polite; keep it small)

Output
------
- Table printed in the terminal (available now first, then shortest wait)
- results.csv   for sorting/filtering in a spreadsheet
- results.html  sortable page with links straight to each title

Only uses the Python standard library (3.8+). No login needed: it reads the
same public catalog data Libby shows before you sign in.
"""

import argparse
import csv
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

API = "https://thunder.api.overdrive.com/v2/libraries/{lib}/media"
UA = "libby-scan/1.0 (personal to-read checker)"
FORMATS = ("ebook", "audiobook")


# ---------------------------------------------------------------- text utils
def norm(s):
    """Lowercase, strip accents/punctuation/articles for fuzzy comparison."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"^(the|a|an) ", "", s.strip())
    return re.sub(r"\s+", " ", s).strip()


def clean_title(t):
    """'Dune (Dune, #1)' -> 'Dune';  'Sapiens: A Brief History' -> 'Sapiens'."""
    t = re.sub(r"\s*\([^)]*#\s*[\d.]+[^)]*\)\s*$", "", t or "")  # series tag
    t = re.sub(r"\s*\([^)]*\)\s*$", "", t)                         # other trailing ()
    return t.strip()


def short_title(t):
    return re.split(r"\s*[:—–]\s*| - ", clean_title(t), maxsplit=1)[0].strip()


def author_last(a):
    parts = [w for w in norm(a).split() if w not in ("jr", "sr", "ii", "iii", "iv", "phd", "md")]
    return parts[-1] if parts else ""


def title_score(gr_title, od_title):
    a, b = norm(short_title(gr_title)), norm(short_title(od_title))
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


# ---------------------------------------------------------------- goodreads
def read_goodreads(path, shelf):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows or "Exclusive Shelf" not in rows[0]:
        sys.exit("That doesn't look like a Goodreads export CSV "
                 "(no 'Exclusive Shelf' column).")
    books = []
    for r in rows:
        shelves = {r.get("Exclusive Shelf", "").strip()}
        shelves |= {s.strip() for s in (r.get("Bookshelves") or "").split(",") if s.strip()}
        if shelf not in shelves:
            continue
        isbn13 = re.sub(r"[^0-9Xx]", "", r.get("ISBN13", ""))
        books.append({
            "title": r.get("Title", "").strip(),
            "author": r.get("Author", "").strip(),
            "isbn13": isbn13,
            "goodreads_id": r.get("Book Id", "").strip(),
            "date_added": r.get("Date Added", "").strip(),
        })
    return books


# ---------------------------------------------------------------- overdrive
def od_search(lib, query, retries=3):
    params = urllib.parse.urlencode({"query": query, "perPage": 24})
    url = f"{API.format(lib=lib)}?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r).get("items", [])
        except urllib.error.HTTPError as e:
            if e.code == 404:
                sys.exit(f"Library key '{lib}' not found on OverDrive.")
            if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    return []


def creators_text(item):
    names = [item.get("firstCreatorName", "")]
    names += [c.get("name", "") for c in item.get("creators", [])
              if c.get("role", "").lower() in ("author", "editor", "")]
    return norm(" ".join(names))


def best_matches(book, items):
    """Return {format: (score, item)} for the best match of each format."""
    last = author_last(book["author"])
    best = {}
    for it in items:
        fmt = (it.get("type") or {}).get("id")
        if fmt not in FORMATS or not it.get("isOwned", True):
            continue
        ts = title_score(book["title"], it.get("title", ""))
        if last and last not in creators_text(it):
            continue                      # author must match
        if ts < 0.82:
            continue
        # tie-breakers: shorter wait, more copies
        score = (ts, it.get("isAvailable", False),
                 -(it.get("estimatedWaitDays") or 9999), it.get("ownedCopies", 0))
        if fmt not in best or score > best[fmt][0]:
            best[fmt] = (score, it)
    return best


def check_book(book, libs):
    results = []
    queries = [f"{short_title(book['title'])} {author_last(book['author'])}".strip()]
    if book["isbn13"]:
        queries.insert(0, book["isbn13"])
    for lib in libs:
        found = {}
        for q in queries:
            try:
                items = od_search(lib, q)
            except Exception as e:  # network trouble on one title shouldn't kill the run
                print(f"  ! {book['title']!r} ({lib}): {e}", file=sys.stderr)
                items = []
            for fmt, val in best_matches(book, items).items():
                if fmt not in found or val[0] > found[fmt][0]:
                    found[fmt] = val
            if len(found) == len(FORMATS):
                break                     # ISBN hit both formats; skip title search
        for fmt, (_, it) in found.items():
            results.append({
                "title": book["title"],
                "author": book["author"],
                "library": lib,
                "format": fmt,
                "available_now": bool(it.get("isAvailable")),
                "available_copies": it.get("availableCopies", 0),
                "owned_copies": it.get("ownedCopies", 0),
                "holds": it.get("holdsCount", 0),
                "est_wait_days": 0 if it.get("isAvailable") else it.get("estimatedWaitDays"),
                "od_title": it.get("title", ""),
                "link": f"https://{lib}.overdrive.com/media/{it.get('id')}",
                "goodreads_link": f"https://www.goodreads.com/book/show/{book['goodreads_id']}",
                "date_added": book["date_added"],
            })
    return book, results


# ---------------------------------------------------------------- output
def sort_key(r):
    wait = r["est_wait_days"]
    return (not r["available_now"], wait if wait is not None else 10**6, r["title"].lower())


def write_csv(rows, path):
    cols = ["title", "author", "format", "library", "available_now", "est_wait_days",
            "holds", "available_copies", "owned_copies", "od_title",
            "link", "goodreads_link", "date_added"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def write_html(rows, missing, path, libs):
    def esc(x):
        return html.escape(str(x if x is not None else ""))

    trs = []
    for r in rows:
        status = ("Available now" if r["available_now"]
                  else f"~{r['est_wait_days']} days" if r["est_wait_days"] is not None
                  else "Hold")
        cls = "now" if r["available_now"] else ("soon" if (r["est_wait_days"] or 999) <= 14 else "")
        trs.append(
            f'<tr class="{cls}" data-fmt="{esc(r["format"])}">'
            f'<td><a href="{esc(r["link"])}" target="_blank">{esc(r["title"])}</a></td>'
            f'<td>{esc(r["author"])}</td><td>{esc(r["format"])}</td>'
            f'<td data-v="{0 if r["available_now"] else (r["est_wait_days"] if r["est_wait_days"] is not None else 99999)}">{esc(status)}</td>'
            f'<td data-v="{r["holds"]}">{esc(r["holds"])}</td>'
            f'<td>{esc(r["available_copies"])}/{esc(r["owned_copies"])}</td>'
            f'<td><a href="{esc(r["goodreads_link"])}" target="_blank">Goodreads</a></td></tr>')
    miss = "".join(f"<li>{esc(b['title'])} — {esc(b['author'])}</li>" for b in missing)
    n_now = len({r["title"] for r in rows if r["available_now"]})
    page = f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Want to Read × {esc(', '.join(libs))}</title><style>
body{{font:15px/1.45 system-ui,sans-serif;margin:2rem auto;max-width:1100px;padding:0 1rem;color:#222}}
h1{{font-size:1.4rem;margin-bottom:.2rem}} .sub{{color:#666;margin-bottom:1rem}}
table{{border-collapse:collapse;width:100%}} th,td{{padding:.45rem .6rem;border-bottom:1px solid #e5e5e5;text-align:left}}
th{{cursor:pointer;user-select:none;background:#f6f6f6;position:sticky;top:0}} th:hover{{background:#eee}}
tr.now td:nth-child(4){{color:#0a7a2f;font-weight:600}} tr.soon td:nth-child(4){{color:#9a6a00}}
a{{color:#1a5fb4;text-decoration:none}} a:hover{{text-decoration:underline}}
.ctl{{margin:.8rem 0}} .ctl label{{margin-right:1rem}} details{{margin-top:2rem}}
</style></head><body>
<h1>Want to Read × {esc(', '.join(libs))}</h1>
<div class="sub">{n_now} titles available right now · {len({r['title'] for r in rows})} in the catalog ·
{len(missing)} not found · scanned {time.strftime('%Y-%m-%d %H:%M')}</div>
<div class="ctl"><label><input type="checkbox" id="eb" checked> ebooks</label>
<label><input type="checkbox" id="ab" checked> audiobooks</label>
<label><input type="checkbox" id="on"> available now only</label></div>
<table id="t"><thead><tr><th>Title</th><th>Author</th><th>Format</th><th>Status</th>
<th>Holds</th><th>Copies free/owned</th><th>Links</th></tr></thead><tbody>
{''.join(trs)}</tbody></table>
<details><summary>Not found in the catalog ({len(missing)})</summary><ul>{miss}</ul></details>
<script>
const t=document.getElementById('t'),tb=t.tBodies[0];
function filt(){{const eb=eb_.checked,ab=ab_.checked,on=on_.checked;
 for(const r of tb.rows){{const f=r.dataset.fmt;
  r.style.display=((f==='ebook'&&eb)||(f==='audiobook'&&ab))&&(!on||r.classList.contains('now'))?'':'none';}}}}
const eb_=document.getElementById('eb'),ab_=document.getElementById('ab'),on_=document.getElementById('on');
[eb_,ab_,on_].forEach(x=>x.onchange=filt);
t.tHead.querySelectorAll('th').forEach((th,i)=>{{let asc=true;th.onclick=()=>{{
 const rows=[...tb.rows],num=rows.every(r=>r.cells[i].dataset.v!==undefined);
 rows.sort((a,b)=>{{const x=num?+a.cells[i].dataset.v:a.cells[i].innerText.toLowerCase(),
   y=num?+b.cells[i].dataset.v:b.cells[i].innerText.toLowerCase();return (x>y?1:x<y?-1:0)*(asc?1:-1);}});
 asc=!asc;rows.forEach(r=>tb.appendChild(r));}};}});
</script></body></html>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)


def print_table(rows):
    if not rows:
        print("No matches found.")
        return
    print(f"\n{'STATUS':<14}{'FMT':<11}{'HOLDS':>6}  TITLE — AUTHOR")
    print("-" * 80)
    for r in rows:
        st = "NOW" if r["available_now"] else (
            f"~{r['est_wait_days']}d" if r["est_wait_days"] is not None else "hold")
        print(f"{st:<14}{r['format']:<11}{r['holds']:>6}  {r['title'][:45]} — {r['author'][:22]}")


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", help="goodreads_library_export.csv")
    ap.add_argument("--library", action="append", default=None,
                    help="OverDrive library key (default: adl). Repeatable.")
    ap.add_argument("--shelf", default="to-read")
    ap.add_argument("--format", choices=FORMATS, default=None)
    ap.add_argument("--out", default="libby_scan_results")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    libs = a.library or ["adl"]

    books = read_goodreads(a.csv, a.shelf)
    if not books:
        sys.exit(f"No books on shelf '{a.shelf}' in {a.csv}.")
    print(f"Checking {len(books)} books from '{a.shelf}' against {', '.join(libs)}...")

    rows, missing, done = [], [], 0
    with ThreadPoolExecutor(max_workers=max(1, min(a.workers, 8))) as ex:
        for book, res in ex.map(lambda b: check_book(b, libs), books):
            done += 1
            print(f"\r  {done}/{len(books)}", end="", file=sys.stderr, flush=True)
            if a.format:
                res = [r for r in res if r["format"] == a.format]
            rows.extend(res) if res else missing.append(book)
    print(file=sys.stderr)

    rows.sort(key=sort_key)
    print_table(rows)
    write_csv(rows, a.out + ".csv")
    write_html(rows, missing, a.out + ".html", libs)
    n_now = len({r["title"] for r in rows if r["available_now"]})
    print(f"\n{n_now} available now · {len({r['title'] for r in rows})} in catalog · "
          f"{len(missing)} not found")
    print(f"Wrote {a.out}.csv and {a.out}.html")


if __name__ == "__main__":
    main()