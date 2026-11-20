# P1-D 商户入驻接口清单（Stage 1）

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
- 限类型：`pdf` / `jpg` / `jpeg` / `png`（以后缀 + content-type 双重校验）。
  其他类型 400。
- 限大小：单个文件 ≤ `UPLOAD_MAX_BYTES`（默认 5 MB），超限 400。
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
- 成功返回文件字节流，`Content-Type` 为上传时的类型，
  `Content-Disposition: attachment; filename="<原文件名>"`。

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
2. **已完成 Stripe 开户**：`stripe_account_id` 非空，且
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

- 作用：把 P1-B 的开户结果写进申请记录，供批准门禁读取；同时写入
  `admin_audit_logs`（`merchant_application.stripe_onboarding`）。
- **这是过渡桥接**：P1-B 未合入前，由 admin 在 Stripe Dashboard 核实后
  手动登记；P1-B 合入后，P1-B 的开户完成回调应直接写同一组字段
  （`stripe_account_id` / `stripe_charges_enabled` /
  `stripe_payouts_enabled`），本端点即退为后台补录口。
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
- 签署成功后生成 **PDF 存档**（reportlab + Noto Sans CJK TC 渲染中文），
  存为 `kind=contract_pdf` 的私有文件，并与签署记录关联。
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
- PDF 由 reportlab 生成（新增依赖 `reportlab`，见 `requirements.txt`），
  字体为文泉驿正黑（TrueType 轮廓，`fonts-wqy-zenhei`，
  `/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc`，子字体 0）；
  系统自带的 Noto Sans CJK TTC 是 CFF 轮廓，reportlab 不支持，会自动跳过。
  若运行环境没有任何可用 CJK 字体，降级为 Helvetica 只渲染 ASCII 字段
  （仍是有效 PDF、可下载），并在日志里告警。
  可用 `MERCHANT_CONTRACT_FONT` 环境变量指定其他字体文件。
- 上线前必须换的点（不做完不能上生产）：
  1. 对象存储：把 `merchant-files-private/` 换成 S3 / GCS 私有桶
     （只换存储层：`_save_merchant_file()` 与下载读取处，其余不动）；
     同时把 `reportlab` 的字体文件随部署包带上或改由对象存储/配置指定。
  2. 签名链接改为对象存储的预签名 URL 或保留现有 HMAC 方案（二选一，
     不要两套并存）。
  3. 文件病毒扫描、图片缩略图（review-images 已有同样待办）。
  4. 合约正文 v1 为占位文本（迁移与种子数据预置，明确标注
     「待法务确认」）；法务定稿后由 admin 发新版本，商户须重签当前版
     才能批准——旧版签署不顶用（批准门禁第 1 条）。
  5. 客户端 IP 目前取直连 `request.client.host`；上生产若在反向代理后，
     须按部署的受信代理配置取 `X-Forwarded-For`，否则记的是代理 IP。

## 5. 与 P1-B（Stripe Connect Express 开户）的衔接

- P1-B 负责：商户在 Stripe 的实名与银行帐户收集、开户流程、开户状态
  回调。本 PR 不碰 Stripe API、不改 FIX-1 支付验签。
- 衔接字段（申请记录上，P1-B 直接写同一组列）：`stripe_account_id`
  （Stripe Connect 帐户 id，如 `acct_…`）、`stripe_charges_enabled`、
  `stripe_payouts_enabled`（均须为 true 才算「已完成开户」）。
- P1-B 未合入前，批准门禁的开户状态由 admin 经
  `POST /admin/merchant-applications/{id}/stripe-onboarding` 登记。
- 批准门禁只读这三个字段，不调 Stripe、不依赖 P1-B 的路由是否存在。

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
