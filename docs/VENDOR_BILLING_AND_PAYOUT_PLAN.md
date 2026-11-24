# 企業版訂閱與正式提款：實作前方案

本 PR 只記錄 A6、C1、C2 及 V3 收款操作的決定。未加入扣款、提款、價格、付款設定或生產開關。

## 訂閱：先做商戶網頁版，沿用現價

- 香港商戶網頁先接 Stripe Billing 的託管結帳和帳單入口。只有擁有該已驗證商戶的店主或獲授權帳單管理員可操作；每個簽約商戶主體只開一份。初期 App 只顯示方案與狀態，不放站外付款入口。正式啟用前核對 [App Store](https://developer.apple.com/app-store/review/guidelines/) 和 [Google Play](https://support.google.com/googleplay/android-developer/answer/9858738) 的數碼服務規則。
- 延用已发布方案及幣別，平台承擔標準支付和帳單費用。沒有自動扣費試用，不以人工標記作付款憑證，不從商戶待結算款或返現抵扣。
- 候選 API：`GET /merchant/subscription` 查詢狀態；`POST /merchant/subscription/checkout` 與 `POST /merchant/subscription/portal` 按服務端的商戶與方案設定建立短效會話。實作時同步更新 `docs/API.md`，在目前仍未提供接口時不加入現行 API 清單。
- 付款回跳只顯示待確認。按驗簽事件與服務端查回的商戶、客戶、方案價格、帳單金額／幣別、週期核對；僅有效已付帳單開通或續期。新一期未付款不延長權益；期末取消保留已付週期。事件、帳單與會話設冪等，測試重複、亂序、重試及跨店拒絕。
- 參考：[訂閱事件](https://docs.stripe.com/billing/subscriptions/webhooks)、[帳單自助管理](https://docs.stripe.com/customer-management)、[香港 Billing 定價](https://stripe.com/en-hk/billing/pricing)。

## 正式提款：現階段不啟用

- 從已付款且可核驗的港幣券單出發，按核銷時鎖定的淨額及合約佣金，將轉帳與原收款或已可用餘額的證據關聯，再由銀行資料已驗證的本店收款帳戶提款。不從訂閱款或顧客返現墊付，不以結算單標記代替實際收款。試點由平台承擔標準提款費，合約佣金及商戶淨額不加扣。費率以啟用時合約為準。
- 現有產品只支援測試或模擬出款；不能靠換金鑰啟用。正式和測試資金來源、交易物件及對帳全程隔離；先完成商戶合約與收款驗證、每日對帳、付款與核銷一致性、退款沖回／負數餘額處置。超時或結果未知不釋放款項，也不重新出款；用原冪等編號確認結果。
- [Stripe 手動出款期限](https://docs.stripe.com/connect/manual-payouts)中的香港 90 天是最晚持有期限，非凍結期或金額限制。現有欄位僅展示。逐筆留存入帳證據、可用時間與到期日；每日監控最舊款項，60／75／85／88 天逐級處理，試點目標最晚第 85 天提交以留緩衝；受限制、失敗或逾期風險交予指定財務與支付服務方。停試點不影響已有餘額或在途交易的處理。
- 參考：[資金流與轉帳](https://docs.stripe.com/connect/separate-charges-and-transfers)、[香港 Connect 費用](https://stripe.com/en-hk/connect/pricing)。

## 交付次序

1. [V3 帳戶](https://github.com/QIU-Guanzong/Coupon-Go/pull/47)先做好讀取及角色顯示。普通同店員工若需看進度，另回傳最小的未開始／辦理中／已開通狀態，沒有帳戶編號、餘額、銀行資料，不放寬原餘額與流水接口。
2. 財務方案落到具體 API 與操作責任後，先用本地單元測試及隔離 PostgreSQL 驗收上述權限、重放、並發、超時、退款、期限及對帳；需要時再做一輪支付沙箱驗證。各階段都不碰真實用戶資金。正式啟用另有獨立上線驗收。
