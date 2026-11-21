# 上架（Dianping listing，GAP §3）API

- 基線：遠端 main `3f2d8bc`；分支 `dianping/listing`；遷移 `0021_dianping_listing`
 （`down_revision = 0020_m92_legal_consents`，見末節「遷移與合入順序」）。
- 範圍：`GAP_ANALYSIS_20261121.md` §3.2–§3.6。§3.1（普通券售價與購買流程）
  不在本片：購買流程是 P1-A 的活；本片只把售價字段的口徑建好（見「與 P1-A 的銜接點」），免費券行為不變。
- Base URL 與鑑權與 `docs/API.md` 相同：路徑前綴 `/api/v3`，
  `Authorization: Bearer <JWT>`。

## 1. 狀態機（券與套餐同一套）

```
draft ──submit──▶ pending_review ──approve──▶ active ⇄ paused
                     │  ▲                         │  │
                     │  │                         │  └─unlist──▶ draft
                     │  └─reject（帶 reason）      │
                     │                             ├─ 定時：valid_from 未到 ──▶ scheduled
                     │                             │     （sweep 到點自動轉 active）
                     │                             ├─ valid_to 已過 ──▶ expired（sweep）
                     │                             └─ 庫存歸零 ──▶ sold_out（sweep）
                     └─ 商戶編輯 ──▶ draft（被駁回的改完回草稿）
```

- 建券 / 建套餐一律進 `pending_review`，不再直接上架。套餐以前是建完即
  `active`，本片改為待審（`store_package`）。
- 只有 `pending_review` 能審批；審批通過的落點由
  `listing.live_status(valid_from, valid_to, 庫存）` 決定：`valid_from` 未到
  → `scheduled`，庫存為 0 → `sold_out`，已過 `valid_to` → 400 不給批。
- 商戶編輯已上架 / 已暫停 / 已排期內容（改價、改庫存、改有效期）會送回
  `pending_review` 重新審核，審核期間暫時下架；草稿 / 被駁回的編輯留在
  `draft`；待審的編輯留在 `pending_review`。已過期 / 已售罄不可編輯，請重建。
- 套餐的券模板（`kind='package'` 的 `Coupon`）與套餐狀態始終同步：審批、
  暫停、恢復、下架、提交、編輯、sweep 都同步寫模板券狀態。已購套餐券的核銷
  讀模板券狀態，所以套餐重審期間，已購券暫時不可核銷（與券重審期間已領券
  不可核銷同一口徑）。
- 公開列表只認 `status == "active"` 且在有效期內（`valid_from <= now <
  valid_to`）：`scheduled`、`paused`、`pending_review`、`draft`、`rejected`
  都不會出現在首頁 / 分類 / 搜尋 / 附近。領券與下單同樣只認 `active`。

## 2. 新接口一覽

| 方法 | 路徑 | 權限 | 說明 |
|---|---|---|---|
| PATCH | `/vendor/coupons/{coupon_id}` | vendor（本人）/ admin | §3.2 編輯券 |
| POST | `/vendor/coupons/{coupon_id}/pause` | vendor（本人）/ admin | 上架中 → 已暫停 |
| POST | `/vendor/coupons/{coupon_id}/resume` | vendor（本人）/ admin | 已暫停 → 上架中 / 已排期 |
| POST | `/vendor/coupons/{coupon_id}/unlist` | vendor（本人）/ admin | 下架回草稿 |
| POST | `/vendor/coupons/{coupon_id}/submit` | vendor（本人）/ admin | 草稿 / 被駁回 → 提交審核 |
| POST | `/vendor/coupons/{coupon_id}/approve?approve=&reason=` | admin | 審核券（見 §3） |
| POST | `/vendor/coupons/bulk` | vendor / admin | §3.3 批量建券（1–20 張） |
| GET | `/coupon-templates` | vendor / admin | §3.3 三個固定模板 |
| PATCH | `/vendor/packages/{package_id}` | vendor（本人）/ admin | §3.2 編輯套餐 |
| POST | `/vendor/packages/{package_id}/pause` | vendor（本人）/ admin | 上架中 → 已暫停 |
| POST | `/vendor/packages/{package_id}/resume` | vendor（本人）/ admin | 已暫停 → 上架中 / 已排期 |
| POST | `/vendor/packages/{package_id}/unlist` | vendor（本人）/ admin | 下架回草稿 |
| POST | `/vendor/packages/{package_id}/submit` | vendor（本人）/ admin | 草稿 / 被駁回 → 提交審核 |
| POST | `/admin/packages/{package_id}/approve?approve=&reason=` | admin | §3.2 審核套餐 |
| GET | `/admin/packages?status=&shop_id=` | admin | §3.2 審核隊列 |
| POST | `/admin/listing-sweep` | admin | §3.3 手動跑定時上架 sweep |
| POST | `/uploads/merchant-images` | vendor / admin | §3.5 商戶圖片上傳 |

既有接口的變化：

| 方法 | 路徑 | 變化 |
|---|---|---|
| GET | `/get-vendor-coupon-list?status=` | 新增 `status` 篩選（草稿 / 待審 / 已上架 / 已暫停…） |
| POST | `/coupon-previewed` | 建券請求新增 `sale_price`、`refund_policy`、`per_user_limit`（見 §6）；仍進 `pending_review` |
| POST | `/packages` | 建立後狀態改為 `pending_review`（以前是 `active`）；菜品校驗見 §7；仍返回 201 |
| POST | `/new-favorite-coupon?coupon_id=` | 新增守衛：未到 `valid_from` 400；付費券（`sale_price > 0`）400「付費券請購買」；按 `per_user_limit` 計數限領（默認 1） |
| GET | `/home`、`/get-classification-coupons`、`/get-limited-time-coupons`、`/get-most-people-grab`、`/recomandation-coupons` | 只列 `active` 且 `valid_from <= now` 的券 |
| GET | `/auto-suggestion-new`、`/nearby-shops` | 同上（`has_coupons` / `has_packages` 也只認有效期內的 `active`） |

## 3. 審核（券 / 套餐同一口徑）

### `POST /vendor/coupons/{coupon_id}/approve?approve=true&reason=`

```json
// 通過，未到 valid_from → scheduled
{ "coupon_id": 12, "status": "scheduled", "reason": "" }

// 通過，已在有效期內 → active
{ "coupon_id": 12, "status": "active", "reason": "" }

// 駁回
{ "coupon_id": 12, "status": "rejected", "reason": "錯別字" }
```

- 只有 `pending_review` 能審批，否則 400；`approve=false` 必須是 admin；
  通過時若 `valid_to` 已過 → 400「優惠券已過期，無法上架」。
- 審批結果寫 `admin_audit_logs`（`coupon.approve` / `coupon.reject`）。

### `POST /admin/packages/{package_id}/approve?approve=true&reason=`

```json
{ "package_id": 7, "status": "active", "coupon_status": "active", "reason": "" }
```

- 與券相同；`coupon_status` 是套餐券模板同步後的狀態。
- 審核隊列：`GET /admin/packages?status=pending_review`（admin），
  券的隊列沿用既有 `GET /admin/coupons?status=pending_review`。

## 4. 編輯 / 暫停 / 恢復 / 下架 / 提交

### `PATCH /vendor/coupons/{coupon_id}`

```json
// 請求（全部可選，只傳要改的）
{ "percentage": 80, "coupon_name": "改價券 v2", "total": 8 }

// 響應（改了已上架券 → 回待審）
{ "message": "已儲存，內容將重新提交審核。",
  "coupon": { "id": 12, "status": "pending_review", "percentage": 80,
              "total": 8, "remaining": 7, "...": "..." } }
```

- 可改：`coupon_name`、`name_en`、`category_id`、`cash_amount`、
  `percentage`、`gift`、`discount_price`、`regular_price`、`sale_price`、
  `refund_policy`、`min_spend`、`per_user_limit`、`image`、`valid_from`、
  `valid_to`、`total`、`additional_terms`、`is_limited_time`。
  不可改：`shop_id`、`vendor_id`、`kind`、`package_id`。
- `total` 改動時 `remaining` 按已領張數同步調整（`remaining =
  max(0, 新 total − 已領）`，永不為負）。
- `valid_to <= valid_from` → 400；`refund_policy` 不在三值內 → 400；
  `per_user_limit < 1` / `sale_price < 0` → 422；非本人 → 403；
  已過期 / 已售罄 → 400。

### `PATCH /vendor/packages/{package_id}`

```json
// 請求（全部可選）
{ "package_price": 99, "stock_total": 10,
  "items": [{ "name": "主菜一份", "qty": 1 }] }

// 響應（改了已上架套餐 → 回待審，模板券同步 pending_review）
{ "message": "已儲存，內容將重新提交審核。",
  "package": { "id": 7, "status": "pending_review", "package_price": 99,
               "stock_total": 10, "stock_left": 8, "...": "..." } }
```

- 可改：`name`、`name_en`、`items`、`person_count`、`original_price`、
  `package_price`、`valid_from`、`valid_to`、`usage_rules`、`stock_total`、
  `refund_policy`。
- `package_price > original_price` → 400；`stock_total` 不可少於已售份數
  → 400（`stock_left` 按差額同步增減，下限 0）；菜品校驗見 §7。

### 暫停 / 恢復 / 下架 / 提交（券與套餐路徑對稱）

```json
// POST /vendor/coupons/12/pause      → { "coupon_id": 12, "status": "paused" }
// POST /vendor/coupons/12/resume     → { "coupon_id": 12, "status": "active" }
//   （valid_from 未到時 resume 回 scheduled；已過期 / 已售罄 → 400）
// POST /vendor/coupons/12/unlist     → { "coupon_id": 12, "status": "draft" }
// POST /vendor/coupons/12/submit     → { "coupon_id": 12, "status": "pending_review" }
// 套餐同一組路徑，回 {"package_id": 7, "status": "..."}
```

- `pause` 只認 `active`；`resume` 只認 `paused`；`unlist` 認 `active` /
  `paused` / `scheduled` / `pending_review` / `rejected`（已過期 / 已售罄
  本來就已下架，400）；`submit` 只認 `draft` / `rejected`。
- 錯狀態一律 400；非本人 403（admin 可操作任何一家的）。

## 5. 定時上架（§3.3）

- 沒有獨立的「定時開關」欄位：`valid_from` 就是定時點。審批通過時若
  `valid_from` 未到，狀態為 `scheduled`；調度器每周期把到點的
  `scheduled` 轉 `active`，把過了 `valid_to` 的 `active` / `scheduled` /
  `paused` 轉 `expired`，把庫存歸零的 `active` 轉 `sold_out`。下架定時點
  就是 `valid_to`（到期自動下架）。
- 調度器第 5 個任務 `listing-schedule`（與 M1 同款：Postgres advisory
  lock key `2026111705`，SQLite 走 `scheduler_locks` 租約行；單條失敗不
  阻塞其他，下一周期重試；冪等，重跑是 no-op）。手動補跑：
  `POST /admin/listing-sweep`（admin）或 CLI
  `python -m app.scheduler run-once --job listing-schedule`。
  詳見 `docs/SCHEDULER.md`。
- Sweep 返回示例：

```json
{ "coupons_activated": 2, "coupons_expired": 1, "coupons_sold_out": 0,
  "coupons_rescheduled": 0, "packages_activated": 1, "packages_expired": 0,
  "packages_sold_out": 0, "packages_rescheduled": 0, "failed": 0 }
```

- 例外：歷史遺留的 `active` 但 `valid_from` 未到的券，領券時直接 400
  「此優惠券尚未到使用日期」，sweep 會把它們打回 `scheduled`。

## 6. 批量建券與模板（§3.3）

### `POST /vendor/coupons/bulk`

```json
// 請求：1–20 張，每張與 POST /coupon-previewed 的 body 相同
{ "coupons": [
  { "shop_id": 1, "coupon_type": "Discount", "coupon_name": "批量券 A",
    "percentage": 90, "valid_from": "2026-11-21T00:00:00",
    "valid_to": "2026-12-21T00:00:00", "total": 50 },
  { "shop_id": 1, "coupon_type": "Cash", "coupon_name": "批量券壞",
    "cash_amount": 20, "valid_from": "2026-11-26T00:00:00",
    "valid_to": "2026-11-22T00:00:00", "total": 10 }
] }

// 響應：200，部分成功；失敗逐項報 index + 原因，不阻塞有效項
{ "created": [ { "id": 21, "status": "pending_review", "...": "..." } ],
  "failed": [ { "index": 1, "error": "到期日期必須晚於生效日期" } ],
  "message": "已建立 1 張，失敗 1 張" }
```

- 空列表 / 超過 20 張 → 400；全部建好都進 `pending_review`。

### `GET /coupon-templates`

```json
{ "templates": [
  { "key": "manjian", "name": "滿減券", "coupon_type": "Cash",
    "cash_amount": 50, "min_spend": 100, "per_user_limit": 1,
    "refund_policy": "anytime",
    "additional_terms": "滿 HK$100 可用，每人限領 1 張；不可與其他優惠同時使用。" },
  { "key": "zhekou", "name": "折扣券", "coupon_type": "Discount",
    "percentage": 90, "min_spend": 0, "per_user_limit": 1,
    "refund_policy": "anytime", "additional_terms": "全單 9 折，每人限領 1 張；不可與其他優惠同時使用。" },
  { "key": "maizeng", "name": "買贈券", "coupon_type": "Gift",
    "gift": "飲品一杯", "min_spend": 0, "per_user_limit": 1,
    "refund_policy": "anytime", "additional_terms": "消費即贈飲品一杯，每人限領 1 張；數量有限，送完即止。" }
] }
```

- 唯讀預設值：前端拿模板填表，商戶補上門店、日期、庫存後照常提交審核。
  後端不存模板表（GAP §3.3 原建議「模板放前端常量，後端不動」；本片把
  同三份常量改由接口下發，Dot 可直接取用，也可繼續寫死前端，兩者一致）。

## 7. 字段變更（先記於此，`docs/API.md` 商戶版 / 套餐兩節同步更新）

### 券（`Coupon`，遷移 `0021_dianping_listing`）

| 欄位 | 類型 | 說明 |
|---|---|---|
| `sale_price` | Numeric(10,2)，可空 | §3.1 售價（HKD）：空 / 0 = 免費，> 0 = 付費。建券 / 編輯可填；`coupon_out` 回 `sale_price` + `is_paid`（> 0 為 true） |
| `per_user_limit` | Integer，默認 1 | §3.4 每人限領張數；舊行回填 1（即舊行為）；`coupon_out` 回傳 |
| `refund_policy` | String，默認 `anytime` | 券的退款政策，與套餐同一組三值 `anytime` / `expiry_only` / `non_refundable`；建券 / 編輯可填；`coupon_out` 回傳 |

- `Coupon.status` 合法值新增 `paused`、`scheduled`、`sold_out`（原有
  `draft` / `pending_review` / `active` / `rejected` / `expired` 不變）。
- `Package.status` 合法值新增 `pending_review`、`draft`、`paused`、
  `scheduled`（原有 `active` / `inactive` / `sold_out` 不變）。

### 領券（`POST /new-favorite-coupon`）

- 每人限領按張數計：同一用戶已領 `per_user_limit` 張 → 409；
  庫存扣減在 `UPDATE ... WHERE 計數 < limit` 裡原子完成，併發領券不會超發。
- `sale_price > 0` 的券領券 400「付費券請購買，不可免費領取」；
  `valid_from` 未到 400「此優惠券尚未到使用日期」。

### 套餐菜品（§3.6，免遷移）

- `items` 每項必須是 `{ "name": 非空字串, "qty": >= 1 的整數 }`，否則 422；
  建套餐與編輯套餐同一校驗。`items` 可為空列表（不強制至少一道菜）；
  `usage_rules` 仍走 `UsageRulesIn` 的既有 JSON 校驗，不新增結構。

## 8. 商戶圖片上傳（§3.5，免遷移）

### `POST /uploads/merchant-images`（multipart，vendor / admin）

```json
// 請求：files（1–9 張，jpg / jpeg / png / webp，單張 ≤ 5 MB）
// 響應：201
{ "message": "圖片已上傳",
  "images": [ "/uploads/merchant-images/<uuid>.png" ] }
```

- 復用 `uploads.py` 的 `_save_file` + `strip_exif`（去 EXIF、UUID 檔名、
  同 `UPLOAD_MAX_BYTES` 上限）；gif → 400；customer → 403。
- 返回的 URL 直接填 `Coupon.image` / `Shop.image`。與評價圖同一套本地磁碟
  佔位存儲，上線前須換對象存儲（見 `uploads.py` 模組說明）。

## 9. 庫存 / 售價 / 免費 / 退款政策的上架口徑（統一）

- 庫存：券 `remaining` / 套餐 `stock_left` 歸零 → `sold_out`（sweep 補底；
  套餐購買時既有 `_sync_coupon_stock` 即時翻轉）；售罄不可領 / 不可買，
  恢復上架（`resume`）時已售罄 → 400。
- 售價 / 免費：`sale_price` 空 / 0 = 免費（領券不變）；> 0 = 付費
  （不可免費領，只能買）。`discount_price` 仍是券面優惠價，不是售價，
  兩者不混用；結算基數仍走 `settlement_basis`（價格券取
  `discount_price`，現金 / 免費 / 純門檻券取 0），本片不改口徑。
- 退款政策：券與套餐同一組三值，建 / 改時校驗；`non_refundable` 與
  `expiry_only` 的退款攔截沿用套餐既有邏輯（券的購買退款隨 P1-A 的訂單
  退款走同一規則）。

## 10. 與 P1-A（付費券購買）的銜接點

- **口徑一致**：本片的 `Coupon.sale_price`（Numeric(10,2)，空 / 0 =
  免費，> 0 = 付費）、`CouponCreateIn.sale_price`（`ge=0`）、
  `refund_policy`（默認 `anytime`）、`coupon_out` 的 `sale_price` /
  `is_paid` / `refund_policy`，與 P1-A 本地分支
  （`stage1/p1-a-paid-coupons`，提交 `5289677`）的設計逐字相同；
  P1-A 的 `claim_coupon` 付費守衛（「付費券請購買，不可免費領取」）
  已在本片的領券路徑實現。
- **本片不做的**：`POST /coupons/{id}/purchase`（Order + `coupon_id`
  路由）、訂單支付 / 退款、`orders.coupon_id` 列、`Coupon.sale_price`
  的結算口徑——全部是 P1-A 的範圍，本片的付費券在 P1-A 合入前建好後
  無法被購買（建好、審批、上架都正常，領券會被攔截）。
- **合入順序**：P1-A 遷移 `0021_p1a_paid_coupons` 與本片遷移
  `0021_dianping_listing` 都是 `down_revision = 0020`、都加
  `coupons.sale_price`（P1-A 還加 `orders.coupon_id` 與
  `ck_coupons_remaining_nonneg`）。兩邊的 `upgrade()` 都是冪等的
  （列 / 約束已存在就跳過），所以**先合入哪一片，另一片都不會在建表時
  報錯；後合入的一片只需改號重排**（建議：後合入者改為 `0022`，
  `down_revision` 指向上一個；若 M9-7（`0021_account_deletion`）先合入，
  兩片再順延）。`orders.coupon_id` 仍由 P1-A 的遷移負責，本片不加。

## 11. 遷移與合入順序

- 本片唯一遷移：`0021_dianping_listing`，`down_revision =
  0020_m92_legal_consents`（核實時遠端 main 最大遷移號即 `0020`）。
  內容：`coupons.sale_price`、`coupons.per_user_limit`（舊行回填 1）、
  `ck_coupons_remaining_nonneg`（`remaining >= 0`，負值舊行先清零）。
  `downgrade()` 依序撤約束、撤兩列。
- 撞號說明：P1-A（本地 `0021_p1a_paid_coupons`）、M9-7（未實現，
  預留 `0021_account_deletion`，P1-D 的 `0022` 指它）在遠端都不存在；
  遠端視角下 `0021` 是空號，本片按「遠端最大號 + 1」取號。合入時以前一節
  的重排規則處理，不改 P1-D 的 `down_revision`。
- 其餘改動全部免遷移：狀態值是既有 `String(20)` 列的取值擴充；模板是
  接口常量；批量是編排層；圖片上傳復用既有存儲。

## 12. 未做 / 延後（不在本片範圍）

- §3.1 購買流程（P1-A，見 §10）。
- §3.4 適用門店關聯（`coupon_branches` 關聯表）：屬第 1 塊（§1.2），
  本片不建表；`assign_branches` 仍是既有行為。
- §3.4 多圖與富文本詳情：P2，先用 `additional_terms` + 單圖頂著。
- §3.6 套餐內容與收銀菜品的關聯：等收銀系統接入時再做。
- 審核通知（審批通過 / 駁回推給商戶）：屬第 1 塊（§1.5），本片只寫
  `admin_audit_logs`。
- 批量建套餐：本片只做批量建券。

## 13. 測試

- `apps/api/tests/test_dianping_listing.py`（30 條）：套餐審核流轉
  （建 → 待審 → 通過 / 駁回 / 定時）、券編輯 / 暫停 / 恢復 / 下架 /
  提交、批量建券、模板、每人限領、售價口徑、sweep（到期 / 售罄 /
  冪等）、商戶圖片上傳、調度器接線、遷移欄位存在。
- 既有測試：10 個文件的 `_create_package` helper 改為「建完先走管理員
  審批再使用」（`test_packages`、`test_fix1_payment_gateway`、
  `test_fix3_row_locks`、`test_gap10_field_topups`、
  `test_gap1_admin_orders`、`test_gap7_purchase_quantity`、
  `test_m1_scheduler`、`test_m4_review_self_check`、
  `test_m6_settlement_basis`、`test_p1_payments_copy`），其餘斷言不變。
- Postgres：見 PR 匯報（遷移 `0021` 在真實 Postgres 上升 / 降級驗證，
  本片測試檔以 `LISTING_TEST_POSTGRES_URL` 跑過）。
