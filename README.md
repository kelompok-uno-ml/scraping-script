# Social Trend Scraper

A free, browser-based scraper for public TikTok and Instagram Reels metadata across six content categories. It stores raw JSON, normalizes it into a flat dataset, and validates global and per-category quotas.

> Use responsibly. Scraping may violate platform Terms of Service. This project is intended for academic and non-commercial research.

## Features

- Six categories: `kuliner`, `fashion_beauty`, `edukasi_tips`, `hiburan_komedi`, `lifestyle_travel`, `gaming`.
- TikTok collector using a real browser and XHR capture.
- Instagram Reels collector using a logged-in burner session.
- Raw JSON is saved before parsing.
- Resume support through `logs/progress.json`.
- Per-category quotas, creator caps, language filtering, and age filtering.
- Global, per-platform, and per-category validation reports.
- CSV and Parquet output.
- AI-agent friendly workflow.

## Requirements

- Python **3.10+** (3.12 recommended)
- `pip` and `venv`
- Google Chrome installed, or let `scrapling install` download Chromium
- macOS or Linux; Windows users should use WSL
- At least 4 GB RAM and 2 GB free disk space
- Stable internet connection
- An Indonesian IP is recommended so TikTok serves relevant Indonesian content
- Optional: a dedicated Instagram burner account for live Instagram scraping

No paid API or public dataset is required.

## Installation

```bash
git clone https://github.com/<your-username>/social-trend-scraper.git
cd social-trend-scraper

python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
scrapling install

cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
# Put the generated value into SCRAPE_SALT inside .env
```

## Configuration

| File | Purpose |
|---|---|
| `config/seeds.yaml` | Hashtag seeds grouped by category |
| `config/settings.yaml` | Targets, quotas, delays, caps, browser options |
| `.env` | `SCRAPE_SALT` and optional Instagram credentials |

Each seed belongs to exactly one category. A video found by multiple seeds is kept once, using the category of the first seed that captured it.

## Usage

### 1. Run tests

```bash
python -m unittest discover -s tests -v
```

### 2. TikTok pilot

```bash
python -m src.scrape_tiktok --pilot --headful
```

This runs one seed per category, one scroll, and up to 20 videos per seed. Check the logs for field completeness and block signals before scaling.

### 3. TikTok full run

```bash
python -m src.scrape_tiktok
```

Or run one category:

```bash
python -m src.scrape_tiktok --categories gaming
```

Or specific seeds:

```bash
python -m src.scrape_tiktok --seeds gamingindonesia,mobilelegends,freefire
```

Completed seeds are skipped automatically. Use `--force` to rerun them.

### 4. Instagram

Instagram requires a dedicated burner account. Log in once in a headful browser:

```bash
python -m src.scrape_instagram --login
python -m src.scrape_instagram --check
python -m src.scrape_instagram --pilot --headful
python -m src.scrape_instagram
```

The browser profile is stored in `.chrome-profile-instagram/` and is ignored by git.

### 5. Build the dataset

```bash
python -m src.parse --platform all
python -m src.merge
python -m src.validate
```

Outputs are timestamped with an Asia/Jakarta (WIB) run ID, for example `20261109T192500+0700`:

- `data/interim/tiktok_videos.jsonl`
- `data/interim/instagram_videos.jsonl`
- `data/processed/dataset_<run_id>.csv`
- `data/processed/dataset_<run_id>.parquet`
- `data/processed/merge_report_<run_id>.json`
- `data/processed/validation_report_<run_id>.json`
- `data/processed/latest_run.json`

Use a custom label if needed:

```bash
python -m src.merge --run-id pilot_01
```

`python -m src.validate` validates the latest dataset automatically. Data timestamps themselves remain UTC (ISO 8601).

### 6. Suggest more seeds

After parsing a few seeds:

```bash
python -m src.expand_seeds --top 10
python -m src.scrape_tiktok --seeds-file config/seeds_generated.yaml
```

## Output schema

| Column | Type | Notes |
|---|---|---|
| `platform` | str | `tiktok` or `instagram` |
| `category` | str | One of the six categories |
| `video_id` | str | Unique video ID per platform |
| `creator_id_hash` | str | SHA-256 of the username plus salt |
| `follower_count` | int | Follower count at scrape time |
| `post_timestamp` | datetime | UTC, ISO 8601 |
| `scrape_timestamp` | datetime | UTC, ISO 8601 |
| `caption` | str | Mentions replaced with `@user` |
| `hashtags` | str | Pipe-separated |
| `hashtag_count` | int | Number of hashtags |
| `duration_sec` | float | Video duration |
| `views` | int | Null when unavailable |
| `likes` | int | Null when unavailable |
| `comments` | int | Null when unavailable |
| `shares` | int | Null when unavailable |
| `saves` | int | Null when unavailable |
| `sound_id` | str | Null when unavailable |
| `sound_title` | str | Null when unavailable |
| `is_original_sound` | bool | Null when unavailable |
| `sound_usage_count` | int | Null when unavailable |
| `seed_source` | str | Seed that found the video |

## Validation rules

- Total TikTok rows >= 3,000
- Total Instagram rows >= 1,000
- Each category >= 400 TikTok rows and >= 150 Instagram rows
- No category exceeds 25% of a platform's total
- No creator exceeds 5 videos per category
- Core columns (`video_id`, `post_timestamp`, `caption`, `likes`, `follower_count`) are null in less than 5% of rows

Actual results are written to `data/processed/validation_report_<run_id>.json`.

## Use with an AI agent

Copy and paste this prompt into your AI coding agent:

```text
You are an AI agent working in this repository. Your task is to collect,
parse, and validate a TikTok/Instagram dataset for the six categories defined
in config/seeds.yaml.

Read these first:
- README.md
- config/settings.yaml
- config/seeds.yaml
- src/

Setup:
1. Create a virtual environment: python3 -m venv .venv
2. Activate it, then install dependencies:
   pip install -r requirements.txt
   scrapling install
3. Copy .env.example to .env and set a random SCRAPE_SALT.
4. Run the tests:
   python -m unittest discover -s tests -v
   Do not continue if any test fails.

TikTok:
1. Run the pilot:
   python -m src.scrape_tiktok --pilot --headful
2. Report per-category field completeness and any block signal.
3. Only if the pilot is clean, run:
   python -m src.scrape_tiktok
4. If a CAPTCHA, login wall, or verification appears, stop immediately and
   report it. Do not attempt to bypass it.
5. Keep one browser session/tab and respect the delays in config/settings.yaml.
6. Do not change category quotas without asking the user.

Instagram (only if a dedicated burner account is available):
1. python -m src.scrape_instagram --login
2. python -m src.scrape_instagram --check
3. python -m src.scrape_instagram --pilot --headful
4. If clean: python -m src.scrape_instagram
5. Stop after three consecutive failures with the same cause.

Finish:
1. python -m src.parse --platform all
2. python -m src.merge
3. python -m src.validate
4. Report rows per category/platform vs quota, null rates, creator-tier
   composition, and every category below its minimum.
5. Never commit .env, data/, logs/, or browser profiles.
```

## Troubleshooting

**Chrome does not open**

Make sure Google Chrome is installed. If Chrome is incompatible with the current Playwright version, set `real_chrome: false` in `config/settings.yaml` to use the bundled Chromium.

**“Please wait…” page or a very small HTML response**

This is TikTok's WAF. Try headful mode and do not use a VPN. If it persists, stop and report it.

**No `item_list` responses**

The page did not trigger infinite scroll. Keep the browser window focused and do not move the mouse or keyboard while the scraper is running.

**The Mac goes to sleep**

Run `caffeinate -dimsu` in another terminal, keep the charger connected, and do not close the lid.

**Instagram always shows a login wall**

The session expired or hit a checkpoint. Run `python -m src.scrape_instagram --login` again with the burner account.

## Repository structure

```text
config/
  seeds.yaml
  settings.yaml
src/
  common.py
  scrape_tiktok.py
  scrape_instagram.py
  parse.py
  merge.py
  validate.py
  expand_seeds.py
tests/
data/          # ignored by git
logs/          # ignored by git
```

## Commit convention

This repository follows [Conventional Commits](https://www.conventionalcommits.org/):

```text
feat: add a new feature
fix: fix a bug
docs: update documentation
chore: maintenance or tooling
refactor: code change without behavior change
test: add or update tests
```

## Compliance

- Collect public metadata only.
- Do not download videos, images, or avatars.
- Usernames are hashed before being written to the final dataset.
- Mentions are anonymized to `@user`.
- Do not bypass CAPTCHAs, login walls, or verification challenges.
- Document platform blocks and missing fields honestly in your report.

## License

MIT
