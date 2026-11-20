# Coupon GO 公開目錄預覽

此包用隔離的示例資料提供匿名目錄。公開介面只提供商戶、優惠、套餐、分類、附近及公開評價的讀取、只讀搜尋建議，以及啟用後有預算限制的 Q仔。註冊、登入、核銷、訂單、支付、退款、管理與其餘 API 由公開網關拒絕。前端需要明示示例資料及瀏覽範圍。

部署環境為 Python 3.10，依 requirements.lock 安裝到專屬虛擬環境。先以 APP_ENV=dev、SEED_ON_STARTUP=0、SCHEDULER_ENABLED=0 和一個不存在的 SQLite 路徑執行 seed_catalog.py，PREVIEW_SEED_PATH 與 DATABASE_URL 指向同一新檔。種子只讀取程式內示例資料，完成後隨機替換示例帳號密碼。不要將任何現有客戶資料庫作為種子。

運行 public_api:app 時設 APP_ENV=production、SEED_ON_STARTUP=0、SCHEDULER_ENABLED=0，由部署程式以安全隨機數生成 SECRET_KEY 和 STRIPE_WEBHOOK_SECRET，寫入僅系統管理員可讀的環境檔。DATABASE_URL 使用 SQLite 的 mode=ro URI，並提供可寫的獨立 UPLOAD_DIR。入口綁定本機回環，由反向代理把 /coupongo/api/ 轉發到 /api/，其餘 /coupongo/ 請求提供 Flutter 靜態檔，缺失資源返回 404。網關同時在每個資料庫連線啟用 query_only；砍價目錄使用無寫入的讀取路徑。

Flutter 構建使用 --base-href=/coupongo/、--dart-define=API_BASE_URL=、--dart-define=PUBLIC_DEMO=true 及對應 PUBLIC_WEB_URL。不要將此預覽作為真實交易環境。

在已安裝 requirements.lock 的 Python 環境執行 `python infra/public-preview/test_public_api.py`。測試會在系統臨時目錄生成並銷毀獨立示例資料庫，檢查匿名讀取、搜尋、拒絕其他接口、過期砍價活動無持久寫入及 SQLite 只讀限制。

Q仔默認關閉。啟用時由系統管理員安全配置 `DEEPSEEK_API_KEY`、`PUBLIC_QBOT_ENABLED=1` 及 `PUBLIC_QBOT_QUOTA_DB`（專用、持久、服務用戶可寫目錄內的絕對路徑）。密鑰只放入 root 限權的服務環境文件，不能放進代碼、打包檔、前端或日誌。額度庫不包含聊天內容或 IP，必須保留以維持跨重啟限制；無法寫入時會拒絕請求。網關按 UTC 每分鐘最多 20 次、每日最多 200 次，最多同時處理 3 次。部署必須固定一個 Uvicorn worker，並在可信 Nginx 對 `/coupongo/api/v3/qbot/chat` 使用 `$binary_remote_addr` 配置逐 IP `limit_req`；不得從客戶端自行提供的轉發頭判定身份。通用 API 日誌不能記錄聊天請求體或密鑰。禁用 Q仔時設 `PUBLIC_QBOT_ENABLED=0`，毋須改動目錄服務。

伺服器部署程式可用以下方式一次性建立環境檔，無須提供密鑰。檔案已存在時此命令會停止，保留現有值。

```sh
sudo python3 - <<'PYCODE'
import os
import secrets

path = "/etc/coupongo-preview.env"
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as out:
    out.write("SECRET_KEY=" + secrets.token_urlsafe(48) + "\n")
    out.write("STRIPE_WEBHOOK_SECRET=" + secrets.token_urlsafe(48) + "\n")
PYCODE
```
