# 商户核销补录与批量处理

接口前缀 `/api/v3`。扫码、手输、补录和批量核销使用同一笔券的付款校验、
实付分摊、积分、返现、商户结算和 Connect 转账记录；并不在请求内调用
Stripe 转账。涉及多张券时按顾客、订单、券、店铺的固定顺序锁定，批量
在一个事务内全部成功或全部回滚。

- `GET /merchant/redemptions/lookup?phone=&code_suffix=&order_id=`：至少
  提供一项；仅返回当前商户可处理的券、顾客手机号后四位及券信息，不返回
  完整手机号或完整兑换码。
- `POST /merchant/redemptions/supplementary-scan`：传入 `wallet_item_id`，
  可选 `idempotency_key`、`reason`、`note`；补录使用现有核销核心并记录
  操作者及原因。同一请求编号重放返回原记录，更换券或撤销后不能用旧编号。
- `POST /admin/redemptions/{id}/force-revoke`：管理员或客服填写 `reason`
  后可处理超出时限的核销；与普通撤销共用券、积分、返现、结算和冲回流程，
  原因与操作人员写入同一事务的记录。
- `POST /new-scan-coupon/batch`：`codes` 1–20 项、`idempotency_key`
  必填，可传逐项 `prepare_tokens` 和 `note`。成功只提交一次；相同编号及
  内容重放返回原记录，改动内容或原核销已撤销时返回 409。失败不消费任何券。
- `POST /new-scan-coupon` 可选 `method=scan|manual`；
  `GET /vendor/redemptions/export?method=` 支持按核销方式筛选和 CSV 导出。

原迁移 `0021_dianping_redemption` 保留历史父版本；前向桥接
`0030_redemption_batch_bridge` 合并它与现行 0029，并保存批次请求的摘要及
结果编号，不保存顾客出示的兑换码。旧核销的金额和佣金快照保持原样。
