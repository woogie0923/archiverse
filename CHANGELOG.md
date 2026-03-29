# Changelog

All notable changes to this project are recorded here. The history below follows the repository from the first commit; related doc-only or merge commits are summarized together.

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

---

## Full commit list (oldest first)

| Date       | Summary |
| ---------- | ------- |
| 2026-03-28 | Weverse archiver: CLI, DRM downloads, lives, and ongoing live recording |
| 2026-03-28 | Updated config.yaml.template |
| 2026-03-28 | Updated README |
| 2026-03-28 | Changed wv-archive.py to archiverse.py |
| 2026-03-28 | Updated config.yaml.template |
| 2026-03-28 | Updated README |
| 2026-03-28 | Updated macOS keyboard handling |
| 2026-03-28 | Delete wv-archive.py |
| 2026-03-29 | Extract date from url when saving profile picture |
| 2026-03-28 | Updated formate template for official_channels |
| 2026-03-28 | Merge pull request #1 from honeyedoasis/add-pfp-date |
| 2026-03-28 | Conclude merge: deleted wv-archive.py |
| 2026-03-28 | Finish merge |
| 2026-03-29 | Reorganized main menu and added --ongoing-live-monitor-no-prompt |
| 2026-03-29 | Fixed macOS keyboard handling |
| 2026-03-29 | Fixed code for HDR streams |
| 2026-03-29 | Updated main menu layout |
| 2026-03-29 | Updated README |
| 2026-03-29 | Added progress bar for video muxing |
| 2026-03-29 | Updated config.yaml.template |
| 2026-03-29 | Updated README |

*Note: Author dates on a few commits may show as 2026-03-28 while they appear later in history; the table follows `git log --reverse` order.*
