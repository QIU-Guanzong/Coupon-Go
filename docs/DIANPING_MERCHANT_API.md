# 商家管理接口（对标大众点评 §1）

基线：远端 main `3f2d8bc`；分支 `dianping/merchant-mgmt`。
Base URL：`http://localhost:8000`，路径前缀 `/api/v3`。
鉴权：`Authorization: Bearer <JWT>`（除注明外都需要）。

本文只列本分支新增/改动的接口；其余接口见 `docs/API.md`。

## 字段约定

### 门店（`shop_out` 新增字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `is_approved` | bool | 入驻批准后建店即 `true` |
| `business_hours` | object \| string | 营业时间，对象如 `{"mon_fri": "11:00-22:00"}`，或自由文字 |
| `gallery` | string[] | 门店相册图片链接，最多 9 张 |
| `announcement` | string | 门店公告 |

### 券（`coupon_out` 新增字段）

| 字段 | 类型 | 说明 |
|---|---|---|
| `branch_ids` | int[] | 券适用分店 id；`[]` = 适用本店全部分店 |
| `branches` | `{id, branch_id, name}[]` | 同上，带分店名 |

### 店员角色（`ShopMember.role`）

| 角色 | 权限（`permissions`） | 典型场景 |
|---|---|---|
| `merchant-admin`（旧值，默认） | `redeem`, `manage_coupons`, `view_settlement`, `view_redemptions`, `manage_members` | 老店员，全权（保持向后兼容） |
| `manager`（店长） | `redeem`, `manage_coupons`, `view_redemptions` | 建券、核销，看不到结算金额 |
| `cashier`（核销员） | `redeem`, `view_redemptions` | 只扫码核销、看核销记录 |
| `finance`（财务） | `view_settlement` | 只读结算单/发票，不能核销 |

店主（`Shop.vendor_id`）与平台 `admin` 拥有全部五项权限。
平台 `finance` / `auditor` 角色可读任意店结算数据（与旧 `can_access_shop` 一致），不参与门店级权限。

## 1.1 入驻批准建店（P0）

批准入驻即自动按申请单开店，商户登录后可直接建券，无需再找平台。

### `POST /admin/merchant-applications/{id}/review`（改动，admin）

请求与原来相同：`{"approve": true, "reject_reason": ""}`。

批准时响应新增 `shop`：

```json
{
  "message": "入駐申請已通過",
  "application": {"id": 12, "status": "approved", "shop_name": "阿婆豆腐花", "...": "..."},
  "shop": {
    "id": 21, "shop_id": 21, "shop_name": "阿婆豆腐花",
    "address": "旺角花園街 1 號", "phone": "91234567",
    "is_approved": true, "business_hours": {}, "gallery": [], "announcement": ""
  }
}
```

- 批准：申请人 `role` 由 `customer` 改为 `vendor`（原有行为），并新建一个 `is_approved=true`
  的门店（店名/地址/电话取自申请单；分类/营业时间/图片由商户后续用 `PATCH /merchant/shops/{id}` 补全）。
- 一个申请只审一次，故一次批准恰好建一家店；同一商户第二次申请获批会再建第二家店。
- 驳回：`shop` 为 `null`，不建店；申请人同时收到站内通知，含驳回原因。
- 批准/驳回都会给申请人写一条站内通知（`GET /get-notification-list` 可见，
  类型 `application_approved` / `application_rejected`）。

## 1.2 门店 / 分店新增与券适用分店（P0）

### `POST /merchant/shops`（新增，vendor / admin）· 201

```json
// 请求
{"name": "新開茶餐廳", "address": "深水埗欽州街 9 號", "phone": "92345678",
 "description": "平民茶餐廳", "category_id": 1, "area_id": 2}
// 响应
{"message": "門店已建立",
 "shop": {"id": 22, "shop_name": "新開茶餐廳", "is_approved": true, "...": "..."}}
```

已通过入驻审核的商户开新店直接 `is_approved=true`（商户身份已审过，不再二次审核）。
`name` 必填；`category_id` / `area_id` 如传须存在，否则 400。

### `GET /merchant/shops/{shop_id}`（新增，店主 / 店员 / admin）

```json
{"shop": {"id": 22, "shop_name": "新開茶餐廳", "business_hours": {}, "...": "..."},
 "branches": [{"id": 5, "branch_id": 5, "name": "太子分店", "address": "太子道 88 號", "phone": ""}]}
```

### `GET /merchant/shops/{shop_id}/branches`（新增，店主 / 店员 / admin）

```json
{"shop_id": 22, "branches": [{"id": 5, "branch_id": 5, "name": "太子分店", "address": "", "phone": ""}]}
```

### `POST /merchant/shops/{shop_id}/branches`（新增，店主 / `manage_coupons` 店员 / admin）· 201

```json
// 请求
{"name": "太子分店", "address": "太子道 88 號", "phone": "92345679"}
// 响应
{"message": "分店已建立",
 "branch": {"id": 5, "branch_id": 5, "shop_id": 22, "name": "太子分店",
            "address": "太子道 88 號", "phone": "92345679",
            "latitude": null, "longitude": null}}
```

非本店人员调用返回 403。

### `POST /assign-branches`（改动：现在真的写库）

```json
// 请求
{"coupon_id": 31, "branch_ids": [5, 6]}
// 响应
{"coupon_id": 31, "assigned_branch_ids": [5, 6]}
```

- 本次集合**替换**旧集合；传 `[]` = 清除限制，券适用本店全部分店。
- 存在无效或不属于该券门店的分店 id 时返回 400，原范围保持不变。
- 关联了未取消订单后不能更改范围；重复提交相同范围仍可成功。
- 只有店主 / `manage_coupons` 店员 / admin 可调；券的 `branch_ids` 在 `coupon_out`
  （如 `GET /coupon-details-new`）中可见。
- 核销时传 `branch_id` 即按适用范围校验（见下）。

### `POST /new-scan-coupon/prepare`（受限券须选择 `branch_id`）

```json
// 请求
{"code": "123456", "branch_id": 6}
// 适用分店之外的响应
{"can_redeem": false, "reason": "此優惠券不適用於此分店", "prepare_token": null, "wallet_item_id": 88}
```

受限券未传分店时，`can_redeem` 为 false，`branch_selection_required` 为 true，
`allowed_branches` 返回可选分店的 `id`、`name`、`address`；选择后携带 `branch_id`
重新预检。不受限券无需选择。成功响应回显 `branch_id`；分店不在该店时 reason
为 `找不到此分店`。

### `POST /new-scan-coupon`（受限券须传 `branch_id`）

```json
// 请求
{"code": "123456", "branch_id": 5, "idempotency_key": "scan-001"}
```

分店不在适用范围返回 400 `此優惠券不適用於此分店`；分店不属于该店返回 400 `找不到此分店`。
受限券不传 `branch_id` 返回 400，提示先选择分店，券不会被核销；不受限券无需传分店。

批量核销 `POST /new-scan-coupon/batch` 可传与 `codes` 等长的 `branch_ids`；
受限券填写所选分店 id，不受限券可填 null。缺失或不适用时整批不核销，400 响应的
`results` 提供每项原因与 `allowed_branches`。旧的不传分店批次及其幂等重试仍可用于
不受限券。补录查询 `/merchant/redemptions/lookup` 每项返回 `branch_required` 和
`branches`，受限券补录须传 `branch_id`。成功核销的分店保存在流水中，历史流水为空。

## 1.3 店员三级权限（P1）

### `POST /admin/shops/{shop_id}/members`（改动：邀请带角色，店主 / admin）· 201

```json
// 请求
{"user_id": 45, "role": "cashier"}
// 响应
{"message": "邀請已發送，待對方確認",
 "member": {"id": 7, "shop_id": 22, "user_id": 45, "role": "cashier",
            "status": "pending",
            "permissions": ["redeem", "view_redemptions"]}}
```

`role` 省略时默认 `merchant-admin`（老行为不变）；非法角色返回 400。
被邀人确认流程不变（`POST .../members/{member_id}/confirm`，本人确认后激活；
customer 确认后平台角色升为 `merchant-admin`，门店级角色以邀请时为准）。

### `PATCH /admin/shops/{shop_id}/members/{member_user_id}`（新增，店主 / admin）

```json
// 请求
{"role": "manager"}
// 响应
{"message": "店員角色已更新",
 "member": {"id": 7, "role": "manager", "status": "active",
            "permissions": ["manage_coupons", "redeem", "view_redemptions"]}}
```

立即生效；非法角色 400；非店员 404；非店主/非 admin 403。

### `GET /admin/shops/{shop_id}/members`（改动）

每条成员新增 `permissions`（按其角色展开的权限列表）。

### 权限收紧点（行为变化）

| 接口 | 变化 |
|---|---|
| `GET /merchant/shops/{id}/settlements`、`.../reconciliation`、`.../invoices` | 只有 `view_settlement` 可见：店主 / admin / 平台 finance-auditor / `merchant-admin` / `finance` 店员；`manager` / `cashier` 返回 403 |
| `GET /merchant/settlements` | 只返回结算可见的门店（`cashier` / `manager` 调此接口得空列表） |
| `POST /new-scan-coupon/prepare`、`POST /new-scan-coupon` | 店员也可核销：`redeem` 权限者（店主 / admin / `manager` / `cashier` / `merchant-admin`）可扫；本店 `finance` 店员 prepare 返回 `can_redeem: false`（`沒有核銷權限`），scan 返回 403；无关人员保持原来的 400 `無效的 QR 碼！此碼屬於其他商店。` |
| `POST /add-coupon-preview`、`POST /coupon-previewed`、`POST /assign-branches`、`POST /update-branch-profile`、`POST /delete-vandor-image` | 店主 / admin / `manage_coupons` 店员可调；券的 `vendor_id` 记为店主（不是代填的店员） |
| `GET /vendor/redemptions/export` | 店员可看本店核销记录（须 `view_redemptions`）；`finance` 店员看不到 |

## 1.4 门店自助维护与商户图片上传（P1）

### `PATCH /merchant/shops/{shop_id}`（新增，店主 / `manage_coupons` 店员 / admin）

```json
// 请求（全字段可选，只改传了的）
{"phone": "92345678",
 "business_hours": {"mon_fri": "11:00-22:00", "sat_sun": "10:00-23:00"},
 "gallery": ["/uploads/merchant-images/a.png", "/uploads/merchant-images/b.png"],
 "announcement": "新店開幕，全場 9 折"}
// 响应
{"message": "門店資料已更新", "shop": {"id": 22, "business_hours": {"mon_fri": "11:00-22:00"}, "...": "..."}}
```

可改：`name` / `name_en` / `category_id` / `area_id` / `address` / `phone` /
`description` / `image` / `business_hours`（对象或文字）/ `gallery`（链接列表，最多 9 张）
/ `announcement`。`gallery` 非列表返回 400；他人门店返回 403。
改后字段通过 `shop_out`（如 `GET /shop-details`、`GET /merchant/shops/{id}`）可见。

### `POST /uploads/merchant-images`（新增，vendor / admin）· 201

multipart `files`（与评价图上传同款规则：jpg / jpeg / png / webp / gif、单张 ≤ 5 MB、
一次最多 9 张、去 EXIF、UUID 文件名）：

```json
{"message": "圖片已上傳", "images": ["/uploads/merchant-images/9f2c….png"]}
```

返回的链接可填入 `Shop.image` / `Shop.gallery` / `Coupon.image`。

## 1.5 商户站内通知（P1）

通知走现有 `GET /get-notification-list`（按用户取，含 `target_shop_id` /
`target_coupon_id`）。本分支新增的通知类型：

| 类型 | 触发点 | 接收人 | 标题 |
|---|---|---|---|
| `application_approved` | 入驻批准（同时建店） | 申请人 | 入駐申請已通過 |
| `application_rejected` | 入驻驳回 | 申请人 | 入駐申請被拒絕（含原因） |
| `coupon_approved` / `coupon_rejected` | 券审核通过 / 驳回 | 券的店主 | 優惠券審核通過 / 優惠券審核未通過 |
| `settlement_generated` | 结算单生成 | 店主 | 結算單已生成 |
| `settlement_approved` | 结算单确认 | 店主 | 結算單已確認 |
| `settlement_paid` | 结算单打款 | 店主 | 結算單已打款 |
| `review_received` | 用户发表评价 | 店主 | 收到新評價 |

推送通道（App 推送/短信）另议，站内信先行。

## 1.6 待审申请编辑（§1 的 P2 项，顺带补）

### `PATCH /admin/merchant-applications/{application_id}`（新增，admin）

```json
// 请求（全字段可选）
{"shop_name": "新店名", "phone": "98765432"}
// 响应
{"message": "入駐申請已更新",
 "application": {"id": 12, "status": "pending", "shop_name": "新店名", "...": "..."}}
```

仅 `pending` 态可改；已审申请返回 400；非 admin 返回 403。
被拒申请仍按 v1 规则走**新申请**重交，不走此接口。

## 未做的事（§1 内明确留后）

- **1.7 代理人体系**：按 `FEATURE_GAP` §5 阶段 2 再做，本阶段不插队。
- **1.6 电子合约到期/版本更新重签提醒**：P2，未做。
- **Stripe Connect 正式收款**：开户及状态回查已由 P1-B 提供；正式环境仍须完成实际配置和验证。

## 迁移

`alembic/versions/0023_dianping_merchant_mgmt.py`（`down_revision = 0020_m92_legal_consents`）：

- 新表 `coupon_branches`（`coupon_id` + `branch_id` 唯一约束）。
- `shops` 加 `business_hours` / `gallery` / `announcement` 三列（`Text`，默认值 `""` / `"[]"` / `""`）。

历史 `0023` 保留原编号与父版本。`0031_dianping_mgmt_bridge` 在现行核销和历史商户
管理两条迁移路径之后衔接，并为核销流水增加可空 `branch_id`；旧流水保留为空。
