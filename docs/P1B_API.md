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
2. 商户核销后：结算基数取 `settlement_basis()` 的同一口径（M6 共享函数；
   套餐 = 订单实付金额，售价券 = discount_price，现金券/免费券/纯门槛券
   基数为 0（M8 已合入），即不产生转帐），
   佣金 = 基数 × 生效佣金率（复用 `MerchantCommissionRate`，无配置时默认
   10%，F5），净额 = 基数 − 佣金。
3. 净额通过 Stripe **separate charges and transfers** 转给商户的 Connect
   帐户（`transfer_group=coupon-go-redemption-{redemption_id}`）。
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
- 核销时立即转帐（不长期滞留平台余额），既符合“核销后转帐”的决策，
  也避开长期持有客户资金的合规灰色地带；转帐后商户侧 90 天提款上限
  由 `payout_hold_days=90` 在状态接口中提示，超期未提款须运营介入。
- 开户只换网关层（`connect_gateway.create_account / create_account_link /
  retrieve_account`）：对外接口、状态机、余额口径、佣金、fail closed
  规则全部不变；Dot 的对接不用改。Embedded onboarding（Account Session +
  ConnectJS，商户不出 App）是后续可选，不在本 PR——App 仍用 hosted
  Account Link 跳转。
- 未配置 Connect（`STRIPE_CONNECT_ENABLED=0`，或非 dev 环境未显式开启）
  时** fail closed**：开户/转帐/提款接口返回 503，核销本身仍成功，
  但流水停留在 `pending`（待结算），绝不退回成月结算单“标记已付”。
  配置恢复后用 retry 接口补转帐。

## 状态机

```
ConnectAccount.onboarding_status: pending -> incomplete -> active
  （active 当且仅当 charges_enabled 且 payouts_enabled；以 Stripe
   retrieve_account 为准，不以 return_url 到达为准）

ConnectLedgerEntry（kind=transfer，每核销最多一条）:
  pending（待结算） -> transferred（已转帐） -> reversed（已冲回）
  pending --（撤销/退款）--> reversed（未经过 Stripe，无 reversal id）
  transferred --（撤销/退款，Transfer Reversal）--> reversed
  transferred --（提款后余额不足等）--> 仍为 transferred，提款只看余额

ConnectLedgerEntry（kind=payout）: status=paid_out，金额为负数
  （net_amount = -提款金额），stripe_payout_id 记录 Stripe payout。
```

余额定义（`GET /merchant/connect/balance`）：
- `pending`（待结算）= kind=transfer 且 status=pending 的 net 之和
- `available`（可提取）= kind=transfer 且 status=transferred 的 net 之和
  − 已提取总额
- `paid_out`（已提取）= kind=payout 的金额绝对值之和
- 币种固定 `HKD`。基数为 0 的核销（免费券；M8 后现金券）不写流水。

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

```
POST /api/v3/merchant/connect/payouts
Authorization: Bearer <vendor JWT>

请求：{ "shop_id": 1, "amount": 100.00 }

成功 200：
{
  "shop_id": 1,
  "amount": 100.00,
  "currency": "HKD",
  "stripe_payout_id": "po_test_…",
  "status": "paid_out",
  "balance": { "pending": 0.00, "available": 35.00, "paid_out": 100.00 }
}

失败：
  503 未配置 Connect；409 帐户未就绪（未开户 / payouts 未启用）；
  422 金额 <= 0 或超过可提取余额；502 Stripe payout 调用失败
      （失败时不写流水、不扣余额，可重试）。
```

### 7. 补转帐 / 补冲回（待结算卡住时）

```
POST /api/v3/merchant/connect/transfers/{entry_id}/retry
Authorization: Bearer <vendor JWT>

成功 200：
{ "entry_id": 12, "status": "transferred", "stripe_transfer_id": "tr_test_…",
  "reversed": false }
  // 若该笔此前冲回失败（pending_reversal），本次补做 Transfer Reversal：
  // { "entry_id": 12, "status": "reversed", "stripe_reversal_id": "trr_test_…",
  //   "reversed": true }

失败：503 未配置；403 非本店商户；404 找不到流水；409 该笔无需重试
      （已转帐且无待冲回 / 已冲回）；502 网关失败（保持原状态，可再重试）。
```

## 核销 / 撤销 / 退款时的自动行为（无新增接口，后端钩子）

| 事件 | 行为 | 失败时 |
|---|---|---|
| 核销成功（`POST /new-scan-coupon`） | 按基数计佣金、写 `pending` 流水；Connect 已配置且商户帐户就绪时立即创建 Stripe transfer，流水变 `transferred`；响应里多一个 `connect` 块：`{"transfer": "transferred" \| "pending" \| "not_configured" \| "skipped_zero", "net_amount": …}` | 转帐失败不阻断核销：流水留 `pending`，`note` 记原因，可用接口 7 补转 |
| 撤销核销（`POST /redemptions/{id}/revoke`，10 分钟窗） | `pending` → `reversed`；`transferred` → Transfer Reversal 后 `reversed`；响应里多一个 `connect` 块 | 冲回网关失败：流水保持 `transferred` 并置 `pending_reversal`，用接口 7 补冲回；撤销本身仍成功 |
| 套餐退款成功（`POST /orders/{id}/refund` 等既有退款链路） | 对该订单已撤销核销的流水做同样冲回（幂等，已冲回的不重复冲） | 同上 |

**月结算单（Settlement）保留做对帐，不删；Connect 流水与结算单是两套
帐：结算单的“标记已付”（`POST /admin/settlements/{id}/pay`）绝不用
于 Connect 流水，两者不互通。**

## 环境变量（新增）

| 变量 | 缺省 | 说明 |
|---|---|---|
| `STRIPE_CONNECT_ENABLED` | dev 默认开、非 dev 默认关（显式 `1`/`0` 优先） | 总开关；关 = fail closed |
| `STRIPE_SECRET_KEY` | 空（模拟器） | 沿用既有网关约定：只接受 `sk_test_…`，正式密钥拒绝 |
| `STRIPE_CONNECT_RETURN_URL` | 空 | onboarding return_url 缺省值 |
| `STRIPE_CONNECT_REFRESH_URL` | 空 | onboarding refresh_url 缺省值 |

测试模式：未配 `STRIPE_SECRET_KEY` 时用本地模拟器（`acct_test_…` /
`tr_test_…` / `po_test_…`），与现有 `payments_gateway` 测试模式一致，
不接真实商户号。真实调用只走 Stripe 测试密钥。

## 与 P1-A 的关系

付费券购买由 P1-A 做；本 PR 的转帐钩在核销后。本 PR 基线（`3f2d8bc`，
M8 已合入，现金券基数为 0）尚无 P1-A：验证用的是现有套餐订单链路（购买 → 付款进平台 →
核销 → 转帐），套餐券的结算基数 = 订单实付金额，与 P1-A 付费券的
“实付金额”口径一致。P1-A 合入后，付费券核销自动走同一钩子，无需
另改；唯一待补的是转帐与原付款的 `source_transaction` 关联——目前
PaymentIntent 的 charge id 未持久化，转帐只带 `transfer_group`，
P1-A 可在确认付款时持久化 charge id 后再补关联。
