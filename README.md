# Coupon GO! — 全棧重構（開發版）

> ⚠️ **Google 憑據：上線前必須換成自有新 Key / 新 Client ID**
>
> Google API Key 和 2 個 OAuth Client ID 在本地 `infra/.env` 配置（本地文件，**不進倉庫**）。上線前必須：
>
> 1. 在 Google Cloud 新建自己的項目，生成新的 API Key 和 OAuth Client ID；
> 2. 把新值填進本地 `infra/.env`；
> 3. 給 API Key 加 API 限制（僅限 Places API）。
>
> 倉庫裡只有佔位符（`infra/.env.example`），**沒有任何真實 Key**。

## 這是什麼

Coupon GO! 優惠券平台的**前後端重構開發版**：Flutter 前端 + FastAPI 後端 +
PostgreSQL + Redis，部署目標為香港區。

來源：對 Coupon GO! 1.2.6（`com.techmatch.coupongonew`）APK 的唯讀靜態逆向
（見 `docs/` 引用的逆向報告要點），以及靜態 MVP「Coupon Go! MVP版」的
功能閉環：發現 → 領取 → 錢包 → 出示 → 核銷。**沒有使用原 App 的任何源碼或
後端**——原後端 `https://app.coupon-go.com/api/v3/` 目前無可用服務，
API 是按逆向出的 endpoint 語義重新設計的。

## 不是什麼

- **不是生產可用版本**：後端停擺、無真實商戶數據、社交登錄目前是開發 stub、
推送未接、支付未做。不要直接上線。
- **種子數據是演示數據**：20 間商户、36 張券 + 1 張 9 積分兌換券全部是為
開發編造的，**不是真實商戶數據**，不要對外宣稱為真實商户。
- **素材來源**：`apps/mobile/assets-packed/` 裡的 112 個圖片/字體文件來自
Coupon GO! 1.2.6 APK 拆包，按 Miso（版權方陳述，**未獨立核實**）的指示
放入倉庫；品牌素材待版權方提供後再替換。Google API Key /
OAuth Client ID 的**值絕不在倉庫裡**。

## 倉庫結構

```
apps/mobile/ Flutter App（消費者版 + 商戶版）
lib/ main / theme / api_client / models / widgets / screens
assets-packed/ APK 拆包素材的 base64 打包（見下）
tools/ unpack_assets.py（解包）/ pack_assets.py（打包）
apps/api/ FastAPI 後端
app/ main / config / models / schemas / routers / seed
app/routers/ auth / home / coupons / wallet / search / notifications / vendor
alembic/ 數據庫遷移（0001 初始建表；0002_line_a 核銷加固；0002_line_b 評分 v1 + 後台 v1）
tests/ pytest（13 個測試）
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

### 前端

```bash
cd apps/mobile
python3 tools/unpack_assets.py
flutter pub get
flutter run --dart-define=API_BASE_URL=http://localhost:8000
# 真機調試：把 API_BASE_URL 改成電腦局域網 IP
```

## 功能概覽

**消費者版**：Splash（原 splash.gif，約 3.1 秒）、首頁七分區
（熱門優惠券 → 最多人搶 → 限時搶購 → 本週精選 → 為你推薦 → 常用商戶 →
新加入商戶）、搜尋三分支建議（券 / 有分店商户 / 商户）、分類頁、券/商户詳情、
收藏、Q Wallet（未使用 / 已使用 / 已失效三狀態、按到期排序）、QR 出示
（15:00 倒數）、Q Wallet 積分（領券 +10、核銷 +20、評價 +15、9 分兌換券）、
通知中心、設置（語言、限時推送開關）、Q仔助手（本地規則問答）、新手指南 FAQ。

**商戶版**：金色企業版、發券兩步（預覽 → 提交審核）、掃碼核銷（相機 +
手動輸碼，錯店/重複/過期/每日 200 次上限）、用量數據、訂閱計劃、商户錢包。
掃碼核銷現為兩步：預核銷（唯讀查券、顯示券面與門店）→ 確認核銷（冪等鍵，
重放只算一次）；核銷後 10 分鐘內可撤销（券退回未使用、換新碼）。

**地圖（線 A）**：首頁「附近商户」入口 → 附近列表（Haversine 距離排序）+
位置示意 marker 視圖 + Google Maps 導航跳轉；支援裝置定位（授權被拒則
用預設中心 / 手動選地區）。示意圖是非實景地圖，Google Maps 瓦片視圖未接。

**評分 v1（線 B）**：口味 / 環境 / 服務三維 5 分評價 + 文字 / 晒圖
（預留）/ 標籤，只允許已核銷用戶評價；評價得 +15 積分與計入星級
拆開——領積分的評價不計入星級，不領積分才計入；商户頁展示聚合分
（總分 + 三維 + 計入星級的評價數）與評價列表，商户可回覆、可申訴。

**後台 v1（線 B）**：商户入駐審核（提交 → 通過 / 拒絕，拒絕須填原因，
被拒可重新提交；通過後申請人升為商户角色）+ 隻讀看板（商户 / 券 /
核銷數據查看，核銷記錄可導出 CSV）+ 用戶列表與操作日誌查看；目前
只有 `admin` 單一角色，權限分權、結算、內容管理留給後台 v2（F 線）。

**登錄**：郵箱/密碼 + 6 位 OTP（註冊/忘記密碼）、Google/Facebook/Apple
社交登錄（開發 stub，上線前須做服務端 token 驗證）、customer/vendor
分開帳戶與錢包、JWT 鑑權。

## 種子帳戶（演示用，上線前改密碼）

| 帳戶 | 密碼 | 角色 |
|---|---|---|
| demo@coupon-go.example | Demo123! | customer |
| vendor@coupon-go.example | Demo123! | vendor |
| admin@coupon-go.example | Demo123! | admin（審核發券） |

種子數據：20 間商户、36 張券（13 張限時）、1 張 9 積分兌換券、
3 個訂閱計劃。

## 測試

```bash
cd apps/api
../../.venv/bin/python -m pytest tests/ -v
# 33 個測試：鑑權（註冊/登錄/OTP/忘記密碼）、首頁分區、搜尋三分支、
# 領券→QR→掃碼核銷→積分鏈（+10/+20/+15）、重複領取/重複核銷攔截、
# 9 積分兌換、商戶發券預覽→建立→審核、用量數據、權限攔截
# 線 A 新增 11 個：預核銷→核銷→重放冪等→撤销窗口（期內恢復 / 超時拒絕 /
# 重複撤销拒絕）、錯店預核銷、無預核銷直接核銷仍可用、核銷記錄導出、
# 附近距離排序 + 半徑篩選、缺定位 400、商户地圖連結
# 線 B 新增 9 個：未核銷不可評、積分加分但星級不變、不領積分才計入星級、
# 重複評價攔截、評價校驗、商户回覆與差評申訴、入駐審核流（通過/拒絕/重提）、
# 後台隻讀看板（含核銷 CSV 導出）
```

## 還沒做的事

- Flutter 真機編譯與 `flutter analyze`（本機無 Flutter SDK，未驗證；線 A
  新增的 `url_launcher` / `geolocator` 依賴未跑過 `flutter pub get`，須在
  有 SDK 的機器上驗證）
- Google Maps 實景瓦片地圖視圖（線 A 目前是位置示意 marker 圖；接
  `google_maps_flutter` 需原生 API Key 配置 + 真機驗證）
- 出示頁原生防截屏（Android FLAG_SECURE / iOS 截屏遮罩；線 A 目前是動態
  一次性兌換碼 + 輪換 + 提示文案）
- 商店上架（App Store / Google Play）、應用簽名、深鏈
- 真實商户數據對接、FCM 推送、支付、社交登錄服務端驗證
- Google Key / Client ID 更換（見頂部）
- 生產部署（香港區服務器、域名、TLS、備份、監控）
- 原 APK 逆向報告：`coupon-go-apk/reverse/REVERSE.md`（不在本倉庫）
