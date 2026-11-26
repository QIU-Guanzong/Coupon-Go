# Q仔接口：M9-3 / M9-4 / M9-5
`POST /api/v3/qbot/chat` 支持訪客；可選 Bearer token，只有經認證帳戶才有服務內積分欄位。回應保留原 answer / points_balance，新增 offers；無法唯一匹配就不展示卡片。以下為測試示例：

```json
{"message":"有花雕雞套餐嗎", "language":"zh-Hant", "history":[]}
```

```json
{"answer":"可以看看花雕雞套餐。","points_balance":null,"offers":[{"type":"package","id":51,"name":"花雕雞套餐","shop":"好味餐廳"}]}
```

type 為 coupon、package 或 bargain。id 取自本次送模型的公開樣本，不採用模型寫出的 ID。offers 最多 8 筆；文字符合樣本的完整名稱且無歧義才返回。售罄、過期、未批准店舖不入候選。套餐價和砍價底價只作模型的公共參考，不保證成交或付款狀態。

## 套餐比較

訪客可在現有 Q仔對話輸入「比較雙人套餐，總預算HK$200」或 `Compare packages for 2, total budget HK$200`。人數須為 1 至 99；省略總預算可按人數比較。系統從已批准店舖的有效、有庫存套餐，篩選人數完全相符且整份售價不高於預算的選項，按總價、商品 ID 排序，最多回傳三個詳情入口。人均金額按套餐建議人數計算及四捨五入，預約要求未列明時會標示。

這種明確格式由目錄直接作答，沿用 Q仔的啟用設定與額度。其他自由提問仍使用 DeepSeek，有限的公開樣本會附上人數、人均價、預約要求與退款條款。預覽版顯示示例套餐。回應欄位沿用現有格式。

## Uvicorn 與額度
- Docker 以 `python -m app.serve` 使用 Uvicorn 的可信代理解析；無代理預設忽略轉發頭。代理部署設 `FORWARDED_ALLOW_IPS` 為**實際直接反代的 IP/CIDR**（逗號分隔），不能配置 `*` 或全網段。反代應覆寫不可信的入站轉發頭或附上其驗證的對端。非 Docker 等價：用 Uvicorn `--proxy-headers --forwarded-allow-ips <可信 IP/CIDR>`；沒有反代用 `--no-proxy-headers`。不要公開允許直接連到應只經代理的 API 端口。
- 帳戶按 user_id，訪客按 Uvicorn 驗證後的連線地址。Redis 原子校驗及 INCR，60 秒過期，每分鐘每身份 8 次／全局 60 次；連線異常時每進程有界降級並記錄通用告警，絕不打印聊天、地址或密鑰。多 worker 降級時全局額度只在各進程內生效。
- 隔離公開目錄的 Q仔網關保留其既有更嚴格的預覽額度，不接受普通用戶登入會話。
