# API 概覽

Base URL：`http://localhost:8000`，路徑前綴 `/api/v3`。
Endpoint 名稱沿用 Coupon GO! 1.2.6 APK 逆向出的原始名稱（含原始拼寫：
`recomandation-coupons`、`get-used-merchents-shop`、`get-postion-shops`、
`get-nearest-branchs`、`store-scaned-qr-values`、`delete-vandor-image`）。
鑑權：`Authorization: Bearer <JWT>`（除註明遊客可調外都需要）。

## 認證 / 帳戶

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/new-register` | 註冊（customer/vendor），發 6 位 OTP |
| POST | `/new-register-vendor` | 商戶註冊 |
| POST | `/new-verify-otp` | 驗證 OTP（purpose=register/forgot），返回 JWT |
| POST | `/password/verify-otp` | 同上（密碼流程別名） |
| POST | `/new-resent-otp`、`/resent-otp` | 重發 OTP |
| POST | `/login` | 郵箱/密碼登錄 |
| POST | `/logout` | 登出 |
| POST | `/social-login` | 社交登錄（開發 stub；上線前須服務端驗證 token） |
| POST | `/password/forgot` | 忘記密碼，發 OTP |
| POST | `/password/reset` | OTP + 新密碼重設 |
| GET | `/get-profile` | 個人資料 |
| POST | `/update-customer-profile` | 更新資料 |
| POST | `/delete-account` | 刪除帳戶（填原因，停用帳戶） |
| GET | `/_dev/otp` | **僅 dev**：讀取最新未用 OTP（手動測試用） |

## 首頁 / 發現（部分遊客可調）

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/home` | 七分區：popular/week/most-grab/recommended/limited/常用/新加入商户 |
| GET | `/advertisement-banners` | 廣告橫幅（遊客可調） |
| GET | `/get-classification-coupons` | 分類券（`category_id`、`area` 篩選） |
| GET | `/get-limited-time-coupons` | 限時搶購 |
| GET | `/get-most-people-grab` | 最多人搶 |
| GET | `/recomandation-coupons` | 為你推薦 |
| GET | `/get-used-merchents-shop` | 常用商戶 |
| GET | `/get-postion-shops` | 按定位找店（`lat`、`lng`） |
| GET | `/get-area-list`、`/get-hong-kong-list`、`/get-kowloon-list`、`/get-new-territories-list` | 地區列表 |
| GET | `/get-category-list` | 分類列表 |
| GET | `/shop-details?shop_id=` | 商户詳情（+ 分店 + 本店券） |
| GET | `/branch-details?branch_id=` | 分店詳情 |
| GET | `/get-nearest-branchs?shop_id=` | 分店列表 |
| GET | `/coupon-details-new?coupon_id=` | 券詳情 |
| GET | `/get-collection-shops` | 收藏商户 |
| GET | `/favorite-coupon-list` | 收藏券 |
| POST | `/favorite-shop`、`/favorite-coupon-all` | 收藏/取消收藏 |
| GET | `/get-subscription-categories-list`、`/get-subscription-plan-list` | 訂閱計劃 |

## 券 / 錢包 / 積分

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/new-favorite-coupon?coupon_id=` | 領券 → Q Wallet，+10 分；重複領取 409 |
| POST | `/store-scaned-qr-values?wallet_item_id=` | 生成 QR 碼 + 6 位兌換碼，15:00 有效 |
| POST | `/new-scan-coupon` | 商戶掃碼核銷 `{code}`，+20 分；錯店/已用/過期/每日 200 次上限 |
| GET | `/get-favorite-coupons-list?page=&status=&sortby=` | 錢包三狀態：1 未使用 / 2 已使用 / 3 已失效 |
| GET | `/get-user-record-list` | 消費記錄 |
| GET | `/points` | 積分餘額 + 明細 |
| POST | `/review?wallet_item_id=` | 評價已用券，+15 分（一次） |
| POST | `/points/exchange?coupon_id=` | 9 積分兌換 9 分分券 |

## 搜尋 / 通知

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/auto-suggestion-new?keyword=` | 三分支建議：券 → 券詳情；有分店商户 → 分店；其餘 → 商户 |
| GET | `/get-search-history` | 搜尋記錄 |
| POST | `/delete-search-history` | 刪除搜尋記錄 |
| GET | `/new-filter-details` | 篩選（分類 + 地區） |
| GET | `/place/autocomplete/json`、`/place/details/json`、`/place/nearbysearch/json` | Google Places 代理（需 `GOOGLE_API_KEY`，未設則 503） |
| GET | `/get-notification-list` | 通知列表 + 未讀數 |
| POST | `/notifications/{id}/read`、`/notifications/read-all` | 標示已讀 |
| POST | `/limited-time-notification?enabled=` | 限時推送開關 |

## 商戶版

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/get-vendor-coupon-list` | 商戶的券 |
| POST | `/add-coupon-preview` | 發券預覽（第一步，不寫庫） |
| POST | `/coupon-previewed` | 發券建立（第二步，狀態 pending_review） |
| POST | `/vendor/coupons/{id}/approve?approve=` | 管理員審核通過/拒絕 |
| GET | `/get-vendor-usage-data-new` | 總下載/總使用 + 每券數據 |
| GET | `/get-vendor-record-list` | 商戶核銷記錄 |
| GET | `/get-company-list` | 商戶的公司/商店列表 |
| POST | `/assign-branches` | 指派分店 `{coupon_id, branch_ids}` |
| POST | `/update-branch-profile` | 更新分店資料 |
| POST | `/delete-vandor-image` | 刪除商户圖片 |
| POST | `/new-update-vendor-profile` | 更新商户資料（提交後待管理員批准） |
