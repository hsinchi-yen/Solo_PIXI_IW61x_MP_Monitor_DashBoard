# IW61x Solo PIXI Dashboard 改善計畫

| 項目 | 內容 |
|------|------|
| 文件版本 | **v0.2**（2026-10-20；附錄 A Q1–Q17 已對齊） |
| 參考 | pixi-dashboard（`Solo_PIXI_MP_Monitor_DashBoard`）v3.1.0 的 `improve_plan.md` 與實作 |
| 範圍 | `iw-solo-pixi-essential/`（看板、API、匯入、部署）；桌面 uploader 只修共用程式 |
| 調查方式 | 正式機程式與 DB 已對齊到本機（見 `docs/sync/remote_diff_261020.md`）；逐檔審閱前後端，在本機實測 API、SQL（EXPLAIN）、無頭 Chrome、AI 報告 |
| 狀態 | **尚未實作**。所有驗證在本機進行；正式機等你確認後才部署 |

---

## 0. 前提與原則（已確認）

1. 所有開發與驗證都在本機（http://10.88.90.136:8004，資料為 2026-10-20 正式機快照）；正式機 `10.20.31.40` 只在你同意後部署，部署前先備份到 `oldbackup/<YYMMDD>/`。
2. 正式機已有資料：既有資料表欄位不 ALTER／RENAME／DROP；**允許新增** `mv_unit`／`run_summary`、`ai_reports`、帳號資料表與索引整理（Q9）。
3. 只支援 IW61x / IW611；IW416 不做。
4. **UI 的排列與風格和 pixi-dashboard v3.1.0 一樣**（Q16）：側欄導覽、頂部篩選列、KPI 卡片、圖表面板、主題與字級都沿用它的 `web/` 結構與 CSS；內容換成 IW611 的資料。
5. 盡量沿用 pixi-dashboard v3.1.0 已驗證的做法（前端模組、主題、i18n、AI 報告、帳號、`deploy.sh`），不重新發明。
6. 不做輪播（Q7）。

---

## 1. 現況總覽

### 1.1 規模
| 項目 | 數字 |
|------|------|
| 實際提供的頁面 | `static/solo_pixi_dashboard.html` 單一檔案 4,573 行（CSS 1,150、JS 2,820）、200 KB，10 個頁面、17 張圖、112 個函式 |
| 沒在用的頁面 | `static/index.html` 等 10 個 HTML＋`common.js`／`ai.js`／`styles.css`／`chart-lite.js`（`/xxx.html` 都 307 轉回 `/?page=`，沒有任何連結指向它們） |
| 後端 | `app.py` 338、`reference_routes.py` 560（模擬 QCA9377 專案的 API）、`query_service.py` 494、`admin_routes.py` 94、`upload_routes.py` 90 行；約 40 個端點 |
| 資料庫 | `test_results` 180 筆、`test_measurements` 196,661 筆、54 MB；每筆測試約 1,100 個量測值 |
| 測試 | 17 項全過，但只檢查路由存在，沒有驗證回傳數字、權限、錯誤參數 |

### 1.2 實測（本機，180 筆）
- 所有 API 10–95 ms，目前不慢。**問題在資料成長後**：大部分統計端點把資料列抓到 Python 再算，而且有 `LIMIT 5000`（見 B1）。
- 以「5 萬筆測試 × 1,100 量測值 ≈ 5,500 萬列」估算：`/api/wifi-metrics`、`/api/calibration` 每次約 8–10 秒、回應 16 KB → 約 4.5 MB；`/api/reparse` 會一次載入約 13 GB 的 raw log 而記憶體不足。
- 日期篩選用 `DATE()`／`EXTRACT()`，`idx_tr_start_time` 永遠用不到（EXPLAIN 確認全表掃描）。
- **AI 報告：一份中文報告 2 分 35 秒**；`/api/calibration` 會把整個 wifi-metrics 再算一次；Advanced 頁重抓 BT／WiFi 頁已抓過的資料。

### 1.3 現成的 bug（本機已重現）

**數字錯誤**
| # | 問題 | 位置 |
|---|------|------|
| B1 | `fetch_records` 預設 `LIMIT 5000`（最新 5000 筆）：summary、良率趨勢、fail analysis、test duration、AI 報告等**資料超過 5000 筆後只算最新 5000 筆**；Fail List 先取最新 10000 筆再篩 FAIL/STOP，舊的失敗會消失、總數錯誤 | `query_service.py:227`、`reference_routes.py:159`、`app.py:193` |
| B2 | 首頁良率是 **any-pass 99.17%**，first-pass 其實只有 **72.5%**；首頁顯示 STOP = 0，但有 46 筆 STOP 測試；畫面沒有標示是哪一種良率（核准決定 #4 的三種良率在 API 有，但單檔頁面沒顯示） | `query_service.py:20`、`html:2785` |
| B3 | AI 報告的數字互相矛盾：說「Stopped 0」、「FAIL 1 unit」，又說「SAVE_NVRAM 13 units」（13 是失敗**次數**不是台數）；報告用 latest-result，首頁用 any-pass | `app.py:250`、`query_service.py:123–128` |
| B4 | BT BER 用 2441／2480 MHz 查詢（QCA9377 的頻點），IW611 是 2403／2478，所以 **Avg BER 永遠是空的**；BER Delta 因 `Number(null)=0` 顯示綠色 0.0000 | `reference_routes.py:277`、`html:2957` |
| B5 | BDR PASS＋FAIL＝137，但只有 136 筆（同一筆混合結果被算兩次）；EVM 平均混入 `EVM_DB_ALL`／`AVG_S1`／`MAX_S1` 三種值；CCK11、OFDM54 被標成 `11n`；重測和 STOP 的量測一起平均 | `reference_routes.py:230、305`；parser |
| B6 | 年／週篩選混用一般年和 ISO 週（2024-12-30 被算成 2024 年第 1 週）；良率趨勢只看最新一天 07:00–19:59，夜班被丟掉 | `query_service.py:241–298`、`:50` |
| B7 | `/api/wifi-analysis` 的 top 30 沒有第二排序鍵，同分項目每次可能不一樣 | `query_service.py:483` |

**安全**
| # | 問題 | 位置 |
|---|------|------|
| S1 | **管理者 token 可偽造**：compose 沒有把 `ADMIN_SECRET`／`ADMIN_USER`／`ADMIN_PASS` 傳進容器，正式機用的是程式預設值 `change-me-local-secret`／`pixi`／`pixipass`；用預設金鑰自簽的 token 被 `/api/tweak/records` 接受（本機驗證） | `admin_routes.py:15–17`、`docker-compose.yml` |
| S2 | 不用登入就能：改 Alignment 目標（`POST /api/alignment-targets`）、上傳 log（`POST /api/upload/`）、下載完整 raw log（`/api/raw-log/{id}`） | `reference_routes.py:368`、`upload_routes.py`、`app.py:281` |
| S3 | token 可放在網址 `?token=`（會進 access log）；DB Tweak 的刪除沒有二次確認；CORS `*`＋credentials | `reference_routes.py:494`、`app.py:33` |
| S4 | 錯誤訊息回傳例外原文（DB 錯誤、LLM 錯誤）；`source_file` 存的是上傳電腦的完整 Windows 路徑並回傳給瀏覽器；`marked` 輸出沒有 DOMPurify | `db.py:71`、`app.py:274`、`html:4499` |

**穩定性與介面**
| # | 問題 | 位置 |
|---|------|------|
| R1 | 任何查詢出錯，DB 連線不會關閉（`with get_connection()` 只結束交易）；30 次 `day=foo` 讓閒置連線從 2 增加到 13 | 所有端點 |
| R2 | 錯誤參數回 500：`day=foo`、`page_size=abc`、非 ASCII token、Alignment 目標非數字；`week=99` 被接受 | 各端點 |
| R3 | 匯入時某一個檔案寫 DB 失敗，同一批後面所有檔案都失敗（交易中斷沒有 rollback／savepoint），回 500；`upload_batches` 從未寫入，`data_quality_issues.batch_id` 全空 | `ingestion/service.py:153` |
| R4 | AI 報告快取在兩個 worker 各一份、10 分鐘；刪除或上傳後不清除；`mode` 任意字串會讓快取無限增長 | `app.py:53–57` |
| U1 | 側欄「AI Report — Latest WO」點下去**主畫面變空白**（與 pixi-dashboard 的 B1 相同） | `html:1156、1757` |
| U2 | 終端風報告視窗點任何地方就關閉、文字無法複製、30 秒自動關閉；中文失敗時 JS 例外；**產品名寫死 `PIXI-9377-S`** | `html:4267、4320、4379` |
| U3 | `fetchJson` 不檢查 HTTP 狀態、頁面載入沒有錯誤處理；Alignment 儲存失敗仍顯示「✓ Saved to DB」；閒置輪播遇錯就停住；「API: checking…」永遠不更新 | `html:1817、3435、2165、1180` |
| U4 | 兩套輪播（閒置＋排程）互相重疊；排程輪播切頁不重新載入，可能顯示舊圖 | `html:2157、4520` |
| U5 | QCA 遺留：Advanced 頁「BT BER 2441 vs 2480」圖永遠空白；Config Analysis 只回 `STOP 46`；Data Alignment 說明前後矛盾（寫 DB／存瀏覽器） | `html:3250、1615、1650` |

**設定**
| # | 問題 |
|---|------|
| C1 | `LLM_MODEL` 預設 `Qwen3.6-35B-A3B-Q6_K`，但 LLM 伺服器現在只有 `Qwen3.8-27B-UD-Q6_K_L`（伺服器忽略名稱所以還能用）；AI 預設開啟、LLM 位址寫在程式裡，和 AGENTS.md「AI 預設關閉、不寫死內部網址」不一致 |

---

## 2. 改善項目

每一項：**現況 → 改善 → 效益**，工作量為粗估人日。標「Q?」的細節待附錄 A 確認。

### A. 介面
#### A1. 首頁：三種良率講清楚
- **現況**：只顯示一個 99.17%，看不出是 any-pass；STOP 顯示 0；未知 MAC 7 筆看不到。
- **改善**：主數字是 **latest-result 良率**（Q1），first-pass 與 any-pass 並排、字較小、都標名稱；台數、PASS／FAIL／STOP 同時列出「台數」和「測試次數」；未知 MAC 次數；預設顯示**最新一張工單**，標題寫工單號和日期範圍（Q3）。
- 圖表 4 張（Q2）：**良率趨勢**（按小時／日，三種良率三條線，不再限 07:00–19:59）、**每小時測試量**、**失敗步驟前 5 名**（台數與次數）、**每月產量＋FAIL/STOP**（合併一張）。
- 工作量：2 天。

#### A2. 主題 Dark／Light、圖表配色跟著主題
- **Dark 預設＋Light**（Q5），沿用 pixi-dashboard 的 `css/theme.css`＋`core/charts.js`；移除 Cyber／Space。1 天。

#### A3. 介面語言
- **現況**：`lang="zh-TW"` 但 95% 英文，沒有 i18n。
- **改善**：**英文預設、一鍵切中文**（Q4），沿用 pixi-dashboard 的 `core/i18n.js`＋`strings.en.js`／`strings.zh.js`。1.5 天。

#### A4. 錯誤與空資料提示
- 統一 `apiGet()`／`ApiError`（沿用 `core/api.js`）：區塊內顯示「讀取失敗，重試」、「此範圍沒有資料」；API 狀態燈與「資料更新於」。修 U3。1 天。

#### A5. 篩選列、頁面合併、輪播
- 五層 Year／Month／Week／Day／WO 改成「時間範圍＋工單」（沿用 `core/scope.js`）；BT／WiFi／Advanced **合併為「量測分析」頁籤**（BT／WiFi／其他：測試時間、XTAL 校正、重測），資料只抓一次（Q6）；**兩套輪播與自動彈出 AI 報告全部移除**（Q7，同時修 U4）；移除 QCA 遺留圖表（U5）。頁面排列與 pixi-dashboard 相同（Q16）。2.5 天。

#### A6. 量測分析改成 IW611 的內容
- **現況**：BT／WiFi 頁沿用 QCA9377 的 metric（BER 2441/2480、固定 EVM 欄位）。
- **改善（Q8）**：以「**離規格多遠**」為主。IW611 的量測大多有 `limit_low`／`limit_high`（BT 64 種、WiFi 107 種、校正 15 種 metric），每個 metric 算 margin（距最近規格的距離）與 Cpk，列出**最接近規格的前 10 項**；再附重點圖：BT 功率／EDR EVM／BER（2403、2478 MHz）、WiFi EVM／功率／PER（目前 34 筆 PER 失敗）／mask margin、XTAL 校正；可篩頻段與速率。修 B4、B5。3 天。

### B. 效能與正確性（為資料成長準備）
#### B1. 統計全部在 SQL 完成，移除 `LIMIT 5000`
- 所有統計端點改成 SQL 聚合；Fail List 改 SQL 分頁；修 B1、B6。2 天。

#### B2. 每台機器／每次測試的摘要
- 新增 materialized view（Q9 已同意）：`mv_unit`（每張工單每個 MAC 的首次／最後結果、是否曾 PASS、測試次數）＋`run_summary`（每次測試的 XTAL、頻偏、BER、EVM、測試時間），上傳後刷新。量測頁和首頁都查這兩個，不再掃 `test_measurements`。2.5 天。

#### B3. 日期範圍查詢、索引、伺服器端分箱
- 篩選改成 `start_time >= :from AND start_time < :to`；直方圖用 `width_bucket()` 在 SQL 算好（不送逐筆值）；整理 `test_measurements` 的 5 個索引（刪掉重複的 `idx_tm_result_id`，加 `(technology, metric_name)`）（Q9）。1.5 天。

#### B4. 連線池、錯誤處理、傳輸
- psycopg2 連線池＋`finally` 關閉（修 R1）；pydantic 參數驗證（修 R2）；統一錯誤格式、不回傳例外原文；nginx gzip 與靜態檔快取。1 天。

#### B5. 匯入可靠性
- 每個檔案一個 SAVEPOINT，單檔失敗不影響整批（修 R3）；寫入 `upload_batches` 與 `batch_id`；`source_file` 在 DB 保留原值，**畫面、API、下載檔名只用檔名**（Q10）；Reparse 改背景分批。1.5 天。

### C. AI 報告（LLM 優化）
#### C1. 事實表＋固定格式，數字由程式填
- **現況**：prompt 只有總數、良率、重測率、前 3 失敗步驟；沒有 first-pass、STOP、未知 MAC、時間範圍、量測邊界；沒有「只能用這些數字」的限制；結果數字矛盾（B3）。
- **改善**：沿用 pixi-dashboard 的 `services/reports.py`＋`report_text.py`：伺服器先算事實表（Q11，四項全要）：**三種良率＋台數與次數分開**（PASS／FAIL／STOP、重測率、未知 MAC）；**失敗步驟與類別前 5**（台數、次數、重測後是否通過）；**量測離規格最近的前 5 項**（margin／Cpk）；**和最近 3 張工單比較＋測試時間範圍**；模型輸出固定欄位 JSON（總評、3 發現、3 建議、風險等級），數字由程式填入；輸出後檢查句中數字必須在事實表內；溫度 0.2。2.5 天。

#### C2. 速度：2 分 35 秒 → 目標 30 秒內
- 改用伺服器實際的模型 `Qwen3.8-27B-UD-Q6_K_L`、關閉 thinking／限制輸出長度、JSON 輸出比長篇 Markdown 短；中英兩版並行；手動產生時串流顯示（NDJSON）。1 天。

#### C3. 保存、自動產生、一個報告視窗
- 新增 `ai_reports` 資料表（資料雜湊相同就直接回傳，跨 worker 一致、刪除／上傳自動失效；修 R4）（Q9）；產生時機（Q12）：**上傳完成後自動＋每天 18:00 批次＋手動**，中英兩版一起產生，背景佇列一次一張；單一報告視窗沿用 `web/js/report.js`：中英切換、可選取複製、下載 Markdown、重新產生、和上一份比較、追問；修 U1、U2。2.5 天。

#### C4. 設定
- **程式預設 `LLM_ENABLED=false`、不寫死內部網址；正式機 `.env` 開啟**並設 `LLM_API_BASE`、`LLM_MODEL=Qwen3.8-27B-UD-Q6_K_L`（Q13）；同步更新 AGENTS.md；LLM 離線時按鈕灰掉並提示。0.5 天。

### D. 安全
- S1：compose 傳入 `ADMIN_*`，`ADMIN_SECRET` 未設定時拒絕啟動；正式機 `.env` 補上隨機金鑰（部署時做）。
- S1 修正**隨第 1 階段一起部署**（Q17）。
- S2（Q14）：沿用 pixi-dashboard 的帳號機制，分**管理者**（刪除、改 Alignment 目標、管理帳號）與**上傳者**（上傳、下載 raw log）；看板瀏覽不需登入。
- S3／S4：只接受 `Authorization` header；刪除需二次確認；CORS 限定來源；DOMPurify。
- 合併兩套登入（`/api/tweak/login` 與 `/api/admin/login`）成一套。
- 工作量：1.5 天。

### E. 可維護性
#### E1. 前端拆檔
- 單檔 4,573 行拆成 pixi-dashboard v3 的 `web/` 結構（`index.html`＋`css/`＋`js/core`＋`js/pages`，ES module、不用 build）；刪除沒在用的 10 個舊 HTML 與 `common.js`／`ai.js`／`styles.css`／`chart-lite.js`；vendor 檔名加版本。3 天。
#### E2. 後端分模組
- `routers/`＋`services/`＋`db.py`＋`schemas.py`（同 pixi-dashboard）；合併 3 份篩選函式、4 種 any-pass 算法、2 套登入、2 個 raw-log；lifespan 取代 `on_event`；logging 含請求耗時；`/health` 回報版本、DB、LLM、view 更新時間；Dockerfile COPY 程式（不靠 10 個 bind mount）；schema 版本化，只由一個程序執行；`requirements.txt` 固定版本。3 天。
#### E3. 測試
- API 數字測試（以 180 筆資料的已知值：first-pass 72.5%、any-pass 99.17%、STOP 46…）、權限 401 測試、錯誤參數測試、BT／WiFi metric 測試、匯入失敗測試；瀏覽器測試（無頭 Chrome，10 頁無 console error）。2 天。
#### E4. 部署
- 沿用 pixi-dashboard 的 `scripts/deploy.sh`（備份 → rsync → build → 健康檢查 → 失敗自動還原）與 `backup_db.sh`（每日 pg_dump、保留 14 天）；`deployguide.md`。1 天。

---

## 3. 執行順序與階段（Q15：止血 → 數字 → AI → 介面 → 結構）

| 階段 | 內容 | 預估 | 驗收（全部在本機） |
|------|------|------|------|
| **1. 止血** | S1–S4、R1–R4、B1、B6、B7、U1–U3、D 安全、E3 的基本測試 | 4 天 | 每個 bug 有測試且不再重現；用預設金鑰自簽的 token 被拒 |
| **2. 正確的數字** | A1 三種良率、A6 IW611 量測、B2 摘要 view、B3 SQL 化 | 7 天 | API 數字與 180 筆手算基準一致；用合成資料放大到 5 萬筆，每個端點 < 0.5 秒 |
| **3. AI** | C1–C4 | 6.5 天 | 3 份報告數字 0 錯；單份 < 30 秒；跨 worker 一致 |
| **4. 介面** | A2–A5、E1 拆檔：以 pixi-dashboard `web/` 為底重建頁面（Q16），舊單檔保留到驗收完成 | 8 天 | 與 pixi-dashboard 並排比對版面一致；全部頁面無 console error；1080p 不需捲動；你實看 |
| **5. 結構與部署** | E2、E4、B4、B5 | 6.5 天 | 整合與瀏覽器測試全過；`deploy.sh` 本機演練成功 |
| | **合計** | **約 32 天** | |

- 每階段：本機實作與驗證 → 回報 → 你測試 → 你同意後才部署到正式機。

---

## 4. 風險與對策

| 風險 | 對策 |
|------|------|
| 改寫後數字和現在不同 | 先把目前每個端點在本機 DB 的輸出存成基準；**有意修正的差異（B1–B6）逐項列出說明**，其他必須一致 |
| 正式機資料太少（180 筆），效能問題看不出來 | 用 180 筆複製、改 MAC／時間產生 5 萬筆合成資料，只在本機另一個 DB 測 |
| 改了首頁良率定義，產線看到數字變了 | 首頁同時列出三種並標名稱；部署前讓你和產線先看本機版本 |
| 新的 view／資料表影響桌面 uploader | 不動既有欄位；用桌面 uploader 在本機上傳 180 筆，比較時間與結果 |
| AI 報告仍然慢 | 先在本機量測不同設定（模型、關 thinking、JSON 長度）再決定 |
| 部署失敗 | `deploy.sh` 自動備份與還原；先在本機演練 |

---

## 附錄 A：需求對齊紀錄（2026-10-20）

| # | 問題 | 決定 |
|---|------|------|
| Q1 | 首頁主數字 | **latest-result 良率**為主，first-pass、any-pass 並排並標名稱 |
| Q2 | 首頁圖表 | 4 張：良率趨勢（小時／日）、每小時測試量、失敗步驟前 5、每月產量＋FAIL/STOP |
| Q3 | 首頁預設範圍 | 最新一張工單 |
| Q4 | 介面語言 | 英文預設，一鍵切中文（同 pixi-dashboard） |
| Q5 | 主題 | Dark 預設＋Light |
| Q6 | BT／WiFi／Advanced | 合併成「量測分析」頁籤 |
| Q7 | 輪播 | **不要輪播**（閒置、排程輪播與自動彈出 AI 報告全部移除） |
| Q8 | 量測呈現 | 以「離規格多遠」（margin／Cpk）為主，前 10 項＋重點圖，可篩頻段與速率 |
| Q9 | DB 新增物件 | 全部同意：`mv_unit`／`run_summary`、`ai_reports`、索引整理、帳號資料表 |
| Q10 | 上傳檔案完整路徑 | DB 保留原值，畫面與 API 只顯示檔名 |
| Q11 | AI 事實表 | 三種良率＋台數與次數、失敗步驟與類別前 5、量測離規格最近項目、和最近 3 張工單比較＋測試時間範圍 |
| Q12 | AI 產生時機 | 上傳後自動＋每日 18:00＋手動 |
| Q13 | AI 開關與模型 | 程式預設關閉；正式機 `.env` 開啟，模型 `Qwen3.8-27B-UD-Q6_K_L` |
| Q14 | 帳號權限 | 管理者＋上傳者兩種；瀏覽不用登入 |
| Q15 | 執行順序 | 止血 → 數字 → AI → 介面 → 結構；每階段本機驗證後回報 |
| Q16 | UI 排列風格 | **和 pixi-dashboard v3.1.0 一樣**（使用者 2026-10-20 指定） |
| Q17 | S1 預設金鑰 | 隨第 1 階段一起部署 |
