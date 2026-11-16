# Coupon GO! — 全棧重構（開發版）

> ⚠️ **Google 憑據：上線前必須換成自有新 Key / 新 Client ID**
>
> 目前 `infra/.env`（本地文件，**不進倉庫**）裡的 Google API Key 和 2 個 OAuth
> Client ID，是暫時沿用原 Coupon GO! 1.2.6 APK bundle 裡提取的舊值，讓 Places
> 接口和 Google 登錄先跑起來。上線前必須：
>
> 1. 在 Google Cloud 新建自己的項目，生成新的 API Key 和 OAuth Client ID；
> 2. 把新值填進本地 `infra/.env`，替換舊值；
> 3. 給原 API Key 加 API 限制（僅限 Places API），並輪換 / 作廢原 Key。
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
放入倉庫；品牌素材待版權方提供後再替換。原 bundle 裡的 Google API Key /
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
alembic/ 數據庫遷移（0001 初始建表）
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
# 13 個測試：鑑權（註冊/登錄/OTP/忘記密碼）、首頁分區、搜尋三分支、
# 領券→QR→掃碼核銷→積分鏈（+10/+20/+15）、重複領取/重複核銷攔截、
# 9 積分兌換、商戶發券預覽→建立→審核、用量數據、權限攔截
```

## 還沒做的事

- Flutter 真機編譯與 `flutter analyze`（本機無 Flutter SDK，未驗證）
- 商店上架（App Store / Google Play）、應用簽名、深鏈
- 真實商户數據對接、FCM 推送、支付、社交登錄服務端驗證
- Google Key / Client ID 更換（見頂部）
- 生產部署（香港區服務器、域名、TLS、備份、監控）
- 原 APK 逆向報告：`coupon-go-apk/reverse/REVERSE.md`（不在本倉庫）
