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
| POST | `/store-scaned-qr-values?wallet_item_id=` | 生成 QR 碼 + 6 位兌換碼，15:00 有效；每次出示輪換新碼（線 A 防截圖） |
| POST | `/new-scan-coupon/prepare` | **線 A** 預核銷（唯讀查券，`{code}`）→ `can_redeem` + 5 分鐘 prepare token，不改券狀態 |
| POST | `/new-scan-coupon` | 商戶掃碼核銷 `{code, idempotency_key?, prepare_token?}`，+20 分；錯店/已用/過期/每日 200 次上限；同 `idempotency_key` 重放只算一次（`duplicate: true`）；核銷後兌換碼失效 |
| POST | `/redemptions/{id}/revoke` | **線 A** 核銷撤销（10 分鐘窗口內；券退回未使用、換新碼、+20 分沖回；超時 400） |
| GET | `/vendor/redemptions/export?format=json\|csv` | **線 A** 商戶核銷記錄導出 / 匯總（對賬用；admin 看全部） |
| GET | `/get-favorite-coupons-list?page=&status=&sortby=` | 錢包三狀態：1 未使用 / 2 已使用 / 3 已失效 |
| GET | `/get-user-record-list` | 消費記錄 |
| GET | `/points` | 積分餘額 + 明細 |
| POST | `/review?wallet_item_id=` | 【舊版】評價已用券，+15 分（一次）；不寫評價記錄、不影響星級 |
| POST | `/points/exchange?coupon_id=` | 9 積分兌換 9 分分券 |

## 地圖（線 A）

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/nearby-shops?lat=&lng=&radius_km=&sort=distance` | 附近商户（Haversine 直線距離升序；預設半徑 10 公里，上限 50；缺 lat/lng 400；無座標商户不列入） |
| GET | `/shops/{id}/map` | 商户位置（經緯度、地址、Google Maps 搜尋 / 導航連結） |

距離是直線距離，不是步行 / 駕車距離與耗時。地圖預設 Google Maps（香港可用）。

## 評分 v1（F6 v1，線 B）

三維 5 分評分（口味 / 環境 / 服務）+ 文字 + 晒圖（預留）+ 標籤。只允許
已核銷用戶評價（`redemption_id` 即消費憑證；也可傳 `wallet_item_id`，
取其最近一次核銷記錄）。每張券（每次核銷）只能評價一次；舊版
`POST /review` 與新版共用 `wallet_items.reviewed` 去重標記。

**積分與星級拆開（防誘導評價）**：`claim_points=true`（默認）時評價
得 +15 分，但 `counts_toward_rating=false`，**不計入商户星級**；
`claim_points=false` 時不得分，但 `counts_toward_rating=true`，計入
星級聚合。`GET /shops/{id}/rating` 只聚合 `status=visible` 且
`counts_toward_rating=true` 的評價；全是積分評價時
`review_count=0`、`overall=null`。差評申訴後（`status=disputed`）
的評價暫時退出聚合。

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/reviews` | 寫評價。Body：`{wallet_item_id?, redemption_id?, taste 1–5, ambience 1–5, service 1–5, content ≤1000 字, images[] ≤9（晒圖連結，v1 預留欄位，無上傳接口）, tags[] ≤10, claim_points=true}`；二者至少傳一個。未核銷 400、重複評價 400、維度越界 422。返回 `{message, review, balance}` |
| GET | `/shops/{id}/reviews?page=&has_images=` | 商户評價列表（僅 `visible`，按新到舊，20/頁）；`has_images=true` 只看有圖 |
| GET | `/shops/{id}/rating` | 商户評分聚合 `{shop_id, overall, taste, ambience, service, review_count, updated_at}`；無計入星級評價時分數為 `null`、`review_count=0` |
| POST | `/reviews/{id}/reply` | 商户回覆。該店商户或 admin 可調；Body `{reply ≤500 字}` |
| POST | `/reviews/{id}/dispute` | 差評申訴。作者 / 該店商户 / admin 可調；Body `{reason}`；申訴後評價退出聚合 |

評價對象示例：`{id, user_id, user_name, shop_id, redemption_id,
wallet_item_id, taste, ambience, service, overall（三分均值）, content,
images[], tags[], points_given, counts_toward_rating, status
（visible/disputed/hidden）, reply_text, replied_at, created_at}`。

## 後台 v1（F4 v1，線 B，`/api/v3/admin` 前綴）

v1 角色：**只有 `admin` 單一角色**（`require_role("admin")`；入駐申請提交
除外，任何登錄用戶可提交）。權限分權（審核員 / 客服、商户內店長 /
收銀員 / 財務）留給 F 線（後台 v2）。看板全部**隻讀**，唯一寫操作是
入駐申請的審核決定；每次審核決定寫入 `admin_audit_logs`。

v1 **沒有**的接口（F 線再做）：訂單管理（等套餐線的 `orders` 表）、
結算單 / 對賬 / 發票、橫幅 / 內容管理、用戶與角色管理（寫操作）。

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/admin/merchant-applications` | 提交入駐申請（登錄即可）。Body：`{shop_name*, contact_name, phone, address, business_license, id_card, shop_photos[]（門店照片連結，v1 預留）, industry_license}`；同一用戶已有 `pending` 申請時 400。201 返回 `{message, application}` |
| GET | `/admin/merchant-applications?status=&page=` | 入駐申請列表（admin；`status=pending/approved/rejected` 篩選） |
| GET | `/admin/merchant-applications/{id}` | 入駐申請詳情（admin） |
| POST | `/admin/merchant-applications/{id}/review` | 審核（admin）。Body `{approve: bool, reject_reason}`；拒絕時 `reject_reason` 必填；已審核的重審 400。通過時申請人 `role` 由 `customer` 升為 `vendor`。被拒絕後可重新提交**新**申請。返回 `{message, application}` |
| GET | `/admin/dashboard` | 看板總覽（admin，隻讀）：`{total_users, total_shops, total_coupons, coupons_by_status, wallet_by_status{1/2/3}, total_redemptions, total_reviews, rated_reviews, pending_applications, pending_coupons}` |
| GET | `/admin/shops?page=` | 商户列表（admin，隻讀）：`shop_out` + `vendor_email, coupon_count, redemption_count, rating_overall, review_count` |
| GET | `/admin/coupons?status=&shop_id=&page=` | 券列表（admin，隻讀） |
| GET | `/admin/redemptions?shop_id=&page=&format=` | 核銷記錄（admin，隻讀）：`{id, wallet_item_id, coupon_id, coupon_name, shop_id, shop_name, scanned_by_vendor_id, scanned_at}`；`format=csv` 返回 `text/csv` 附件（核銷對賬導出） |
| GET | `/admin/users?role=&page=` | 用戶列表（admin，**隻讀**；v1 無改角色接口） |
| GET | `/admin/audit-logs?page=` | 後台操作日誌（admin，隻讀）：`{id, admin_id, action（merchant_application.approve/reject）, target_type, target_id, detail, created_at}` |

入駐申請對象：`{id, user_id, applicant_email, shop_name, contact_name,
phone, address, business_license, id_card, shop_photos[],
industry_license, status（pending/approved/rejected）, reject_reason,
reviewed_by, reviewed_at, created_at}`。
## 返現（線 D）

返現與積分分開記賬（`cashback_transactions`，絕不混用 `point_transactions`）。
兩條狀態機：線上/通用 `tracked → pending → confirmed → withdrawable`（按
確認周期，預設 30 天）；線下餐飲 `pending → confirmed → withdrawable`，90 天內
在同一商戶再消費一次才解鎖，否則過期作廢。追蹤以核銷記錄為準，不用 Cookie。

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/cashback` | 返現餘額（可提現/待確認/待追蹤）+ 明細（按狀態分組） |
| GET | `/cashback/transactions?status=&limit=` | 返現流水（含預計確認時間、90 天解鎖期限） |
| POST | `/cashback/record` | 消費記返現 `{merchant_id, amount, channel: dinein\|online, rate?, redemption_id?}`；線下餐飲固定 5%（預設） |
| POST | `/cashback/withdraw` | 申請提現 `{amount, method: fps\|bank, account_name, account_ref?}`；門檻 HK$50，每日上限 HK$1,500，賬戶名須與註冊名一致 |
| GET | `/cashback/withdrawals` | 提現記錄（v1 狀態 `pending`，待人工打款，無真實 FPS 打款集成） |
| POST | `/cashback/transactions/{id}/void` | 取消/退貨作廢 `{reason: cancelled\|returned}`（僅待確認可作廢） |
| POST | `/cashback/missing` | Missing Cashback 申訴 `{merchant_id, order_no, spent_at, amount, evidence?, note?}`；消費後 30 天內提交 |
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

## 砍價 2C（分享助力，線 E）

分享助力砍價（不是荷蘭式降價）：發起 → 分享 → 好友助力降價 → 到底價 / 成團 → 低價拿套餐券進 Q Wallet。金額為港幣（HKD）。開發版未接支付網關：發起時記錄一筆模擬預付（原價），未成團 / 超時全額退款、成團後退回砍價差價，均為退款記賬（原路返回，1–3 個工作日），不發生真實轉賬。

預設值（報告 §14 未定事項的假設）：成團人數 5 人；每場砍價有效期 24 小時；活動有效期 7 天；套餐券有效期 30 天；每刀遞減 30% → 25% → 20% → 15% → 10% → 5%，新用戶（註冊 7 天內）+10 個百分點，每刀最少 HK$1、永不低於底價；每人每單限助一次，每人每日最多助力 10 次；發起即鎖 1 份庫存。

退款規則（購買頁顯著說明）：未成團 / 超時全額退款；套餐券未核銷可退、過期自動退；已核銷不可退。不扣 30% 占單費，沒有「一經售出不退不換」。

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/bargain-campaigns` | 砍價活動列表（遊客可調；剩餘份數、底價、成團人數） |
| POST | `/bargain-campaigns` | 建立砍價活動（vendor/admin；vendor 只能為自己的商店開） |
| POST | `/bargain-campaigns/{id}/start` | 發起砍價（customer；鎖 1 份庫存，每活動限一場進行中） |
| GET | `/bargain-sessions/{id}` | 砍價進度（遊客可調；當前價、助力列表、剩餘時間、退款記錄、分享連結） |
| GET | `/bargain-my-sessions` | 我的砍價 |
| POST | `/bargain-sessions/{id}/help` | 助力砍價（customer；`{device_id}`，防刷：每人每單一次、同一設備去重、每日 10 次上限） |
| POST | `/bargain-sessions/{id}/purchase` | 砍到目標價後下單：生成套餐券（`kind=package`）進 Q Wallet（`source=bargain`，三狀態 1 未使用），退回差價 |
| POST | `/bargain/expire-sweep` | 結算過期砍價（admin；未成團全額退款 + 釋放庫存） |

狀態機：`active → success`（砍到底價，或助力人數達上限）→ 下單生成套餐券；`active → failed`（超時未成團，全額退款 + 釋放庫存）；`success → expired`（成團後未在有效期內下單，全額退款 + 釋放庫存）。
## 套餐 2A：付費套餐券 / 訂單 / 支付 / 退款（線 C）

套餐券（`kind=package`）與免費代金券並行：購買後進 Q Wallet 三狀態
（1 未使用 / 2 已使用 / 3 已失效），出示 QR 與核銷走現有閉環；
套餐券不可免費領取（`/new-favorite-coupon` 會攔截）。
支付為 Stripe **測試模式**（FPS + 信用卡），未接真實商戶號；
未設 `STRIPE_SECRET_KEY` 時用本地測試模擬器（`pi_test_…`）。

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/packages` | 套餐列表（`shop_id`、`status` 篩選；遊客可調） |
| GET | `/package-details?package_id=` | 套餐詳情（菜品組合、原價/套餐價、退款規則） |
| POST | `/packages` | 商戶/管理員建立套餐（同時生成 `kind=package` 券模板） |
| POST | `/packages/{id}/purchase` | 下單（預留庫存，生成訂單 + 支付單；`client_ref` 冪等） |
| POST | `/orders/{id}/confirm-payment` | 測試模式支付結果 `{outcome: succeeded/failed/timeout}` |
| POST | `/orders/{id}/retry-payment` | 失敗/逾時訂單重新支付（新支付單） |
| POST | `/orders/{id}/cancel` | 取消待支付訂單（釋放庫存） |
| GET | `/orders` | 我的訂單 |
| GET | `/orders/{id}` | 訂單詳情（含支付記錄、券 id） |
| POST | `/orders/{id}/refund` | 未核銷退款（特價券 `non_refundable` 攔截；退款後券作廢） |
| POST | `/packages/{id}/refund` | 按套餐退最近一筆可退訂單 |
| POST | `/orders/process-expired-refunds` | 管理員觸發：過期自動退（特價券只作廢不退） |
| POST | `/payments/webhook` | Stripe 測試模式 webhook（`payment_intent.succeeded/failed`） |

退款默認：未核銷隨時退、過期自動退（原路返回，1–3 個工作日）；
已核銷不可退。結算記賬字段（`settlement_status` /
`commission_amount` / `net_amount`，佣金默認 0）隨支付成功記賬，
對賬在 F 線。