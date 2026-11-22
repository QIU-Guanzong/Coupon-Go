# Stripe 测试付款联调

## 当前状态

公网 `/coupongo/` 是只读预览，`/coupongo-trial/` 是独立模拟体验；两者均不是 Stripe 测试环境。当前没有 Stripe 测试密钥，本文件中的 Stripe 端到端付款、退款和 webhook 场景尚未实测。模拟环境通过不代表 Stripe 测试通过。

当前接口只允许 `sk_test_` 和 `pk_test_`；只有同时配置 webhook 签名密钥才启用真实 Stripe 测试付款。开发环境中三项均为空时才启用本地模拟，配置不完整时付款入口不可用。真实 Stripe 测试仅支持卡付款，FPS 仍未开放。套餐与付费券共用订单支付和退款链路。支付成功必须由服务器向 Stripe 查询确认，或收到匹配金额及币种的成功 webhook 后才发券。

## 环境准备

1. 在独立的 Stripe 测试环境和独立测试数据库中，配置同一测试环境的 `STRIPE_SECRET_KEY`、`STRIPE_PUBLISHABLE_KEY` 和 webhook 签名密钥 `STRIPE_WEBHOOK_SECRET`。不要把密钥写进代码、日志或聊天记录。按部署环境的密钥管理方式注入，不改公网只读预览或模拟体验的配置。
2. 在测试环境暴露可接收 Stripe 请求的 HTTPS 业务接口，webhook 指向该环境的 `/api/v3/payments/webhook`（若有网关前缀，以实际转发路径为准）。至少订阅 `payment_intent.succeeded`、`payment_intent.payment_failed` 和 `payment_intent.canceled`。签名密钥必须与该 webhook 端点匹配。
3. 验证 `/api/v3/payment-options` 返回 `stripe_test`、`card: true`、`fps: false`。确保支付界面有明确的测试付款标识，只使用 Stripe 提供的测试卡信息。

## 拿到密钥后需要实测

- 分别购买套餐和付费券：核对金额、币种、PaymentIntent、订单、钱包券和库存。实际扣款成功后，即使用户没有回到网页，也要通过 webhook 核对只发券一次。
- 先用测试拒付卡触发失败，再在相同付款表单中使用成功测试卡重试：第一次失败后订单仍待支付、库存仍被占用；成功后只生成一张券。重复发送或乱序发送回调，不应重复发券或释放库存。
- 测试 Stripe 的明确取消、用户取消以及支付超时。取消或超时后库存只恢复一次；如果付款成功通知晚于本地取消或超时，核对补偿退款、订单、券和库存，不得悄悄复活订单。
- 测试 3D Secure、网络中断和关闭付款页后重新查询；以服务端状态为准，核对订单与 Stripe 测试仪表板的结果。
- 完成核销、撤销和退款，核对测试退款状态、商家结算与对账流水。Connect 的真实 Stripe 测试开户和转账需要另外配置并验证，不能由本地模拟代替。
- 记录各场景的测试环境、订单编号、事件编号、时间和结果；不要记录卡资料、密钥、访问令牌或 webhook 签名内容。

## 回调语义依据

- Stripe 的 `payment_intent.payment_failed` 表示一次创建支付方式或付款尝试失败，不代表该 PaymentIntent 一定终结。付款失败后它可以回到 `requires_payment_method`，再用同一 PaymentIntent 重试：[PaymentIntent 生命周期](https://docs.stripe.com/payments/paymentintents/lifecycle)、[事件类型](https://docs.stripe.com/api/events/types)。
- `payment_intent.canceled` 表示 PaymentIntent 已取消，不可再用于付款；Stripe 不保证事件投递顺序，因此处理时不能让迟到的失败覆盖已成功订单：[事件类型](https://docs.stripe.com/api/events/types)、[Webhook 事件顺序](https://docs.stripe.com/webhooks#event-ordering)。
- Stripe 提供测试用卡号和场景，详见 [测试文档](https://docs.stripe.com/testing)。
