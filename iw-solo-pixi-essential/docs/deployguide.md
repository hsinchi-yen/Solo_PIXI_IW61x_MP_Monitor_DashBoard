# 部署指南 — IW61x Solo PIXI Dashboard

正式環境：`sit@10.20.31.40:/mnt/md127/iw-solo-pixi-essential`（compose 專案 `iw-solo-pixi-essential`；看板 port 8004、API 8003、資料庫 5434）。
同一台主機上的 `pixi-solo-dashboard`（8001／5433）和其他系統**不要動**。

> 任何正式環境部署都先在本機驗證，經負責人同意後才執行。

## 1. 一般部署

在開發機的 repo 內執行：

```bash
cd iw-solo-pixi-essential
export SSHPASS='…'                 # 用密碼登入時（需要 sshpass）；有 SSH key 則不需要
scripts/deploy.sh --dry-run        # 先看會同步哪些檔案
scripts/deploy.sh --delete         # 正式部署（--delete 會移除伺服器上 repo 已經沒有的舊檔）
```

`deploy.sh` 依序做：

| 步驟 | 內容 |
|---|---|
| 1. 備份 | 一天一個資料夾 `oldbackup/<YYMMDD>/`：目前的檔案 → `files_<HHMMSS>.tgz`；資料庫 `pg_dump -Fc` → `pixi_test_<時間>.dump`（任何一個是空的就中止，不做變更） |
| 2. 同步 | `rsync` 整個 `iw-solo-pixi-essential/`（不含伺服器上的 `.env`、`.system_up.env`、`oldbackup/`、`dbbackup/`） |
| 3. 建置 | `system_up.sh`：`docker compose up -d --build`、重建 nginx、等健康檢查（port 沿用 `.system_up.env`） |
| 4. 健康檢查 | `/health` 回 `ok` 且版本號正確、首頁是新版、`/api/summary` 有資料 |
| 5. 失敗自動還原 | 還原 `files.tgz` 並重新 `system_up.sh`；資料庫不受影響（新增的資料表舊版不會用到） |

本機演練（不會用到 SSH，也不會碰本機正在跑的看板）：準備一個目錄，放入正式機的舊版檔案、`.env`、`.system_up.env`（例如 `IW_API_PORT=8023`、`IW_WEB_PORT=8024`、`IW_DB_PORT=5444`），再執行：

```bash
COMPOSE_PROJECT_NAME=iw-rehearsal scripts/deploy.sh --local <目錄> --delete
```

2026-10-20 已用「正式機舊版檔案＋正式機資料庫備份」演練升級到 2.0.0（成功，約 10 秒）以及故意部署壞掉的版本（自動還原成功，約 3 分鐘）。

## 2. 第一次部署 2.0.0 要注意

- **`.env` 要先補上**（正式機目前只有 `LLM_API_KEY`），否則 AI 功能會關閉：
  ```
  LLM_ENABLED=true
  LLM_API_BASE=http://10.20.30.23:8000/v1
  LLM_MODEL=Qwen3.8-27B-UD-Q6_K_L
  ```
  其他設定見 `.env.example`，全部都有預設值。`ADMIN_PASS` 目前沿用預設（負責人決定）。
- API 啟動時會自動建立新物件（帳號、上傳紀錄、AI 報告、統計 view 與 `metric_day`）。**既有資料表、欄位、索引都不改**；目前 180 筆約 2 秒。
- 舊的單檔頁面、`static/`、`log_iw_uploader_app.py` 會因 `--delete` 移除（Q28）。桌面上傳程式改用 repo 裡的 `Utilities/log_iw_uploader_app.py`，資料庫連線方式不變。
- 部署後所有人需要重新登入；上傳者帳號由管理者在「帳號管理」頁建立。

## 3. 每日資料庫備份（保留 14 天）

在伺服器加入 cron（`crontab -e`，使用者 `sit`）：

```
0 2 * * *  /mnt/md127/iw-solo-pixi-essential/scripts/backup_db.sh >> /mnt/md127/iw-solo-pixi-essential/dbbackup/backup.log 2>&1
```

還原：

```bash
docker exec -i iw-solo-pixi-essential-postgres-1 pg_restore -U pixi -d pixi_test --clean --if-exists < dbbackup/pixi_test_<時間>.dump
```

## 4. 手動回復到上一版

```bash
cd /mnt/md127/iw-solo-pixi-essential
ls oldbackup/*/                                  # 找到要回復的日期與時間
find . -mindepth 1 -maxdepth 1 ! -name oldbackup ! -name dbbackup ! -name .env ! -name .system_up.env -exec rm -rf {} + 2>/dev/null
tar xzf oldbackup/<YYMMDD>/files_<HHMMSS>.tgz
bash system_up.sh
```

回到 2.0 之前的版本：用 2.0 部署當天的 `oldbackup/<YYMMDD>/files_*.tgz`。新增的資料表不影響舊版，可以保留。
（舊版會在程式資料夾留下 root 擁有的 `__pycache__`，刪不掉是正常的，解壓縮會直接覆蓋。）

## 5. 常用指令（伺服器上）

| 目的 | 指令 |
|---|---|
| 狀態 | `docker compose -p iw-solo-pixi-essential ps`；`curl http://localhost:8003/health` |
| API 日誌（含每個請求耗時、慢查詢） | `docker compose -p iw-solo-pixi-essential logs -f api` |
| 立即更新統計 | 看板右上「Refresh／立即更新」（10 秒內只能按一次） |
| 重新啟動 | `docker compose -p iw-solo-pixi-essential restart api` |

## 部署紀錄

| 日期 | 版本 | 備份 | 驗收 |
|---|---|---|---|
| — | 2.0.0 | — | 尚未部署 |
