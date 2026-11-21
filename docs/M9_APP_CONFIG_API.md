# 公開 App 顯示規則（M9-6）

`GET /api/v3/app-config` 可由訪客呼叫，不需要 token 或參數。

HTTP 200，現行環境預設範例：
```json
{"points_claim":10,"points_redeem":20,"points_review":15,"qr_ttl_seconds":900,"cashback_unlock_days":90,"cashback_enabled":false,"points_exchange_cost":9,"open_login":false}
```

值由後端配置取出：POINTS_CLAIM、POINTS_REDEEM、POINTS_REVIEW、QR_TTL_SECONDS、CASHBACK_DINEIN_UNLOCK_DAYS。積分／天／秒均為整數。cashback_unlock_days 是歷史返現説明，不是功能開關。未實作下一階段消費積分政策前，勿套用於付費券。M10 新增：`cashback_enabled` 反映 `CASHBACK_ENABLED` 返現總開關（預設 false）；`points_exchange_cost` 為 9 積分兌換券的單一事實來源；`open_login` 預設 false，僅作展示、暫無行為（PROMPTS_OPEN_LOGIN_20261120.md 未找到）。此接口不包含其他伺服器／支付配置、使用者或密鑰資料。
