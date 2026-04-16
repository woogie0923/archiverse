# Archiverse

An all-in-one command-line tool to archive photos, videos, lives, and texts (texts from posts and artist comments) from [Weverse](https://weverse.io/) communities you have access to. It supports artist posts, moments, official channels, the media tab, live VODs, and optional recording of currently on-air streams.

This project is based on [Weverse Archive](https://github.com/honeyedoasis/WeverseArchive) and some scripts from a friend.

**Platform support:** Windows, macOS, and Linux. Interactive menus expect a **real terminal** (TTY). Run from Terminal, Windows Terminal, iTerm, etc. Some IDE “Run” panels do not provide a TTY, so keyboard navigation may not work there.

---

## Prerequisites

- **Python 3.10+** (Streamlink requires Python 3.10+; uses `zoneinfo` in the standard library)
- **PyPI dependencies** — see [`requirements.txt`](requirements.txt)
- **External programs** (install separately and/or set full paths in `config.yaml`):
  - [FFmpeg](https://ffmpeg.org/) (and **ffprobe**, usually in the same install — used for mux/remux progress)
  - [N_m3u8DL-RE](https://github.com/nilaoda/N_m3u8DL-RE) (DRM and HLS)
  - [mkvpropedit](https://mkvtoolnix.download/) (MKV metadata, when used)

You also need:

- A Weverse **bearer token** (`auth_token` in config) for API access (the tool calls the Weverse APIs directly using this token; no browser cookie extraction is required)
- A **Widevine device file** (`.wvd`) at `wvd_device_path` for membership / DRM-protected content

Use this software only in line with Weverse’s terms of service and applicable law. You are responsible for your account and for content you download.

---

## Installation

### 1) Quick install (recommended)

Clone first:

```bash
git clone https://github.com/woogie0923/archiverse.git archiverse
cd archiverse
```

Then run the installer for your OS:

- **Windows:** `install.bat`
- **macOS / Linux:** `chmod +x install.sh` then `./install.sh`

What the installer does:
- Installs `uv` if needed
- Runs `uv sync` to install Python dependencies
- Creates `config.yaml` from `config.yaml.template` if missing

### Using `uv` (recommended)

After running the installer, you can run without manual venv management:

```bash
uv run archiverse --help
```

### 2) Manual install (fallback)

Use this only if you do not want to use the installer scripts.

```bash
git clone https://github.com/woogie0923/archiverse.git archiverse
cd archiverse
python -m venv .venv
```

Activate the virtual environment:

- **Windows (PowerShell):** `.\.venv\Scripts\activate.bat`
- **macOS / Linux:** `source .venv/bin/activate`

Install dependencies:

```bash
pip install -r requirements.txt
```

Then run:

```bash
python -m archiverse --help
```

For manual installation, create your config from the template:

| OS | Command |
|----|---------|
| macOS / Linux | `cp config.yaml.template config.yaml` |
| Windows CMD | `copy config.yaml.template config.yaml` |
| PowerShell | `Copy-Item config.yaml.template config.yaml` |

Edit **`config.yaml`**: set `auth_token`, `wvd_device_path`, `base_dir`, `menu_communities`, and `binaries` as needed. See comments inside [`config.yaml.template`](config.yaml.template).

---

## Usage

Show full help:

```bash
uv run archiverse --help
# or:
python -m archiverse --help
```

### Interactive mode (no action flags)

If `menu_communities` is set in `config.yaml`, you can run:

```bash
uv run archiverse
# or:
python -m archiverse
```

You will get a community picker and then the main menu.

```bash
uv run archiverse -c fromis9
# or:
python -m archiverse -c fromis9
```

### Non-interactive examples (action flags require `-c`)

| Goal | Example |
|------|---------|
| Debug API URLs | `uv run archiverse -c fromis9 --debug` |
| All artists: profiles | `uv run archiverse -c STAYC -a all --profile` |
| Selected artists: moments | `uv run archiverse -c RedVelvet -a IRENE SEULGI --moments` |
| Live menu or direct live ID | `uv run archiverse -c Apink --live` / `uv run archiverse -c fromis9 --live 4-12345678` |
| Artist posts, photos only | `uv run archiverse -c fromis9 -a "SONG HA YOUNG" --artist --type photo` |
| Official channel by member ID | `uv run archiverse -c fromis9 --skip-membership --official 58afde0dbc1fccd94cd44eff91fa3673` |
| Official media tab | `uv run archiverse -c aespa --media` / `uv run archiverse -c aespa --media 4-223153860` |
| Official media browser | `uv run archiverse -c APINK --media-menu` |
| Text + comments only | `uv run archiverse -c LESSERAFIM -a Chaewon --artist --text-only --comments --skip-public` |

### Ongoing (on-air) live recording

Poll Weverse for currently live streams and record with **Streamlink** (then remux with FFmpeg). Subtitles after the fact can use N_m3u8DL-RE:

```bash
uv run archiverse -c fromis9 --ongoing-live-monitor
# or:
python -m archiverse -c fromis9 --ongoing-live-monitor
```

Record what is on air once (optional match string for post / video / URL):

```bash
uv run archiverse -c fromis9 --ongoing-live-now
uv run archiverse -c fromis9 --ongoing-live-now "4-1234567890"
# or:
python -m archiverse -c fromis9 --ongoing-live-now
python -m archiverse -c fromis9 --ongoing-live-now "4-1234567890"
```

Useful flags: `--ongoing-live-poll SECONDS`, `--ongoing-live-record-all`, `--ongoing-live-subs`, `--ongoing-live-output-format mp4|mkv` (flag alone keeps default **mp4**), `--ongoing-live-download-only {both,video,subs}`, `--ongoing-live-mux-subs` (embed downloaded subtitles into the recorded video container), `--ongoing-live-monitor-no-prompt` (with `--ongoing-live-monitor` only: after a live ends, keep polling without asking). The `--ongoing-live-chat` flag is reserved for future use (ongoing chat is not archived yet).
In interactive mode, these same ongoing-live options can be configured under the **Actions** block.

For long runs, consider setting **`weverse_refresh_token`** in `config.yaml` so access tokens can be refreshed (see `weverse_auth.py`). The app shows refresh-token status at startup and in the interactive main menu.

DRM still logs normal N_m3u8DL-RE progress/errors, but no longer prints the full raw command line in terminal output.

### Past live VODs (standard MP4)

For finished lives saved as plain MP4 (not Widevine), resolved stream URLs are **cached per community** in `video_urls.json` (default folder: `{base_dir}/{community}/Cache/` — see `folders.api_cache` in config). Those CDN URLs use **time-limited signatures**; if a download fails (for example HTTP **403**), the tool **drops that cache entry, refetches `playInfo`, and retries the video once**. You can also delete the JSON entry for a given `video_id` manually if you want to force a fresh URL without waiting for a failure.

### Other useful flags

- **`--no-history`** — do not read or update `downloaded.json` for this run (also available as a History/No history toggle in the interactive menu Filters)
- **`--skip-membership` / `--skip-public`** — filter by visibility
- **`-id` / `--community_ids`** — supply community IDs if slug lookup fails (same order as `-c`)
---

## Configuration highlights

| Key | Purpose |
|-----|---------|
| `auth_token` | Weverse API bearer token |
| `weverse_refresh_token` | Optional refresh token for long sessions |
| `wvd_device_path` | Path to `.wvd` for Widevine |
| `base_dir` / `media_folder` / `folders` | Output layout |
| `filename_templates` / `date_format` | How files are named |
| `menu_communities` / `menu_community_ids` | Interactive picker and ID overrides |
| `official_channels` / `former_members` | Extra menu entries per community slug |
| `binaries` | Paths to `ffmpeg`, `ffprobe`, `streamlink`, `N_m3u8DL-RE`, etc. |
| `cache_enabled` | API response cache under each community’s `cache/` folder |
| `download_history_enabled` | `downloaded.json` skip tracking (see template comments) |

Timezone for filenames and text headers is controlled by **`timezone`** (IANA name, e.g. `Asia/Seoul`). A reference list: [tz database time zones](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones).

---

## Troubleshooting

- **Menus do not respond to keys** — use a normal terminal with a TTY, not a minimal IDE run console.
- **403 on Weverse API calls** — refresh `auth_token` and verify it has access to the community/content.
- **403 on a past live VOD MP4 URL** — usually an **expired cached CDN URL**, not account access. The archiver refetches `playInfo` automatically; if it still fails, remove that `video_id` from `video_urls.json` (see [Past live VODs](#past-live-vods-standard-mp4)) or delete the file to clear all cached stream URLs for that community.
- **DRM or live failures after hours** — set `weverse_refresh_token` if supported by your setup.
- **Binary not found** — set explicit paths under `binaries` in `config.yaml`.

---

## Repository layout (main entry points)

| Path | Role |
|------|------|
| `archiverse/` | Python package (CLI + all modules) |
| `archiverse/__main__.py` | Enables `python -m archiverse` |
| `config.yaml` | Your local configuration (not in template) |
| `config.yaml.template` | Safe template for new setups |
| `interactive_menu.py` / `live.py` | TUI menus and live flows |
| `ongoing_live.py` | On-air live monitoring and recording |
| `processors.py` / `official_media.py` | Feed and media archiving |
| `downloader.py` / `api.py` | Downloads and Weverse API helpers |
| `download_cache.py` | Per-community caches (`downloaded.json`, DRM keys, VOD URL cache, etc.) |
| `weverse_auth.py` | Optional access-token refresh using `weverse_refresh_token` |

## License

This project is licensed under the **MIT License**. See [`LICENSE`](LICENSE).

## TO-DO
- Support for on-air membership livestreams

If something is missing or unclear, open an issue or improve this README via pull request.
