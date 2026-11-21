# M1 后台定时任务：过期退款 / 砍价结算 / 超时关闭 / 返现 sweep / Connect 转帐

## 问题

套餐「过期自动退款」、砍价「时间到差额自动退回、未成团全额退款」是界面向用户
承诺的文案，但此前代码里没有任何调度器：`POST /orders/process-expired-refunds`
和 `POST /bargain/expire-sweep` 只能管理员手动调用；pending 订单 15 分钟关闭、
返现 90 天作废也只在用户打开页面时顺带触发。

## 方案：进程内 APScheduler + run-once CLI

- **进程内调度器**：`app/scheduler.py`，FastAPI 启动时（`app/main.py` startup）
启动 `BackgroundScheduler`，每 `SCHEDULER_INTERVAL_SECONDS`（默认 300 秒）
执行 4 个间隔任务，另有 1 个月度任务（每月 1 日 02:00 UTC），各自独立的锁、会话与失败日志。
- **run-once CLI**（运维 / cron / 测试手动跑，不受 `SCHEDULER_ENABLED` 开关影响）：
```bash
cd apps/api
python -m app.scheduler run-once [--job expired-packages|bargain-expire|payment-timeouts|cashback-sweep|monthly-settlements|all] [--batch-size N]
python -m app.scheduler run # 前台常驻调度器
python -m app.scheduler status # 打印当前调度配置
```

## 6 个任务（与手动接口共用同一份逻辑）

| 任务 | 共用逻辑 | 幂等保证 |
|---|---|---|
| `expired-packages` 过期套餐退款 | `routers/packages.py: sweep_expired_packages()`，手动接口 `POST /orders/process-expired-refunds` 同调它 | 已退款订单 `refund_status='refunded'` 不再入选；并发二次退款撞 `(order_id, reason)` UNIQUE 计 skipped |
| `bargain-expire` 砍价到期结算 | `routers/bargain.py: sweep_expired_bargains()`，手动接口 `POST /bargain/expire-sweep` 同调它 | `settle_session` 只迁移 active/success 会话；`_add_refund` 每个 (session, reason) 只建一行 |
| `payment-timeouts` 超时 pending 关闭 | `routers/packages.py: sweep_payment_timeouts()`（与读订单时懒触发的 `_refresh_payment_timeout` 同逻辑） | 超时后订单不再是 pending，不再入选；库存按份数释放 |
| `cashback-sweep` 返现 sweep | `cashback_service.py: sweep_all_users()`（全体用户，按 user id 游标分批，默认每批 100 人；单用户逻辑仍是 `sweep_user()`） | 已确认/已作废的交易不再是 pending，不再入选 |
| `coupon-expiry` 券到期提醒与自动过期（dianping/unify） | `routers/coupons.py: sweep_coupon_expiry()`，手动接口 `POST /admin/coupon-expiry-sweep` 同调它 | 同一（用户，券）24 小时内只提醒一次；过期翻转只对 `status == 1` 的行生效 |
| `monthly-settlements` 月结算单自动生成（Dianping §2.3） | `merchant_finance.py: sweep_monthly_settlements()`，手动接口 `POST /admin/settlements/generate` 同调 `generate_settlement_for_shop()` | 同一商户同一周期重复生成撞 `uq_settlement_shop_period` 计 skipped；单店失败不阻塞其他店，下一周期重试 |

`monthly-settlements` 是唯一不走间隔触发的任务：每月 1 日 02:00 UTC
（CronTrigger，misfire 宽限 1 小时），为上月有核销的每家门店生成结算单。
它不在 `run_all_jobs()` / `run-once --job all` 里（那是 5 个间隔任务），
手动跑用 `python -m app.scheduler run-once --job monthly-settlements`。
| `connect-transfers` Connect 转帐（P1-B 队列 §F 第 3 项） | `connect_service.py: sweep_pending_transfers()`（`pending` 流水按 id 顺序，每批上限 `SCHEDULER_CONNECT_BATCH_SIZE` 默认 50 条；手动接口 `POST /merchant/connect/transfers/{id}/retry` 同调 `_attempt_transfer`） | 已转帐 / 已冲回 / 已 `needs_manual` 的流水不再入选；每条带幂等键 `transfer-{entry_id}`，重试不重复转帐 |

单条失败不影响其他：每个 sweep 按条 try/except，失败写日志（`logger.exception`）、
回滚该条、计入 `failed`，下一周期重试。网关失败的砍价退款仍保持 failed 状态，
可走原有的 `POST /bargain/refunds/{id}/retry` 重试同一行。

## 多实例只执行一次

- **Postgres**：每任务一个固定 advisory lock key（`2026111701`–`2026111706`），
`pg_try_advisory_lock` 抢不到的实例直接跳过（结果 `skipped: true`），
锁随会话结束自动释放。
  > 注意：第 2 块的 `monthly-settlements` 与第 3 块的 `listing-schedule`
  > 都用了 `2026111705`（撞号，待裁决）；本片的 `coupon-expiry` 取了下一个空号
  > `2026111706`。
- **其他数据库（SQLite 等）**：抢 `scheduler_locks` 表的一行
（`INSERT... ON CONFLICT DO NOTHING` + 租约过期回收，默认租约
`SCHEDULER_LOCK_LEASE_SECONDS=600` 秒；迁移 `0013_m1_scheduler_locks`）。
- 同一进程内还另有 `max_instances=1` + `coalesce=True`，慢任务不自我重叠。

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `SCHEDULER_ENABLED` | dev 默认开；非 dev 未设置 = 关 | `1` 开 / `0` 关；未设置时非 dev 启动打明确警告，绝不静默不跑 |
| `SCHEDULER_INTERVAL_SECONDS` | `300` | 执行周期（秒） |
| `SCHEDULER_CASHBACK_BATCH_SIZE` | `100` | 返现 sweep 每批用户数 |
| `SCHEDULER_CONNECT_BATCH_SIZE` | `50` | `connect-transfers` 每批转帐条数上限 |
| `SCHEDULER_LOCK_LEASE_SECONDS` | `600` | 非 Postgres 锁行租约（秒） |

**非 dev 环境缺调度配置时**：启动日志出现明确警告
（`...` + 原因），且 `GET /health` 的 `scheduler` 字段
如实报告 `enabled: false` 及原因。部署前必须设置 `SCHEDULER_ENABLED=1`。

## 部署方式

推荐：**进程内调度器随 API 进程一起跑**（默认）。多实例部署无需额外协调，
advisory lock / 锁行保证只有一个实例实际执行；单实例部署也一样工作。
替代方案：把所有实例的 `SCHEDULER_ENABLED=0`，改由 cron 每 5 分钟调一次
`python -m app.scheduler run-once`（同一把锁保护，两种方式可混用但不要
同时开两个 cron）。不要两个实例一个跑进程内、一个跑 cron 去抢同一任务之外的
事——锁只保证同一任务单跑，不保证跨任务顺序，任务之间本就相互独立。

## 监控

- 每次任务完成打一条 INFO 日志（任务名 + 计数结果）；跳过（被别的实例抢到）
打 INFO；单条失败打 ERROR（含订单/会话/用户 id，可追踪重试）。
- 手动补跑仍走原管理接口（需 admin），返回里新增 `failed` / `skipped` 计数，
其余字段不变。

## 测试

`tests/test_m1_scheduler.py`（12 条）：四项 sweep 各跑一次且只生效一次、
1 秒间隔调度器自动跑、两个真实子进程同时 `run-once` 只退一次、
锁被占时跳过、单条失败不阻塞其他且下一周期重试、手动接口与调度器共用逻辑、
非 dev 未配置警告、Postgres advisory lock 分支（stub 会话）。
`tests/test_dianping_stats.py` 另覆盖月度任务：上月有核销的两家店各生成
一张、重跑零生成、调度器注册了 `monthly-settlements`（下次运行为 1 日 02:00）。

`tests/test_p1b_connect_fixes.py`（P1-B 队列 §F）：核销不调 Stripe、
待结算经 sweep 转帐、批量上限、同一幂等键重试只转一笔、失败 5 次进
`needs_manual` 并通知 admin、提款先写 `processing` 再调 Stripe、
提款失败变 `failed` 释放余额、负余额停提款并上 admin 看板、冲回失败
计次并通知 admin、Pydantic 校验、Stripe v2 版本走配置；两个并发测试
（同一商户两次提款、同一流水两次重试）只在真实 Postgres 上跑
（`pg-run/p1b_pg_run.py`，pgserver），SQLite 下跳过。
