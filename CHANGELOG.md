# Changelog

All notable changes to this project are recorded here. The history below follows the repository from the first commit; related doc-only or merge commits are summarized together.

---

## 2026-04-14

### Fixed

- **Text exports: attachment formatting** — `<w:attachment .../>` placeholders are now treated as line breaks so text doesn’t get glued together (e.g. before hashtags).
- **Text exports: YouTube/snippet links** — YouTube links are appended to the end of the saved `.txt` (official posts: `attachment.snippet[].url`, official media: `extension.youtube.videoPath`), and URL-only `.txt` files are created even when the post body is empty.

---

## 2026-04-06

### Fixed

- **401 handling** — when a Weverse API call fails with HTTP 401 (`account_401`), the app now refreshes the access token (when `weverse_refresh_token` is configured) and retries the request.
- **Token refresh persistence** — refreshed access/refresh tokens are written back into `config.yaml` to keep future runs working without manual copy/paste.
- **Streamlink muxing warnings (Windows)** — ongoing-live recording now passes `--ffmpeg-ffmpeg` using the path from `binaries.ffmpeg` (resolved via PATH when possible) so Streamlink can mux A/V reliably.
- **Streamlink auth after refresh** — Streamlink now prefers the refreshed `COMMON_HEADERS` bearer token over the stale `auth_token` value.
- **Past live VOD 403** — cached CDN URLs can expire; the app now invalidates the cached VOD URL, refetches `playInfo`, and retries once.
- **Mux progress output** — escape square brackets in labels and throttle FFmpeg progress updates to prevent duplicated progress lines in some terminals.

### Added

- **Persist refreshed tokens** — on successful refresh, tokens are also cached in `weverse_token.json` and preferred on subsequent runs (handles refresh-token rotation).

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
