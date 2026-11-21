# P1-B Stripe Connect（Accounts v2）接口清单

> 规格来源：`docs/FEATURE_GAP_20261120.md` §3 已定决策 3（2026-11-20）——
> Stripe Connect（Accounts v2 商户帐户，express dashboard）；用户付款进平台；券核销后扣平台佣金，
> 余额转给商户的 Stripe 帐户；商户在 App 里看余额、点提款；商户实名与银行
> 帐户由 Stripe 收集，我们不存银行帐号。2026-11-20 深夜 Miso 拍板：从 v1 Express 迁到 Accounts v2。
> Base URL 与鉴权沿用 `docs/API.md`：前缀 `/api/v3`，`Authorization: Bearer <JWT>`。
> 本文件是给 Dot 并行对接的接口清单；实现见 `app/routers/connect.py`、
> `app/connect_service.py`、`app/connect_gateway.py`。

## 资金流（已定，不另定规则）

1. 用户付款进**平台** Stripe 帐户（现有套餐 / P1-A 付费券购买链路；本 PR
   不改购买流程）。
2. 只为关联 `purchase` 钱包项、已付款订单和匹配成功付款记录创建意图；`claim` 免费领取、未关联真实订单的砍价券、退款中/已退款订单不得仅按标价生成转帐。每订单唯一意图，金额以订单实付为上限。收费券和多券拆分待 #18 统一来源与金额分摊，不能假定自动兼容。
3. 核销事务只写待转帐流水并提交；后台任务再通过 Stripe **separate charges and transfers** 转给商户 Connect 帐户（`transfer_group=coupon-go-redemption-{redemption_id}`）。
4. 商户在 App 里看余额、点提款：提款 = Stripe Payout（商户帐户已设为
   手动提款），从商户 Connect 帐户余额打到其银行帐户。
5. 撤销核销 / 退款：未转帐的待结算直接冲回；已转帐的调用 Stripe
   Transfer Reversal 冲回。已提取的部分同样冲回（Connect 帐户余额可为负，
   平台承担，见 Stripe 文档）。

## Stripe 官方文档结论（2026-11-20 查阅原文；2026-11-21 迁 Accounts v2 时复核）

| 结论 | 来源 |
|---|---|
| Separate charges and transfers 在**香港**可用；平台是 merchant of record；Stripe 手续费、退款、拒付都从平台余额扣；转帐用 `transfer_group` / `source_transaction` 与原付款关联 | https://docs.stripe.com/connect/separate-charges-and-transfers |
| 跨境转帐：除美国/加拿大/英国/欧洲经济区/瑞士之间外，平台与 Connect 帐户须在**同一地区**——本项目平台与商户同在香港，符合 | 同上 |
| **Accounts v2 开户**：`POST /v2/core/accounts`（JSON，须带 `Stripe-Version: 2026-11-18.preview`）；`dashboard: "express"`（轻量 Express Dashboard，创建后不可改），`identity.country: "hk"`，`configuration.merchant`（`card_payments` + `stripe_balance.payouts`）与 `configuration.recipient`（`stripe_balance.stripe_transfers`），`defaults.responsibilities` fees/losses 均为 `application`（express dashboard 的强制要求，否则 400）；`include` 须列出 `configuration.merchant` / `configuration.recipient` / `requirements`，未列出的属性返回 null | https://docs.stripe.com/connect/accounts-v2 ；https://docs.stripe.com/connect/embedded-onboarding |
| **开户链接 v2**：`POST /v2/core/account_links`（JSON，同一 preview header）；`use_case: {type: "account_onboarding", account_onboarding: {configurations: ["merchant", "recipient"], collection_options: {fields: "eventually_due"}, return_url, refresh_url}}`；单次使用；`configurations` 须与帐户已应用配置一致，否则 `configs_must_match_to_use_account_links`。托管开户只支持真实浏览器，**不可嵌在 App 内 webview** | https://docs.stripe.com/connect/marketplace/tasks/onboard ；https://docs.stripe.com/api/v2/core/account-links/create?api-version=2026-09-30.preview |
| **状态判定走 capability status**：`charges_enabled` = merchant `card_payments.status == "active"`；`payouts_enabled` = merchant `stripe_balance.payouts.status == "active"`；`details_submitted` = `requirements.entries[]` 中无待用户处理且 currently/past due 的项；状态值 `active / pending / restricted / unsupported`。**return_url 不是成功事件**，必须服务端查 capability status（或监听 `v2.core.account[requirements].updated` thin event）才算开户完成 | https://docs.stripe.com/connect/track-account-onboarding?dashboard-or-api=api&accounts-namespace=v2 |
| **转帐 / 提款仍走 v1**：v2 帐户 id 可直接传给 v1 端点——`POST /v1/transfers`（`destination` = v2 帐户 id）、`POST /v1/payouts` 与 `POST /v1/balance_settings`（带 `Stripe-Account` 头）；Balance Settings API 管理 v2 商户的提款计划。v1 `type=express` 开户调用已废弃，不再使用 | https://docs.stripe.com/connect/accounts-v2 |
| **前置条件**：平台须在 Stripe Dashboard（Connect 设置）启用 Accounts v2，否则 v2 端点报 `accounts_v2_access_blocked`；v2 发 thin event 到 event destination，不走经典 webhook——本 PR 不接事件，sync 接口为准 | https://docs.stripe.com/connect/accounts-v2 ；https://docs.stripe.com/api/v2/core/account-links/create?api-version=2026-09-30.preview |
| 手动提款：`payments.payouts.schedule.interval=manual`（Balance Settings API）；商户帐户余额须在**90 天内**付出（香港属 “All other countries”：泰国 10 天、美国 2 年、其他国家 90 天）。Stripe 不提供 escrow；手动提款只是延迟付款的手段 | https://docs.stripe.com/connect/manual-payouts |
| 转帐可冲回：Transfer Reversal API 支持全部/部分冲回 | https://docs.stripe.com/connect/separate-charges-and-transfers （“Connect supports the ability to reverse transfers…”） |
| 平台责任：express dashboard 须 fees/losses 均由 application 承担（`defaults.responsibilities`，否则开户 400 `account_controller_express_dash_without_application_losses_or_fees`）；平台是 merchant of record，须审核入驻商户 | https://docs.stripe.com/connect/accounts-v2 ；https://docs.stripe.com/api/v2/core/accounts/create?api-version=2025-03-31.preview |

**对本项目的直接推论：**
- 核销事务提交后由后台任务派发转帐；转帐后商户侧 `payout_hold_days=90` 仅供提示，实际处理须按 Stripe 与适用规定核实。
- 开户只换网关层（`connect_gateway.create_account / create_account_link /
  retrieve_account`）：对外接口、状态机、余额口径、佣金、fail closed
  规则全部不变；Dot 的对接不用改。Embedded onboarding（Account Session +
  ConnectJS，商户不出 App）是后续可选，不在本 PR——App 仍用 hosted
  Account Link 跳转。
- 未配置 Connect（`STRIPE_CONNECT_ENABLED=0`，或非 dev 环境未显式开启）
  时** fail closed**：开户/转帐/提款接口返回 503，核销本身仍成功，
  但流水停留在 `pending`（待结算），绝不退回成月结算单“标记已付”。
  配置恢复后用 retry 接口补转帐。

## 状态机和余额

- 核销事务只创建 `pending` 转帐意图；后台 `connect-transfers` 持有商户行锁，提交 `processing` 后才请求 Stripe。成功为 `transferred`；超时、5xx、冲突或不确定响应为 `unknown`，绝不自动重复发送。
- 撤销或退款仅登记冲回意图：尚未发起转帐时直接 `reversed`；转帐处理中/结果未知时保留 `pending_reversal`；已转帐的流水由后台处理冲回。冲回不确定为 `reversal_unknown`，保留待对账标记。
- 提款先以商户行锁校验余额并提交 `processing` 预留，再调用 Stripe。Stripe 接单但未最终付款为 `submitted`，确认 `paid` 才是 `paid_out`；网络不确定为 `payout_unknown`。这些状态的金额均占用可用余额；只有 Stripe 确认失败或取消才释放。
- 可用余额 = 已转帐且不待冲回的净额 − 已支付提款 − 处理中、已提交或结果未知的提款预留。冲回后余额可为负，负数不能申请提款。
- Stripe v1 幂等键可能在至少 24 小时后清除；业务流水、首次/最近尝试时间和 Stripe 对象编号须持久化。未知或进程中断的请求不自动重发；通过下面的管理员对账接口验证 Stripe 对象。找不到对象不等于失败，必须保持冻结并联系 Stripe 确认。

## 接口

### 1. 商户开户（生成 onboarding link）

```
POST /api/v3/merchant/connect/onboarding
Authorization: Bearer <vendor JWT>
Content-Type: application/json

请求：
{
  "shop_id": 1,
  "return_url": "coupon-go://connect/return",      // 可选；缺省用环境变量
  "refresh_url": "coupon-go://connect/refresh"     // 可选；缺省用环境变量
}

成功 200：
{
  "shop_id": 1,
  "stripe_account_id": "acct_test_…",              // 模拟器：acct_test_…；真实：acct_…
  "onboarding_url": "https://accounts.stripe.com/r/…",  // v2 Account Link；模拟器：https://accounts.stripe.test/r/…
  "onboarding_status": "pending",
  "charges_enabled": false,
  "payouts_enabled": false,
  "details_submitted": false,
  "connect_configured": true,
  "simulated": true                                // 未配 Stripe key 时为 true
}

失败：
  503 {"detail": "Stripe Connect 未配置…"}         // STRIPE_CONNECT_ENABLED=0 或非 dev 未开启
  403 {"detail": "沒有權限"}                        // 不是该店商户且非 admin
  404 {"detail": "找不到商店"}
  400 {"detail": "缺少 return_url / refresh_url…"} // body 与环境变量都未提供
  502 {"detail": "Stripe Connect 网关失败…"}       // 真实网关调用失败
```

说明：同一 shop 重复调用复用已有 `stripe_account_id`，只生成新 link；
银行帐户/实名由 Stripe 在 onboarding 流程内收集，**后端不存**。
App 内打开 `onboarding_url`（Stripe 托管页，不可在 App 内嵌 webview，
见官方文档 Note）；用户完成后 Stripe 回到 `return_url`，**App 必须随即
调用下面的 sync 接口**（return_url 到达 ≠ 开户完成）。

### 2. 回调后同步开户状态

```
POST /api/v3/merchant/connect/sync
Authorization: Bearer <vendor JWT>

请求：{ "shop_id": 1 }

成功 200：
{
  "shop_id": 1,
  "stripe_account_id": "acct_test_…",
  "onboarding_status": "active",        // pending | incomplete | active
  "charges_enabled": true,
  "payouts_enabled": true,
  "details_submitted": true,
  "manual_payouts": true,               // payouts 启用后自动设为手动提款
  "connect_configured": true
}

失败：503 未配置；403 非本店商户；404 无 Connect 帐户（先调 onboarding）；
      502 网关失败。
```

说明：`manual_payouts` 在 `payouts_enabled` 首次为真时由后端通过
Balance Settings API 设为 manual，之后商户提款只走接口 6。

### 3. 商户 Connect 状态

```
GET /api/v3/merchant/connect/status?shop_id=1
Authorization: Bearer <vendor JWT>

成功 200：
{
  "shop_id": 1,
  "stripe_account_id": "acct_test_…",   // 未开户为 null
  "onboarding_status": "active",        // 未开户为 "pending"
  "charges_enabled": true,
  "payouts_enabled": true,
  "details_submitted": true,
  "manual_payouts": true,
  "connect_configured": true,           // false = 平台未配置 Connect
  "payout_hold_days": 90                // 香港：手动提款余额须 90 天内付出
}

失败：403 / 404 同上。
```

### 4. 商户余额

```
GET /api/v3/merchant/connect/balance?shop_id=1
Authorization: Bearer <vendor JWT>

成功 200：
{
  "shop_id": 1,
  "currency": "HKD",
  "pending": 135.00,      // 待结算
  "available": 124.20,    // 可提取
  "paid_out": 100.00,     // 已提取
  "connect_configured": true,
  "account_ready": true   // charges_enabled 且 payouts_enabled
}
```

### 5. 商户余额流水（分页）

```
GET /api/v3/merchant/connect/ledger?shop_id=1&page=1&page_size=20
Authorization: Bearer <vendor JWT>

成功 200：
{
  "total": 3,
  "page": 1,
  "page_size": 20,
  "entries": [
    {
      "id": 12,
      "kind": "transfer",               // transfer | payout
      "status": "transferred",          // pending | transferred | reversed | paid_out
      "redemption_id": 88,              // payout 为 null
      "gross_amount": 150.00,
      "commission": 15.00,
      "net_amount": 135.00,             // payout 为负数
      "stripe_transfer_id": "tr_test_…",// payout 为 null
      "stripe_reversal_id": null,
      "stripe_payout_id": null,
      "note": "",
      "created_at": "2026-11-20T…"
    }
  ]
}
```

### 6. 商户发起提款

`POST /api/v3/merchant/connect/payouts`，仅店主或管理员。

```json
{"shop_id": 1, "amount": "100.00", "request_id": "一次操作生成的稳定随机编号"}
```

`request_id` 为 16–64 位字母、数字、`_` 或 `-`，客户端须在第一次发送前持久保存；超时、重启、重复点击均复用相同编号和金额。服务端在商户范围按编号去重；同编号不同金额返回 409。当前代码库尚无该接口的 Flutter 调用方，未来接入须先实现这个持久化契约。

成功返回 `entry_id`、`stripe_payout_id` 和 `status`：`submitted` 表示 Stripe 已接收但尚未确认到账，`paid_out` 是已确认付款。处理中、超时或结果不明保持预留；502 提示待确认，不得以新编号盲目重试。

### 7. 派发待转帐 / 冲回

`POST /api/v3/merchant/connect/transfers/{entry_id}/retry`：只处理未发送的 `pending` 或已经确定转帐的 `pending_reversal`。每次 Stripe POST 的 `Idempotency-Key` 分别为 `transfer-{operation_id}` 或 `reversal-{operation_id}`；提款使用 `payout-{operation_id}`；每条流水的 operation_id 是全局随机 UUID，持久化并有唯一约束，不使用可能跨库复用的整数 ID。后台调度器的 `connect-transfers` 任务也使用同一流程、批量上限 100。`processing`、`unknown`、`reversal_unknown` 一律拒绝重发并进入对账。

### 8. 管理员核对 Stripe 对象

`POST /api/v3/admin/connect/ledger/{entry_id}/reconcile`，请求 `{"provider_id": "po_... / tr_... / trr_..."}`，仅管理员。超时未拿到 ID 时，由管理员先在 Stripe 侧根据流水 metadata 的 `connect_operation_key` 核查并取得对象 ID；接口从 Stripe 重新读取对象，验证编号、操作键、金额、币种及商户账户或原转帐，且记录管理员审计日志。提款 `pending` 继续保留，`paid` 标已付，`failed` / `canceled` 才释放；已确认转帐如有待冲回会交由后台继续处理。

正在处理且距上次尝试不到两分钟的请求不得对账。若 Stripe 未找到对象，或无法确认曾否执行，保持未知状态并联系 Stripe；不得凭“查询为空”放开预留或换键重发。处理超过 v1 24 小时幂等窗口的未知请求同样如此。生产转账必须先完成 webhook/对账运维、迁移单头、真实 PostgreSQL 并发和 Stripe test mode 的完整验证，本候选尚不授权真实支付。

## 环境变量（新增）

| 变量 | 缺省 | 说明 |
|---|---|---|
| `STRIPE_CONNECT_ENABLED` | dev 默认开、非 dev 默认关（显式 `1`/`0` 优先） | 总开关；关 = fail closed |
| `STRIPE_SECRET_KEY` | 空（拒绝资金操作） | 只接受 `sk_test_…`，正式密钥拒绝 |
| `STRIPE_CONNECT_SIMULATOR` | `0` | 仅 `APP_ENV=dev` 且显式设置为 `1` 才可启用测试模拟，非 dev 一律不启用 |
| `STRIPE_CONNECT_API_VERSION` | `2026-11-18.preview` | Accounts v2 版本；仅接受日期及 preview/具名稳定后缀的合法格式，无效时 fail closed。升级需在 Stripe test mode 验证开户、状态、转账互通与 webhook 后逐环境切换。该版本仍为 preview，不据此宣称已普遍正式发布。 |
| `STRIPE_CONNECT_RETURN_URL` | 空 | onboarding return_url 缺省值 |
| `STRIPE_CONNECT_REFRESH_URL` | 空 | onboarding refresh_url 缺省值 |

测试模拟必须在 dev 环境明确启用；未配置密钥且未明确启用模拟时 fail closed。Stripe 网络调用只接受测试密钥。

## 当前合并阻断及后续核查

- **真实付款凭证**：无权威实收证明时不能调用 Stripe 转账；当前网络转账入口明确 fail closed 为 `payment_verification_required`。须与 #18 联合实现/验证捕获 charge、退款后净额和每订单金额分配，再解除限制。模拟 `pi_test_…` 不得进入任何真实 Stripe 网络转账。
- **开户并发**：现有 onboarding 在本地无账户时先调用 Stripe，再写本地账户；并发可能创建孤儿账户。需持久化开户意图、全局唯一幂等键和未知结果对账，完成测试后方可合并。
- **佣金快照**：Connect 在核销时定费率，现有月结单在出单时读取当前费率；核销后调费会导致对账差异。`test_fee_change_between_redemption_and_statement_uses_same_snapshot` 是严格 xfail 复现。需与 #12/#19 统一不可变快照和历史账回填，不能静默接受差异。
- **提现状态**：有界后台任务轮询已知 payout 的 submitted 及 paid_out，支持 late failure 并写追加对账事件；未知且无 Stripe 对象编号的操作继续冻结，需要管理员向 Stripe 查询并核实，不可盲目重发。实际 webhook/服务容量与长期轮询滞后监控仍需设计验证。
- **环境克隆**：随机流水 operation_id 防止独立数据库重建时整数编号碰撞，但完整复制数据库也会复制 operation_id；测试与生产必须隔离 Stripe 凭据并禁止复制库自动派发，不能声称 UUID 能解决克隆风险。
- **迁移**：本 PR 0021 尚未跟随 #13 等队列重新编号，必须在实际顺序上 rebase 后验证单头及 PostgreSQL 升降级；当前单表结构测试不证明整条迁移链已通过。
