# 正式機 ↔ 本機 對齊報告（2026-10-20）

| 項目 | 內容 |
|---|---|
| 正式機 | `sit@10.20.31.40:/mnt/md127/iw-solo-pixi-essential`（不是 git repo；容器自 2026-07-27 起運作，`/health` 正常） |
| 本機 | repo commit `94e5dc6`（2026-07-27 10:59）＋ 工作區未 commit 的變更 |
| 比對方式 | `rsync` 拿回 54 個檔案（排除 `.env`、`.system_up.env`、`__pycache__`、`.pytest_cache`），用 `diff -rq` 比對 |
| 正式機有沒有變動 | 只新增了 `oldbackup/261020/pixi_test_20261020_121821.dump`；容器沒有重啟，其他檔案都沒動 |

## 1. 程式檔差異

54 個檔案中有 51 個三方完全相同。有差異的 3 個如下：

| 檔案 | 正式機 vs HEAD | 正式機 vs 本機工作區 | 說明 |
|---|---|---|---|
| `docker-compose.yml` | 不同 | 不同 | 正式機在 postgres、api、nginx 三個 service 多了 `restart: unless-stopped`（2026-07-27 13:51 在正式機上直接改的，晚於 commit） |
| `static/solo_pixi_dashboard.html` | 只有換行字元不同 | **相同** | HEAD 是 LF；正式機和本機工作區是 CRLF/LF 混用。忽略換行後內容完全一樣，所以本機這項「未 commit 的修改」其實只是換行字元 |
| `log_iw_uploader_app.py` | 相同 | 只在正式機 | 本機已經搬到 repo 根目錄的 `Utilities/log_iw_uploader_app.py`（內容和 HEAD、正式機都一樣） |

正式機的 `.env` 只有一個 key：`LLM_API_KEY`（沒有拿回本機，也沒有看值）。

## 2. 資料庫

| | 正式機 | 本機（還原後） |
|---|---:|---:|
| `test_results` | 180 | 180 |
| `test_measurements` | 196,661 | 196,661 |
| `data_quality_issues` | 15 | 15 |
| `upload_batches` | 0 | 0 |
| `alignment_targets` | 1 | 1 |
| 工單 `5101-260715003` PASS / FAIL / STOP | 121 / 13 / 46 | 121 / 13 / 46 |
| DB 大小 | 54 MB | — |

- 備份：正式機 `oldbackup/261020/pixi_test_20261020_121821.dump`（6,080,669 bytes），本機 `dbbackup/261020/` 一份。
  兩邊的 SHA-256 都是 `4150e43c…53774e`。
- 本機原本沒有 iw volume，用 `system_up.sh` 建立新的 stack 之後，再用 `pg_restore --clean --if-exists` 還原。
- 正式機的資料就是 180 筆驗收資料集，到目前沒有新上傳的資料（`upload_batches` = 0）。

## 3. API 比對（正式機 vs 本機，22 個不需要參數的 GET 端點）

- 19 個完全相同。
- `/api/wifi-metrics`、`/api/calibration`：只有浮點數最後一位不同（加總順序不同），四捨五入到 1e-9 後相同。
- `/api/wifi-analysis`：`top_metrics` 有 1 項不同，**這是程式的 bug，不是資料不同**。
  `api/query_service.py:483` 用 `ORDER BY COUNT(*) DESC LIMIT 30`，但第 30 名附近有很多 metric 的 count 都是 1902，
  沒有第二排序鍵，所以每次查詢列出來的項目可能不一樣。列入改善計畫。

## 4. 測試

在本機（Python 3.12 容器，連本機 DB 5434）跑 `pytest tests/`：**17 passed**。

## 5. 待決定

- `docker-compose.yml` 的 `restart: unless-stopped` 要不要收進 repo
- `static/solo_pixi_dashboard.html` 的換行字元要統一成哪一種
- `log_iw_uploader_app.py` 搬到 `Utilities/` 要不要 commit；正式機上的舊檔要怎麼處理

## 6. 決定與處理（branch `sync/remote-261020`）

| 項目 | 決定 | 處理 |
|---|---|---|
| `docker-compose.yml` | 收進 repo | 改用正式機版本（多了 `restart: unless-stopped`） |
| `solo_pixi_dashboard.html` 換行 | 還原成 HEAD 的 LF | `git checkout`；新增 `.gitattributes`（`* text=auto eol=lf`、`*.cmd` 用 CRLF） |
| uploader 搬到 `Utilities/` | commit 搬移，正式機先不動 | 修正 path bootstrap：搬移後找不到 `ingestion`，現在也會找 `../iw-solo-pixi-essential/`；README 的執行指令改成新路徑 |
| `dbbackup/` | 不進 git | 加進 `.gitignore` |

所有驗證都在本機做，正式機要等使用者確認後才部署。
