# Coupon GO! 统计系统接口（对标大众点评商家后台 §2，供 Dot 对接）

- 基线：main `3f2d8bc` + 本分支 `dianping/stats-dashboard`
- 实现：`apps/api/app/routers/merchant_stats.py`、`apps/api/app/merchant_finance.py`
- 全部接口挂在 `/api/v3` 前缀下；认证与其它接口一致（Bearer token）。
- **口径红线**：所有金额只认实付金额，复用 `settlement_basis()` —— 套餐取
  `Order.paid_amount`、价格券取 `discount_price`，现金 / 免费 / 纯门槛券为 0，
  已退款订单的核销不进结算。本分支不另造口径。

## 余额口径（§2.1，P0-3）

金额唯一来源为 Stripe Connect 账本：`pending_settlement` 是待转账净额，
`withdrawable` 是已转入商户账户且扣除已提款及处理中提款的余额，
`reserved` 是处理中提款，`withdrawn` 是已完成提款。结算单是对账记录，
审核结算单不会直接改变可提款余额。旧核销若缺 Connect 流水，须另行核对，
不能按结算单推断已转款。

## 接口清单

### 余额

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/merchant/balance` | 店主 / 店员 | 本人名下全部商户的余额汇总 + 按店分列 |
| GET | `/merchant/shops/{shop_id}/balance` | 店主 / 店员 / 平台财务·审核 | 单店余额 |
| GET | `/merchant/shops/{shop_id}/balance/entries?page=&page_size=` | 同上 | 按单明细（分页，默认 20 条）：哪张券、何时核销、基数、佣金、净额、状态 |

`GET /merchant/shops/5/balance` 响应示例：

```json
{"shop_id": 5, "shop_name": "櫻花壽司屋",
 "pending_settlement": 124.2, "withdrawable": 0,
 "reserved": 0, "withdrawn": 0, "commission_rate": 0.1}
```

`entries` 每一项：

```json
{"redemption_id": 12, "coupon_id": 10, "coupon_name": "點心拼盤 $138",
 "scanned_at": "2026-11-21T10:00:00", "period": "2026-11",
 "basis_amount": 138, "basis_note": "price_coupon",
 "commission": 13.8, "net_amount": 124.2,
 "state": "pending_settlement", "settlement_id": null, "reversed": false}
```

`state` 为 Connect 转账流水状态；`reversal_pending` 表示冲回未确认，
`unavailable` 表示没有转账流水，不能据此断言已付款。

### 经营看板（§2.4）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/merchant/shops/{shop_id}/dashboard?days=30` | 店主 / 店员 / 平台财务·审核 | 单店看板：近 N 天（默认 30，1–90）核销趋势、收入、客单、按券转化 |
| GET | `/merchant/dashboard?days=30` | 店主 / 店员 | 全部商户汇总 + 按店对比 |

`dashboard` 响应示例：

```json
{"shop_id": 5, "shop_name": "櫻花壽司屋",
 "period": {"from": "2026-10-23", "to": "2026-11-21", "days": 30},
 "totals": {"redemptions": 12, "gross_amount": 1656, "commission": 165.6,
            "net_amount": 1490.4, "avg_order_value": 138,
            "claims": 15, "conversion_rate": 0.8},
 "trend": [{"date": "2026-11-21", "redemptions": 2,
            "gross_amount": 276, "net_amount": 248.4}],
 "coupons": [{"coupon_id": 10, "coupon_name": "點心拼盤 $138",
              "claims": 15, "redemptions": 12, "expired": 1,
              "conversion_rate": 0.8, "gross_amount": 1656}]}
```

说明：`gross_amount` 是结算基数之和（不是券面原价）；`avg_order_value =
gross / redemptions`；`conversion_rate = redemptions / claims`（领券→核销）；
`trend` 按日补零（无核销的日子也返回，值为 0）；`expired` 是该券在窗口内
领券后已过期的张数。评价趋势与关键词未做（第二批）。

`GET /merchant/dashboard` 在此基础上把 `totals` 汇总，并给 `shops` 数组
每店一行 `{shop_id, shop_name, totals}`（分店对比）。

### 商户收款

核销后的商户转账和提款统一使用 Stripe Connect；具体接口和状态见
`P1B_API.md`。不另设基于月结单的商户提现审批流程。

### 结算单异议（§2.6，商户侧入口）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/merchant/settlements/{id}/dispute` | 店主 / 店员 | 对本人结算单提异议。Body `{reason*}`；仅 pending / approved 可提，成功后状态变 disputed，与平台侧 `dispute` 同一状态机；后续由平台 `resolve` 处理 |

### 结算单口径说明（§2.5）与导出

- `GET /admin/settlements/{id}` 与 `POST /admin/settlements/generate`
  的响应现在每行带 `basis_note`，结算单顶层带 `basis_explainer`（见下）。
- 结算单列表支持 `?format=csv`：`GET /admin/settlements?format=csv`、
  `GET /merchant/shops/{shop_id}/settlements?format=csv`、
  `GET /merchant/settlements?format=csv`；单张结算单的逐行明细：
  `GET /admin/settlements/{id}?format=csv`（含 `basis_note` 列）。

`basis_note` 取值（与 `settlement_basis()` 同分支）：

| 值 | 含义 | 金额 |
|---|---|---|
| `package_paid` | 套餐，按订单实付金额 | `Order.paid_amount` |
| `price_coupon` | 价格券，按券面优惠价 | `discount_price` |
| `cash_zero` | 现金券是折扣，不是平台代收 | 0 |
| `free_zero` | 免费 / 礼品 / 纯折扣 / 纯门槛券 | 0 |
| `refunded_skip` | 订单已退款，不结算 | 不进结算单（事后退款由对账标 `amount_mismatch`） |

`basis_explainer`（结算单顶层文案，前端可直接展示）：

> 结算基数只认实付金额：套餐按订单实付金额，价格券按券面优惠价；
> 现金券、免费券、纯门槛券不进结算（0 元）；已退款订单不结算。
> 佣金按商户生效费率从基数中扣除。

## 调度器（§2.3）

新增第 5 个任务 `monthly-settlements`：每月 1 日 02:00 UTC，为上月有核销
的每家门店自动生成结算单（与手动 `POST /admin/settlements/generate`
同一份逻辑；重复生成跳过，单店失败不阻塞其他店，下周期重试）。
运维手动跑：`python -m app.scheduler run-once --job monthly-settlements`。
详见 `docs/SCHEDULER.md`。

## 对账变化（提款相关）

商户部分提款后，对账不再按“打款行数”校验（旧 `payment_entry_count`，
部分打款会被误报），改为按金额校验：已付清的结算单打款总额须等于净额，
未付清的不得超付。类型名变为 `payment_amount_mismatch` /
`payment_over_settlement`。其余对账逻辑不变。

## 未做（留给后续块 / 待裁决）

- §2.7 佣金阶梯与活动费率（P2）：需费率规则表与 Miso 裁决阶梯口径，
  本分支未做；目前仍是每店一个费率。
- 提款到账通知：待 §1.5 商户消息中心（通知助手）落地后接入。
- 新老客 / 复购口径、评价趋势与词频：看板第二批。
