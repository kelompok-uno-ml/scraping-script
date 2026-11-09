# Scraping Report — Pilot Status

> This is a pilot report, not the final dataset report. Update the numbers after the full run.

## 1. Environment

- Scrapling 0.5.1 with `scrapling[fetchers]`
- Python 3.12 on macOS (Apple Silicon)
- Google Chrome via `StealthySession(real_chrome=True)`
- Local machine on a residential Indonesian connection, no proxy or VPN

## 2. Scope

The project covers six categories:

- `kuliner`
- `fashion_beauty`
- `edukasi_tips`
- `hiburan_komedi`
- `lifestyle_travel`
- `gaming`

The `category` column is derived from the first seed that captured a video. Targets are 4,200 TikTok rows and 1,500 Instagram rows, with per-category quotas defined in `config/settings.yaml`.

## 3. TikTok method

1. Open `https://www.tiktok.com/tag/<seed>?lang=id-ID` in headful Chrome.
2. Scroll with `window.scrollTo(0, document.body.scrollHeight)`.
3. Capture XHR responses from `/api/challenge/item_list/` using `record_requests=True`.
4. Save the raw JSON body and a `.meta.json` sidecar containing `seed`, `category`, and `captured_at`.
5. Parse into the standard schema, deduplicate by video ID, keep the earliest captured record, and apply filters.

Notes:

- `/api/challenge/item_list/` is the correct endpoint; `/api/post/item_list/` is not used by the web tag page.
- `mouse.wheel` does not reliably trigger infinite scroll; `window.scrollTo` does.
- Responses are saved after each scroll so early pages are not lost when browser history reaches its 20 MiB limit.
- A guest hashtag feed stops at roughly 181 videos (`hasMore: false`).
- `sound_usage_count` is not exposed by the web response and is stored as `null`.

## 4. Pilot results

Only a subset of categories has been run beyond the first scroll. `gaming` was tested with 9 seeds and 8 scrolls; the other categories used one seed and one scroll.

| Category | TikTok rows | Target | Status |
|---|---:|---:|---|
| `kuliner` | 75 | 700 | 1 seed, 8 scrolls |
| `fashion_beauty` | 20 | 700 | 1 seed, 1 scroll |
| `edukasi_tips` | 12 | 700 | 1 seed, 1 scroll |
| `hiburan_komedi` | 11 | 700 | 1 seed, 1 scroll |
| `lifestyle_travel` | 14 | 700 | 1 seed, 1 scroll |
| `gaming` | 191 | 700 | 9 seeds, 8 scrolls |
| **Total** | **323** | **4,200** | pilot only |

Instagram pilot was run with a logged-in burner session (6 seeds, 1 scroll each):

| Category | Instagram rows | Target |
|---|---:|---:|
| `kuliner` | 14 | 250 |
| `fashion_beauty` | 6 | 250 |
| `edukasi_tips` | 7 | 250 |
| `hiburan_komedi` | 11 | 250 |
| `lifestyle_travel` | 3 | 250 |
| `gaming` | 10 | 250 |
| **Total** | **51** | **1,500** |

Raw Instagram pilot: 137 unique videos. After enrichment, `follower_count`, `duration_sec`, `views`, `likes`, and `comments` are 0% null. `shares`, `saves`, and `sound_usage_count` remain unavailable; `sound_id` is 5.9% null and `sound_title` is 29.4% null.

### Creator-tier composition (TikTok pilot)

Target: 30% small / 40% medium / 30% large. Actual pilot composition is reported as-is.

| Category | Small | Medium | Large |
|---|---:|---:|---:|
| `kuliner` | 17 (22.7%) | 23 (30.7%) | 35 (46.7%) |
| `fashion_beauty` | 7 (35.0%) | 10 (50.0%) | 3 (15.0%) |
| `edukasi_tips` | 9 (75.0%) | 2 (16.7%) | 1 (8.3%) |
| `hiburan_komedi` | 2 (18.2%) | 5 (45.5%) | 4 (36.4%) |
| `lifestyle_travel` | 8 (57.1%) | 3 (21.4%) | 3 (21.4%) |
| `gaming` | 74 (38.7%) | 47 (24.6%) | 70 (36.6%) |

### Videos per seed

| Category | Seed | Rows |
|---|---|---:|
| `kuliner` | `kulinerindonesia` | 75 |
| `fashion_beauty` | `ootd` | 20 |
| `edukasi_tips` | `tipsbelajar` | 12 |
| `hiburan_komedi` | `komedi` | 11 |
| `lifestyle_travel` | `wisataindonesia` | 14 |
| `gaming` | `freefire` | 36 |
| `gaming` | `gameplay` | 33 |
| `gaming` | `gamerindonesia` | 18 |
| `gaming` | `gaming` | 21 |
| `gaming` | `gamingindonesia` | 36 |
| `gaming` | `minecraft` | 6 |
| `gaming` | `mobilelegends` | 36 |
| `gaming` | `pubgmobile` | 2 |
| `gaming` | `roblox` | 3 |

Other seeds produced no valid rows in the pilot. The full per-seed table is available in `data/processed/validation_report_<run_id>.json` under `by_seed`.

## 5. Findings

- Guest hashtag feeds are limited to roughly 181 videos per seed, so each category needs several seeds.
- The pilot yield was around 40% valid videos for high-volume categories, but only around 14% for `gaming` because many results are old or in English.
- Many old viral videos appear in tag feeds and are removed by the 7–90 day filter.
- `follower_count` is available from `authorStats.followerCount` for the sampled TikTok videos.
- TikTok's `textLanguage` field is used before caption detection to avoid dropping mixed-language gaming captions too aggressively.
- `sound_usage_count` is unavailable and remains `null`.
- Instagram hashtag pages redirect to keyword search, so the collector stores search XHR responses.
- Instagram media enrichment (`/api/v1/media/<pk>/info/`) fills duration, views, likes, comments, and sound metadata.
- Instagram creator follower counts are parsed from the profile page `og:description`.
- Instagram `shares`, `saves`, and `sound_usage_count` are not exposed and remain `null`.

## 6. Not done yet

- Full TikTok scale-up across all categories.
- The additional gaming seeds added to `config/seeds.yaml`.
- Instagram scale-up (pilot and enrichment are done).
- Final creator-tier balancing, weekly distribution review, and target-vs-actual tables.
- Final acceptance check.

## 7. Compliance

- Public metadata only.
- No videos, images, or avatars are downloaded.
- Usernames are hashed with SHA-256 and a random salt.
- Mentions are anonymized to `@user`.
- No CAPTCHA solving, no Cloudflare solving, and no proxy evasion.
- Scraping may still violate platform Terms of Service. This project is academic and non-commercial.
