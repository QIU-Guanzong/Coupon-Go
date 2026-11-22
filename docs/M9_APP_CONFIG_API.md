# 公開 App 顯示規則（M9-6）

`GET /api/v3/app-config` 可由訪客呼叫，不需要 token 或參數。

HTTP 200，現行環境預設範例：
```json
{"points_claim":10,"points_redeem":20,"points_review":15,"points_per_hkd":1,"points_expire_months":12,"qr_ttl_seconds":900,"cashback_unlock_days":90,"cashback_enabled":false,"points_exchange_cost":9,"open_login":false}
```

值由後端配置取出：POINTS_CLAIM、POINTS_REDEEM、POINTS_REVIEW、POINTS_PER_HKD、POINTS_EXPIRE_MONTHS、QR_TTL_SECONDS、CASHBACK_DINEIN_UNLOCK_DAYS。`points_per_hkd` 為付費券核銷得分率，`points_expire_months` 為積分有效月數；`cashback_enabled` 反映返現總開關，`points_exchange_cost` 為積分兌換成本。`open_login` 僅作展示，暫無行為。此接口不包含其他伺服器／支付配置、使用者或密鑰資料。
