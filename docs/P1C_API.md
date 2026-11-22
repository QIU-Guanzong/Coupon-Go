# P1-C 积分按消费发放 — 接口清单（供 Dot 并行对接）

基线：main @ `c1f3bef`（2026-11-20；原 `7e9c4f9`，rebase 时 M9-6 /app-config 与 M9-3/4/5 已上 main）。分支：`stage1/p1-c-points-by-spend`。
规格来源：`docs/FEATURE_GAP_20261120.md` §3 已定决策第 4 条（积分规则）。
本文档只写接口与规则，不另定规则。

## 积分规则（数值全部放 env，可改不用改代码）

| 事件 | 得分 | 说明 |
|---|---|---|
| 免费券领券 | `POINTS_CLAIM`（默认 +10） | 不变 |
| 免费券核销 | `POINTS_REDEEM`（默认 +20） | 不变 |
| 付费券（套餐及付费券订单）核销 | `floor(该券冻结实付分摊 × POINTS_PER_HKD)` | 单券代表整单时取整单实付，多券时取该券分摊；每券独立向下取整。 |
| 评价 | `POINTS_REVIEW`（默认 +15） | 不变 |
| 积分过期 | 每笔积分自获得起 `POINTS_EXPIRE_MONTHS`（默认 12）个月有效 | 过期由 scheduler 任务 `points-expire` 清掉并写 `reason=expire` 流水 |
| 退款 | 扣回该订单核销时发的积分 | 余额不足可为负；扣回写 `reason=refund_clawback` 流水，`ref_id=订单 id`；同一订单只扣一次 |
| 负数余额 | 禁止兑换和转出 | 兑换返回 400（见下）；转出接口尚不存在（阶段 3 才做），新建时必须复用同一余额检查 |

范围说明：
- 「付费券」指带 `Order` 的套餐券（`WalletItem.order_id` 非空）。砍价券目前没有 `Order`（模拟预付在 `BargainSession` 上），核销仍按免费券 +20，直到砍价走真实订单后再议——不在本 PR 改。
- 核销撤销（`POST /redemptions/{id}/revoke`）本来就是按账本实际发放额冲回，付费券撤销自动冲回按消费发的分数，无需 Dot 改动。
- Q商城首期只做电子礼券（兑换后进 Q Wallet），不寄实物、不收地址；本 PR 不改商城目录，只做积分侧规则。

## 配置（env）

| 变量 | 默认 | 说明 |
|---|---|---|
| `POINTS_PER_HKD` | `1` | 付费券核销：每 HK$ 实付得几分 |
| `POINTS_EXPIRE_MONTHS` | `12` | 每笔积分有效月数 |
| `POINTS_EXPIRE_SOON_DAYS` | `30` | `/points` 里「即将过期」的窗口（天） |
| `POINTS_CLAIM` / `POINTS_REDEEM` / `POINTS_REVIEW` | `10` / `20` / `15` | 已有，不变 |

`/app-config` 两个新字段：已在本 PR 加进 M9-6 的接口（`GET /api/v3/app-config`，
`apps/api/app/routers/home.py`，公开接口，只放展示规则）：`points_per_hkd`
（默认 1）与 `points_expire_months`（默认 12）；`test_m96_app_config.py`
的期望值已同步更新。未自创第二个同名接口。

## 接口

### GET `/api/v3/points`（已有，返回加字段）

请求：Bearer token，无参数。

响应（新增 `expiring_soon`，其余不变）：

```json
{
  "balance": 141,
  "expiring_soon": 30,
  "transactions": [
    {"id": 12, "delta": 150, "reason": "redeem", "created_at": "2026-11-20T06:00:00", "expires_at": "2027-11-20T06:00:00"},
    {"id": 11, "delta": 10, "reason": "claim", "created_at": "2026-11-20T05:00:00", "expires_at": "2027-11-20T05:00:00"},
    {"id": 10, "delta": -9, "reason": "exchange", "created_at": "2026-11-20T06:30:00", "expires_at": null}
  ]
}
```

- `balance`：可用分数（全部流水求和；退款扣回后可为负）。
- `expiring_soon`：未来 `POINTS_EXPIRE_SOON_DAYS`（默认 30）天内到期、且尚未被消费/扣回的分数。按先进先出（最早获得的积分先被消费/扣回）计算。
- `transactions`：最新 50 条；每条新增 `expires_at`（正向发放有值；消费/扣回/过期冲回为 `null`）。
- 流水 `reason` 新增：`expire`（过期冲回）、`refund_clawback`（退款扣回）。原有 `claim` / `redeem` / `review` / `exchange` / `revoke` 不变。

### POST `/api/v3/points/exchange?coupon_id=`（已有，加负数检查）

请求、成功响应不变（9 分兑换 9 分分券，进 Q Wallet）。

新增错误（余额为负时，优先于「积分不足」返回）：

```json
// HTTP 400
{"detail": "積分餘額為負數，退款扣回後請先累積積分再兌換"}
```

余额 ≥ 0 但不足 9 分时仍返回原 400「積分不足」。

### 调度器：新增任务 `points-expire`（后端内部，Dot 无需对接）

- 每 `SCHEDULER_INTERVAL_SECONDS`（默认 300 秒）执行一次，与其余 4 个任务共用同一套锁/单实例机制。
- 过期一笔写一条 `reason=expire` 流水（幂等，重跑不重复扣）。
- 过期流水按 `ref_id` 指向的积分批次扣除；兑换、退款扣回和过期任务共用顾客事务锁。已花尽的到期批次记录在独立处理标记表，不生成零金额积分流水，后续周期不再扫描这些批次。
- 前向迁移新增到期查询索引及处理标记表；不更改已有积分流水。队列整合时需与其他迁移的 revision 编号协调。
- 运维手动跑：`python -m app.scheduler run-once --job points-expire`。

## 前端对接要点（Dot）

1. `/points` 多了 `expiring_soon` 与每条的 `expires_at`：积分页可显示「30 天内过期 X 分」；旧客户端忽略新字段即可。
2. 兑换失败新增一种 400 文案（余额为负），直接展示 `detail` 即可。
3. 付费券核销后积分不再是固定 +20：核销成功页不要写死「+20 分」，用 `/points` 的 `balance` 或流水 `delta` 显示实际得分。
4. 余额可能为负：积分页余额按数字原样显示，不要当成 0 或隐藏。
