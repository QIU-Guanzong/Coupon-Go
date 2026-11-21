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
| GET | `/app-config` | [公開顯示規則](M9_APP_CONFIG_API.md)（遊客可調） |
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

## Q仔

見 [Q仔 API（額度、公開商品與 offers）](M9_QBOT_API.md)。

## 券 / 錢包 / 積分

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/new-favorite-coupon?coupon_id=` | 領券 → Q Wallet，+10 分；重複領取 409 |
| POST | `/store-scaned-qr-values?wallet_item_id=` | 生成 QR 碼 + 6 位兌換碼，15:00 有效；每次出示輪換新碼（線 A 防截圖） |
| POST | `/new-scan-coupon/prepare` | **線 A** 預核銷（唯讀查券，`{code}`）→ `can_redeem` + 5 分鐘 prepare token，不改券狀態 |
| POST | `/new-scan-coupon` | 商戶掃碼核銷 `{code, idempotency_key?, prepare_token?}`，+20 分；錯店/已用/過期/每日 200 次上限；同 `idempotency_key` 重放只算一次（`duplicate: true`）；核銷後兌換碼失效 |
| POST | `/redemptions/{id}/revoke` | **線 A** 核銷撤销（10 分鐘窗口內；券退回未使用、換新碼、+20 分沖回；超時 400；M7：若該核銷已有評價，評價改為隱藏、已發的 +15 評價積分沖回，響應新增 `review: {review_id, points_clawed_back, status}`，無評價時為 `null`） |
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

同一筆訂單購買多份會發一張整券。預核銷的 `package_quantity` 顯示整券份數。核銷記一次，返現按訂單實付金額計。商戶撤銷核銷後，未到期券回到錢包，相關返現沖回。退款處理中（`requested`）暫停出示，原訂單份數在網關確認成功後才回補。網關明確回覆失敗時記為 `rejected`，有效期內可再次申請。已退款券可在錢包的「已失效」查看。庫存含待付預留，已售份數只計已付且未退款的訂單。

## 評分 v1（F6 v1，線 B）

三維 5 分評分（口味 / 環境 / 服務）+ 文字 + 晒圖（預留）+ 標籤。只允許
已核銷用戶評價（`redemption_id` 即消費憑證；也可傳 `wallet_item_id`，
取其最近一次核銷記錄）。每張券（每次核銷）只能評價一次；舊版
`POST /review` 與新版共用 `wallet_items.reviewed` 去重標記。

**積分與星級解耦（M7）**：所有 `status=visible` 的評價都計入星級，
`counts_toward_rating` 恆為 `true`；`claim_points` 只決定是否得
+15 分（默認 `true`，不看分數、不要求好評），不影響星級。
`GET /shops/{id}/rating` 聚合全部 `visible` 評價；無可見評價時
`review_count=0`、`overall=null`。差評申訴後（`status=disputed`）
的評價暫時退出聚合。

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/reviews` | 寫評價。Body：`{wallet_item_id?, redemption_id?, taste 1–5, ambience 1–5, service 1–5, content ≤1000 字, images[] ≤9（晒圖連結，v1 預留欄位，無上傳接口）, tags[] ≤10, claim_points=true}`；二者至少傳一個。未核銷 400、已撤销核銷 400、重複評價 400、維度越界 422。返回 `{message, review, balance}` |
| GET | `/shops/{id}/reviews?page=&has_images=` | 商户評價列表（僅 `visible`，按新到舊，20/頁）；`has_images=true` 只看有圖 |
| GET | `/shops/{id}/rating` | 商户評分聚合 `{shop_id, overall, taste, ambience, service, review_count, updated_at}`；無可見評價時分數為 `null`、`review_count=0` |
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
| GET | `/cashback` | 返現餘額（可提現/待確認/待追蹤）+ 明細（按狀態分組）+ 負向調整（`adjustments`，`adjustment_total` 計入可提現餘額，可為負） |
| GET | `/cashback/transactions?status=&limit=` | 返現流水（含預計確認時間、90 天解鎖期限） |
| POST | `/cashback/withdraw` | 申請提現 `{amount, method: fps\|bank, account_name, account_ref?}`；門檻 HK$50，每日上限 HK$1,500，賬戶名須與註冊名一致；未填註冊姓名時拒絕提現 |
| GET | `/cashback/withdrawals` | 提現記錄（v1 狀態 `pending`，待人工打款，無真實 FPS 打款集成） |
| POST | `/cashback/transactions/{id}/void` | 取消/退貨作廢 `{reason: cancelled\|returned}`（僅待確認可作廢） |
| POST | `/cashback/missing` | Missing Cashback 申訴 `{merchant_id, order_no, spent_at, amount, evidence?, note?}`；消費後 30 天內提交 |

核銷撤销（10 分鐘窗口）會同步回滾返現：該次核銷的返現作廢
（`void_reason=revoked`），由它觸發解鎖的前序 dine-in 筆退回待解鎖；
已提現部分記負向調整（`cashback_adjustments`），可提現餘額可為負、
為負時提現被攔截。`confirmed → withdrawable` 沒有冷靜期（待產品決定）。
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
| POST | `/assign-branches` | 指派分店 `{coupon_id, branch_ids}`（dianping §1.2：現已真寫庫，空列表 = 全部分店） |
| POST | `/update-branch-profile` | 更新分店資料 |
| POST | `/delete-vandor-image` | 刪除商户圖片 |
| POST | `/new-update-vendor-profile` | 更新商户資料（提交後待管理員批准） |
| POST | `/merchant/shops` | 新增門店（dianping §1.2，vendor/admin） |
| GET | `/merchant/shops/{shop_id}` | 門店詳情 + 分店列表（店主/店員/admin） |
| GET | `/merchant/shops/{shop_id}/branches` | 分店列表（店主/店員/admin） |
| POST | `/merchant/shops/{shop_id}/branches` | 新增分店（店主/`manage_coupons` 店員/admin） |
| PATCH | `/merchant/shops/{shop_id}` | 門店自助維護：營業時間/相冊/公告/聯繫資料（dianping §1.4） |
| POST | `/uploads/merchant-images` | 商戶圖片上傳（vendor/admin，jpg/png/webp/gif，≤5MB，最多 9 張） |
| PATCH | `/admin/merchant-applications/{id}` | 修改待審核的入駐申請（dianping §1.6，admin） |
| PATCH | `/admin/shops/{shop_id}/members/{user_id}` | 改店員角色（dianping §1.3，店主/admin） |

**dianping 商家管理（§1）字段變更**（詳見 `docs/DIANPING_MERCHANT_API.md`）：

- `POST /admin/merchant-applications/{id}/review` 批准時響應新增 `shop`（自動開店，
  `is_approved=true`）；批准/駁回給申請人寫站内通知。
- `shop_out` 新增 `is_approved` / `business_hours` / `gallery` / `announcement`。
- `coupon_out` 新增 `branch_ids` / `branches`（`[]` = 適用全部分店）。
- `ShopMember.role` 擴展為 `merchant-admin`（舊，全權）/ `manager` / `cashier` /
  `finance`；成員列表與邀請/改角色響應新增 `permissions` 列表。
- `POST /new-scan-coupon/prepare` 與 `POST /new-scan-coupon` 支持可選 `branch_id`
 （分店適用範圍校验）；核销端点現接受有 `redeem` 權限的店員（不只 vendor/admin）。
- 商户結算類只讀接口（`/merchant/shops/{id}/settlements`、`/merchant/shops/{id}/reconciliation`、
  `/merchant/shops/{id}/invoices`、`/merchant/settlements`）現要求 `view_settlement`
  權限（店主/admin/平台 finance-auditor/`merchant-admin`/`finance` 店員；`manager`/`cashier` 為 403）。
- 新增通知類型：`application_approved` / `application_rejected` / `coupon_approved` /
  `coupon_rejected` / `settlement_generated` / `settlement_approved` / `settlement_paid` /
  `review_received`（站内信，`GET /get-notification-list` 可見）。

## 砍價 2C（分享助力，線 E）

分享助力砍價（不是荷蘭式降價）：發起 → 分享 → 好友助力降價 → 到底價 / 成團 → 低價拿套餐券進 Q Wallet。金額為港幣（HKD）。發起時記錄一筆模擬預付（原價，未收真錢）；未成團 / 超時全額退款、成團後退回砍價差價，退款走支付網關（見下），不發生真實轉賬。

預設值（報告 §14 未定事項的假設）：成團人數 5 人；每場砍價有效期 24 小時；活動有效期 7 天；套餐券有效期 30 天；每刀遞減 30% → 25% → 20% → 15% → 10% → 5%，新用戶（註冊 7 天內）+10 個百分點，每刀最少 HK$1、永不低於底價；每人每單限助一次，每人每日最多助力 10 次；發起即鎖 1 份庫存。

退款規則（購買頁顯著說明）：未成團 / 超時全額退款；套餐券未核銷可退、過期自動退；已核銷不可退。不扣 30% 占單費，沒有「一經售出不退不換」。退款走支付網關（`refund_payment`，與套餐退款同一口徑）：退款記錄狀態為 `pending/succeeded/failed` + `gateway_ref`，用戶可見文案只有「退款将在 1–3 个工作日原路退回」；網關失敗時退款停在 `failed`，可在同一記錄上重試（`POST /bargain/refunds/{id}/retry`，本人或管理員）。預付仍是模擬記賬（未收真錢），網關調用用合成支付引用，開發版模擬器返回 `re_test_…`。

M3 字段變更：`GET /bargain-sessions/{id}`、`POST /bargain-campaigns/{id}/start`、`POST /bargain-sessions/{id}/purchase` 的響應不再返回 `payment_simulated` 欄位（內部狀態用語，不應出現在客戶端可見響應中；預付模擬記賬的說明仍見本節正文，僅供對接方理解，不向終端用戶展示）。

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/bargain-campaigns` | 砍價活動列表（遊客可調；剩餘份數、底價、成團人數） |
| POST | `/bargain-campaigns` | 建立砍價活動（vendor/admin；vendor 只能為自己的商店開） |
| POST | `/bargain-campaigns/{id}/start` | 發起砍價（customer；鎖 1 份庫存，每活動限一場進行中） |
| GET | `/bargain-sessions/{id}` | 砍價進度（遊客可調；當前價、助力列表、剩餘時間、退款記錄、分享連結） |
| GET | `/bargain-my-sessions` | 我的砍價 |
| POST | `/bargain-sessions/{id}/help` | 助力砍價（customer；`{device_id}`，防刷：每人每單一次、同一設備去重、每日 10 次上限） |
| POST | `/bargain-sessions/{id}/purchase` | 砍到目標價後下單：生成套餐券（`kind=package`）進 Q Wallet（`source=bargain`，三狀態 1 未使用），退回差價 |
| POST | `/bargain/expire-sweep` | 結算過期砍價（admin；未成團全額退款 + 釋放庫存）。M1：與後台定時任務共用同一份邏輯（見 `docs/SCHEDULER.md`），返回新增 `failed`（單條失敗數，下一周期重試） |
| POST | `/bargain/refunds/{id}/retry` | 重試一筆失敗的砍價退款（本人或管理員；僅 `failed` 可重試，同一退款記錄原地更新） |

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
| POST | `/orders/{id}/demo-payment` | 本機模擬付款，只限開發環境、無支付密鑰且屬於當前用戶的模擬訂單 |
| POST | `/orders/{id}/confirm-payment` | 測試模式支付結果 `{outcome: succeeded/failed/timeout}` |
| POST | `/orders/{id}/retry-payment` | 失敗/逾時訂單重新支付（新支付單） |
| POST | `/orders/{id}/cancel` | 取消待支付訂單（釋放庫存） |
| GET | `/orders` | 我的訂單 |
| GET | `/orders/{id}` | 訂單詳情（含支付記錄、券 id） |
| POST | `/orders/{id}/refund` | 未核銷退款（特價券 `non_refundable` 攔截；退款後券作廢） |
| POST | `/packages/{id}/refund` | 按套餐退最近一筆可退訂單 |
| GET | `/admin/orders/{id}` | 按後台訂單列表相同權限讀取詳情 |
| POST | `/admin/orders/{id}/refund` | 平台管理員填寫原因，按顧客同一退款規則處理，保留操作記錄 |
| POST | `/orders/process-expired-refunds` | 管理員觸發：過期自動退（特價券只作廢不退）。M1：與後台定時任務共用同一份邏輯（見 `docs/SCHEDULER.md`），返回新增 `failed` / `skipped` 計數 |
| POST | `/payments/webhook` | Stripe 測試模式 webhook（`payment_intent.succeeded/failed`） |

退款默認：未核銷隨時退、過期自動退（原路返回，1–3 個工作日）；
已核銷不可退。結算記賬字段（`settlement_status` /
`commission_amount` / `net_amount`，佣金默認 0）隨支付成功記賬，
對賬在 F 線。
## 評分 v1（F6 v1，線 B；v1 檔案已併入本線工作區，合入時與線 B 同內容）

三維 5 分評分（口味 / 環境 / 服務）+ 文字 + 晒圖（預留）+ 標籤。只允許
已核銷用戶評價（`redemption_id` 即消費憑證；也可傳 `wallet_item_id`，
取其最近一次核銷記錄）。每張券（每次核銷）只能評價一次；舊版
`POST /review` 與新版共用 `wallet_items.reviewed` 去重標記。

**積分與星級解耦（M7）**：所有 `status=visible` 的評價都計入星級，
`counts_toward_rating` 恆為 `true`；`claim_points` 只決定是否得
+15 分（默認 `true`，不看分數、不要求好評），不影響星級。
`GET /shops/{id}/rating` 聚合全部 `visible` 評價；無可見評價時
`review_count=0`、`overall=null`。差評申訴後（`status=disputed`）
的評價暫時退出聚合；申訴解決見後台 v2（`POST /reviews/{id}/resolve`）。
已撤销核銷不能評價（400「此核銷已被撤销，無法評價」）；撤销核銷
時其評價改為 `hidden`、已發的 +15 分沖回（見撤销接口的 `review` 欄位）。

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/reviews` | 寫評價。Body：`{wallet_item_id?, redemption_id?, taste 1–5, ambience 1–5, service 1–5, content ≤1000 字, images[] ≤9（晒圖連結，v1 預留欄位，v2 起由 POST /uploads/review-images 上傳後填入）, tags[] ≤10, claim_points=true}`；二者至少傳一個。未核銷 400、已撤销核銷 400、重複評價 400、維度越界 422。返回 `{message, review, balance}` |
| GET | `/shops/{id}/reviews?page=&has_images=` | 商户評價列表（僅 `visible`，按新到舊，20/頁）；`has_images=true` 只看有圖 |
| GET | `/shops/{id}/rating` | 商户評分聚合 `{shop_id, overall, taste, ambience, service, review_count, updated_at}`；無可見評價時分數為 `null`、`review_count=0` |
| POST | `/reviews/{id}/reply` | 商户回覆。該店商户或 admin 可調；Body `{reply ≤500 字}` |
| POST | `/reviews/{id}/dispute` | 差評申訴。作者 / 該店商户 / admin 可調；Body `{reason}`；申訴後評價退出聚合 |

評價對象示例：`{id, user_id, user_name, shop_id, redemption_id,
wallet_item_id, taste, ambience, service, overall（三分均值）, content,
images[], tags[], points_given, counts_toward_rating, status
（visible/disputed/hidden）, reply_text, replied_at, created_at}`。

## 後台 v1（F4 v1，線 B，`/api/v3/admin` 前綴；v1 檔案已併入本線工作區）

v1 角色：**只有 `admin` 單一角色**（`require_role("admin")`；入駐申請提交
除外，任何登錄用戶可提交）。看板全部**隻讀**，唯一寫操作是
入駐申請的審核決定；每次審核決定寫入 `admin_audit_logs`。

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

## 後台 v2（F4 v2，線 F，`/api/v3/admin` 前綴）

v2 在 v1 之上，不改任何 v1 路徑與欄位。權限改為角色制
（`app/permissions_line_f.py`）：**admin 擁有全部權限**（v1 單一 admin
帳戶無縫升級）；finance 可寫結算 / 對賬 / 發票；auditor 可讀看板數據 +
處理評價申訴；support 可處理評價申訴 + 讀看板；merchant-admin 是商户
店員，權限按店（`shop_members` 表）限定；vendor 是店主，對自家店有同
等店內權限。v1 的 admin-only 端點保持不變（auditor 暫不能審核入駐申請，
見 README「還沒做的事」）。

結算口徑（報告 §5.1-8、§14 #6；M6 起按實付額）：**按核銷結算**。毛額
= 該商户該週期核銷的結算基數之和；基數取核銷時寫入的
`face_value` 快照，快照口徑：套餐券取訂單實付
`Order.paid_amount`，價格券取 `discount_price`；現金券及免費／禮品／純折扣／
純門檻券為 0（`cash_amount` 是商戶提供的折扣額，`regular_price` 也不是實付金額）。
現金券的舊正額快照在產生新結算及對賬時按 0；其他快照為 NULL 的遺留核銷按同一
口徑以當前訂單／券行回退計算。已撤销核銷（`revoked_at` 不為空）不進結算；訂單已
退款（`refund_status=refunded`）的核銷不進結算；其餘佣金 = 毛額 ×
生成時傳入的 `commission_rate`（默認 0.10；真實費率按商户逐個談判，
v2 只記錄所用費率）；淨額 = 毛額 − 佣金。一張核銷最多進一張結算單。
已生成結算單的週期即使事後發現基數口徑變化也不改舊金額，由對賬
以 `amount_mismatch` 標記人工處理。尚未支付的歷史帳單如有未沖回的正額現金券行，
登記付款返回 409，要求先對賬。已登記付款的同一憑證可冪等重放，不改歷史金額。

| 方法 | 路徑 | 權限 | 說明 |
|---|---|---|---|
| GET | `/admin/overview` | dashboard.read | v2 總覽：各狀態結算單數、待審核結算淨額、待審核發票數、未處理差異數、申訴中評價數、啟用中橫幅數 |
| POST | `/admin/settlements/generate` | settlements.write | 生成結算單。Body `{shop_id, period: "YYYY-MM", commission_rate=0.10}`；同一商户同一週期重複生成 409。201 返回 `{message, settlement（含 items）}` |
| GET | `/admin/settlements?shop_id=&status=&page=` | settlements.read | 結算單列表（`status=pending/approved/disputed/paid`） |
| GET | `/admin/settlements/{id}` | settlements.read | 結算單詳情（含逐筆核銷行） |
| POST | `/admin/settlements/{id}/approve` | settlements.write | pending → approved；非 pending 400 |
| POST | `/admin/settlements/{id}/dispute` | settlements.write | pending/approved → disputed；Body `{reason*}` |
| POST | `/admin/settlements/{id}/resolve` | settlements.write | disputed → approved（`decision=approve`）或 pending（`decision=reopen`）；Body `{decision, note}` |
| POST | `/admin/settlements/{id}/pay` | settlements.write | approved → paid；非 approved 400 |
| GET | `/merchant/shops/{shop_id}/settlements` | 店主 / 店員 / 平台財務·審核 | 該店結算單（商户端） |
| POST | `/admin/reconciliation/run` | reconciliation.write | 執行對賬。Body `{shop_id, period}`；同一商户同一週期重跑會覆蓋上次的差異記錄後重新生成。201 返回 `{message, report（含 discrepancies）}`；無結算單時 `status=no_settlement` |
| GET | `/admin/reconciliation/reports?shop_id=&period=&page=` | reconciliation.read | 對賬報告列表 |
| GET | `/admin/reconciliation/reports/{id}` | reconciliation.read | 對賬報告詳情（含差異） |
| POST | `/admin/reconciliation/discrepancies/{id}/resolve` | reconciliation.write | 處理一項差異；Body `{resolution*}`；全部差異處理完後報告自動變 `resolved` |
| GET | `/merchant/shops/{shop_id}/reconciliation` | 店主 / 店員 / 平台財務·審核 | 該店對賬報告（商户端） |
| POST | `/admin/invoices` | invoices.write 或店主 / 店員（自家店） | 申請開票。Body `{shop_id, settlement_id?, amount>0, title*, tax_id, invoice_type=receipt}`；掛結算單時，同一結算單未拒絕的開票總額不得超過淨額，否則 400。201 返回 `{message, invoice}` |
| GET | `/admin/invoices?shop_id=&status=&page=` | invoices.read | 發票列表（`status=pending/approved/rejected/issued`） |
| GET | `/admin/invoices/{id}` | invoices.read | 發票詳情 |
| POST | `/admin/invoices/{id}/approve` | invoices.write | pending → approved |
| POST | `/admin/invoices/{id}/reject` | invoices.write | pending → rejected；Body `{reason*}` |
| POST | `/admin/invoices/{id}/issue` | invoices.write | approved → issued（記錄 `issued_at`） |
| GET | `/merchant/shops/{shop_id}/invoices` | 店主 / 店員 / 平台財務·審核 | 該店發票（商户端） |
| GET | `/admin/banners?status=&page=` | content.read | 橫幅列表 |
| POST | `/admin/banners` | content.write（admin） | 新增橫幅。Body `{title*, image, link_type, link_target, sort_order, start_at, end_at, status=draft/active/archived}`；`end_at ≤ start_at` 時 400 |
| PUT | `/admin/banners/{id}` | content.write（admin） | 更新橫幅（全量） |
| DELETE | `/admin/banners/{id}` | content.write（admin） | 下架橫幅（軟刪除：狀態改 `archived`，不刪行） |
| GET | `/banners` | 遊客可調 | 公開橫幅：`active` 且在展示窗口內，按 `sort_order` 排序。v0.1 的 `GET /advertisement-banners`（寫死演示橫幅）保持不變，首頁切 CMS 橫幅時改調此端點 |
| GET | `/admin/announcements?status=&page=` | content.read | 公告列表 |
| POST | `/admin/announcements` | content.write（admin） | 新增公告。Body `{title*, body, status, sort_order, start_at, end_at}` |
| PUT | `/admin/announcements/{id}` | content.write（admin） | 更新公告（全量） |
| DELETE | `/admin/announcements/{id}` | content.write（admin） | 下架公告（軟刪除：`archived`） |
| GET | `/announcements` | 遊客可調 | 公開公告：`active` 且在展示窗口內 |
| GET | `/admin/categories` | content.read | 全部分類（含停用，附 `is_active`） |
| POST | `/admin/categories` | content.write（admin） | 新增分類；重名 400 |
| PUT | `/admin/categories/{id}` | content.write（admin） | 更新分類 |
| POST | `/admin/categories/{id}/deactivate` | content.write（admin） | 停用分類 |
| POST | `/admin/categories/{id}/activate` | content.write（admin） | 重新啟用分類 |
| GET | `/admin/roles` | admin | 角色清單 + 角色→權限對照表 |
| POST | `/admin/users/{id}/role` | admin | 改用戶角色。Body `{role}`（customer/vendor/merchant-admin/finance/auditor/support/admin）；改自己 400、未知角色 400、停用最後一位管理員 400 |
| GET | `/admin/shops/{shop_id}/members` | members.read | 店員列表 |
| POST | `/admin/shops/{shop_id}/members` | admin 或店主 | 加店員。Body `{user_id}`；`customer` 會自動升為 `merchant-admin`；重複加入 400 |
| DELETE | `/admin/shops/{shop_id}/members/{user_id}` | admin 或店主 | 移除店員（不改該用戶的平台角色） |

結算單對象：`{id, shop_id, shop_name, period, redemption_count,
gross_amount, commission_rate, commission, net_amount,
status（pending/approved/disputed/paid）, dispute_reason, decided_by,
decided_at, paid_at, created_at, updated_at, items?}`。

對賬差異類型：`missing_settlement_line`（有核銷、無結算行）、
`missing_redemption`（有結算行、無此週期核銷）、`amount_mismatch`
（結算行金額 ≠ 結算基數）、`revoked_but_settled`（M6：結算單裡有
明細、核銷已撤销；`expected_amount=0`，`actual_amount` 為結算行金額）。差異對象：`{id, report_id,
discrepancy_type, redemption_id, settlement_item_id, expected_amount,
actual_amount, status（open/resolved）, resolution, resolved_by,
resolved_at, created_at}`。

發票對象：`{id, shop_id, shop_name, settlement_id, user_id,
applicant_email, amount, title, tax_id, invoice_type,
status（pending/approved/rejected/issued）, reject_reason, decided_by,
decided_at, issued_at, created_at}`。

v2 所有寫操作都會寫入 `admin_audit_logs`（沿用 v1 表），action 如
`settlement.generate/approve/dispute/resolve/pay`、
`reconciliation.run/resolve`、`invoice.create/approve/reject/issue`、
`banner.create/update/archive`、`announcement.create/update/archive`、
`category.create/update/deactivate/activate`、
`user.role.change`、`shop.member.add/remove`、`review.dispute.resolve`。

## 申訴解決（v2，線 F）

v1 只做到申訴後評價退出聚合，無解決接口。v2 補上：

| 方法 | 路徑 | 權限 | 說明 |
|---|---|---|---|
| GET | `/admin/reviews/disputes?page=` | reviews.disputes.read（admin/auditor/support） | 申訴中評價隊列（附 `dispute_reason`） |
| POST | `/reviews/{id}/resolve` | reviews.dispute.resolve（admin/auditor/support） | Body `{decision: uphold\|dismiss, note}`；`uphold`（申訴成立）→ `hidden`，永久退出聚合；`dismiss`（申訴不成立）→ `visible`，重新計入聚合並重算 `shop_ratings`。非 `disputed` 狀態 400 |

## 晒圖上傳（v2，線 F）

v1 的 `images[]` 只是預留欄位。v2 接上傳，寫評價時把返回的路徑填入
`images[]` 即可（`POST /reviews` 本身不改）。

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/uploads/review-images` | 上傳晒圖（multipart，欄位 `files`，最多 9 張，每張 ≤5MB，只接受 jpg/jpeg/png/webp/gif）。需登錄。201 返回 `{message, images: ["/uploads/review-images/<uuid>.png", ...]}`；非圖片 400、超過 9 張 400、空檔案 400。上傳的檔案由 `/uploads` 靜態掛載直接提供訪問 |

**儲存約定（本地 / 佔位實現，未接真實對象儲存）**：檔案存在 API
服務器本地磁盤 `<UPLOAD_DIR>/review-images/`（默認
`apps/api/uploads/`，`UPLOAD_DIR` 環境變量可改），檔名是服務器生成的
UUID，不用客戶端檔名，無路徑穿越風險。上傳時已無損剝離 EXIF
（JPEG APP1 / PNG eXIf / WebP EXIF 段丟棄，其餘字節原樣保留）。
未做病毒掃描、縮圖。**本地磁盤是開發佔位，上線前須換成對象儲存
（S3 / GCS 等）**：只需替換
`app/routers/uploads.py` 的存檔函數與返回的 URL 前綴，其餘接口不變。

## 移動端對接（M5，給 Dot 的 Flutter 整合用）

本節是移動端唯一的對接清單：請求示例、響應字段、錯誤碼與用戶可見
文案，全部以代碼為準。字段名、枚舉取值、狀態流轉與上文各節一致；
上文已有重複節（評分 v1、後台 v1 各兩份，內容相同），本節只新增，
不複製、不改動已有章節。

### 通則

- Base URL 與前綴見文首：`http://localhost:8000` + `/api/v3`。
- 鑑權：`Authorization: Bearer <JWT>`；下表標「遊客可調」以外的端點
  都需要登錄。未登錄 / token 失效時返回 401，`detail` 為
  `請登錄以繼續` / `登錄已失效，請重新登錄`（見 `app/deps.py`），
  客戶端應彈登錄頁並在登錄後回到原界面。
- 錯誤包絡：業務錯誤一律 HTTP 400 / 403 / 404 / 409，body 為
  `{"detail": "<中文文案>"}`；**直接向用戶顯示 `detail`**，不要顯示
  英文報錯或堆棧。校驗錯誤為 422（FastAPI 默認包絡，與業務 `detail`
  不同，客戶端顯示通用文案「請檢查輸入後重試」）。
- 權限不足為 403，`detail` 為 `沒有權限`；找不到對象為 404，
  `detail` 為 `找不到此優惠券` / `找不到此商户` / `找不到此核銷記錄`
  等（各接口見下）。
- 金額一律 HK$，數字字段為 JSON number（非字符串）；時間為 ISO 8601。
- 不向用戶展示的內部字段：`test_mode`、網關名稱、`pi_` 開頭的支付
  單號。`payment_out` 已把模擬器生成的 `pi_test_…` 置空
  （`gateway_ref: ""`），真實 Stripe 的 `pi_…` 保留原值但同樣不要
  向用戶展示。
- 用戶可見文案統一：網絡問題「網絡不穩定，請重試」；支付失敗
  「支付失敗，請稍後重試」（服務端 `detail` 優先於通用文案）。

### 枚舉總表

**訂單支付狀態 `Order.payment_status`**（`models_packages.py`
`PAYMENT_STATUSES`）：

| 取值 | 含義 | 客戶端顯示 |
|---|---|---|
| `pending` | 已下單待支付 | 待支付 |
| `paid` | 支付成功 | 已支付 |
| `failed` | 支付失敗（可重試） | 支付失敗 |
| `timeout` | 支付逾時（可重試；pending 超 15 分鐘自動轉此狀態） | 支付逾時 / 訂單已關閉 |
| `cancelled` | 用戶 / 系統取消（可重新下單） | 已取消 |

**支付單狀態 `Payment.status`**（`models_packages.py`）：
`requires_payment`（待支付）/ `processing`（處理中）/
`succeeded`（成功）/ `failed`（失敗）/ `cancelled`（已取消）/
`timeout`（逾時）/ `refunded`（已退款）。訂單詳情 `payments[]`
裡的 `failure_message` 是服務端記錄，可為空；向用戶顯示通用文案，
不要原文展示網關報錯。

**訂單退款狀態 `Order.refund_status`**：
`none`（無退款）/ `requested`（已申請）/ `refunded`（已退款）/
`rejected`（已拒絕）。退款記錄 `Refund.status`：
`requested` / `refunded` / `rejected`。

**錢包券狀態 `WalletItem.status`**：`1` 未使用 / `2` 已使用 /
`3` 已失效。`source`：`claim`（領取）/ `purchase`（套餐購買）/
`bargain`（砍價成團）/ `exchange`（積分兌換）。

**套餐狀態 `Package.status`**：`active`（在售）/ `inactive`
（已下架）/ `sold_out`（已售罄）。`refund_policy`：
`anytime`（未使用隨時退，過期自動退）/ `expiry_only`（僅過期自動
退）/ `non_refundable`（特價券，不設退款）；`refund_policy_text`
是對應的用戶可見文案。

**砍價場次狀態 `BargainSession.status`**：`active`（進行中）/
`success`（已砍到底價 / 已成團）/ `failed`（超時未成團，全額退款）/
`expired`（成團後未在有效期內下單，全額退款）。砍價活動狀態
`BargainCampaign.status`：`active` / `sold_out` / `expired`。
砍價退款記錄 `BargainRefund.status`：`pending`（處理中）/
`succeeded`（已退款）/ `failed`（失敗，可重試）。

**返現交易狀態 `CashbackTransaction.status`**
（`models_cashback.py` `CASHBACK_STATUSES`）：

| 取值 | 含義 | `status_text`（接口已給，直接顯示） |
|---|---|---|
| `tracked` | 已追蹤（線上渠道，待進入確認期） | 已追蹤 |
| `pending` | 待確認；線下渠道顯示「待下次消費解鎖」 | 待確認 / 待下次消費解鎖 |
| `confirmed` | 已確認（瞬時過渡態，sweep 內隨即轉可提現） | 已確認 |
| `withdrawable` | 可提現 | 可提現 |
| `void` | 已失效（`void_reason`：`cancelled` 取消 / `returned` 退貨 / `expired_90d` 90 天未解鎖 / `revoked` 核銷被撤銷） | 已失效 |
| `withdrawn` | 已提現（整筆已被提現消耗完） | 已提現 |

返現狀態膠囊固定四詞（Dot 階段 6）：待確認 / 可提現 / 已提現 /
已失效，對應 `pending` / `withdrawable` / `withdrawn` / `void`。
`tracked` 不進四詞膠囊，顯示「已追蹤」；`confirmed` 為瞬時態，
客戶端一般不會見到，如見到按「待確認」顯示。

**提現記錄狀態 `CashbackWithdrawal.status`**：`pending`（處理中）/
`paid`（已到賬）/ `failed`（失敗，`failure_reason` 有值時顯示該
原因）。v1 無真實 FPS / 銀行打款集成，`pending` 為待人工打款。

**漏記返現申訴狀態 `CashbackAppeal.status`**：`submitted`（已提交）/
`under_review`（處理中）/ `resolved`（已解決）/ `rejected`（已拒絕）；
接口同時返回 `status_text`，直接顯示。

**評價狀態 `Review.status`**：`visible`（可見）/ `disputed`（申訴中，
暫時退出星級聚合）/ `hidden`（申訴成立後隱藏，永久退出聚合）。

### 狀態流轉圖

**訂單**：`pending` → `paid`（支付成功，套餐券存入 Q Wallet）；
`pending` → `failed` / `timeout`（可 `retry-payment` 重新支付，
生成新支付單）；`pending` → `cancelled`（用戶取消，釋放庫存）；
`pending` → `timeout`（15 分鐘未支付，定時任務 / 讀訂單時自動關閉）。
`paid` + 未核銷 → `refund_status: requested` → `refunded`（券作廢）；
已核銷不可退。過期未核銷的已支付套餐券由定時任務自動退款
（特價券只作廢不退）。

**券（Q Wallet）**：`1` 未使用 → `2` 已使用（核銷成功）；
`1` → `3` 已失效（過期，或退款 / 特價券過期作廢後）。核銷後
10 分鐘內商户可撤銷，券退回 `1` 並換新碼。

**砍價**：`active` → `success`（砍到底價，或助力人數達上限）→ 下單
生成套餐券（`source=bargain`）；`active` → `failed`（超時未成團，
全額退款 + 釋放庫存）；`success` → `expired`（成團後未在有效期內
下單，全額退款 + 釋放庫存）。

**返現（線下餐飲，唯一在用渠道）**：
`pending` → `withdrawable`（同一商户 90 天內再消費一次解鎖）；
`pending` → `void`（90 天內無再消費，`expired_90d`）。
線上 / 通用渠道：`tracked` → `pending` → `confirmed` →
`withdrawable`（按確認周期，預設 30 天）。`withdrawable` →
`withdrawn`（被提現全部消耗）；部分提現時仍為 `withdrawable`，
看 `withdrawn_amount` / `remaining_amount`。核銷被撤銷時對應返現
作廢（`void_reason=revoked`），已提現部分記負向調整
（`adjustments` / `adjustment_total`，可提現餘額可為負，為負時
提現被攔截）。

### 首頁

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/home?lat=&lng=` | 遊客可調。七分區 + 橫幅 + 分類 + 商户列表（見下） |
| GET | `/advertisement-banners` | 遊客可調。寫死演示橫幅（v0.1）；首頁切 CMS 橫幅時改調 `GET /banners` |
| GET | `/banners` | 遊客可調。CMS 公開橫幅（`active` 且在展示窗口內，按 `sort_order`） |
| GET | `/get-classification-coupons?category_id=&area=` | 分類券 |
| GET | `/get-limited-time-coupons` | 限時搶購 |
| GET | `/get-most-people-grab` | 最多人搶 |
| GET | `/recomandation-coupons` | 為你推薦（注意原始拼寫） |
| GET | `/get-used-merchents-shop` | 常用商戶 |
| GET | `/get-postion-shops?lat=&lng=` | 按定位找店 |
| GET | `/get-area-list`、`/get-hong-kong-list`、`/get-kowloon-list`、`/get-new-territories-list` | 地區列表 |
| GET | `/get-category-list` | 分類列表 |
| GET | `/shop-details?shop_id=` | 商户詳情（+ 分店 + 本店券） |
| GET | `/branch-details?branch_id=` | 分店詳情 |
| GET | `/get-nearest-branchs?shop_id=` | 分店列表 |
| GET | `/coupon-details-new?coupon_id=` | 券詳情 |
| GET | `/get-collection-shops` | 收藏商户 |
| GET | `/favorite-coupon-list` | 收藏券 |
| POST | `/favorite-shop?shop_id=`、`/favorite-coupon-all?coupon_id=` | 收藏 / 取消收藏 |
| GET | `/get-subscription-categories-list`、`/get-subscription-plan-list` | 訂閱計劃 |

請求示例：`GET /api/v3/home?lat=22.3193&lng=114.1694`

`GET /home` 響應字段：`banners[]`（`image/title/subtitle`）、
`categories[]`（`id/name/name_en/icon`）、`popular_coupons[]` /
`week_coupons[]` / `most_grab[]` / `recommended[]` /
`limited_time_coupons[]`（均為券對象，見下）、
`popular_merchants[]` / `frequently_used_shops[]` / `new_shops[]`
（均為商户對象，見下）。傳 `lat` + `lng` 時券對象帶
`distance_km`（直線距離，km）；不傳時為 `null`，客戶端隱藏距離行。

券對象（`coupon_out`）：`id` / `coupon_id` / `shop_id` /
`vendor_name` / `shop_name` / `coupon_name` / `name_en` /
`coupon_type` / `cash_amount`（可為 `null`）/ `percentage`
（可為 `null`）/ `gift` / `discount_price`（可為 `null`）/
`regular_price`（可為 `null`）/ `min_spend` / `image` /
`valid_from` / `valid_to` / `remaining` / `total` / `downloads` /
`used_count` / `status` / `additional_terms` /
`is_coupon_favorite` / `distance_km`。

商户對象（`shop_out`）：`id` / `shop_id` / `vendor_name` /
`shop_name` / `name_en` / `category_id` / `category_name` /
`area_name` / `address` / `phone` / `description` / `image` /
`latitude` / `longitude`（可為 `null`）/ `branch_count` /
`is_shop_favorite`。

`GET /shop-details` 響應：`{shop, branches[], coupons[]}`，分店項為
`{id, branch_id, shop_id, branch_name, address, phone}`。
`GET /coupon-details-new` 響應：`{coupon, shop}`（`shop` 可為 `null`）。
找不到商户 / 券時 404，`detail` 為 `找不到此商店` /
`找不到此優惠券` / `找不到此分店`。

**返現比例欄位（未確認）**：目前沒有任何接口返回商户返现比例，
商户對象與券對象均無返現比例字段。服務端默認線下返現比例為 5%
（配置項 `CASHBACK_DINEIN_RATE`，未經接口暴露）。商户詳情頁
「可返現 X%」一行在接口有比例字段前不要顯示；如產品要顯示，
需後端補字段後再對接。

### 套餐 / 訂單 / 支付 / 退款

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/packages?shop_id=&status=active` | 遊客可調。套餐列表 |
| GET | `/package-details?package_id=` | 套餐詳情（含菜品、價格、退款規則、適用門店、評價摘要） |
| POST | `/packages/{id}/purchase` | 下單（預留庫存，生成訂單 + 支付單；`client_ref` 冪等） |
| POST | `/orders/{id}/confirm-payment` | 測試模式支付結果（僅 dev 模擬器接受客戶端 `outcome`；見下） |
| POST | `/orders/{id}/retry-payment` | 失敗 / 逾時訂單重新支付（新支付單） |
| POST | `/orders/{id}/cancel` | 取消待支付訂單（釋放庫存） |
| GET | `/orders` | 我的訂單（無服務端狀態篩選，客戶端按 `payment_status` / `refund_status` 本地分組） |
| GET | `/orders/{id}` | 訂單詳情（含支付記錄、券 id） |
| POST | `/orders/{id}/refund` | 未核銷退款（特價券 `non_refundable` 攔截；退款後券作廢） |
| POST | `/packages/{id}/refund` | 按套餐退最近一筆可退訂單 |
| POST | `/orders/process-expired-refunds` | 僅 admin：過期自動退手動補跑（M1 字段見下） |
| POST | `/payments/webhook` | Stripe webhook（非移動端調用） |

下單請求示例：

```json
POST /api/v3/packages/12/purchase
{"payment_method": "fps", "quantity": 1, "client_ref": "app-20261117-0001"}
```

`payment_method`：`fps` / `card`（默認 `fps`）；`quantity` ≥ 1，
上限取 `min(限購余量, 庫存)`，即詳情接口的 `limit_remaining`；
`client_ref` 為客戶端冪等鍵，同一鍵重複下單返回原訂單，不生成
新訂單。下單響應：`{order, payment, client_secret, gateway:
"stripe", message}`；`client_secret` 供 App 完成支付，**不要向
用戶展示**。錯誤：套餐不存在 404；庫存不足 400
`已售罄，先到先得`；套餐已下架時整頁只留返回（`status` 非
`active` 不可下單）。

套餐對象（`package_out`，`GET /package-details` 返回
`{package: {...}}`）：`id` / `package_id` / `shop_id` /
`shop_name` / `name` / `name_en` / `items[]`（`{name, qty}`）/
`person_count` / `original_price` / `package_price` /
`save_amount` / `save_percent` / `valid_from` / `valid_to` /
`usage_rules` / `stock_total` / `stock_left` / `sold_count` /
`refund_policy` / `refund_policy_text`（用戶可見退款規則文案，
下單頁再提示一次）/ `status` / `images[]` / `branches[]` /
`rating`（`{shop_id, overall, taste, ambience, service,
review_count, updated_at}`，無評價時為 `null`）/
`latest_reviews[]`（最多 5 條）/ `limit_remaining` /
`per_user_limit`（可為 `null`）/ `time_slots[]` /
`stackable`（可為 `null`，未聲明時不顯示）。

訂單對象（`order_out`）：`id` / `order_id` / `order_no`
（`CG-` + 8 位訂單號，如 `CG-00000042`，詳情頁顯示此號）/
`user_id` / `order_type` / `package_id` / `quantity` /
`package_name` / `shop_name` / `amount`（應付）/ `paid_amount`
（實付）/ `payment_method` / `payment_status` / `refund_status` /
`refund_amount`（已成功退款金額，0 表示無）/ `refundable`
（是否可手動退款；`false` 時隱藏「申請退款」按鈕，不是置灰）/
`voucher_redeemed` / `settlement_status` / `commission_amount` /
`net_amount`（結算記賬字段，移動端不展示）/ `wallet_item_id` /
`created_at` / `paid_at` / `payments[]`（支付單對象：
`id` / `order_id` / `gateway` / `gateway_ref`（模擬器值已置空）/
`method` / `amount` / `currency` / `status` / `failure_message` /
`created_at`）。

支付結果確認：點「確認支付」後每 2 秒輪詢 `GET /orders/{id}`
最多 60 秒，期間顯示「正在確認支付結果」。`payment_status`
為 `paid` 顯示成功頁「套餐券已存入錢包」，按鈕「查看券」
（落到 Q Wallet 優惠券標籤）/「繼續逛」；`failed` / `timeout`
顯示失敗頁，按鈕「重新支付」/「取消訂單」；`pending` 超 15 分鐘
顯示「訂單已關閉」。`POST /orders/{id}/confirm-payment` 的
`outcome`（`succeeded/failed/timeout`）**僅開發模擬器接受**：
配了 Stripe key 時服務端改查網關、`outcome` 被忽略；非 dev
且無 key 時直接 403。客戶端代碼裡不得向 `confirm-payment`
傳 `outcome` 作為支付成功的依據；成功與否以輪詢到的
`payment_status` 為準。`confirm-payment` 成功響應：
`{order, message}`，`message` 為 `支付成功，套餐券已存入 Q
Wallet` / `支付失敗，可重試或取消訂單` / `支付逾時，可重試或取消
訂單`；訂單已不在待支付狀態時 400 `訂單已不在待支付狀態`。

退款：`POST /orders/{id}/refund`，body `{reason: ""}`。成功響應
`{refund, message}`，`message` 為 `退款成功，套餐券已作廢；款項
原路返回，預計 1–3 個工作日到賬`；詳情頁預計到賬文案固定為
「1–3 個工作日原路退回」。錯誤（400）：`只有已支付的訂單可以退款` /
`此訂單已有退款記錄` / `找不到此訂單的套餐券` /
`已核銷的套餐券不可退款` / `套餐券已失效，不可退款` /
`此為特價券，不設退款` / `此券僅支持過期自動退，過期前不設手動退款`。

### 砍價

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/bargain-campaigns` | 遊客可調。砍價活動列表（剩餘份數、底價、成團人數） |
| POST | `/bargain-campaigns/{id}/start` | 發起砍價（鎖 1 份庫存，每活動限一場進行中） |
| GET | `/bargain-sessions/{id}` | 遊客可調。砍價進度（當前價、助力列表、剩餘時間、退款記錄、分享連結） |
| GET | `/bargain-my-sessions` | 我的砍價 |
| POST | `/bargain-sessions/{id}/help` | 助力砍價，body `{device_id}`（可為空） |
| POST | `/bargain-sessions/{id}/purchase` | 砍到目標價後下單：生成套餐券進 Q Wallet，退回差價 |
| POST | `/bargain/refunds/{id}/retry` | 重試一筆失敗的砍價退款（本人或管理員；僅 `failed` 可重試） |
| POST | `/bargain/expire-sweep` | 僅 admin：過期砍價結算手動補跑（M1 字段見下） |

活動對象（`campaign_out`）：`id` / `campaign_id` / `package_id` /
`shop_id` / `shop_name` / `name` / `items` / `person_count` /
`original_price` / `floor_price` / `stock_total` / `stock_left` /
`helper_limit` / `duration_minutes` / `lock_stock` / `status` /
`image` / `helper_count` / `seconds_left` / `expires_at` /
`created_at`。預設值：成團人數 5 人；每場有效期 24 小時；活動
有效期 7 天；套餐券有效期 30 天；每刀遞減 30% → 25% → 20% →
15% → 10% → 5%，新用戶（註冊 7 天內）+10 個百分點，每刀最少
HK$1、永不低於底價；每人每單限助一次，每人每日最多助力 10 次。

場次對象（`session_out`）：`id` / `session_id` / `campaign_id` /
`campaign`（活動對象）/ `user_id` / `is_owner`（遊客為 `null`）/
`already_helped` / `cut_amount_total`（已砍金額，原價 − 現價）/
`remaining_amount`（距底價還差多少）/ `current_price` /
`target_price` / `floor_price` / `original_price` / `paid_amount` /
`status` / `helper_count` / `helper_limit` / `stock_locked` /
`wallet_item_id` / `expires_at` / `seconds_left` / `can_purchase`
（僅 `success` 且未下單時為 `true`）/ `share_link`
（`coupon-go://bargain?session={id}`）/ `helps[]`（`{id,
helper_user_id, helper_name, cut_amount, is_new_user, created_at}`）/
`refunds[]`（`{id, amount, reason, status, gateway_ref, note,
created_at}`；`gateway_ref` 為網關退款單號，移動端不展示）。
金額全部取接口字段，客戶端不自己算砍後價或退款額。

助力錯誤：場次已結束 400 `此砍價已結束，不能再助力`；給自己助力
400 `不能為自己的砍價助力`；重複助力 409
`您已助力過一次，每人每單限助一次`（好友助力頁顯示
「你已經幫 TA 砍過啦」）；同一設備重複 409 `同一設備已助力過此單`；
當日達上限 400 `今日助力已達上限（10 次）`。發起錯誤：活動已結束
400 `此砍價活動已結束`；已售罄 400 `已售罄，先到先得`；已有進行中
場次 409 `您已在砍此套餐，請先完成它`。下單錯誤：非發起人 403
`只有發起人可以下單`；未砍到目標價 400 `還未砍到目標價，不能下單`；
已下單 409 `已下單生成套餐券`。

終態文案：成功顯示「已購買，套餐券在錢包」加退款明細（`refunds[]`
金額取接口字段）；失敗顯示「砍價已結束，已全額退款 HK$X」加預計
到賬「1–3 個工作日原路退回」。不得出現「不退」「扣手續費」。
砍價券成功後進入同一個 Q Wallet 優惠券列表，帶「套餐」角標
（`is_package: true`，`source: "bargain"`）。

M3 字段變更：`GET /bargain-sessions/{id}`、
`POST /bargain-campaigns/{id}/start`、
`POST /bargain-sessions/{id}/purchase` 的響應**不再返回
`payment_simulated` 字段**（內部狀態用語，不應出現在客戶端可見
響應中；預付模擬記賬的說明見「砍價 2C」節正文，僅供對接方理解，
不向終端用戶展示）。

### 返現

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/cashback` | 返現餘額（可提現 / 待確認 / 待追蹤）+ 明細 + 門檻 / 每日上限 + 負向調整 |
| GET | `/cashback/transactions?status=&limit=` | 返現流水（含預計確認時間、90 天解鎖期限） |
| POST | `/cashback/withdraw` | 申請提現 `{amount, method: fps\|bank, account_name, account_ref?}` |
| GET | `/cashback/withdrawals` | 提現記錄（`failure_reason` 見下） |
| POST | `/cashback/transactions/{id}/void` | 取消 / 退貨作廢 `{reason: cancelled\|returned}`（僅待確認可作廢） |
| GET | `/cashback/missing` | 申訴列表（Gap 3 已補） |
| POST | `/cashback/missing` | Missing Cashback 申訴 |

`GET /cashback` 響應字段：`withdrawable_balance`（可提現，HK$，
可為負）/ `pending_balance`（待確認）/ `tracked_balance`（待追蹤）/
`total_earned`（累計返現）/ `adjustment_total`（負向調整合計，
可為負）/ `min_withdraw_hkd`（提現門檻，默認 50，取接口不寫死）/
`daily_withdraw_limit_hkd`（每日提現上限，默認 1500，取接口不寫死）/
`transactions[]`（最新 50 條）/ `adjustments[]` /
`by_status`（`{狀態: 筆數}`）。返現標籤頂部兩塊並列數字取
`withdrawable_balance` 與 `pending_balance`；積分與返現分開記賬，
絕不相加、不放在同一個數字裡。

返現交易對象：`id` / `merchant_id` / `shop_name` /
`redemption_id` / `channel`（`dinein` / `online`）/ `amount` /
`rate` / `status` / `status_text` / `funded_by` /
`expected_confirm_at`（線上渠道預計確認時間，可為 `null`）/
`expires_at`（線下 90 天解鎖期限，可為 `null`）/ `confirmed_at` /
`withdrawn_amount` / `remaining_amount` / `void_reason` /
`created_at`。線下待確認行顯示「再來一次即可到賬」和倒計時
（`expires_at` − 現在）；頂部可關閉的規則提示文案由客戶端寫，
不取接口字段。

提現請求示例：

```json
POST /api/v3/cashback/withdraw
{"amount": 120, "method": "fps", "account_name": "CHAN TAI MAN", "account_ref": "91234567"}
```

成功響應：`{message: "提現申請已提交，待打款", withdrawal, ...balances}`。
提現記錄對象：`id` / `amount` / `method` / `method_text`
（`轉數快 (FPS)` / `銀行轉賬`）/ `account_name` / `status` /
`failure_reason` / `processed_at` / `created_at`。**Gap 6 已補**：
提現失敗時 `failure_reason` 為服務端記錄的失敗原因
（如「銀行退回：賬戶不存在」），`pending` / `paid` 時為 `null`；
提現記錄頁「失敗」狀態必須同時顯示 `failure_reason`。另注意：
申請提現時校驗失敗（400）同樣在 body 裡返回 `failure_reason`
（與 `detail` 同值），客戶端直接顯示。提現校驗錯誤（400）：
`提現方式須為 fps（轉數快）或 bank（銀行轉賬）` / `提現金額須大於 0` /
`提現門檻為 HK$50，未達門檻`（金額取接口 `min_withdraw_hkd`，
文案裡的數字是服務端按配置生成的）/ `請填寫收款賬戶姓名` /
`請先完善註冊姓名後再提現`（註冊姓名為空時）/
`收款賬戶姓名須與註冊姓名一致`（內聯報錯）/ `可提現餘額不足` /
`每日提現上限為 HK$1500`（取接口 `daily_withdraw_limit_hkd`）。
**未確認**：目前沒有公開接口能把一筆 `pending` 提現標記為
`failed`（`failure_reason` 的寫入方是服務端函數
`mark_withdrawal_failed`，供運營側調用）；客戶端只讀該字段，
不要自己構造失敗原因。

申訴：`POST /cashback/missing`，body
`{merchant_id, order_no, spent_at, amount, evidence?, note?}`。
消費後 30 天內提交，30 天外日期禁用並提示；錯誤（400）：
`找不到此商戶` / `請提供訂單號 / 核銷憑證號` / `消費金額須大於 0` /
`消費日期不能是未來` / `申訴須在消費後 30 天內提交`。成功響應
`{message: "申訴已提交，調查期最長 120 天", appeal}`。
**Gap 3 已補**：`GET /cashback/missing?limit=` 返回本人申訴列表
`{appeals[]}`（最新在前），申訴對象：`id` / `merchant_id` /
`shop_name` / `order_no` / `spent_at` / `amount` / `evidence` /
`note` / `status` / `status_text` / `created_at` / `resolved_at`。

作廢：`POST /cashback/transactions/{id}/void`，僅 `tracked` /
`pending` 可作廢；錯誤（400）：`找不到此返現記錄` /
`只有待確認的返現可以作廢` / `void 原因須為 cancelled（取消）或
returned（退貨）`。成功響應 `{message: "返現已作廢", transaction}`。

**Gap 4 已補**：提現門檻與每日上限取 `GET /cashback` 的
`min_withdraw_hkd` / `daily_withdraw_limit_hkd`（默認 50 / 1500，
服務端配置可改），不要寫死。

### 附近

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/nearby-shops?lat=&lng=&radius_km=&sort=distance` | 附近商户（直線距離升序；預設半徑 10 公里，上限 50） |
| GET | `/shops/{id}/map` | 商户位置（經緯度、地址、Google Maps 搜尋 / 導航連結） |

請求示例：`GET /api/v3/nearby-shops?lat=22.3193&lng=114.1694&radius_km=10&sort=distance`

響應：`{center: {lat, lng}, radius_km, sort, total, shops[]}`。
`sort`：`distance`（距離升序，默認）/ `rating`（評分降序，無評分
排最後）/ `newest`（最新入駐在前）。每項為商户對象 + 附加字段：
`distance_km`（直線距離，km，保留 2 位小數）/ `latitude` /
`longitude` / `google_maps_url` / `directions_url` / `rating`
（商户星級，可為 `null`）/ `has_coupons` / `has_packages` /
`created_at`。無座標商户不列入；缺 `lat` / `lng` 時 400
`需要定位（lat/lng）才能列出附近商户`；`sort` 非法時 400
`sort 只支援 distance / rating / newest`；經緯度超範圍時 400
`經緯度超出範圍`。距離是直線距離，不是步行 / 駕車距離與耗時；
定位被拒時降級為按區域選擇，不反覆弹窗。

### 評價

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/reviews` | 寫評價（見下；只有已核銷可評，同一核銷只能評一次，接口層校驗） |
| GET | `/shops/{id}/reviews?page=&has_images=&sort=latest` | 商户評價列表（僅 `visible`，20/頁） |
| GET | `/shops/{id}/rating` | 商户評分聚合 |
| POST | `/reviews/{id}/reply` | 商户回覆（該店商户或 admin） |
| POST | `/reviews/{id}/dispute` | 差評申訴（作者 / 該店商户 / admin） |
| POST | `/reviews/{id}/helpful` | 有用投票 |
| POST | `/uploads/review-images` | 上傳晒圖（multipart，`files`，最多 9 張，每張 ≤5MB；見「晒圖上傳」節） |
| POST | `/review?wallet_item_id=` | 【舊版】評價已用券，+15 分（一次）；不寫評價記錄、不影響星級 |

請求示例：

```json
POST /api/v3/reviews
{"redemption_id": 88, "taste": 5, "ambience": 4, "service": 5,
 "content": "味道很好，服務也快。", "images": [], "tags": ["抵食"], "claim_points": true}
```

`redemption_id` 與 `wallet_item_id` 二者至少傳一個（傳
`wallet_item_id` 時取其最近一次核銷記錄）；三維評分各 1–5 分；
`content` ≤ 1000 字；`images[]` ≤ 9（上傳後填入返回路徑）；
`tags[]` ≤ 10；`claim_points` 默認 `true`。成功響應
`{message, review, balance}`（`balance` 為評價後積分餘額）；
`claim_points=true` 時 `message` 為
`評價成功，+15 分，已計入商户星級`，評價得 +15 分且計入星級；
`claim_points=false` 時 `message` 為 `評價成功，已計入商户星級`，
不得分但同樣計入星級（M7：所有 `visible` 評價都計入星級，積分
與星級完全解耦，`counts_toward_rating` 恆為 `true`）。錯誤：未核銷 400
`只有已核銷的優惠券可以評價` / `只有已使用的優惠券可以評價`；已撤销核銷 400 `此核銷已被撤销，無法評價`；
重複評價 400 `已經評價過`；找不到核銷 / 券 404
`找不到此核銷記錄` / `找不到此優惠券`；非本人 403
`只能評價自己的已核銷優惠券`；二者都沒傳 422
`請提供 redemption_id 或 wallet_item_id`；維度越界 422。

評價對象：`{id, user_id, user_name, shop_id, redemption_id,
wallet_item_id, taste, ambience, service, overall（三分均值）,
content, images[], tags[], points_given, counts_toward_rating,
status, reply_text, replied_at, created_at}`（另有列表接口附加的
`helpful_count` / `helped`，見 `review_out`）。

`GET /shops/{id}/rating` 響應：`{shop_id, overall, taste, ambience,
service, review_count, rating_distribution, updated_at}`；無可見評價
時分數為 `null`、`review_count=0`。`rating_distribution` 為
1–5 星分佈計數。`GET /shops/{id}/reviews` 響應：
`{shop_id, current_page, total_pages, total, rating_distribution,
reviews[]}`；`sort=latest`（默認）/ `helpful`，非法值 422
`sort 只支援 latest / helpful`；`has_images=true` 只看有圖。

### 核銷 / 出示 / 錢包 / 積分

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/new-favorite-coupon?coupon_id=` | 領券 → Q Wallet，+10 分；重複領取 409 |
| POST | `/store-scaned-qr-values?wallet_item_id=` | 生成 QR 碼 + 6 位兌換碼，15:00 有效；每次出示輪換新碼 |
| POST | `/new-scan-coupon/prepare` | 預核銷（唯讀查券，`{code}`）→ `can_redeem` + 5 分鐘 prepare token，不改券狀態 |
| POST | `/new-scan-coupon` | 商戶掃碼核銷（商户 / admin 調用） |
| POST | `/redemptions/{id}/revoke` | 核銷撤销（10 分鐘窗口內；券退回未使用、換新碼、+20 分沖回；超時 400；M7：若該核銷已有評價，評價改為隱藏、已發的 +15 評價積分沖回，響應新增 `review: {review_id, points_clawed_back, status}`，無評價時為 `null`） |
| GET | `/get-favorite-coupons-list?page=&status=&sortby=` | 錢包列表，三狀態：1 未使用 / 2 已使用 / 3 已失效；20/頁 |
| GET | `/get-user-record-list` | 消費記錄 |
| GET | `/points` | 積分餘額 + 明細（最新 50 條） |
| POST | `/points/exchange?coupon_id=` | 9 積分兌換 9 分分券 |

領券：`POST /api/v3/new-favorite-coupon?coupon_id=5`，成功響應
`{message: "已存入 Q Wallet", wallet_item_id, coupon}`；錯誤：
券不存在 404 `找不到此優惠券`；已失效 400 `此優惠券已失效`；
套餐券 400 `套餐券請購買，不可免費領取`；積分券 400
`積分券請用積分兌換`；重複領取 409 `您已領取此 Coupon`；已達
下載上限 400 `優惠券已達下載上限`。

錢包項對象（`wallet_out`）：`id` / `coupon_id` / `coupon_name` /
`shop_name` / `coupon_type` / `status`（1/2/3）/ `code`
（當前 6 位兌換碼，未出示時為 `""`）/ `qr_expires_at` /
`claimed_at` / `used_at` / `reviewed` / `valid_to` /
`discount_price` / `regular_price` / `source` / `order_id` /
`is_package` / `refund_policy`。

**出示頁二維碼（Dot 階段 6 需求）**：
`POST /store-scaned-qr-values?wallet_item_id=` 返回
`{wallet_item_id, code, qr_payload, expires_in_seconds}`。
`expires_in_seconds` 固定為 900（15:00，配置項
`QR_TTL_SECONDS`）；`qr_payload` 為
`coupon-go://redeem?code={code}&item={id}`。每次調用都生成全新
6 位碼並覆蓋舊碼（防截圖：舊碼立即失效）；倒計時以
`expires_in_seconds` / 錢包項的 `qr_expires_at` 為準，到期後
**重新調用本接口換碼**，不要在客戶端自己編碼。核銷成功後碼
被清空（`code: ""`、`qr_expires_at: null`），截圖也無法再用。
出示頁同時：亮度拉滿、退出時恢復亮度、顯示「請勿轉發二維碼」。
錯誤：券不存在 404 `找不到此優惠券`；券不可用 400
`此優惠券不能使用`。

預核銷：`POST /new-scan-coupon/prepare`，body `{code}`，返回
`can_redeem` 與 5 分鐘 prepare token（僅商户端調用，移動端用戶頁
不需要）。核銷：`POST /new-scan-coupon`，body
`{code, idempotency_key?, prepare_token?}`，成功響應
`{message: "您的優惠券已成功使用", coupon_name, shop_id,
redemption_id, duplicate}`；同 `idempotency_key` 重放只算一次
（`duplicate: true`）。錯誤（400）：`無效的 QR 碼！` /
`無效的 QR 碼！此碼屬於其他商店。` / `此優惠券已使用` /
`此優惠券已失效` / `QR 碼已過期，請重新出示` /
`預核銷已失效，請重新預核銷` / `今日掃碼已達上限`（每日 200 次）。
核銷成功用戶 +20 分。

**核銷成功頁返現金額（Dot 階段 6 需求）**：核銷接口的響應裡
**沒有返現金額字段**。顯示規則：核銷成功後調
`GET /cashback/transactions?limit=50`，在返回的 `transactions[]`
裡按 `redemption_id` 匹配本次核銷（`redemption_id` 即核銷響應
的 `redemption_id`）；匹配到且狀態為 `pending` / `tracked` 時
顯示「本次返現 HK$X · 待確認」（`amount` 取接口字段）；
**沒有匹配到時不顯示**。免費券 / 純門檻券 / 無價券沒有可驗證
實付金額，服務端不記返現（`record_spend` 返回空），因此不顯示；
套餐券按訂單實付 `Order.paid_amount` 計，價格券按
`discount_price` 計。不要用券面值或 `min_spend` 自己算返現。

積分：`GET /points` 響應 `{balance, transactions[]}`，明細項
`{id, delta, reason, created_at}`。積分規則：領券 +10、核銷 +20、
評價（領積分時）+15；積分行用「分」後綴、中性色，返現行用 HK$
前綴，兩者絕不相加。`POST /points/exchange?coupon_id=` 為
9 積分兌換。

### 搜尋 / 通知

| 方法 | 路徑 | 說明 |
|---|---|---|
| POST | `/auto-suggestion-new?keyword=` | 三分支建議：券 → 券詳情；有分店商户 → 分店；其餘 → 商户 |
| GET | `/get-search-history` | 搜尋記錄（最新 20 條） |
| POST | `/delete-search-history?history_id=` | 刪除搜尋記錄（不傳 `history_id` 刪全部） |
| GET | `/new-filter-details` | 篩選（分類 + 地區） |
| GET | `/place/autocomplete/json`、`/place/details/json`、`/place/nearbysearch/json` | Google Places 代理（需 `GOOGLE_API_KEY`，未設則 503） |
| GET | `/get-notification-list` | 通知列表 + 未讀數 |
| POST | `/notifications/{id}/read`、`/notifications/read-all` | 標示已讀 |
| POST | `/limited-time-notification?enabled=` | 限時推送開關 |

`POST /auto-suggestion-new?keyword=火鍋` 響應：
`{suggestions: [{kind: "coupon"|"shop"|"branch", id, title,
subtitle, has_branch: 0|1}], keyword}`（券與商户各最多 5 條）。
`GET /get-notification-list` 響應：
`{notification_count（未讀數）, notifications[]}`；標示已讀成功
響應 `{message: "已標示為已讀"}` / `{message: "全部已標示為已讀"}`；
找不到通知 404 `找不到此通知`。

### 健康檢查（M1，移動端可調）

| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/health` | 無需登錄（注意：不在 `/api/v3` 前綴下）。返回 `{status, env, scheduler}` |

`GET /health` 響應示例：

```json
{"status": "ok", "env": "dev",
 "scheduler": {"enabled": true, "configured_explicitly": false,
               "interval_seconds": 300, "cashback_batch_size": 100,
               "reason": "开发环境默认启用调度器，每 300 秒执行一次"}}
```

`scheduler.enabled=false` 時，過期退款 / 砍價結算 / 超時關閉 /
返現 sweep 不會自動執行，只能走管理員手動接口補跑（見下）；
`reason` 字段說明原因，客戶端如需展示狀態可直接顯示該文案。
默認每 300 秒執行一次（配置項 `SCHEDULER_INTERVAL_SECONDS`）。

### 管理員手動補跑接口（M1，僅 admin，移動端不調）

| 方法 | 路徑 | 響應新增字段（M1） |
|---|---|---|
| POST | `/orders/process-expired-refunds` | `{refunded, voided, failed, skipped, message}`；`failed` 為單條失敗數（下一周期重試），`skipped` 為併發衝突跳過數 |
| POST | `/bargain/expire-sweep` | `{message, settled, checked, failed}`；`failed` 為單條失敗數（下一周期重試） |

兩個接口與後台定時任務共用同一份邏輯（見 `docs/SCHEDULER.md`），
冪等，可重複調用。

### Dot 字段缺口對照（M5 已處理）

| 缺口 | 狀態 | 對接方式 |
|---|---|---|
| Gap 6 提現失敗原因 | 已補 | `GET /cashback/withdrawals` 的 `failure_reason`；提現申請校驗失敗時 400 body 同時帶 `failure_reason` |
| Gap 3 申訴列表 | 已補 | `GET /cashback/missing`，見「返現」節 |
| Gap 4 返現門檻 / 每日上限 | 已補 | `GET /cashback` 的 `min_withdraw_hkd` / `daily_withdraw_limit_hkd`，見「返現」節 |
| 核銷成功頁返現金額 | 已寫清 | 核銷響應無此字段；用 `GET /cashback/transactions` 按 `redemption_id` 匹配，僅匹配到時顯示，免費券不顯示 |
| 出示頁二維碼倒計時與換碼 | 已寫清 | `expires_in_seconds=900`；到期重新調 `POST /store-scaned-qr-values` 換碼 |
| 商户返現比例 X% | **未確認** | 無接口返回該字段；服務端默認線下比例 5% 未經接口暴露，商户詳情頁暫不顯示，待後端補字段 |
| 提現 `failed` 的標記接口 | **未確認** | 無公開接口可把提現標記為失敗；`failure_reason` 由運營側服務端函數寫入，客戶端只讀 |