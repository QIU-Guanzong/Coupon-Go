# P1-D 商户入驻接口清单（Stage 1）

当前实现：占位合同仅供测试，不是正式协议。手填 Stripe 状态仅供开发
环境模拟，接口会标记 `stripe_status_source=simulated`；正式环境关闭
此模拟接口并暂不批准入驻，须待 #16 接入真实账户核验及正式合同后启用。

基线：main @ `c1f3bef`（2026-11-20，PR #6 合入后；最初基于 `7e9c4f9` 开发，
已 rebase 到 `c1f3bef`，无冲突）。分支：`stage1/p1-d-merchant-onboarding`。
迁移：`0022_p1d_merchant_onboarding`（`down_revision = 0021_account_deletion`，
即 M9-7；**合入顺序依赖**：本 PR 必须在 M9-2（0020）与 M9-7（0021）合入 main
之后才能合入，否则 alembic 链断裂）。

规格来源：`docs/FEATURE_GAP_20261120.md` §3 已定决策（§3.3 资金与 §2 文件提交行）
与本任务书；文档已定的口径直接照做，不另定规则。

> 用户端「商户加盟」入口与商户注册**共用同一条流程**：同一个申请接口
> `POST /api/v3/admin/merchant-applications`，不另写一套。加盟入口只是
> App 内的一个入口按钮，走到同一个申请、上传、签署、审核流程。

前缀均为 `/api/v3`。除下载端点外全部要 `Authorization: Bearer <token>`。

## 1. 文件上传（私有存储，签名下载）

### POST `/merchant-files` — 上传入驻文件（201）

`multipart/form-data`，字段：`kind`
（`br` | `ci` | `director_id` | `shop_photo`）、`file`（单个文件）、
`application_id`（可选，int）。

- 上传时机有两种，取其一：① 先提交申请、再传文件（传 `application_id`，
  文件立即挂到该申请下）；② 先传文件、再提交申请（不传 `application_id`，
  文件暂为「未挂接」，提交时在申请 body 里用 `br_file_id` 等字段认领，
  未挂接文件只能被上传者本人认领，且只能认领一次）。
- 权限：申请的申请人本人，或 admin（代传）。他人 403；`application_id`
  指向不存在的申请 404。
- 限类型：`pdf` / `jpg` / `jpeg` / `png`（以后缀 + content-type
  + 文件头魔数三重校验：PDF 须以 `%PDF-` 开头，PNG 须为
  `89 50 4E 47 0D 0A 1A 0A`，JPEG 须以 `FF D8 FF` 开头）。
  内容与后缀不符 → 400。
- 限大小：单个文件 ≤ `UPLOAD_MAX_BYTES`（默认 5 MB），超限 400。
- **未挂接文件限额**（「未挂接」= `application_id IS NULL`，即先传
  文件、后提交申请的暂存文件；已挂接到申请的文件不受此限）：
  每用户最多 `MERCHANT_UNATTACHED_MAX_FILES` 个（默认 20），总量最多
  `MERCHANT_UNATTACHED_MAX_BYTES` 字节（默认 52428800 = 50 MB）。
  保存前检查，任一超限 → 429：

```json
{"detail": "未掛接文件已達上限（20 個），請先提交申請認領或刪除舊文件"}
```

- **上传限流**：同一用户每分钟最多 `MERCHANT_UPLOAD_RATE_LIMIT_PER_MINUTE`
  次 `POST /merchant-files`（默认 10），超限 → 429
  `{"detail": "上傳過於頻繁，請稍後再試"}`（带 `Retry-After: 60`）。
  实现照 `app/qbot_budget.py` 的模式：Redis INCR + 过期（原子），
  Redis 不可用时进程内计数 fallback。过期未挂接文件由清理脚本删除（见 §4）。
- 图片上传时去 EXIF（复用 `app/routers/uploads.py::strip_exif` 的逻辑）。
- 文件名由服务端生成 UUID，杜绝路径穿越。
- **不返回公开 URL**。返回文件元数据：

```json
{
  "message": "文件已上传",
  "file": {
    "id": 12,
    "application_id": 3,
    "kind": "br",
    "original_filename": "BR.pdf",
    "content_type": "application/pdf",
    "size_bytes": 184320,
    "sha256": "9f86d081…",
    "created_at": "2026-11-20T14:00:00"
  }
}
```

### POST `/merchant-files/{file_id}/download-link` — 取短期签名下载链接（200）

- 权限：文件所属申请的申请人本人，或 admin。他人 403；文件不存在 404。
- 返回：

```json
{
  "download_url": "/api/v3/merchant-files/12/download?exp=1763634000&token=hmac-hex…",
  "expires_at": "2026-11-20T14:15:00"
}
```

- 链接有效期 15 分钟（`MERCHANT_DOWNLOAD_LINK_TTL_SECONDS`，默认 900）。
  token = HMAC-SHA256（`SECRET_KEY`）over `"<file_id>:<exp>"`，不绑定用户——
  是短期能力凭证，过期即废；不要把它当长期分享链接。

### GET `/merchant-files/{file_id}/download?exp=…&token=…` — 下载文件（200）

- 不需要 Authorization header（token 即凭证）。过期 / 篡改 / 缺参数 → 403。
- 成功返回文件字节流，`Content-Type` 由服务端按磁盘文件的魔数判定
  （PDF / PNG / JPEG；验不出 → `application/octet-stream`），
  不用上传时客户端声明的值，
  `Content-Disposition: attachment; filename="<原文件名>"`。
- **日志不记录下载链接**：应用与 uvicorn access 日志均已配置为不记录
  完整查询串（见 §4），`exp` / `token` 不会进日志。

## 2. 入驻申请（香港口径）

### POST `/admin/merchant-applications` — 提交申请（201，既有端点，P1-D 扩展）

Body（JSON）：

```json
{
  "shop_name": "茶餐廳 A",
  "contact_name": "陳大文",
  "phone": "91234567",
  "address": "旺角彌敦道 123 號",
  "br_number": "12345678",
  "br_file_id": 12,
  "ci_file_id": 13,
  "director_id_file_id": 14,
  "shop_photo_file_ids": [15, 16],
  "business_license": "",
  "id_card": "",
  "industry_license": ""
}
```

- `shop_name` 必填；`br_number` 如填，须为 8 位数字（香港商業登記號碼，
  去除空格/連字符後校驗）；文件 id 须存在、属于本次申请的申请人、
  且 kind 与字段对应（`br_file_id` 须是 kind=`br` 的文件），否则 400。
- `shop_photo_file_ids` 最多 10 个，须为 kind=`shop_photo` 的文件。
- **旧内地口径字段保留**：`business_license`（營業執照）、`id_card`
  （法人身份證）、`industry_license` 仍可提交、仍存库、仍在详情里返回，
  但不再作为审批依据；新申请请用香港口径字段。旧数据不丢弃、不迁移改写。
- 同一用户已有 `pending` 申请时 400（沿用 v1 规则）。
- 201 返回 `{message, application}`，application 对象见下。

### GET `/merchant-applications/mine` — 我的申请列表（200，新增）

登录用户查看自己的全部申请（按创建时间倒序，分页 `?page=`，每页 20）。
他人申请不可见。

### GET `/merchant-applications/{application_id}` — 我的申请详情（200，新增）

申请人本人可读；他人 403；不存在 404。admin 请用
`GET /admin/merchant-applications/{id}`。

### GET `/admin/merchant-applications` / `/{id}`（既有，admin，返回体扩展）

申请对象新增字段（旧字段全部保留）：

```json
{
  "id": 3, "user_id": 7, "applicant_email": "a@example.com",
  "shop_name": "茶餐廳 A", "contact_name": "陳大文",
  "phone": "91234567", "address": "旺角彌敦道 123 號",
  "br_number": "12345678",
  "documents": {
    "br": {"id": 12, "original_filename": "BR.pdf", "size_bytes": 184320, "sha256": "9f86…", "created_at": "…"},
    "ci": {"id": 13, "original_filename": "CI.pdf", "size_bytes": 99210, "sha256": "ab12…", "created_at": "…"},
    "director_id": {"id": 14, "original_filename": "ID.pdf", "size_bytes": 120400, "sha256": "cd34…", "created_at": "…"}
  },
  "shop_photo_files": [
    {"id": 15, "original_filename": "shop1.jpg", "size_bytes": 204800, "sha256": "ef56…", "created_at": "…"}
  ],
  "business_license": "", "id_card": "", "shop_photos": [], "industry_license": "",
  "stripe_account_id": "acct_…",
  "stripe_charges_enabled": true, "stripe_payouts_enabled": true,
  "contract": {
    "version": "1", "signed_at": "2026-11-20T14:05:00",
    "signed_ip": "203.0.113.10", "pdf_file_id": 21
  },
  "status": "pending", "reject_reason": "",
  "reviewed_by": null, "reviewed_at": null, "created_at": "…"
}
```

说明：

- `documents` / `shop_photo_files` 只含元数据，不含可直接打开的 URL；
  要下载须另调 `POST /merchant-files/{id}/download-link` 取签名链接。
- `business_license` / `id_card` / `shop_photos` / `industry_license`
  为旧口径遗留字段，原样返回，仅供追溯。
- `contract` 为 null 表示尚未签署当前版本合约。

### POST `/admin/merchant-applications/{id}/review` — 审核（既有，P1-D 加批准门禁）

Body `{approve: bool, reject_reason}` 不变。**批准**时新增两道门禁，
任一不满足即 400（拒绝不受门禁影响，仍只需 `reject_reason`）：

1. **已签当前版本合约**：申请须有一条签署记录，且签署的版本 =
   当前 `is_current` 的合约版本。未签 → 400 `尚未簽署當前版本合約`；
   只签了旧版 → 400 `合約版本已更新，請簽署當前版本`。
2. **测试环境模拟开户**：`stripe_account_id` 非空，且
   `stripe_charges_enabled` 与 `stripe_payouts_enabled` 均为 true。
   否则 400 `尚未完成 Stripe 開戶`。

通过时申请人 `role` 由 `customer` 升为 `vendor`（沿用 v1），决定写入
`admin_audit_logs`（沿用 v1）。

### POST `/admin/merchant-applications/{id}/stripe-onboarding` — 登记 Stripe 开户状态（200，新增，admin）

Body：

```json
{
  "stripe_account_id": "acct_1AbC…",
  "charges_enabled": true,
  "payouts_enabled": true
}
```

- 此端点仅在开发环境模拟，不查询 Stripe、不证明真实开户；正式环境
  返回 409。已绑定的申请不能换账号，同一模拟账号不能绑定其他申请人。
- 银行帐户由 Stripe 收集，**我们不存银行帐号**（FEATURE_GAP §3.3）。

## 3. 电子合约

### GET `/merchant-contracts/current` — 当前版本合约（200）

登录即可。返回 `{id, version, title, body_text, published_at}`。
尚无当前版本时 404（种子数据与迁移会预置 v1 占位版，见下）。

### GET `/merchant-contracts` — 合约版本列表（200，admin）

全部版本倒序：`{id, version, title, is_current, published_at}`（不含正文）。

### POST `/merchant-contracts` — 发布新版本（201，admin）

Body：`{version*, title*, body_text*}`。`version` 全局唯一，重复 409。
发布后该版本成为唯一 `is_current`，旧版本自动失去 current 标记，
但旧签署记录保留（可追溯）。写入 `admin_audit_logs`
（`merchant_contract.publish`）。

### POST `/merchant-applications/{application_id}/sign-contract` — 签署当前版合约（201）

Body：`{}`（签当前版）或 `{"contract_version_id": 2}`（明确指定版本；
只能签当前版，指定旧版 → 400）。

- 权限：申请人本人。申请须为 `pending`，否则 400。
- 记录：`user_id`、合约版本、签署时间（服务器时间，UTC）、
  客户端 IP（`request.client.host`）。
- **客户端 IP 的来源**：路由只读 `request.client.host`，不另写
  `X-Forwarded-For` 解析。部署时必须把反向代理的 IP/CIDR 配进
  `FORWARDED_ALLOW_IPS`（逗号分隔，如 `203.0.113.5, 198.51.100.0/24`；
  `0.0.0.0/0` 全捕捉会被启动时拒绝，空值则关闭代理头处理、
  fail-closed）；配好后 `request.client.host` 就是真实客户端 IP，
  否则记录的是代理 IP。启动入口见 `app/serve.py`
  （`proxy_headers` + `forwarded_allow_ips` 取自该变量）。
- 签署成功后生成 **PDF 存档**（reportlab + 文泉驿正黑渲染中文），
  存为 `kind=contract_pdf` 的私有文件，并与签署记录关联。
- **字体缺失 → 503**：运行环境没有任何可用 CJK 字体时，签署接口返回
  503，不生成 PDF、不写任何签署记录（渲染在所有 DB 写入之前进行）：

```json
{"detail": "沒有可用的中文字體，無法生成合約 PDF 存檔：請安裝 fonts-wqy-zenhei（見 docs/P1D_API.md §4）"}
```

  之前「降级为 Helvetica 只渲染 ASCII 字段」的做法已删除——缺正文的
  存档不是对签署内容的存档。Dockerfile 已装 `fonts-wqy-zenhei`；
  可用 `MERCHANT_CONTRACT_FONT` 环境变量指定其他字体文件。
- 同一申请重复签同一版本 → 200 返回已有签署记录（幂等，不重复生成 PDF）。
- 返回：

```json
{
  "message": "合約已簽署",
  "signature": {
    "id": 5, "application_id": 3, "user_id": 7,
    "contract_version": "1", "signed_at": "2026-11-20T14:05:00",
    "ip_address": "203.0.113.10", "pdf_file_id": 21
  }
}
```

### GET `/merchant-applications/{application_id}/contract-pdf` — 取已签合约 PDF 下载链接（200）

- 权限：申请人本人或 admin；未签署 → 404。
- 返回与 §1 相同的 `{download_url, expires_at}`（指向
  `GET /merchant-files/{pdf_file_id}/download`，15 分钟有效）。

## 4. 存储方式与上线前要换的点（诚实说明）

当前实现（与既有 review-images 上传占位同一思路，见
`app/routers/uploads.py` 顶部注释）：

- **商户文件存本地磁盘**：`<MERCHANT_FILE_DIR>/`（默认
  `<repo>/apps/api/merchant-files-private/`，可用 `MERCHANT_FILE_DIR`
  环境变量改），按 `<uuid>.<ext>` 存放。**不在 `/uploads` 静态挂载下**，
  公网无法直接访问，只能走签名下载端点。
  存储收进小接口 `app/merchant_file_storage.py`：
  `save(stored_name, data)` / `open_bytes(stored_name)` /
  `delete(stored_name)`，以 `MERCHANT_FILE_DIR` 为根；上传保存、签署时
  PDF 保存、下载读取、清理脚本删除都走这个接口，路由本身不变。
  **本地磁盘只供体验环境；正式上线前换成私有云存储，届时只换这个
  接口的实现，不动路由。**
- PDF 由 reportlab 生成（新增依赖 `reportlab`，见 `requirements.txt`；
  测试用 `pypdf` 抽文本断言中文条款，也在 `requirements.txt`），
  字体为文泉驿正黑（TrueType 轮廓，`fonts-wqy-zenhei`，
  `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`，子字体 0）；
  系统自带的 Noto Sans CJK TTC 是 CFF 轮廓，reportlab 不支持，会自动跳过。
  Dockerfile 已装 `fonts-wqy-zenhei`（`apt-get install -y
  --no-install-recommends fonts-wqy-zenhei`）；若运行环境没有任何可用
  CJK 字体，签署接口返回 503（见 §3），不再降级渲染。
  可用 `MERCHANT_CONTRACT_FONT` 环境变量指定其他字体文件。
- **下载链接不进日志**：uvicorn 默认 access 日志会把带查询串的完整
  请求行（`?exp=…&token=…`）记下来。`app/serve.py` 给 `uvicorn.access`
  logger 加了剥离查询串的 filter（保留方法 / 路径 / 状态码），
  应用自身也没有记录请求 URL 的地方（核实过）。前面的反向代理也须
  同样配置（不记录完整查询串），否则 token 会在代理日志里泄漏。
- **清理过期未挂接文件**：`python -m app.cleanup_merchant_files`
  （从 `apps/api/` 运行），删除 `application_id` 为 NULL 且
  `created_at` 超过 `MERCHANT_UNATTACHED_MAX_AGE_DAYS` 天（默认 7）
  的文件，同时删磁盘文件和库记录；幂等，打印清理数量汇总。
  建议每天跑一次，例如 cron：`0 3 * * * cd /path/to/apps/api &&
  python -m app.cleanup_merchant_files`。
- **反向代理配置要求**：部署时必须把反向代理的 IP/CIDR 配进
  `FORWARDED_ALLOW_IPS`（见 §3 的 IP 说明）；不配则代理头不被信任、
  签约 IP 记的是代理 IP。
- 上线前必须换的点（不做完不能上生产）：
  1. 对象存储：把 `merchant-files-private/` 换成 S3 / GCS 私有桶
     （只换存储层：`app/merchant_file_storage.py` 的实现，其余不动）；
     同时把 `reportlab` 的字体文件随部署包带上或改由对象存储/配置指定。
  2. 签名链接改为对象存储的预签名 URL 或保留现有 HMAC 方案（二选一，
     不要两套并存）。
  3. 文件病毒扫描、图片缩略图（review-images 已有同样待办）。
  4. 合约正文 v1 为占位文本（迁移与种子数据预置，明确标注
     「待法务确认」）；法务定稿后由 admin 发新版本，商户须重签当前版
     才能批准——旧版签署不顶用（批准门禁第 1 条）。
  5. ~~客户端 IP 目前取直连 `request.client.host`；上生产若在反向代理后，
     须按部署的受信代理配置取 `X-Forwarded-For`，否则记的是代理 IP。~~
     已改为 `FORWARDED_ALLOW_IPS` 受信代理配置（见 §3）：路由仍读
     `request.client.host`，配好受信代理后它就是真实客户端 IP。

## 5. 与 P1-B（Stripe Connect Express 开户）的衔接

- P1-B 负责：商户在 Stripe 的实名与银行帐户收集、开户流程、开户状态
  回调。本 PR 不碰 Stripe API、不改 FIX-1 支付验签。
- 衔接字段（申请记录上，P1-B 直接写同一组列）：`stripe_account_id`
  （Stripe Connect 帐户 id，如 `acct_…`）、`stripe_charges_enabled`、
  `stripe_payouts_enabled`（均须为 true 才算「已完成开户」）。
- P1-B 未合入前只允许开发环境模拟；正式环境不允许凭手填状态批准。
- P1-B 必须核验并复用申请中的同一 Stripe 账户，不能重复开户；通过
  真实状态校验与正式合同门禁后，才能替换目前的正式环境阻断。

## 6. 旧字段数据的迁移处理（0022）

- `business_license` / `id_card` / `industry_license` / `shop_photos`
  四列**不动**：不改名、不删除、不改写值；旧申请的旧数据原样保留，
  详情接口原样返回。
- 新增列全部 nullable / 有默认值，旧行无需回填；`shop_photo_file_ids`
  默认 `"[]"`；`stripe_*` 默认 false/空；合约相关列默认空。
- 新表：`merchant_files`、`merchant_contract_versions`、
  `merchant_contract_signatures`。
- 迁移预置合约 v1 占位版（与 M9-2 预置 legal v1 占位行同一做法），
  这样旧库升级后签署流程立即可用；正文明确标注待法务确认。
- `downgrade()`：删新表与新增列（空表才删；已有签署记录时拒绝，
  须先做数据恢复协调——签署记录是法律存档，不可静默丢弃）。

## 7. Dot 对接（Flutter）

1. 加盟入口（用户端「商户加盟」）与商户注册页共用：
   `POST /admin/merchant-applications` 提交申请。
2. 文件先调 `POST /merchant-files`（multipart，`kind` + `file`；
   已有申请可附 `application_id`，否则先不传、提交时认领）拿 `file.id`，
   再把 id 填进申请 body 的
   `br_file_id` / `ci_file_id` / `director_id_file_id` /
   `shop_photo_file_ids`。**不要**把文件直传进申请 body，也不要拼
   `/uploads/…` 公开链接。
3. 合约页先调 `GET /merchant-contracts/current` 取正文展示，
   用户勾选后调 `POST /merchant-applications/{id}/sign-contract`。
4. 申请进度页调 `GET /merchant-applications/mine`；
   已签合约 PDF 调 `GET /merchant-applications/{id}/contract-pdf`
   取签名链接后下载。
5. 批准前置条件由后端强制：未签当前版合约 / 未完成 Stripe 开户时，
   后台点「通过」会 400 并返回中文原因，前端直接展示该文案即可。
