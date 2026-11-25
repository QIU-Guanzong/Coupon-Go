# C4–C5 · 發券與編輯的實作及上線方案

此 PR 只有前置核對與方案。**不打開真實付費券／價格／退款政策寫入、不改結算／退款計算，不部署。** 後端對照 `main@e1c4fb3`；Flutter 對照 #56 加 L1–L17 的本地候選。下列建議依 C4–C5 指令整理；完整設計文件未在此工作區取得，不假定已核對該文件。

## 已在跑的發券功能（C4）

| 欄位／流程 | 實況及下一步 |
| --- | --- |
| 券類型 | 已有 Cash / Discount / Gift / Price。Price 已送 `sale_price`；Gift 已送 `gift`，原樣保留。 |
| 英文名、分類、每人限領 | 已送 `name_en`、`category_id`（有選才送）、`per_user_limit`（正整數）。不再重建。 |
| 生效 | 已可選香港日期及時間，再轉 UTC；送審前再次檢查生效尚未經過及早於到期。留存原輸入，維持「送審」，不承諾自動上架。 |
| 類型切換 | 目前不會把不可見的 Price / Gift 欄位送入不相干的 payload，但切回原類型時原控制器仍保有舊值。保留共用欄位；清空不相干的商業欄位及錯誤、必要時才顯示已清除，避免使用者以為新類型沿用舊價格。 |
| 待補 | `image`、`regular_price`、`discount_price`、`refund_policy`。三節沿用 L5，現有欄位相對次序不動；新增欄位置於「基本資料」、「價格與數量」或「有效期與使用條款」的對應組。 |

價格以港幣兩位小數的文字輸入先驗證有限、非負，不改既有 JSON 數字合約。**Price 選擇付費時仍必須大於零**：現有後端 `sale_price=null/0` 視為免費，用 0 送出「付費券」會產生相反效果。不可用顯示的「售價」覆蓋抵扣價，也不可從 `discount_price` 推算實收；前者進支付路徑，後者屬券面條件。退款政策白名單為 `anytime / expiry_only / non_refundable`；免費券維持現有預設，未清楚選定不改退款規則。

券圖使用商家公開刊登媒體接口 **`POST /uploads/merchant-images`**，單張圖用 multipart `files`，接受 JPG/JPEG/PNG/WebP；只把成功返回的 `/uploads/merchant-images/...` 放入 `image`。不要用 `/merchant-files`（入駐附件，需授權下載），不把檔案或臨時路徑送入公開券資料。上傳失敗保留其他欄位；取消選圖或上傳中不得誤送空值。

## 共用表單與編輯（C5）

抽出三節共用表單與不可變資料模型，保留兩套提交策略：發券仍用 `POST /add-coupon-preview` → `POST /coupon-previewed`（兩個請求用同一份已確認的 snapshot）；編輯以授權詳情預填，生成真正有差異的 PATCH，對話框逐項列舊值 → 新值（本地 diff，**不是用 POST 發券預覽去模擬 PATCH**）。檢查期間發起人或店舖變更時丟棄舊結果；取消、零差異不發請求。成功用後端回應更新詳情，不本地猜最終狀態。連線中斷、回應不明時先刷新核對再處理，避免盲目重送。

| 分類 | 合約及處理 |
| --- | --- |
| 已支援的 PATCH | `coupon_name, name_en, category_id, cash_amount, percentage, gift, discount_price, regular_price, sale_price, refund_policy, min_spend, per_user_limit, image, valid_from, valid_to, total, additional_terms, is_limited_time`。僅發改動且後端可表示的欄位。 |
| 固定／獨立欄位 | `shop_id`、`coupon_type` 為唯讀；換店／類型走「建立新券」。`branch_ids` 維持獨立 `POST /assign-branches` 已有權限和訂單校驗，不在表單內偷偷做第二次寫入。 |
| 明確清空 | PATCH 迴圈略過 `null`，不能把「清空分類／可空數字」送成 `null` 又回報成功；目前不顯示不可表達的清空操作。文字欄 `''` 與未傳不同，需依該欄允許的清除語義測試。不可把 `sale_price: 0` 當通用空值。 |
| 狀態 | active/paused/scheduled → pending_review；pending_review 留在原狀態；rejected → draft；draft 留原狀態。expired/sold_out 不可編輯。以後端獲准的管理資料為準。 |
| 已有訂單 | 後端鎖券後檢查 **任何非 cancelled 的該券訂單**，包含 pending/failed/timeout、已付或已退，直接拒絕整個 PATCH 409（不只價款欄位）；保留原條款。見既有競態測試。不要拿 `downloads`、`used_count` 或公開 `is_claimed` 猜是否能存。 |
| 讀取／409 | 商家列表有券欄位，但沒有訂單存在標記；公開券詳情的 `is_claimed` 是該訪客是否持券。與 C6 共用有商家授權的詳情來源；如需事前提示，加最小唯讀 `has_non_cancelled_orders`，僅店主、授權管理者或管理員可見，不能加到共用公開序列化。未知不裝作「無訂單」，PATCH 如回 409 顯示服務端原話與「建立新券」。新券只預填可再使用的表單欄位、不複製 ID／狀態／銷量；重新確認庫存、生效、到期與分店。 |
| 交易欄位 | 第一版可先製作唯讀值及 fixture 對照。實際送出價格、券面金額／折扣、付款後退款政策或改變已購資格的欄位前，須另檢閱這份方案與 C13 稽核策略；不放寬 409、不更動歷史訂單。 |

券編輯存量仍按後端原算式保留已領數、下限零；不把「已領但無訂單」等同於「有訂單」。已領券的核銷與有效期影響待 C3 契約核對後再寫頁面承諾。套餐採版本化模板，僅部分未快照訂單會擋住編輯，不能把套餐 PATCH 行為直接套在券上（C17/P3 不在本 PR 改動）。

## 下一批驗收及發布順序

1. 先合 #56 和 L1–L17，再與 C1–C3/C6 合併唯讀、管理入口及授權詳情；同一券僅一個編輯工單。後端如新增欄位或端點，同 PR 補 `docs/API.md`、403/404、鎖與 409 測試。
2. 先做共用表單及新欄位的 fixture 證據：390×844 繁中／英文三節，類型切換、空值、上傳失敗／成功，香港跨日、重複提交、取消、無差異與伺服器拒絕清空。重測既有發券請求身體、分店及 UTC 對照，不做支付測試以外的生產寫入。
3. 編輯流再補 390×844 的 active → pending_review、逐項審查、409 → 預填新券、切換帳戶及慢請求。以測試商店／資料庫驗付費券表單提交一次（只送審，不購買、不扣款）；已購券保持原金額／條款，套餐歷史／直接付費券不要互相代替。完成真實價格寫入前由維護者審閱方案及審核回滾條件。

來源：[後端輸入 schemas](../apps/api/app/schemas.py#L180) · [建立、編輯與上架切換](../apps/api/app/routers/vendor.py#L257) · [分店獨立更新](../apps/api/app/routers/vendor.py#L662) · [公開刊登圖](../apps/api/app/routers/uploads.py#L180) · [公開詳情讀取](../apps/api/app/routers/home.py#L221) · [套餐版本規則](../apps/api/app/routers/packages.py#L763)

此次前置核對：在隔離 SQLite、本地開發設定下，既有後端發券／編輯／價款鎖定／競態／券圖等定向回歸 14 項通過；Flutter 本地候選的發券與分店定向回歸 10 項通過。尚無 C4–C5 新 UI，不提供虛構的前後截圖；未用正式資料、支付或部署。
