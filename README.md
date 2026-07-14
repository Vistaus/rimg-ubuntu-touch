# remoteImage (`rimg`)

A simple SSH image and video library for Ubuntu Touch with local thumbnail
caching.

Browse a media directory on a remote server over SSH (key-based auth) as a
gallery. Because the SSH link can be slow, the app **indexes** the remote folder
once — producing a small **local thumbnail** per file — so the gallery then
renders instantly from the local cache. Full files are fetched on demand.
The remote folder structure is preserved as browsable sub-folders.

## Features

- Key-based SSH/SFTP only: the app generates its own Ed25519 key on first run
  (trust-on-first-use host-key pinning); add the shown public key to the
  server's `authorized_keys` and you're set.
- Incremental indexing with progress; thumbnail grid adapts to screen width.
- **Videos** are indexed too — the thumbnail is the actual first frame, decoded
  over SFTP without downloading the whole file — and play inline in the viewer.
- Full-screen viewer: swipe between items (neighbours prefetched), the cached
  thumb stands in while the full image downloads, natural pinch/double-tap zoom.
- **Rotate** images 90° from the viewer — the rotated file also atomically
  replaces the original on the server.
- Settings show index stats: item count, last update, size on disk.

## How it works

- **Backend** ([src/backend.py](src/backend.py)): Python via PyOtherSide.
  Uses `paramiko` for SSH/SFTP, `cryptography` for the in-app Ed25519 key,
  `Pillow` for thumbnails, and `PyAV` (bundled FFmpeg) for video first-frame
  extraction. These deps are vendored into `src/vendor/` at build time (see
  below).
- **UI** ([qml/](qml/)): pure QML / Lomiri.Components — a settings page, a
  folder-preserving gallery grid, and a swipeable pinch-zoom media viewer
  (QtMultimedia/media-hub for video playback).
- **Storage** (confined app dirs): generated key + `known_hosts` + `index.json`
  in the data dir; thumbnail and on-demand full-file caches in the cache dir.

## First run

1. Open **Settings**, fill in host, port, username, and the remote image directory.
2. Tap **Copy public key** to put it on the clipboard (it's also shown on screen),
   and add it to `~/.ssh/authorized_keys` on the server.
3. Tap **Test connection**, then **Re-index folder** to build the thumbnail cache.
4. Go back to the gallery and browse.

## Build & install

Dependencies are vendored automatically by the `prebuild` step in
[clickable.yaml](clickable.yaml), which runs `pip` **inside the arm64 build
container** so native wheels match the device.

```sh
clickable build --arch arm64          # produces the .click
clickable --arch arm64 install launch # install + run on a connected device
```

> **Note:** building requires a working Docker (Clickable runs the build in an
> arm64 container). It cannot be built from inside a nested dev-container without
> Docker. Target is **Ubuntu Touch 24.04 (1.x), arm64** — set in
> [clickable.yaml](clickable.yaml) via `framework: ubuntu-touch-24.04-1.x`. If your
> device is on the 24.04 **2.x** channel, change that to `...-2.x`. The SSH/image
> native deps ship as prebuilt cp312 aarch64 wheels.

## Tests

Backend logic (keygen, SSH/SFTP, recursive indexing incl. video first-frame
thumbs, incremental re-index, `fetch_full`, rotation round-trip incl. the
server-side replace) is covered by a host-side test that runs against an
in-process SFTP server — no device needed:

```sh
pip install paramiko pillow av   # host-only, for the test
python3 tests/test_backend.py
```

## License

Copyright (C) 2026  Franz Thiemann

This program is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License version 3, as published by the
Free Software Foundation.

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranties of MERCHANTABILITY, SATISFACTORY
QUALITY, or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License
for more details.

You should have received a copy of the GNU General Public License along with
this program. If not, see <http://www.gnu.org/licenses/>.
