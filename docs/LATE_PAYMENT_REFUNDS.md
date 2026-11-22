# 迟到支付补偿

取消或超时后收到成功支付事件时，系统按支付尝试单独登记补偿退款；
不重新占用库存、不发券，也不修改该订单其他正常支付或退款。记录先
提交，再使用固定幂等键调用支付网关。超时或本地提交失败时保持待处理，
后续通知、定时任务或财务重试使用同一键。超过 23 小时仍无网关退款编号、
付款金额或币种不一致、网关返回失败等情况进入人工核对，禁止自动另发。

订单详情的 `late_payment_refunds` 展示金额、币种和状态。管理员或财务可用
`GET /api/v3/admin/late-payment-refunds` 查看列表，`POST /api/v3/admin/late-payment-refunds/{id}/retry`
重试待处理项，或在找到已存在的网关退款时调用
`POST /api/v3/admin/late-payment-refunds/{id}/verify?gateway_ref=...`；系统会向网关核验
支付编号、金额、币种和成功状态，才记为已退款。`manual_review` 不会因普通
重试重新打款，未找到网关记录时应先与支付服务商核对。

后台任务 `late-payment-refunds` 可通过 `python -m app.scheduler run-once --job late-payment-refunds`
手动执行；调度器关闭时必须由运维运行，待处理记录不会自行完成。网关为空
时的开发环境使用模拟支付，不能将其结果作为实际资金到账证明。
