# Coupon GO! 核销接口（对标大众点评 · 第 4 块）

> 分支 `dianping/redemption`，基线 main `3f2d8bc`。对应差距清单
> `~/workspace/coupon-go-dianping/GAP_ANALYSIS_20261121.md` 第 4 节。
> 字段变更另见 `docs/API.md`（核销一节）。

所有路径都挂在 `/api/v3` 下。商户端接口需 `vendor`（或 `admin`）身份；
`admin` 在只读类接口上可见全部门店（与导出接口一致），在核销类接口
上与商户走同一条消费链路。

## 一条链路（统一入口）

二维码扫码、手输 6 位码、补核销、批量核销，**消费的都是同一段代码**：
`app/routers/coupons.py::_redeem_item()`。它依次做：过期翻转 → 状态/
门店/已付款/可核销校验 → 每日扫码上限 → 顾客账户行锁 → 条件 UPDATE
消费（原子防重）→ `Coupon.used_count` → `settlement_basis()` 快照 →
`Redemption` 行 → 积分 → 返现 `record_spend()` → 结算流水
`add_scanned_sale()`。调用方只负责：解析出 `WalletItem`、准备
`prepare_token`（如有）、决定 `method`、提交事务。

`method`（`redemptions.method`，迁移 `0021` 新增）记录核销方式：
`scan`（扫 QR）、`manual`（手输 6 位码）、`supplementary`（补核销）、
`batch`（批量核销）。`note`（同迁移新增）记操作员备注。

## 1. 券码找回（P0-7，§4.1）

### `GET /merchant/redemptions/lookup?phone=&code_suffix=&order_id=`

商户侧。至少传一个参数，否则 400。只返回**本店未核销**的券
（admin 看全部）。

| 参数 | 说明 |
|---|---|
| `phone` | 顾客手机号（全号比对，忽略空格/横杠） |
| `code_suffix` | 已出示兑换码的后几位（1–6 位数字；码只在顾客出示后 15 分钟内存在） |
| `order_id` | 套餐订单号 |

响应：

```json
{"items": [{"wallet_item_id": 12, "coupon_id": 2, "coupon_name": "壽司放題 85 折",
  "shop_id": 1, "shop_name": "櫻花壽司", "customer_phone_last4": "8000",
  "valid_to": "2026-12-21T00:00:00", "order_id": null, "package_quantity": null,
  "source": "claim", "status": 1}]}
```

**不返回完整手机号、不返回兑换码**——商户凭后四位 + 券名 + 有效期
与顾客核对身份。`package_quantity` 只有套餐订单才有（订单份数）。

## 2. 事后补核销（P0-7，§4.1）

### `POST /merchant/redemptions/supplementary-scan`

```json
{"wallet_item_id": 12, "idempotency_key": "supp-20261121-001",
 "reason": "顧客離店後發現未核銷", "note": "收銀台交班補登"}
```

- 不需要顾客出示码、不需要 `prepare_token`：商户已通过第 1 节找回
  并核对了身份。
- 消费走同一 `_redeem_item` 链路：结算快照、积分、返现、结算流水与
  正常扫码完全一致；`method="supplementary"`。
- 每次成功都在 `admin_audit_logs` 写一条
  `redemption.supplementary_scan`（含 `wallet_item_id`、原因、操作商户），
  可审计；`redemption_id` 为 `target_id`。
- `idempotency_key` 可选；传了则重放返回原核销（`duplicate: true`），
  不传则同一张券第二次补核销报 400（已使用）。
- 不是本店的券 → 403；券不存在 → 404；已使用/已失效 → 400。

响应（与 `POST /new-scan-coupon` 同形，多 `method`/`note`）：

```json
{"message": "您的優惠券已成功使用", "coupon_name": "壽司放題 85 折",
 "shop_id": 1, "redemption_id": 88, "wallet_item_id": 12,
 "revoke_window_minutes": 10, "scanned_at": "2026-11-21T00:10:00+00:00",
 "duplicate": false, "method": "supplementary", "note": "收銀台交班補登"}
```

## 3. 超窗强制撤销（P1，§4.2）

### `POST /admin/redemptions/{redemption_id}/force-revoke`

仅 `admin` / `support`（客服）。`reason` 必填（空串报 400，缺失报 422）。

```json
{"reason": "掃錯碼，顧客投訴"}
```

- 撤销本身与 10 分钟窗口内的 `POST /redemptions/{id}/revoke` **同一段
  代码**（`_reverse_redemption`）：券退回未使用（已过期则标失效）、
  实际发放的核销积分冲回、返现作废（已提现记负向调整）、评价隐藏+
  评价积分冲回、结算流水冲销。只放宽时间校验。
- 响应在普通撤销字段之外多三项：

```json
{"message": "核銷已撤銷，優惠券已退回未使用", "wallet_status": 1,
 "redemption_id": 88, "wallet_item_id": 12,
 "revoked_at": "2026-11-21T00:40:00", "revoke_window_minutes": 10,
 "cashback": {...}, "review": null,
 "forced": true, "reason": "掃錯碼，顧客投訴", "minutes_over_window": 20}
```

- 每次强制撤销都在 `admin_audit_logs` 写一条 `redemption.force_revoke`
 （原因、原核销时间、超时分钟数、操作人角色），可审计。
- 已撤销的再调 → 400。商户超窗没有自助入口：商户端按钮文案应为
  “申請客服撤销”，由客服走本接口处理。

## 4. 一次核销多张（P1，§4.3）

### `POST /new-scan-coupon/batch`

```json
{"codes": ["123456", "654321"],
 "prepare_tokens": ["<token1>", "<token2>"],
 "idempotency_key": "batch-20261121-001",
 "note": "四人套餐到店"}
```

- `codes` 1–20 张；`idempotency_key` **必填**；`prepare_tokens` 可选，
  传了必须与 `codes` 一一对应（同顺序、同长度）。
- **一个事务、全部成功或全部回滚**：先逐张解析 + 校验（码有效、
  未使用、未过期、本店、可核销、`prepare_token` 有效），任一张失败
  即 400 且一张都不消费，响应逐张报 `ok` / `error`（含原因）/
  `duplicate`；校验全过才逐张走同一 `_redeem_item` 链路消费，消费
  中途失败（如并发抢先）同样整体回滚。
- 幂等：批量键是整批的键，第 `i` 张记为 `{idempotency_key}:{i}`；
  **原样重发同一请求**返回原核销（`duplicate_count`，不产生新核销）。
  换码、换顺序重发视为新批次。
- 每张的结算快照、积分、返现、结算流水与单张核销一致；
  `method="batch"`。成功时写一条 `redemption.batch_scan` 审计日志
  （批量键、张数、各 `redemption_id`）。

响应（成功）：

```json
{"total": 2, "redeemed_count": 2, "duplicate_count": 0,
 "results": [{"code": "123456", "status": "redeemed", "wallet_item_id": 12,
   "redemption_id": 88, "coupon_name": "壽司放題 85 折", "duplicate": false},
  {"code": "654321", "status": "redeemed", "wallet_item_id": 13,
   "redemption_id": 89, "coupon_name": "火鍋任食 9 折", "duplicate": false}]}
```

响应（400，任一张失败——此时 `redeemed_count` 为 0，什么都没消费）：

```json
{"total": 2, "redeemed_count": 0, "duplicate_count": 0,
 "results": [{"code": "123456", "status": "ok", "wallet_item_id": 12},
  {"code": "000000", "status": "error", "reason": "無效的 QR 碼！",
   "wallet_item_id": null}]}
```

注意：一个套餐订单只产生**一张** `WalletItem`（见
`packages.py::_issue_voucher`），无论订单份数是多少；`prepare` 回显的
`package_quantity` 是订单份数，不是券张数。一次核销多张 = 一次消费
多个 `WalletItem`，不是拆分一张券。

## 5. 单张核销的补充（§4.4）

### `POST /new-scan-coupon`

在原有 `{code, idempotency_key?, prepare_token?}` 之外新增可选字段：

```json
{"code": "123456", "idempotency_key": "k-1", "method": "manual"}
```

- `method`：`scan`（默认，扫 QR）或 `manual`（手输 6 位码），其它值报 422。
  记入 `redemptions.method`，导出可见。消费链路不变。

## 6. 核销导出筛选（§4.4 / §4.5）

### `GET /vendor/redemptions/export?format=json|csv&from=&to=&coupon_id=&revoked=&method=`

- `from` / `to`：`YYYY-MM-DD`，按 `scanned_at` 含端筛选；格式错报 400。
- `coupon_id`：只看一张券；`revoked=true/false`：只看已撤销 / 未撤销；
  `method`：只看 `scan` / `manual` / `supplementary` / `batch`。
- 每条记录新增 `method`、`note`、`scanned_by_name`（操作员姓名）；
  CSV 末尾新增 `method`、`scanned_by_name` 两列。

## 钩子复用与 P1-C 衔接

- **结算**：所有核销路径都调 `settlement_basis()`（实付口径；现金/
  免费/纯门槛券 0；已退款订单不结算），快照进 `Redemption.face_value`。
  没有第二套口径。
- **积分**：所有核销路径都调 `coupons.py::award_redeem_points()`。
  今天 P1-C 未合入 main，这里仍发固定 `POINTS_REDEEM`（+20），与原来
  一致。**P1-C 落地时只改这一个函数**：按 `FEATURE_GAP §3.4`，付费券
  按实付 HK$1 = 1 分、核销后入帐（读 `item.order_id` 背后的
  `Order.paid_amount`），免费券沿用现值。撤销侧无需改动——
  `_reverse_redemption` 冲回的是账上实际发放的那笔 `redeem` 积分，
  不是按规则重算的。
- **返现**：沿用 `cashback_service.record_spend()` / 撤销时
  `void_for_revocation()`，不另写钩子。
- **幂等与并发**：`idempotency_key` 唯一约束 + `presented_item(lock=True)`
  行锁 + 条件 UPDATE 消费（`status=1` 且码匹配且码未过期），三件都
  原样保留；批量只是在同一事务里多调几次同一函数。

## 未做

- §4.6 弱网/离线核销：App 端离线队列，后端已幂等、无需改接口，本块
  不做（P2，留给 App 侧）。
- 商户超窗撤销的“申请单”流转：商户侧只有“申請客服撤销”入口文案，
  申请单本身复用统计块的异议流程（§2.6），本块不另建表。
- 实付金额（堂食加付）字段：§4.4 列为可选小迁移、第二批，本块只做
  了方式与备注；如需再加 `actual_paid_amount` 列，另排迁移。
