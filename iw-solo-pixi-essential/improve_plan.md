# IW61x Solo PIXI Dashboard 改善計畫

| 項目 | 內容 |
|------|------|
| 文件版本 | **v0.2**（2026-10-20；附錄 A Q1–Q17 已對齊） |
| 參考 | pixi-dashboard（`Solo_PIXI_MP_Monitor_DashBoard`）v3.1.0 的 `improve_plan.md` 與實作 |
| 範圍 | `iw-solo-pixi-essential/`（看板、API、匯入、部署）；桌面 uploader 只修共用程式 |
| 調查方式 | 正式機程式與 DB 已對齊到本機（見 `docs/sync/remote_diff_261020.md`）；逐檔審閱前後端，在本機實測 API、SQL（EXPLAIN）、無頭 Chrome、AI 報告 |
| 狀態 | 第 1＋2＋3＋4 階段**本機完成**（見第 6 節，v2.0.0）；尚未部署到正式機；第 5 階段（部署流程）未開始 |

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

## 5. 第 2＋3＋4 階段實作方式（Q18–Q25）

1. **後端結構**同 pixi-dashboard v3：`api/routers/`（system、analytics、auth、admin、tweak、upload、reports、legacy）＋`api/services/`（stats、measurements、filters、llm、reports、report_text、upload_chat、ingest、migrations、scheduler）。程式碼打包進 image，開發時用 `docker-compose.dev.yml` 掛載原始碼。
2. **舊 API 不變**：舊單檔頁面用的端點搬到 `routers/legacy.py`，回應格式不改，`/legacy.html` 照常可用。
3. **新增物件**（只新增，不改既有表）：`upload_log`（上傳批次、對話、驗證結果）、`upload_runs`（批次 ↔ `test_results.id`，撤銷用）、`ai_reports`、`app_meta`、`job_runs`，materialized view `mv_unit`（每台機器每張工單的首次／最後／任一 PASS）與 `mv_metric_day`（每天每個 metric 的筆數、平均、標準差、最小／最大、規格、失敗數，用來算 margin／Cpk）。
4. **前端**以 pixi-dashboard 的 `web/` 為底（側欄、頂部篩選列、KPI 卡片、圖表面板、Dark／Light、英文預設＋中文切換），內容換成 IW611：首頁（Q1、Q2）、工單、Fail List、Fail Analysis、量測分析（BT／WiFi／其他頁籤，Q8）、DB Tweak、Data Alignment、Log Uploader（AI 助理）、帳號管理。
5. **AI 上傳助理**：上傳 zip → 伺服器切檔分析（不寫 DB）→ AI 對話問工單號與計畫數（規則決定、AI 只負責對話；LLM 離線自動改表單）→ 確認後背景寫入並串流進度 → 紀錄可查、可撤銷。
6. **AI 報告**：事實表（Q11）＋固定格式 JSON、數字由程式填、輸出檢查；中英兩版；上傳後自動＋每日 18:00＋手動（Q12）；`ai_reports` 保存與比較；thinking 關閉、模型 `Qwen3.8-27B-UD-Q6_K_L`。

---

## 6. 實作紀錄

### 第 1 階段：止血（2026-10-20，本機完成，未部署）

| 項目 | 處理 | 驗證 |
|------|------|------|
| S1 token 可偽造 | 改成 pixi-dashboard 的做法：登入 token 是亂數，DB 只存 SHA-256（`app_sessions`），**沒有簽章金鑰**；改 `ADMIN_PASS` 會讓舊的管理者登入失效。compose 傳入 `ADMIN_USER`／`ADMIN_PASS`；用預設密碼時 API 會記警告 | 舊格式（預設金鑰自簽）的 token → 401 |
| S2 不用登入就能寫入 | Alignment 目標、上傳、raw log、DB Tweak、reparse 都要登入；**管理者**／**上傳者**兩種角色（Q14），上傳者帳號在 `app_users` | 未登入 401、上傳者做管理動作 403 |
| S3 | token 只接受 `X-Tweak-Token` header（不再接受網址 `?token=`）；刪除需 `confirm=DELETE`；CORS 預設只允許同源；帳號、目標、刪除、上傳、reparse 都寫入 `account_audit` | 測試 |
| S4 | 統一錯誤格式 `{error, error_zh, detail, code}`，不回傳例外原文；`source_file` 畫面與 API 只顯示檔名（DB 保留原值，Q10）；AI 報告 Markdown 經 DOMPurify 3.1.6 | 測試＋瀏覽器 |
| R1 連線外洩 | 每個 worker 一個連線池，例外時一定歸還 | 60 次錯誤＋正常請求後連線數不增加 |
| R2 錯誤參數 500 | `day` 必須是日期、`month` 1–12、`week` 1–53、`year` 2000–2100、`page_size` 數字或 All；Alignment 目標必須是整數 | 一律 400 |
| R3 匯入中斷 | 每個檔案一個 SAVEPOINT，單檔失敗只回滾該檔；網頁與桌面上傳都寫 `upload_batches`，`data_quality_issues.batch_id` 有值；上傳在背景執行緒處理，不卡住 API | 測試 |
| R4 AI 快取 | 快取鍵加上資料指紋（筆數＋最大 id），上傳或刪除後兩個 worker 都不會再給舊報告；`lang`／`mode` 只接受固定值 | 測試 |
| B1 截斷 | 移除 `LIMIT 5000`；Fail List 在 SQL 篩 FAIL/STOP 並分頁 | 測試 |
| B6 ISO 週 | 三份篩選函式合併成 `sql_filters()`；有週時年份用 ISO 年 | 測試 |
| B7 排序 | top 30 加第二排序鍵 | 連續呼叫結果相同 |
| U1–U3 | AI 連結不再讓畫面變空白；報告視窗只有 Esc／×／點外面才關、不自動關閉、文字可選取、產品名改 IW611，英文完成後按「中文 ▶」看中文；API 失敗顯示訊息與重試；API 狀態與「資料更新時間」會更新 | 瀏覽器 29 項 |
| Q7 輪播 | 閒置輪播與排程輪播停用（每小時閒置時的資料更新保留） | 瀏覽器 |
| 其他 | AI 報告的失敗步驟改寫「failed attempts」（原本誤寫 units）；reparse 一次只載入一筆 log，並修正 Windows 路徑檔名；schema 初始化加 advisory lock，兩個 worker 不會同時執行 | |

- **測試**：pytest 43 項全過（原 17 項＋單元 14 項＋API 整合 13 項，整合測試用暫時資料庫 `iw_it_*`，跑完刪除）；180 筆 dry run 0 錯誤；瀏覽器 29 項全過（`tests/browser/stage1_browser.js`）。
- **與改動前比對**：31 個 API 回應除了刻意修正的項目（排序、錯誤格式、`/health` 多 `db_ms`）以外完全相同。
- **新增資料表**（Q9）：`app_users`、`app_sessions`、`auth_log`、`account_audit`。既有資料表沒有變更。
- **部署到正式機前要決定**：正式機 `.env` 目前沒有設 `ADMIN_PASS`，會沿用預設 `pixipass`；部署時需要設定新密碼。

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
| Q18 | 下一輪範圍 | **第 2＋3＋4 階段一起做**，架構參考 pixi-dashboard v3（routers／services／web） |
| Q19 | 資料庫原則 | **既有資料表與欄位完全不改**；可新增獨立的資料表與 view；桌面上傳程式與舊資料照常可用 |
| Q20 | 網頁上傳格式 | 原始工單 zip（含各站 `Log_all.txt`，伺服器自動切檔）＋已切好的 .txt／zip 都接受 |
| Q21 | 撤銷上傳 | 支援；用新的對照表 `upload_runs` 記錄每批新增的資料，不改 `test_results` |
| Q22 | 計畫生產數 | 必填；上傳成功後寫入 Data Alignment 目標數 |
| Q23 | 舊頁面 | 新版上線；舊單檔暫留 `/legacy.html` 供對照，驗收後刪除；沒在用的 10 個舊多檔頁面現在刪除 |
| Q24 | 上傳助理語言 | 跟著介面語言（英文／中文）切換 |
| Q25 | 上傳紀錄可見範圍 | 上傳者看、撤銷自己的；管理者看、撤銷全部並可依上傳者篩選 |
| Q26 | 下一步 | 使用者在本機檢查 v2.0；同時做第 5 階段（deploy.sh、每日備份、操作手冊），只在本機演練 |
| Q27 | AI 報告速度（中英合計約 55 秒） | 可以接受（上傳後與每日自動產生並保存） |
| Q28 | 舊單檔頁面 `/legacy.html` | **部署時就移除**（連同 `/api/legacy`） |
| 其他 | AGENTS.md 第 9 項 | 已改為「資料庫存的 session token，管理者＋上傳者」（2026-10-20） |
| 其他 | 管理者密碼 | 先沿用預設；帳號管理頁第 4 階段完成後再看 |

### 第 2＋3＋4 階段：數字、AI、介面（2026-10-20，本機完成，未部署，v2.0.0）

**架構**（同 pixi-dashboard v3）
- 後端 `api/routers/`＋`api/services/`；舊版 API 原封不動搬到 `api/legacy/`，掛在 `/api/legacy`，舊單檔頁面在 `/legacy.html` 照常可用（Q23），只加了一段把 `/api/…` 導到 `/api/legacy/…` 的程式。
- 前端 `web/`：以 pixi-dashboard v3.1.0 的 `web/` 為底（側欄、篩選列、KPI 卡、圖表面板、Dark／Light、英文預設＋中文），內容全部換成 IW611（Q16）。沒在用的 10 個舊多檔頁面已刪除。
- 程式碼打包進 image（`api/Dockerfile`），開發用 `docker-compose.dev.yml` 掛原始碼；nginx 直接提供 `web/`，有 gzip。
- splitter 核心搬到 `ingestion/splitter.py`，網頁上傳與桌面 splitter 共用（`Utilities/iw_log_splitter_core.py` 改為轉接）。

**資料庫**（Q19：既有表與欄位、索引都沒改）
- 新增：`upload_log`、`upload_runs`、`ai_reports`、`app_meta`、`job_runs`、`mv_unit_day`、`mv_unit`、`metric_day`、`metric_wo_state`。
- `metric_day` 原本設計為 materialized view，實測 1 萬筆測試／1,100 萬筆量測時每次刷新 55 秒；改成「只重算有變動的工單」的資料表後：沒變動 0.2 秒、一張工單變動 3 秒；第一次建立每張工單約 1–3 秒（正式機目前 1 張）。

**數字（第 2 階段）**
- 三種良率：首頁主數字 latest，first-pass／any-pass 並排（Q1）；台數與測試次數分開；未知 MAC 只算次數。
- 首頁 4 張圖（Q2）：三線良率趨勢（依時／日，不再限 07–19 點）、每小時測試量、失敗步驟前 5（次數／台數／重測後通過）、每月台數＋FAIL/STOP。
- 量測（Q8）：「規格檢查」頁籤，271 個有規格的 IW611 項目依「超出規格 → Cpk → 餘裕」排序，點一列看直方圖與規格線；BT（BER 2403／2478 MHz，修正 B4）、Wi-Fi（依頻段／標準／頻寬／速率）、其他（測試時間、XTAL、頻偏、重測）。
- 修正 B5：CCK／OFDM 在統計中依速率顯示為 11b／11g／11a（舊資料不改，parser 也已修正）；BDR 筆數不再重複計算。

**AI（第 3 階段）**
- 事實表（Q11）＋固定格式 JSON＋數字檢查（不在事實表的數字會重寫一次，仍有就標示）；中英兩版同時產生；`ai_reports` 保存、可比較、可追問、可下載 Markdown；上傳後自動＋每日 18:00＋手動（Q12）。
- 速度：thinking 關閉、模型 `Qwen3.8-27B-UD-Q6_K_L`，中英兩版合計 **55 秒**（原本單一中文 2 分 35 秒）。實際報告內容正確區分「台」與「次」（修正 B3）。
- AI 上傳助理：與 pixi-dashboard 相同的流程與規則（R1–R8），另加 **R9：非 IW611 的 log 不能上傳**；規則決定、AI 只負責對話；AI 離線自動改表單；回覆跟著介面語言（Q24）。
- 程式預設 AI 關閉（Q13），由 `.env` 的 `LLM_ENABLED`／`LLM_API_BASE` 開啟。

**驗證（全部在本機）**
- pytest：53 項＋61 個子測試全過（含 API 整合：用原始 Log_all 打包 zip 經網頁上傳 → 180 筆、與快照數字相同、撤銷只刪該批並還原計畫數、規則 R1–R7、AI 關閉時的退回）；180 筆 dry run 0 錯誤。
- 瀏覽器：新介面 34 項（中英各 9 頁無錯誤、規格檢查、報告、管理功能）；舊頁面 29 項；上傳助理端對端 16 項（AI 對話取得工單與計畫數 → 上傳 180 筆 → 撤銷 → 用表單重傳）。
- 網頁切檔的 hash 與桌面切檔完全相同（180／180），兩種上傳方式之間可正確判斷重複。
- 效能（本機合成 1 萬筆測試、1,100 萬筆量測）：單一工單（預設畫面）所有 API < 0.16 秒；「全部資料」的量測頁 0.58 秒、全部資料的直方圖 0.79 秒（之後有快取）。

**部署前需要的 `.env` 設定**（正式機目前只有 `LLM_API_KEY`）
- `LLM_ENABLED=true`、`LLM_API_BASE=http://10.20.30.23:8000/v1`、`LLM_MODEL=Qwen3.8-27B-UD-Q6_K_L`（Q13；不設的話 AI 功能會關閉）。
- `ADMIN_PASS`：目前依你的決定沿用預設。
- 部署後所有人需要重新登入；上傳者帳號在新的「帳號管理」頁建立。

**還沒做（第 5 階段）**：`deploy.sh`（備份 → 同步 → 建置 → 健康檢查 → 失敗自動還原）、每日 DB 備份排程、CI、操作手冊、移除 `/legacy.html`。

### 第 5 階段：部署與維運（2026-10-20，本機演練完成，未部署）

- **移除舊頁面（Q28）**：`/legacy.html`、`/api/legacy/*`、`api/legacy/` 全部刪除；契約測試確認不存在。
- **`scripts/deploy.sh`**：備份（`oldbackup/<YYMMDD>/files_<HHMMSS>.tgz`＋`pg_dump`，任一為空就中止）→ `rsync`（不碰伺服器的 `.env`、`.system_up.env`、`oldbackup/`、`dbbackup/`）→ `system_up.sh` → 健康檢查（版本號、首頁、`/api/summary`）→ 失敗自動還原檔案。支援 `--dry-run`、`--delete`、`--local`（本機演練）。
- **`scripts/backup_db.sh`**：每日 `pg_dump` 到 `dbbackup/`，保留 14 天（cron 指令見 `docs/deployguide.md`）。
- **`system_up.sh`**：nginx 改成重建容器（還原檔案後 bind mount 才會指到新的資料夾）。
- **「立即更新」改為公開**（和看板一樣不用登入，10 秒內只能按一次），按鈕不需要登入即可使用。
- **文件**：`docs/deployguide.md`（部署、第一次部署 2.0.0 的 `.env`、備份與回復）、`docs/operator_manual.md`（看板與上傳操作）。

**演練（本機，compose 專案 `iw-rehearsal`，port 8023／8024／5444，用正式機舊版檔案＋正式機資料庫備份）**
- 舊版 → 2.0.0：成功，約 10 秒；數字與正式機相同；舊的 `static/`、`log_iw_uploader_app.py` 被移除；`.env`、`.system_up.env` 保留。
- 故意部署壞掉的版本：自動還原成功、看板正常（過程中修正 3 個問題：舊版留下 root 擁有的 `__pycache__`、管線遮住 `system_up.sh` 的失敗、nginx 掛載指到已刪除的資料夾）。
- `backup_db.sh`：備份可 `pg_restore -l` 讀取，超過保留天數的檔案會刪除。
- 驗證：pytest 52 項＋39 個子測試全過；瀏覽器 34 項全過。

**還沒做**：CI（GitHub Actions）；正式機部署（等你在本機確認後同意才執行）。

### 部署前總驗證（2026-10-20，本機，compose 專案 `iw-verify`）

流程：正式機舊版檔案＋正式機資料庫備份 → 啟動舊版、記錄基準 → `deploy.sh --local --delete` 升級 → 逐項驗證。

| 項目 | 結果 |
|---|---|
| 升級 | 10 秒完成；同版本重新部署 9 秒；每次都有 `oldbackup/<YYMMDD>/` 檔案＋資料庫備份 |
| 資料庫相容（Q19） | 既有 5 張表的結構完全相同、內容逐筆 md5 相同；新增的索引都在新表上，既有索引 0 變動 |
| 新舊版數字 | 台數、三種良率、測試次數、重測、無 MAC、平均測試時間、重測清單、每月、每小時、FAIL/STOP 清單 12/12 相同 |
| 量測規格統計 | 271 個測試項目的 n、超規數、最小、最大、平均、標準差，和直接對原始表 SQL 算的 271/271 相同 |
| 瀏覽器 | 中英 9 頁、規格檢查、報告、管理功能 34/34（演練環境與本機各一次） |
| AI 上傳助理 | 對話取得工單與計畫數 → 上傳 180 筆 → 撤銷 → 表單重傳，16/16 |
| 桌面上傳程式 | PyQt5（無畫面模式）上傳 180 筆、再上傳 180 筆全部略過；hash 與網頁切檔完全相同 |
| AI 報告 | 中英兩版 11 秒、數字檢查通過；追問「哪個步驟失敗最多」回答 SAVE_NVRAM 13 次（正確）；比較、Markdown 下載正常 |
| 每日備份 | `backup_db.sh` 產生 5.9 MB 備份，可 `pg_restore -l` 讀取 |
| 並行 | 本機正式版（8003/8004/5434）與演練環境（8023/8024/5444）同時運作無衝突 |
| 測試 | pytest 68 項＋39 子測試全過；180 筆 dry run 0 錯誤（PASS 121／FAIL 13／STOP 46、工單 180/180） |

驗證中發現並修正 2 個小問題：
- 上傳後排入的 AI 報告，如果執行前資料已被撤銷，會記成「失敗」→ 改記「略過（no data）」。
- 桌面上傳程式的批次在 `upload_batches` 沒有工單號（由資料夾名稱決定）→ 批次結束時補上檔案解析出的唯一工單號（只寫入既有欄位的值，結構不變）。
