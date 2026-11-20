# libby-scan

Check which books on your Goodreads **Want to Read** shelf are available from your
OverDrive/Libby library, without RSS feeds or browser extensions.

Goodreads retired its RSS feeds in November 2026, which broke tools like the
Library Scan Chrome extension. This script works from a Goodreads library export
instead, then looks each book up in your library's public OverDrive catalog.

Defaults to the [Alaska Digital Library](https://adl.overdrive.com) (`adl`), but
works with any OverDrive library.

## What you get

- A table in your terminal, with books **available now** first and the rest sorted by estimated wait
- `libby_scan_results.html`: a sortable page you can filter by ebook, audiobook, or available now, with each title linked to its library page (which has an "open in Libby" button)
- `libby_scan_results.csv`: the same data for a spreadsheet
- A list of shelf books your library doesn't carry

## Requirements

Python 3.8 or newer. No packages to install.

## Usage

### 1. Export your Goodreads library

On Goodreads, go to **My Books**, click **Import and export** in the left sidebar,
click **Export Library**, then download `goodreads_library_export.csv` when it's ready.

Re-export whenever you want a fresh scan that includes books you've added since.

### 2. Run the scan

```bash
python3 libby_scan.py goodreads_library_export.csv
```

Then open `libby_scan_results.html` in your browser.

### Options

| Option | What it does | Default |
|---|---|---|
| `--library KEY` | OverDrive library key. Repeat it to check several libraries. | `adl` |
| `--shelf NAME` | Goodreads shelf to scan | `to-read` |
| `--format ebook` / `audiobook` | Check one format only | both |
| `--out NAME` | Base name for the output files | `libby_scan_results` |
| `--workers N` | Number of parallel requests (keep it small to be polite) | `4` |

Examples:

```bash
# Audiobooks only
python3 libby_scan.py goodreads_library_export.csv --format audiobook

# A custom shelf, checked against two libraries
python3 libby_scan.py goodreads_library_export.csv --shelf summer-reading --library adl --library otherlibrary
```

**Finding your library key:** it's the subdomain of your library's OverDrive
site. For `adl.overdrive.com` the key is `adl`.

## How matching works

The script searches by ISBN first, then by title plus the author's last name. A
result counts as a match only if the author matches and the titles are close
after dropping series tags and subtitles. So "Dune (Dune, #1)" matches *Dune* but
not *Dune Messiah*. Ebooks and audiobooks are reported separately.

If a book you know is in the catalog shows up as "not found," the Goodreads title
is probably very different from the library's (a different edition name, for
example). Search for it in Libby directly.

## Privacy

The script needs no login. It reads the same public catalog data Libby shows
before you sign in, and your Goodreads export never leaves your computer. The
included `.gitignore` keeps your export and results out of the repo.

## Disclaimer

This is a personal tool and is not affiliated with Goodreads, OverDrive, or Libby.
It uses OverDrive's unofficial public catalog API, which could change without notice.
