# Changelog

All notable changes to this project are recorded here. The history below follows the repository from the first commit; related doc-only or merge commits are summarized together.

## Unreleased

### Added

- **Add an “Ongoing Lives Settings” submenu** (download-only, poll, subs, output format, mux subs, record-all)
- **Ongoing lives controls**: `--ongoing-live-download-only {both,video,subs}` and `--ongoing-live-mux-subs` (plus matching interactive menu toggles for poll interval, subtitles, output format, and record-all).
- **Main menu history toggle** — filters can disable reading/updating `downloaded.json` for the run (via `--no-history` behavior).
- **Token refresh visibility** — startup and interactive main menu now show refresh-token status, and successful token refreshes print an `[Auth]` confirmation line.

### Fixed

- **401 handling** — when a Weverse API call fails with HTTP 401 (`account_401`), the app now refreshes the access token (when `weverse_refresh_token` is configured) and retries the request.
- **Token refresh persistence** — refreshed access/refresh tokens are written back into `config.yaml` to keep future runs working without manual copy/paste.
- **Streamlink muxing warnings (Windows)** — ongoing-live recording now passes `--ffmpeg-ffmpeg` using the path from `binaries.ffmpeg` (resolved via PATH when possible) so Streamlink can mux A/V reliably.
- **Streamlink auth after refresh** — Streamlink now prefers the refreshed `COMMON_HEADERS` bearer token over the stale `auth_token` value.

- **Remove ongoing-live option rows from the main Actions list**
- **Ongoing lives MKV remux** — map only v/a/sub streams when remuxing TS recordings into Matroska to avoid Matroska header/codec-parameter failures.
- **`downloaded.json` correctness across downloads** — only record a post as downloaded after the requested downloads/muxing succeed (covers ongoing lives, feed archivers in `processors.py`, and official media in `official_media.py` / `official_media_menu.py`).
- **Official-channel VOD quality selection** — `get_official_video_url()` now robustly selects the best MP4 representation (highest height, then bandwidth) and supports MPD XML as well as JSON responses.
- **Saved post text (`.txt`)** — decode HTML/XML character references (`&gt;`, `&lt;`, `&amp;`, etc.) and strip WordprocessingML fragments (`<w:…>`, `</w:…>`) from post and comment bodies in `text_writer.py`.
- **Moments video quality selection** — `cvideo` selection for moments now prefers `encodingOption.profile=HIGH` when available.
- **Debug noise reduction** — `[Neonplayer]` and `[Video URL]` lines are only printed in `--debug` mode; raw N_m3u8DL-RE command lines are no longer printed in terminal output.

### Changed

- **API layer no longer depends on yt-dlp extractor** — requests now call Weverse endpoints directly with required signed parameters (`wmd`/`wmsgpad`) and shared headers.
- **Runtime structure cleanup** — execution flow is centralized via `app_runtime.py` (`AppRuntime`) and entrypoint/menu orchestration was tidied without changing behavior.

---

## 2026-03-29

### Added

- **`--ongoing-live-monitor-no-prompt`** — with `--ongoing-live-monitor`, continue polling after a live ends without prompting to keep monitoring.
- **Mux / remux progress** — Rich progress bar (or spinner when duration is unknown) while FFmpeg muxes or remuxes; uses **ffprobe** when available for duration.

### Changed

- **Interactive menu** — Archive and Actions as separate Tab/→ sections; clearer spacing on smaller terminals; ongoing layout fixes.
- **README** and **`config.yaml.template`** — ongoing-live tooling (Streamlink, ffprobe, flags), prerequisites, and configuration notes.

### Fixed

- **macOS** — keyboard handling in interactive menus.
- **HDR streams** — download/mux path adjustments for HDR content.

### Other

- Extract creation date from image URL when saving **profile pictures** (community contribution, merged via PR #1).
- **`official_channels`** filename template updates.

---

## 2026-03-28

### Added

- **Initial Weverse archiver** — CLI entry point, community selection, filters, artist/channel/archive actions, DRM VOD downloads (N_m3u8DL-RE / Widevine), live VOD flow, **ongoing (on-air) live** recording, download history, configurable paths and templates.

### Changed

- **Entry script** renamed from `wv-archive.py` to **`archiverse.py`** (legacy file removed after merges).
- **macOS** — early keyboard-handling improvements for menus.

### Project

- **`config.yaml.template`** and **README** — first-pass setup and usage documentation.
