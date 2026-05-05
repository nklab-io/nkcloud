# AGENTS.md

本文件適用於整個 repository。目標是讓 coding agent 在修改 NkCloud 時先理解現有架構、風險邊界與驗證方式。

## 專案概要

NkCloud 是單容器自架檔案雲服務：

- Backend: Python 3.12, FastAPI, SQLite
- Frontend: Vanilla JavaScript ES modules, 無 build step
- WebDAV: WsgiDAV + Cheroot, 由 `app/webdav.py` 啟動背景 server
- Storage: 使用者檔案在 `NKCLOUD_FILE_ROOT`，資料庫/縮圖/chunk/session secret 在 `/app/data` 類路徑

主要程式位置：

- `app/main.py`: FastAPI app、session cookie、CSRF、setup/login/invite 流程、router mount
- `app/permissions.py`: 多使用者路徑 remap、權限檢查、symlink-safe resolve helper
- `app/routers/`: API endpoints
- `app/services/`: filesystem、thumbnail、trash、zip 等共用邏輯
- `app/static/`: 前端 HTML/CSS/JS 與 i18n JSON

## 常用指令

本 repo 目前沒有正式測試套件。至少跑基本語法檢查：

```bash
python3 -m compileall app
```

本機第一次跑測試：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest
```

容器啟動與驗證：

```bash
docker compose up --build
docker compose logs -f nkcloud
```

如果只改 Python、requirements、Dockerfile，預期需要 rebuild image。若只改 static 檔，仍需確認部署方式是否把 static COPY 在 image 內；預設 compose 不掛載 `app/static`。

## 開發規則

- 優先沿用現有 FastAPI router + service 模式；不要引入 build system 或大型框架，除非需求明確。
- 所有使用者輸入的檔案路徑都必須經過 `resolve_and_authorize()` 或 `resolve_parent_and_authorize()`。不要只用 `safe_resolve()` 當作權限檢查。
- 任何會遍歷目錄、打包 ZIP、產生縮圖、搜尋或公開分享的流程，都要重新確認 canonical path 仍在使用者可讀範圍內，並避免跟隨 symlink 洩漏其他範圍資料。
- Mutating API 需通過 CSRF middleware；新增 public endpoint 前要明確確認是否真的應該加入 CSRF exempt/public path。
- `.trash/` 不應透過一般檔案 API 或 WebDAV 直接操作。trash 內資料仍計入 quota，永久刪除或過期清理時才扣 `used_bytes`。
- 上傳流程要維持 quota 檢查與 chunk session reservation；失敗路徑需清理暫存 chunk 與未完成目的檔。
- WebDAV 權限模型依賴 `PerUserFilesystemProvider` 加上 `GateMiddleware`，middleware 順序很重要，修改前要回歸檢查 MOVE/COPY Destination、admin write gate、`.trash` block。
- 前端維持 Vanilla JS + ES modules。新增可見文字時同步更新 `app/static/lang/*.json`，不要只寫死單一語言。
- API client 放在 `app/static/js/api.js`；一般 UI 狀態與互動在 `app/static/js/app.js`，保持錯誤 toast 與 i18n 慣例一致。

## 安全與資料保護

- 不要在文件、commit message、log、回覆或測試資料中暴露密碼、session secret、token、cookie、私鑰或私人部署細節。
- `.env`、`data/`、`storage/`、thumbnail cache、chunk cache 都應視為本機/部署資料，不應提交。
- `CLAUDE.md` 可能含有私有維運筆記；引用前先去識別化，不要複製帳密、IP 或生產環境識別資訊到公開文件。
- 進行高風險操作前先說明目標、備份/回復方式與驗證計畫，尤其是刪檔、資料庫、Docker volume、遠端主機或正式服務重啟。

## 審查重點

修改後至少檢查：

- 權限邊界：owner/admin/user 是否仍符合 README 的角色表
- 路徑安全：`..`、symlink、`.trash`、`/_homes/{user}` remap
- Quota：upload/move/delete/trash/purge 是否維持 `used_bytes`
- 大檔案：download、ZIP、upload 是否串流處理，避免整檔進 RAM
- Public share：password、expiry、download count、匿名可見 metadata
- WebDAV：Basic auth、rate limit、per-user root、admin mutating methods
- i18n：新增文案是否四種語言都有 key
