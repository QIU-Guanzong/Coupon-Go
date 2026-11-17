# Coupon-Go 修复单（审计基线：main @ 2873027，69 pytest 通过）

合入顺序：FIX-1 → FIX-2 → FIX-3 → P1。每张单先写失败用例再修，修完全量 pytest 必须通过。

## FIX-1【P0】支付确认必须来自网关，不能来自买家
位置：`apps/api/app/routers/packages.py`（confirm-payment 约 425 行、webhook 约 599 行）、`payments_gateway.py`

问题
- 买家自己调 `/orders/{id}/confirm-payment` 并自带 `outcome=succeeded` 即可发券。
- `/payments/webhook` 不验签；`verify_webhook_signature` 已 import 但从未调用；`STRIPE_WEBHOOK_SECRET` 无人使用。
- 模拟器的 `pi_test_…` id 会返回给客户端，可被拿来伪造 webhook。

改法
1. 设有 `STRIPE_SECRET_KEY` 时：webhook 必须校验 `Stripe-Signature`（读 `config.STRIPE_WEBHOOK_SECRET`，带时间戳容差），验签失败返回 400；`confirm-payment` 只允许以 `payment_intent` 向网关查询状态后落账，不再信任客户端传入的 outcome。
2. 未设密钥（本地模拟）时：`confirm-payment` 的 outcome 仅在 `APP_ENV=dev` 开放；其他环境返回 403。webhook 在非 dev 且无 secret 时直接拒绝。
3. 启动检查：非 dev 环境缺 `STRIPE_WEBHOOK_SECRET` 则拒绝启动。

验收用例
- 买家带 `succeeded` 调 confirm-payment（非 dev）→ 403，订单仍 pending、未发券。
- 无签名 / 错签名 webhook → 400；正确签名 → 订单 paid 且仅发一张券（重放不重复发）。
- 同一事件重放 → 幂等返回。

## FIX-2【P0】返现入账只能来自核销记录
位置：`apps/api/app/routers/cashback.py`（`/cashback/record` 约 99 行）、`cashback_service.record_spend`

问题：任何登录用户可自填金额、商户、比例入账，再触发线下「下次消费解锁」并提现。与报告「追踪以核销记录为准」相悖。

改法
1. 删除用户可调的 `/cashback/record`；入账改为核销成功后在服务端内部调用 `record_spend`，金额取订单/券的实付价，比例取商户配置，不接受客户端传入。
2. `redemption_id` 改为必填，并校验：属于该用户、未撤销、同一 redemption 只能入账一次（唯一约束 `cashback_transactions.redemption_id`，需新增迁移）。
3. 若要保留商户 / POS 入账口，单独走 `require_role("vendor")` 并校验该商户与核销记录。
4. 线下解锁：只在「新的有效核销」发生时触发，不由用户请求触发。

验收用例
- 普通用户调旧 `/cashback/record` → 404 或 403。
- 无核销凭据无法产生返现；同一核销重复入账 → 拒绝。
- 用户自造两笔记账再提现 → 不可能（余额为 0）。
- 核销撤销后，关联返现自动作废（见 P1-1）。

## FIX-3【P0】资金路径加行锁与事务隔离
位置：`cashback_service.withdraw`、packages 的下单 / 退款 / 库存、bargain 助力与退款、`redemption.revoke_redemption`、核销扣减

问题：全仓库 `with_for_update` 为 0 处；并发提现、并发退款、库存超卖、重复核销撤销均可同时通过检查。

改法
1. 提现：先对用户行加 `with_for_update()`，再重算余额并扣减；同一事务内写 `CashbackWithdrawal`。
2. 退款：对订单行加锁，检查 `refund` 状态后再调网关，网关失败回滚；`Refund` 加 `(order_id, reason)` 唯一约束。
3. 库存与砍价：对 package / campaign 行加锁后扣减，并加 `CHECK (stock >= 0)`。
4. 撤销核销：对 `Redemption` 行加锁，再检查 `revoked_at`。
5. SQLite 测试环境下 `with_for_update` 为空操作，并发用例改用 Postgres（docker-compose 的 db）跑，或用线程 + 唯一约束断言。

验收用例：并发 2 路同额提现，仅 1 路成功；并发 2 路退款同一订单，仅 1 笔网关退款；并发抢最后 1 个库存，仅 1 单成功。

## P1（上线前）
1. 核销撤销时，同步把该 `redemption_id` 关联的返现作废，并回滚已解锁的前序交易。
2. `allow_origins=["*"]` 改为环境变量白名单；`SECRET_KEY` 缺省值在非 dev 环境启动即拒绝。
3. 砍价退款改为调用网关 `refund_payment`，与套餐退款同一口径；`BargainRefund` 记录网关 ref，失败可重试。
4. 提现户名校验：用户未填注册姓名时拒绝提现，不再跳过。
5. 返现 confirmed → withdrawable 目前同一次扫描完成，没有冷静期；上线前确认是否需要 hold，需你决定。

## 并线合入前补审
- F 后台 v2：合入远端后审结算 / 对账 / 发票 / 角色分权。
- 评价积分与星级是否拆开、README 是否写明默认假设：下一轮核对。
