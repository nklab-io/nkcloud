<p align="center">
  <h1 align="center">NkCloud</h1>
  <p align="center"><em>Self-hosted file storage, sitting between Nextcloud and Copyparty.</em></p>
</p>

---

I tried **Nextcloud** — too heavy for what I actually do.
I tried **Copyparty** — powerful, but the UI hurt my eyes.
So I built what I wanted sitting in between.

Single Docker container, modern dark UI, multi-user with share links, WebDAV. That's it.

I'm using it every day to host my own files. Maybe you'll like it too.

> 我試過 Nextcloud，太重；試過 Copyparty，功能夠但 UI 看不下去。就自己做一個在中間的。一個 Docker 容器、暗色 UI、多用戶、分享連結、WebDAV。我自己天天在用，放上來看看有沒有人也喜歡。

---

## Status

**v0.2.3 — large-file streaming + batch-download diagnostics on top of v0.2.2.**

- ✅ Daily-driven on my homelab (Linux host, used from macOS / iPhone Safari / iPadOS)
- ✅ Tested: Chrome, Safari, Firefox on desktop + mobile
- ⚠️ Windows client: untested (WebDAV mount should work but I haven't verified)
- ⚠️ No automated test suite yet — relying on "I use it myself" for now
- ⚠️ Expect rough edges. Open an issue or PR and I'll take a look

If you need rock-solid multi-tenant collaboration, use Nextcloud. If you need every protocol under the sun, use Copyparty. If you want something small and nice-looking to self-host your own files, give this a try.

## What's new in v0.2.3

- 🚿 **Large files no longer buffer fully in RAM before streaming** — the zip writer used to read each source completely into the in-memory drain buffer before any byte left the server. A 5 GB media file is now copied 1 MB at a time and drained between chunks, so peak memory stays flat regardless of file size.
- 🔎 **`download-batch` skips are diagnosable** — added `X-Nkcloud-Total` / `X-Nkcloud-Included` / `X-Nkcloud-Skipped-Forbidden` / `X-Nkcloud-Skipped-Missing` response headers. Streaming responses can't carry a structured `failed[]` like move/delete do, but the counts let an admin reproduce a "my zip is missing files" report without rummaging through audit logs.

## What's new in v0.2.2

- 🐛 **Folder ZIP download was producing corrupted archives** — the streaming buffer truncated mid-write, throwing off the central-directory offsets. Public folder shares hit the same path. Fixed.
- 🐛 **"Select all → Download" only downloaded one file** — the toolbar fired one `window.open()` per selection and modern browsers block all but the first popup. Replaced with a single `/api/files/download-batch` endpoint that streams one ZIP for the whole selection.
- 🌏 **Non-ASCII ZIP filenames work everywhere** — `Content-Disposition` now emits both an ASCII fallback and the RFC 5987 `filename*=UTF-8''…` form, so Safari stops mangling Chinese / Japanese folder names.
- 📋 **Partial failures are visible** — `/files/move` and `DELETE /files` now return `{moved/deleted, failed:[{path,reason}]}`; the UI toasts "N moved, M failed" instead of silently swallowing the misses.

## What's new in v0.2.0

- 🗑 **Trash with 14-day retention** — soft delete, restore, quota-aware purge
- 📄 **Text & code preview** — in-browser viewer for logs, configs, source files (40+ extensions)
- 🖼 **Image zoom & pan** — wheel zoom toward pointer, drag to pan, double-click to toggle 100% / fit
- ⌨️ **Keyboard navigation** — Finder-like arrow keys, Enter to open, Backspace to go up, Space to select
- 📁 **Folder drag-and-drop upload** — preserves directory structure, auto-creates folders
- ✨ iOS-style spring animations across the UI
- 🐛 Lots of small fixes: empty-folder toolbar, double-click ghost clicks, viewer close performance, mobile header overflow

## Screenshots

![File browser](docs/screenshots/browser.png)
*Desktop file browser — list view, dark theme, thumbnails for images and video*

<p align="center">
  <img src="docs/screenshots/share.png" width="48%" alt="Share dialog">
  <img src="docs/screenshots/upload-settings.png" width="48%" alt="Upload settings">
</p>

<p align="center">
  <img src="docs/screenshots/mobile.jpeg" width="30%" alt="Mobile view">
  &nbsp;&nbsp;
  <img src="docs/screenshots/help.png" width="55%" alt="Help panel">
</p>

## Quick Start

```bash
git clone https://github.com/nklab-io/nkcloud.git
cd nkcloud
docker compose up -d
```

Open **http://localhost:8000** and follow the setup wizard to create your admin account.

No database to install, no config file to fill in. Your files live under `./storage/` on the host — they're just files, you can read them without NkCloud running.

## Features

**Files**
- Upload files **and folders** (folders preserve subdirectory structure) — drag & drop with configurable chunk size and concurrent uploads
- Grid and list views, breadcrumb navigation, filename search
- Folder download as streaming ZIP
- Full keyboard navigation (arrows, Enter, Backspace, Space, Home/End)

**Media preview**
- Images: JPG, PNG, GIF, WebP, BMP, SVG, **HEIC / HEIF / AVIF / TIFF** (HEIC/TIFF converted server-side because browsers don't support them) — with **wheel zoom** + **drag-pan**
- Video: MP4, WebM, MOV, M4V in-browser. MKV/AVI/FLV/WMV show an honest "download required" prompt instead of pretending to play
- Audio: HTML5 player, thumbnails generated from video frames

**Trash**
- Soft delete: removed files move to `.trash/` and stay for **14 days**
- Restore in one click; permanent delete or "Empty trash" anytime
- Counts toward your storage quota (no surprise reclaim)
- Expired entries auto-purged on next trash open
- Per-user: Members see only their own trash; Owner has root-level trash

**Text & code preview**
- In-browser viewer for `.txt .md .json .yaml .log .csv` and 40+ programming languages
- Line numbers, encoding info, monospace
- Up to 5 MB per file; fallback decoders for GBK / Big5 / Latin-1
- LRU cache so re-opening is instant

**Sharing**
- Public share links with optional password and expiry
- Share management page, download counts

**Multi-user**
- Invite-only registration (single-use links)
- Three roles: **Owner** (full control) / **Admin** (manage files + invites) / **Member** (own directory only)
- Per-user home directories with storage quotas
- Admin panel: users, invites, audit log, **live login-attempt log** (IP, username, user-agent, auto-refresh every 3 seconds)

**WebDAV**
- Mount as a network drive (macOS Finder, Windows Explorer, `davfs2` on Linux)
- Same credentials as the web UI; each user only sees their own scope

**Security**
- PBKDF2-SHA256 password hashing (260k iterations)
- CSRF protection (double-submit cookie)
- Persistent login rate limiting (30-day audit trail)
- Signed session cookies (HMAC-SHA256)
- Full audit log of all operations

**i18n**
- English, 繁體中文, 简体中文, 日本語
- Auto-detects browser language, switchable in the header

## WebDAV

Built-in WebDAV server on port **8001**.

- **macOS Finder:** Go → Connect to Server → `http://your-server:8001`
- **Windows:** Map Network Drive → `\\your-server@8001\DavWWWRoot`
- **Linux:** `mount -t davfs http://your-server:8001 /mnt/nkcloud`

Use your NkCloud credentials. Put it behind HTTPS if you expose it to the public internet — WebDAV Basic Auth is otherwise plaintext.

## Configuration

Copy `.env.example` to `.env` to customise. An empty `.env` works fine — everything has sensible defaults.

| Variable | Default | Description |
|----------|---------|-------------|
| `NKCLOUD_FILE_ROOT` | `/data` | Root directory for file storage (inside the container) |
| `NKCLOUD_SESSION_SECRET` | *auto-generated* | Session signing key (persisted to disk on first run) |
| `NKCLOUD_DB_PATH` | `/app/data/nkcloud.db` | SQLite database location |

### Docker Compose

```yaml
services:
  nkcloud:
    build: .
    container_name: nkcloud
    restart: unless-stopped
    ports:
      - "8000:8000"    # Web UI
      - "8001:8001"    # WebDAV
    env_file:
      - .env
    volumes:
      - ./storage:/data:rw       # Your files
      - ./data:/app/data:rw      # Database, thumbnails, config
```

### Reverse Proxy

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    client_max_body_size 0;    # no upload size limit
}
```

## Roles & Permissions

| | Owner | Admin | Member |
|---|:---:|:---:|:---:|
| Browse all files | ✓ | ✓ (root is read-only) | own directory only |
| Upload to root | ✓ | ✗ | ✗ |
| Delete other users' files | ✓ | ✓ (except owner's) | ✗ |
| Manage users & quotas | ✓ | ✗ | ✗ |
| Create invite links | ✓ | ✓ | ✗ |
| WebDAV scope | everything | own directory | own directory |

## Architecture

- **Backend:** Python 3.12 / FastAPI / SQLite
- **Frontend:** Vanilla JS (ES modules, no build step)
- **WebDAV:** WsgiDAV + Cheroot
- **Thumbnails:** Pillow + pillow-heif (images) + FFmpeg (video frames)
- **Container:** Single image based on `python:3.12-slim`

No external services. Your files are plain files on disk; the database stores only users, shares, invites, and logs.

## What NkCloud isn't

To set expectations before you try it:

- **Not a Nextcloud replacement** — no calendars, contacts, docs, group collaboration
- **Not a backup tool** — it's a file server, not Duplicati
- **Not optimised for huge deployments** — SQLite scales fine for dozens of users, not thousands
- **No FTP/SFTP** — HTTP + WebDAV only
- **No transcoding** — unplayable formats are download-only (by design — transcoding belongs in Jellyfin/Plex)
- **No RAW photo support** (CR2/NEF/ARW/DNG not previewable)
- **No content deduplication, no full-text search inside files**
- **WebDAV deletes are permanent** — they bypass the trash. The web UI is the safe path for recoverable deletes.

## Contributing

PRs and issues welcome. This is a solo side project, so response times are "when I get to it." If you're planning something big, open an issue first so we can agree on direction before you write code.

## License

[MIT](LICENSE)
