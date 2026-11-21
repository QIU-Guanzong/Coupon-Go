# 公開 App 顯示規則（M9-6）

`GET /api/v3/app-config` 可由訪客呼叫，不需要 token 或參數。

HTTP 200，現行環境預設範例：
```json
{"points_claim":10,"points_redeem":20,"points_review":15,"points_per_hkd":1,"points_expire_months":12,"qr_ttl_seconds":900,"cashback_unlock_days":90}
```

值由後端配置取出：POINTS_CLAIM、POINTS_REDEEM、POINTS_REVIEW、POINTS_PER_HKD、POINTS_EXPIRE_MONTHS、QR_TTL_SECONDS、CASHBACK_DINEIN_UNLOCK_DAYS。積分／天／秒均為整數。cashback_unlock_days 是歷史返現説明，不是功能開關。`points_per_hkd` 是付費券核銷得分率（P1-C 已實作：實付 HK$1 = 1 分）；`points_expire_months` 是每筆積分有效月數。此接口不包含其他伺服器／支付配置、使用者或密鑰資料。
