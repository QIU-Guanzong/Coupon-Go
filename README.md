# 優惠券平台 — 全棧參考實現

> **憑據**：Google API Key 與 OAuth Client ID 在本地 `infra/.env` 配置（本地文件，**不進倉庫**）。上線前必須：
>
> 1. 在 Google Cloud 新建自己的項目，生成新的 API Key 和 OAuth Client ID；
> 2. 把新值填進本地 `infra/.env`；
> 3. 給 API Key 加 API 限制（僅限 Places API）。
>
> 倉庫裡只有佔位符（`infra/.env.example`），**沒有任何真實 Key**。

## 概覽

一個優惠券平台的全棧參考實現：Flutter 前端 + FastAPI 後端 +
PostgreSQL + Redis，部署目標為香港區。

核心閉環：發現 → 領取 → 錢包 → 出示 → 核銷。消費者版與商戶版在同一
App 內切換；另有管理後台。

## 狀態

- **不是生產可用版本**：無真實商戶數據、社交登錄目前是開發 stub、
推送未接、支付未做。不要直接上線。
- **種子數據是示例數據**：20 間商戶、36 張券 + 1 張 9 積分兌換券全部是為
開發編造的，**不是真實商戶數據**，不要對外宣稱為真實商戶。
- **素材**：`apps/mobile/assets-packed/` 裡的 112 個圖片/字體文件以
base64 文本打包（見下節）；品牌素材待提供後再替換。Google API Key /
OAuth Client ID 的**值絕不在倉庫裡**。

## 倉庫結構

```
apps/mobile/ Flutter App（消費者版 + 商戶版）
lib/ main / theme / api_client / models / widgets / screens
assets-packed/ 圖片/字體素材的 base64 打包（見下）
tools/ unpack_assets.py（解包）/ pack_assets.py（打包）
apps/api/ FastAPI 後端
app/ main / config / models / schemas / routers / seed
app/routers/ auth / home / coupons / wallet / search / notifications / vendor / cashback（線 D 返現）/ bargain（線 E 砍價）/ packages（線 C 套餐）/ admin_v2 + uploads（線 F 後台 v2）
app/models_cashback.py、app/cashback_service.py（線 D 返現，與 point_transactions 分開記賬）
app/models_bargain.py、app/seed_bargain.py（線 E 砍價）
app/models_packages.py、app/payments_gateway.py（線 C 套餐，Stripe 測試模式）
app/models_line_f.py、app/permissions_line_f.py（線 F 後台 v2：結算 / 對賬 / 發票 / 內容 / 角色）
alembic/ 數據庫遷移（0001 初始建表；0002_line_a 核銷加固；0002_line_b 評分 v1 + 後台 v1；0002_cashback 線 D 返現三表；0002_bargain_e 線 E 砍價；0002_line_c 線 C 套餐；0003_line_f 線 F 後台 v2；0004 返現 redemption 唯一；0005 FIX-3 行鎖兜底約束；0006 P1 返現撤销回滾 + 砍價退款網關 ref）
tests/ pytest（168 個測試）
infra/ docker-compose（api + postgres + redis）、.env.example
docs/ API.md（接口概覽）
```

### 為什麼素材是 base64 打包

本倉庫的發布通道只接受 UTF-8 文本，二進制文件無法直接推送。
`assets-packed/` 裡每個素材都是一份 `.b64` 文本 + `manifest.json`
（SHA-256 校驗）。clone 後跑一次解包即可：

```bash
cd apps/mobile
python3 tools/unpack_assets.py # 生成 apps/mobile/assets/（git-ignored）
flutter pub get
```

## 本地運行

### 後端（任選其一）

```bash
# 方式 A：docker-compose（推薦）
cp infra/.env.example infra/.env # 按需填值；GOOGLE_* 留空亦可跑
docker compose -f infra/docker-compose.yml up --build

# 方式 B：本地 venv + SQLite
cd apps/api
python3 -m venv../../.venv &&../../.venv/bin/pip install -r requirements.txt
SEED_ON_STARTUP=1../../.venv/bin/uvicorn app.main:app --reload
```

API 文檔：http://localhost:8000/docs ；健康檢查：`/health`。

數據庫遷移：

```bash
cd apps/api
alembic upgrade head
```

### 上線前配置（P1）

- **CORS**：`CORS_ORIGINS` 環境變量（逗號分隔的白名單）；開發環境未配置時
  默認為 `*`，非開發環境未配置時默認為空（不信任任何來源）。生產必須
  顯式列出前端域名。
- **SECRET_KEY**：非開發環境仍為開發預設值時，啟動即拒絕
  （與 FIX-1 的 `validate_payment_config` 同一啟動檢查；見
  `app/config.py`）。開發預設值絕不可用於非開發環境。
- **支付網關報錯**：接口統一返回「支付失败，请稍后重试」，網關原始報錯
  只寫服務端日誌，不透傳給用戶；套餐購買響應不再含 `test_mode` 字段。

### 前端

```bash
cd apps/mobile
python3 tools/unpack_assets.py
flutter pub get
flutter run --dart-define=API_BASE_URL=http://localhost:8000
# 真機調試：把 API_BASE_URL 改成電腦局域網 IP
```

## 功能概覽

**消費者版**：Splash（約 3.1 秒）、首頁七分區
（熱門優惠券 → 最多人搶 → 限時搶購 → 本週精選 → 為你推薦 → 常用商戶 →
新加入商戶）、搜尋三分支建議（券 / 有分店商戶 / 商戶）、分類頁、券/商戶詳情、
收藏、Q Wallet（未使用 / 已使用 / 已失效三狀態、按到期排序）、QR 出示
（15:00 倒數）、Q Wallet 積分（領券 +10、核銷 +20、評價 +15、9 分兌換券）、
通知中心、設置（語言、限時推送開關）、Q仔助手（本地規則問答）、新手指南 FAQ。
返現（線 D）：Q Wallet 內返現餘額與積分並列展示、明細分開記賬；提現（FPS /
銀行轉賬）；Missing Cashback 申訴。詳見下節「返現 2B（線 D）」。

**套餐 2A（線 C）**：付費套餐券（FPS + 信用卡，Stripe 測試模式，未接真實
商戶號）→ 訂單 → 支付成功後套餐券進 Q Wallet 三狀態 → 到店掃碼核銷；
未核銷隨時退、過期自動退（原路返回，1–3 個工作日），特價券可標不退；
退款後券作廢、不可再核銷。套餐列表/詳情、購買頁、我的訂單與退款入口
在 App 內（抽屜：套餐、我的訂單；錢包內套餐券有退款按鈕）。
代金券（免費領）與套餐券並行，領券閉環不動。

**商戶版**：企業版、發券兩步（預覽 → 提交審核）、掃碼核銷（相機 +
手動輸碼，錯店/重複/過期/每日 200 次上限）、用量數據、訂閱計劃、商戶錢包。
掃碼核銷現為兩步：預核銷（唯讀查券、顯示券面與門店）→ 確認核銷（冪等鍵，
重放只算一次）；核銷後 10 分鐘內可撤销（券退回未使用、換新碼）。

**地圖（線 A）**：首頁「附近商戶」入口 → 附近列表（Haversine 距離排序）+
位置示意 marker 視圖 + Google Maps 導航跳轉；支援裝置定位（授權被拒則
用預設中心 / 手動選地區）。示意圖是非實景地圖，Google Maps 瓦片視圖未接。

**評分 v1（線 B）**：口味 / 環境 / 服務三維 5 分評價 + 文字 / 晒圖
（預留）/ 標籤，只允許已核銷用戶評價；評價得 +15 積分與計入星級
拆開——領積分的評價不計入星級，不領積分才計入；商戶頁展示聚合分
（總分 + 三維 + 計入星級的評價數）與評價列表，商戶可回覆、可申訴。

**後台 v1（線 B）**：商戶入駐審核（提交 → 通過 / 拒絕，拒絕須填原因，
被拒可重新提交；通過後申請人升為商戶角色）+ 隻讀看板（商戶 / 券 /
核銷數據查看，核銷記錄可導出 CSV）+ 用戶列表與操作日誌查看；目前
只有 `admin` 單一角色，權限分權、結算、內容管理留給後台 v2（F 線）。

**後台 v2（線 F）**：結算單（按核銷生成 → 確認 / 爭議 / 解決 → 付款）、
結算週期為月結：結算單按自然月（period 為 YYYY-MM）生成，佣金與淨額
按該月核銷結算；
對賬（核銷 vs 結算行差異記錄與處理）、發票（申請 → 批准 / 拒絕 →
開具，掛結算單時不超過淨額）、內容管理（橫幅 / 公告 / 分類）、角色分權
（admin / finance / auditor / support / merchant-admin，店員按店授權）、
評價申訴解決（成立隱藏 / 不成立恢復計入星級）、晒圖上傳
（`POST /uploads/review-images`，本地佔位儲存，未接對象儲存）。
詳見 `docs/API.md`「後台 v2」「申訴解決」「晒圖上傳」三節。

**登錄**：郵箱/密碼 + 6 位 OTP（註冊/忘記密碼）、Google/Facebook/Apple
社交登錄（開發 stub，上線前須做服務端 token 驗證）、customer/vendor
分開帳戶與錢包、JWT 鑑權。

**砍價 2C（分享助力）**：砍價活動列表、發起砍價（鎖 1 份庫存）、分享連結、好友助力降價（每刀遞減，新用戶加成）、到底價 / 成團後低價下單，套餐券進 Q Wallet 三狀態（走現有出示 → 核銷閉環）；未成團 / 超時全額退款並釋放庫存；防刷（每人每單限助一次、同一設備去重、每日助力上限）。預設值：成團 5 人、每場 24 小時、活動 7 天、套餐券 30 天；預付仍是模擬記賬（未收真錢），退款改走支付網關（`refund_payment`，與套餐退款同一口徑，狀態 `pending/succeeded/failed` + `gateway_ref`，失敗可在同一退款記錄上重試）；砍價退款走支付網關，當前只接測試模式，未配置正式商戶密鑰時，真實商戶密鑰場景下退款會失敗；用戶可見的退款文案只有「退款将在 1–3 个工作日原路退回」，開發說明只寫在代碼註釋、日誌與本文檔。詳見 `docs/API.md`「砍價 2C」一節。

## 種子帳戶（演示用，上線前改密碼）

| 帳戶 | 密碼 | 角色 |
|---|---|---|
| demo@coupon-go.example | Demo123! | customer |
| vendor@coupon-go.example | Demo123! | vendor |
| admin@coupon-go.example | Demo123! | admin（審核發券） |
| finance@coupon-go.example | Demo123! | finance（結算 / 對賬 / 發票） |
| auditor@coupon-go.example | Demo123! | auditor（看板唯讀 + 申訴解決） |
| support@coupon-go.example | Demo123! | support（申訴解決 + 看板唯讀） |

種子數據：20 間商戶、36 張券（13 張限時）、1 張 9 積分兌換券、
3 個訂閱計劃。

## 測試

```bash
cd apps/api
../../.venv/bin/python -m pytest tests/ -v
# 168 個測試：鑑權（註冊/登錄/OTP/忘記密碼）、首頁分區、搜尋三分支、
# 領券→QR→掃碼核銷→積分鏈（+10/+20/+15）、重複領取/重複核銷攔截、
# 9 積分兌換、商戶發券預覽→建立→審核、用量數據、權限攔截
# 線 A 新增 11 個：預核銷→核銷→重放冪等→撤销窗口（期內恢復 / 超時拒絕 /
# 重複撤销拒絕）、錯店預核銷、無預核銷直接核銷仍可用、核銷記錄導出、
# 附近距離排序 + 半徑篩選、缺定位 400、商戶地圖連結
# 線 B 新增 9 個：未核銷不可評、積分加分但星級不變、不領積分才計入星級、
# 重複評價攔截、評價校驗、商戶回覆與差評申訴、入駐審核流（通過/拒絕/重提）、
# 後台隻讀看板（含核銷 CSV 導出）
# 線 D 返現 8 個：線下解鎖→可提現→提現鏈、僅同商戶解鎖、90 天過期作廢、
# 線上確認窗口、取消/退貨作廢、返現與積分分開記賬、申訴、未登錄攔截
# 線 E 砍價 12 個：發起→助力到底價→成團下單→套餐券進錢包→核銷、未成團超時全額退款、
# 庫存鎖/釋放、重複助力/自助/同設備攔截、成團人數達標
# 線 C 套餐 16 個：套餐列表/詳情/建立、下單→支付成功→券入錢包→核銷、
# 退款後券作廢且不可再核銷、特價不退攔截、已用券退款攔截、
# 支付失敗/逾時→重試/取消、過期自動退、webhook、下單冪等、
# 代金券與套餐券並行。
# 線 F 後台 v2 10 個：結算單生成→確認→付款、爭議→解決、對賬差異→處理、
# 發票申請→批准→開具及淨額上限、角色變更與權限矩陣、評價申訴解決、
# 橫幅/公告/分類內容管理、店員授權、晒圖上傳→評價帶圖。
# 其餘為 FIX-1/2/3、P1、F1–F3、N1 的修復回歸與併發用例（見各 test_* 檔案）。
```

## 返現 2B（線 D）

兩條狀態機，分開實現：

- **線上 / 通用**：`tracked → pending → confirmed → withdrawable`，按商戶確認
  周期確認（預設 30 天，`CASHBACK_ONLINE_CONFIRM_DAYS`）。v1 同步入賬直接寫
  `pending`（消費已由核銷記錄驗證）；`tracked` 保留給日後異步 / 未驗證入賬。
- **線下餐飲**：`pending → confirmed → withdrawable`，**90 天內在同一商戶再消費
  一次才解鎖上一次返現**；90 天內沒再來則過期作廢（`void_reason=expired_90d`）。
  追蹤以核銷記錄為準（`redemption_id`），不用 Cookie 點擊歸因。
- v1 沒有打款保留環節：`confirmed` 在同一輪掃描（sweep）內即轉為
  `withdrawable`，但 `confirmed_at` 會留痕；日後如需打款審核，可在
  `confirmed` 設停留點。`withdrawn` 為擴展的終態。

返現進 Q Wallet，與積分並列展示、明細分開記賬（絕不混用
`point_transactions`；另建 `cashback_transactions`）。接口見
`docs/API.md`「返現」一節：`GET /cashback`、`GET /cashback/transactions`、
`POST /cashback/withdraw`、`GET /cashback/withdrawals`、
`POST /cashback/transactions/{id}/void`（取消 / 退貨）、
`POST /cashback/missing`（申訴）。

**默認值假設（未與商戶談判前暫定，以環境變量覆蓋；上線前須與商戶確認）：**

| 項 | 默認值 | 環境變量 |
|---|---|---|
| 線下餐飲返現比例（固定） | 5% | `CASHBACK_DEFAULT_RATE` / `CASHBACK_DINEIN_RATE` |
| 線下解鎖 / 過期窗口 | 90 天 | `CASHBACK_DINEIN_UNLOCK_DAYS` |
| 線上確認周期 | 30 天 | `CASHBACK_ONLINE_CONFIRM_DAYS` |
| 提現門檻（香港） | HK$50 | `CASHBACK_MIN_WITHDRAW_HKD` |
| 每日提現上限 | HK$1,500 | `CASHBACK_DAILY_WITHDRAW_LIMIT_HKD` |
| 單筆返現上限 | 不設 | `CASHBACK_MAX_PER_TXN_HKD`（0 = 不設） |
| 申訴期限 | 消費後 30 天內，調查期最長 120 天 | `CASHBACK_MISSING_WINDOW_DAYS` |
| 返現資金來源 | 商戶佣金里出 | `funded_by=merchant_commission` |

提現規則：FPS / 銀行轉賬；收款賬戶姓名須與註冊姓名一致；v1 提現申請後狀態為 `pending`，待人工打款——**沒有真實 FPS /
銀行打款集成**，打款流程、提現審核後台、商戶佣金結算對接都還沒做。
FIX-2 已刪除 `POST /cashback/record`：返現只能由核銷成功後服務端內部
入賬（見 `app/cashback_service.py::record_spend`），
用戶自填金額 / 商戶 / 比例再也無法入賬；App 內的開發演示記賬表單一併刪除。

**核銷撤销與返現回滾（P1-1）**：商戶在 10 分鐘窗口內撤销核銷時，
該次核銷的返現一併作廢（`void_reason=revoked`），並回滾由它觸發解鎖的
前序 dine-in 待確認筆——那些筆重新變回待解鎖（`pending`），解鎖溯源
記在 `unlocked_by_redemption_id`，其他核銷觸發的解鎖不受影響。
已提現部分的處理規則（保守做法）：已提現的錢不直接扣負數，而是記一筆
負向調整（`cashback_adjustments`，`reason=revoked_withdrawn`），可提現
餘額隨之減少、可為負，餘額為負時提現被攔截（`withdrawable_balance`
低於提現金額即拒絕），直至後續返現補回。部分提現過的前序筆只把未提現
餘額退回待解鎖，已提現部分保留（該筆返現本身有效，只是確認被回滾）；
已全部提現的前序筆視為已完成，不動。

**冷靜期（P1-7，未改，待產品決定）**：`confirmed → withdrawable`
目前沒有冷靜期，確認後立即可以提現；保留現狀，待產品決定是否加
冷靜期後再改。

**提現戶名（P1-4）**：收款賬戶姓名須與註冊姓名（`User.name`）一致；
未填註冊姓名時拒絕提現，不再跳過校驗。

## 還沒做的事

- Flutter 真機編譯與 `flutter analyze`（本機無 Flutter SDK，未驗證；線 A
  新增的 `url_launcher` / `geolocator` 依賴未跑過 `flutter pub get`，須在
  有 SDK 的機器上驗證；含線 C 新增的套餐購買頁/訂單頁；管理端頁面只做了括號平衡粗查）
- Google Maps 實景瓦片地圖視圖（線 A 目前是位置示意 marker 圖；接
  `google_maps_flutter` 需原生 API Key 配置 + 真機驗證）
- 出示頁原生防截屏（Android FLAG_SECURE / iOS 截屏遮罩；線 A 目前是動態
  一次性兌換碼 + 輪換 + 提示文案）
- 返現（線 D）：真實 FPS / 銀行打款集成與提現審核後台；`record` 入口改為僅
  商戶 / POS 憑據可調用；商戶佣金結算對接；返現比例、90 天規則、HK$50 門檻
  與商戶談判定稿（現為默認值假設）
- 後台 v2 待定：auditor 暫不能審核入駐申請（v1 端點保持 admin-only，
  未改 v1 契約）；首頁仍調寫死的 `GET /advertisement-banners`，未切到
  CMS 的 `GET /banners`；晒圖上傳是本地磁盤佔位儲存（**開發佔位，上線前
  必須換對象儲存**，見 `app/routers/uploads.py`），上傳時已剝離 EXIF，
  無病毒掃描 / 縮圖；結算佣金比例默認 0.10，需按商戶談判
  後逐個設定
- 商店上架（App Store / Google Play）、應用簽名、深鏈
- 真實商戶數據對接、FCM 推送、社交登錄服務端驗證
- 支付：Stripe 正式商戶號接入與 webhook 簽名驗證（現為測試模式）
- Google Key / Client ID 更換（見頂部）
- 生產部署（香港區服務器、域名、TLS、備份、監控）

## 本地 PostgreSQL：跑 FIX-3 / F3 併發測試（真驗證）

> ⚠️ **SQLite 通過不算驗證**：`tests/test_fix3_row_locks.py` 和
> `tests/test_f3_settlement_locks.py` 在連不上 Postgres 時會靜默回落到
> SQLite（`BEGIN IMMEDIATE` 全事務串行化），`SELECT ... FOR UPDATE`
> 在 SQLite 上被忽略——回落模式只能證明加鎖後重查的邏輯，不能證明
> 行鎖本身存在。**併發用例必須在真 Postgres 上跑過才算驗證**。
> 跑完後檢查 `BACKEND`：在 `apps/api` 裡
> `python -c "import tests.test_fix3_row_locks as m; print(m.BACKEND)"`
> 必須輸出 `postgres`（不是 `sqlite-immediate`）；也可以直接查庫裡
> 有表（`psql <FIX3_POSTGRES_URL> -c "\dt"`）——SQLite 回落時庫裡是空的。

### 安裝與啟動（Ubuntu/Debian，本機無 docker 時）

```bash
sudo apt-get install -y postgresql        # 已驗證：PostgreSQL 16.15
sudo pg_ctlcluster 16 main start          # 集群裝完可能是 down 的，需手動啟動
pg_isready -h localhost -p 5432           # 應回 accepting connections
```

### 建用戶與測試庫（與 `infra/docker-compose.yml` 的 db 憑據一致）

```bash
sudo -u postgres psql -c "CREATE USER coupon_go WITH PASSWORD 'coupon_go' CREATEDB;"
sudo -u postgres psql -c "CREATE DATABASE coupon_go_test_fix3 OWNER coupon_go;"
sudo -u postgres psql -c "CREATE DATABASE coupon_go_test_f3 OWNER coupon_go;"
```

測試只會 `drop_all` / `create_all` 建表，**不會自己建庫**——庫必須先存在，
否則連接探測失敗、測試靜默回落 SQLite。兩個庫的用途不同，不要只建一個：
`coupon_go_test_fix3` 給 FIX-3，`coupon_go_test_f3` 給 F3。

### Python 驅動

測試用的是 `postgresql+psycopg2://`（顯式 psycopg2 驅動；裸
`postgresql://` 會讓 SQLAlchemy 2.x 去找 psycopg v3 而報錯）。共用 venv
（`~/workspace/coupon-go-fullstack/.venv`）裡已有 `psycopg2-binary 2.9.13`，
無需再裝；如在別的環境跑：

```bash
~/workspace/coupon-go-fullstack/.venv/bin/pip install psycopg2-binary
```

### 連接方式（環境變量，不寫進 `.env`）

| 測試文件 | 環境變量 | 默認值 |
|---|---|---|
| `tests/test_fix3_row_locks.py` | `FIX3_POSTGRES_URL` | `postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_fix3` |
| `tests/test_f3_settlement_locks.py` | `F3_POSTGRES_URL` | `postgresql+psycopg2://coupon_go:coupon_go@localhost:5432/coupon_go_test_f3` |

默認值即本地 Postgres 的地址，一般不用改；改地址/端口/密碼時設對應變量。
`FIX3_FORCE_SQLITE=1` 會強制回落 SQLite——只用於對照，正式驗證時不要設。

### 跑測試

```bash
cd apps/api
~/workspace/coupon-go-fullstack/.venv/bin/python -m pytest tests/test_fix3_row_locks.py -v
~/workspace/coupon-go-fullstack/.venv/bin/python -m pytest tests/test_f3_settlement_locks.py -v
```

已驗證（2026-11-16，真 Postgres 16.15）：FIX-3 全 14 個通過、F3 全 8 個
通過，6 個併發場景逐一通過——同額提現僅 1 路成功、同一訂單退款僅 1 筆
網關退款、搶最後 1 個庫存僅 1 單成功、同一結算單付款僅 1 次、併發開票
總額不超過淨額、同一核銷撤销僅 1 次（另有砍價搶最後 1 件 1 單成功）。
完整輸出見工作區 `fix3-postgres-run.log` / `f3-postgres-run.log`。

已驗證（2026-11-17，M2，真 Postgres 16.15，本地 apt 安裝）：FIX-3 全 16 個
通過（新增 2 個併發用例：兩人同時助力同一砍價不超底價、同一券碼同時掃
兩次僅 1 次核銷成功）、F3 全 8 個通過；`BACKEND` 兩文件皆為 `postgres`。
完整輸出見工作區 `fix3-postgres-run.log` / `f3-postgres-run.log`（本次已更新）。
