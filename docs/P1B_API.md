# 商户开户与 Connect 结算

本集成只支持 Stripe 测试密钥和开发环境模拟器。模拟状态、测试账户及待法务确认的合同不能作为正式入驻批准依据。启用真实商户前，需完成正式合同与 Stripe 正式环境接入和验证。

申请人先提交商户入驻申请并签署当前合同，再调用：

- `POST /api/v3/merchant-applications/{id}/connect/onboarding`：生成托管开户链接。请求可携带 `return_url` 和 `refresh_url`；响应的 `simulated` 表示是否为模拟链接。
- `POST /api/v3/merchant-applications/{id}/connect/sync`：由后端读取账户状态，返回收款、提现、平台转账三个能力与模拟标记。返回页面本身不代表开户完成。

使用上述开户流程时，管理员批准前，后端重新核验三个能力和手动提款设置，并在同一事务中新建申请人的店铺、绑定 Connect 账户和更新申请状态。仅开发环境仍保留手工登记模拟状态的旧流程；它不会创建或验证真实 Stripe 账户。遇到已有店铺账户冲突时停止并要求核对，不自动猜测归属。旧商户仍可通过 `POST /api/v3/merchant/connect/onboarding` 开户。开户操作在请求前先持久化标识并复用同一幂等键；结果不明超过十分钟时停止自动重试，需核对。

每张已付款券以其冻结的实付分摊金额生成核销流水，按核销时记录的佣金率计算；结算单、返现和积分沿用同一分摊依据。核销事务只记录待处理转账，`connect-transfers` 定时任务在事务完成后发送。提款结果不明时保留待处理余额，不重复发送；需通过供应商记录核对后更新。撤销或退款采用持久冲回记录；结果不明时停止自动重发。

财务核对可用 `POST /api/v3/admin/connect/transfers/{id}/reconcile`，提交 Stripe 转账对象的 `provider_id`；未知冲回使用 `POST /api/v3/admin/connect/transfers/{id}/reversal/reconcile`，提交冲回对象的 `provider_id`。后端只读取供应商对象，并比对转账时保存的原商户账户、金额、币种和唯一操作标识；冲回还需匹配原转账及全额冲回。核对失败的流水仍保持待处理并记录原因，不会重发转账或冲回。没有原账户快照或唯一操作标识的历史记录不能通过此接口确认，需保留并人工核对；接口不会把“未找到对象”当作款项未发生。

当前固定的 Accounts v2 版本为 `2026-11-18.preview`，可用 `STRIPE_V2_VERSION` 配置。2026-11-22 核对时，Stripe 的[创建账户接口参考](https://docs.stripe.com/api/v2/core/accounts/create)示例使用 `2026-11-18.endive`，而 [Connect Accounts v2 指南](https://docs.stripe.com/connect/accounts-v2)示例仍使用 `2026-11-18.preview`。两个示例存在差异；切换版本之前需要用测试账户验证完整开户与结算行为。

## 接口与运维状态

接口均位于 `/api/v3`，使用现有登录凭证。申请人的开户入口为上述
`/merchant-applications/{id}/connect/onboarding` 与 `/connect/sync`；
已有店铺可使用 `/merchant/connect/onboarding`、`/merchant/connect/sync`，
请求含 `shop_id`，开户链接还需要返回和刷新地址（请求或服务端配置）。
商户可按店调用 `/merchant/connect/status`、`/merchant/connect/balance`、
`/merchant/connect/ledger`；余额和流水均以 Connect 记录为准。

`POST /merchant/connect/payouts` 要求 `shop_id`、正数 `amount`（最多两位小数）
和 16–64 位稳定 `request_id`，重试同一操作必须复用该编号。已提交或结果
不明的提款继续占用余额；`POST /admin/connect/payouts/{entry_id}/reconcile`
由后台提供供应商记录编号以核对。`GET /admin/connect/recoveries` 列出
负余额、待核对转账、待处理冲回和未完成提款。结果不明的转账与冲回
不会自动重复发送；财务须取得供应商对象编号后使用上方核对接口。
旧流水缺少原账户或找不到匹配对象时仍需人工处理，不能将其当作自动闭环。本地模拟供应商对象仅存在进程内，服务重启后不能用其完成跨重启核对；真实 Stripe 测试账户核对尚未实际联调。
`POST /merchant/connect/transfers/{entry_id}/retry`
仅适用于允许重试的状态，不会重发结果不明的转账。

调度器 `connect-transfers` 可使用配置的批次大小或 `run-once --batch-size`
执行；调度器关闭时会在状态原因中说明商户转账不会自动处理。原始
PR 中较早的接口说明及测试证据保留于 Git 历史和 `docs/evidence/`，
其中旧提款调用形式及未知结果处理已由本节和当前实现替代。
