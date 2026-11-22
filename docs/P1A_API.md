# P1-A 付费券 — 接口清单（供 Dot 并行对接）

基线：`main @ 3f2d8bc`（2026-11-21，rebase 后；原 c1f3bef 已合入 #7/#8/#9）。规格：`docs/FEATURE_GAP_20261120.md` §3 决策 1 —
免费券与付费券并存，商户建券时选「免费」或填售价。全部接口前缀 `/api/v3`。

## 决策：Order 加 coupon 类型（路线 b）

不走 Package（路线 a）。理由：

1. §3 已定「售价是建券时填的字段」——价格属于券本身，不是另一个商品；
   走 Package 会为每张付费券造一条假套餐（人数/菜品/套餐语义全是错的），
   还会污染 `/packages` 列表和套餐券模板（`kind='package'`）。
2. 订单/支付/退款/超时/回调本来就是订单通用链路，只有少数 helper 认
   `package_id`；加一个可空 `orders.coupon_id` + `order_type='coupon'`
   就能复用整条链路，改动最小。
3. 结算零改动：钱包券关联订单后，`settlement_basis()` 直接返回
   `order.paid_amount`；已退款订单返回 `None`（不结算）。核销路径
   （`ensure_paid_voucher`）本来就认 `source='purchase'` 的券。

## 字段

| 位置 | 字段 | 说明 |
|---|---|---|
| `coupons.sale_price`（新增，可空） | `number \| null` | 售价（HKD）。`null` 或 `0` = 免费券，行为不变；`> 0` = 付费券，只能购买，不可免费领 |
| `coupons.refund_policy`（建券时可填） | `anytime \| expiry_only \| non_refundable` | 默认 `anytime`；与套餐同一口径 |
| `orders.order_type` | `package \| coupon` | 新增 `coupon` |
| `orders.coupon_id`（新增，可空） | `int \| null` | 付费券订单关联的券 |
| `coupon_out` / `wallet_out` | `sale_price`、`is_paid` | 新增返回字段，客户端据此显示「购买」还是「领取」 |
| `order_out` | `coupon_id`、`coupon_name` | 新增；套餐订单仍返回 `package_id` / `package_name` |

## 接口

### 1. 商户建券（改动：接受两个新字段）

```
POST /coupon-previewed
Authorization: Bearer <vendor>
{
  "shop_id": 1, "coupon_type": "Price", "coupon_name": "午市 8 折券",
  "discount_price": 80, "regular_price": 100,
  "valid_from": "2026-11-20T00:00:00", "valid_to": "2026-12-31T00:00:00",
  "total": 100,
  "sale_price": 20,            // 不填或 0 = 免费券；> 0 = 付费券，售价 HK$20
  "refund_policy": "anytime"  // anytime | expiry_only | non_refundable，默认 anytime
}
→ 201 { "message": "內容將提交審核，請耐心等待。", "coupon": { ..., "sale_price": 20, "is_paid": true } }
```

- 校验：`sale_price >= 0`；`refund_policy` 非法 → 400。
- 建券后状态 `pending_review`，须管理员 `POST /vendor/coupons/{id}/approve`
  通过（`status=active`）才能购买或领取（流程不变）。
- `POST /add-coupon-preview` 同样接受这两个字段（预览响应不变）。

### 2. 购买付费券（新增）

```
POST /coupons/{coupon_id}/purchase
Authorization: Bearer <customer>
{ "payment_method": "fps", "client_ref": "app-uuid-1" }
→ 200 {
  "order": { "id": 101, "order_type": "coupon", "coupon_id": 12, "coupon_name": "午市 8 折券",
             "amount": 20, "paid_amount": 0, "payment_method": "fps",
             "payment_status": "pending", "refund_status": "none",
             "settlement_status": "pending", "commission_amount": 0, "net_amount": 0, ... },
  "payment": { "id": 55, "order_id": 101, "gateway": "stripe", "gateway_ref": "",
               "method": "fps", "amount": 20, "currency": "HKD", "status": "requires_payment" },
  "client_secret": "cs_test_...",
  "gateway": "stripe",
  "message": "訂單已建立，請完成支付"
}
```

- 下单即预留库存（`coupons.remaining - 1`）；支付失败 / 超时 / 取消 / 退款均释放。
- `quantity`：付费券每次限购 1 张，传 `> 1` → 400。想买多张请多次下单
  （每单一张券，钱包里每张券独立核销/退款；与套餐多份不同）。
- `client_ref` 幂等：同一用户 + 同一券 + 同一 `client_ref` 重复下单返回原订单，
  不重复扣库存（与套餐同一口径；份数或付款方式不同 → 409）。
- 400 情形：免费券（`sale_price` 为空或 0，请走领取）、券未上架/不在有效期、
  已售罄（`remaining = 0`）、`kind=package` 的套餐券模板（请走套餐购买）、
  支付方式不是 `fps` / `card`。

### 3. 确认支付 / 模拟支付（不变，付费券订单同样适用）

```
POST /orders/{id}/confirm-payment
{ "outcome": "succeeded" }        // 仅 dev 无 Stripe key 时接受客户端结果；FIX-1 不变
→ 200 { "order": { ..., "payment_status": "paid", "paid_amount": 20,
                   "wallet_item_id": 201, ... },
        "message": "支付成功，套餐券已存入 Q Wallet" }
```

- 支付成功后券进入 Q Wallet：`source=purchase`、`order_id` 关联订单、`status=1` 未使用。
  文案里的「套餐券」指代购买所得的券，含义不变。
- `POST /orders/{id}/demo-payment`（仅 dev 模拟）同样适用。

### 4. 重试 / 取消（不变，付费券订单同样适用）

```
POST /orders/{id}/retry-payment   { "payment_method": "card" }   // 仅 failed / timeout 可重试
POST /orders/{id}/cancel                                       // 仅 pending 可取消，释放库存
```

### 5. 订单查询（不变，返回新增 `coupon_id` / `coupon_name`）

```
GET /orders
GET /orders/{id}
→ { "order": { "id": 101, "order_type": "coupon", "coupon_id": 12,
               "coupon_name": "午市 8 折券", "shop_name": "…",
               "amount": 20, "paid_amount": 20, "payment_status": "paid",
               "refund_status": "none", "refundable": true, "wallet_item_id": 201,
               "payments": [ ... ], ... } }
```

- 超时订单在查询时懒处理：待支付超过 `PAYMENT_TIMEOUT_MINUTES`（默认 15 分钟）
  → `timeout` 并释放库存（与套餐同一口径）。

### 6. 退款（不变的路径 + 新增的按券路径）

```
POST /orders/{id}/refund
{ "reason": "行程有變" }
→ 200 { "refund": { "id": 9, "order_id": 101, "amount": 20, "status": "refunded",
                    "gateway_ref": "re_test_...", "expected_arrival": "原路返回，預計 1–3 個工作日到賬" },
        "order": { ..., "refund_status": "refunded", "settlement_status": "void" },
        "message": "退款成功，套餐券已作廢" }

POST /coupons/{coupon_id}/refund        // 新增：退该券最近一笔可退订单（与 /packages/{id}/refund 对称）
{ "reason": "不想去了" }
→ 200 同上；无可退订单 → 404
```

- 规则与套餐完全一致：仅已支付、未核销的券可退；`non_refundable` → 400；
  `expiry_only` 仅过期自动退，过期前手动退 → 400；已核销 → 400；
  退款成功后券作废（钱包 `status=3`）、库存释放、结算作废（`settlement_status=void`）。
- 过期自动退：`POST /orders/process-expired-refunds`（仅 admin，后台定时任务同逻辑）
  同样覆盖过期的付费券订单。

### 7. Stripe 回调（不变，FIX-1 不变）

```
POST /payments/webhook
{ "type": "payment_intent.succeeded", "data": { "object": { "id": "pi_..." } } }
→ 200 { "received": true, "order_id": 101, "outcome": "succeeded" }
```

- 重放同一事件：订单已结算 → `200 { "received": true, "ignored": "order already settled" }`，
  不重复发券（发券本身也幂等）。

### 8. 免费领取（加拦截）

```
POST /new-favorite-coupon?coupon_id=12
→ 400 "付费券请购买，不可免费领取"        // 仅当 sale_price > 0；免费券行为不变
```

### 9. 钱包（返回新增字段）

```
GET /get-favorite-coupons-list?status=1
→ { "items": [ { "id": 201, "coupon_id": 12, "coupon_name": "午市 8 折券",
                 "source": "purchase", "order_id": 101, "status": 1,
                 "sale_price": 20, "is_paid": true, ... } ] }
```

- 购买所得的券与免费领的券在钱包里同一列表，`source` 区分；核销（扫码 / 手输码）
  流程不变；已退款/未支付订单的券不可核销。

## 不在 P1-A 范围（后续阶段）

- 积分按消费金额发放（P1-C）：购买现阶段不发积分，核销仍按现有 `+20`；
  退款扣回积分随 P1-C 一起做。
- Stripe Connect 商户分账 / 商户余额 / 提款（P1-B）。
- 券转赠（阶段 3）：付费券与免费券一样，每张限转 1 次——转赠接口未做，
  本阶段购买不设转赠字段。
- 正式 Stripe 密钥接入：仍走测试模式网关；`FIX-1` 验签逻辑不动。
