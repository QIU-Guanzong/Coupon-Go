# 统一功能（对标大众点评 · 第 5 块，供 Dot 对接）

- 基线：远端 main `3f2d8bc`；分支 `dianping/unify`；**无迁移**。
- 范围：`GAP_ANALYSIS_20261121.md` 第 5 节中属于“收敛”的部分——把券 /
  套餐 / 砍价的购买、核销、退款、统计口径收到同一套共享函数与同一套
  API 风格，去掉重复逻辑。§5.3 转赠、§5.4 印花卡 / 刮刮乐、§5.5 好友
  推荐、§5.7 商户申诉按 `FEATURE_GAP` §5 排在阶段 2 / 3，本片不做；
  §5.1 按消费积分（P1-C）与 §5.8 M9-7 真删号在途，本片只写衔接点；
  §5.2 积分商城见末节“留待后续”。
- Base URL 与鉴权与 `docs/API.md` 相同：路径前缀 `/api/v3`，
  `Authorization: Bearer <JWT>`。

## 1. 收敛的共享函数（只读这一节就知道调谁）

| 共享入口 | 位置 | 替代掉的重复实现 | 调用方 |
|---|---|---|---|
| `utcnow()`（唯一的钟） | `app/timeutil.py` | 18 个一模一样的 `_now()` 定义（routers × 10、services / models × 8） | 全部模块以 `from ..timeutil import utcnow as _now` 引用，调用点不动 |
| `settlement_basis()`（唯一的结算口径） | `app/settlement_basis.py`（M6 已有，本片不动） | ——（本片只确认：核销快照、结算生成、对账三处仍调它，没有第四套口径） | `coupons._redeem_item`、 `admin_v2.generate_settlement` / 对账 |
| `_redeem_item()`（唯一的消费链） | `app/routers/coupons.py` | `scan_coupon` 里内联的消费块；补核销 / 批量核销（第 4 块）直接调它，不再各写一套 | `scan_coupon`（薄 wrapper：只做幂等重放查找 + prepare token 校验）；第 4 块的补核销 / 批量是它的下一批调用方 |
| `award_claim_points` / `award_redeem_points` / `award_review_points` / `spend_points` / `clawback_points`（唯一的积分政策层） | `app/points.py`（底层写账仍是 `deps.add_points`） | 领券 +10、核销 +20、评价 +15 的三处内联 `add_points`；撤销时两处“找最近一笔 award 再取负”的重复查找（核销撤销、评价隐藏） | `claim_coupon`、 `_redeem_item`、 `create_review`、 legacy `/review`、 `wallet.exchange_points`、 `redemption.revoke_redemption`、 `reviews.hide_reviews_for_revoked_redemption` |
| `issue_wallet_item()`（唯一的发券入口） | `app/shared.py` | 免费领券、套餐 `_issue_voucher`、砍价购买、积分兑换四处内联的 `WalletItem(...)` 构造 | `claim_coupon`（`claim`）、 `packages._issue_voucher`（`purchase`）、 `bargain.purchase_bargain`（`bargain`）、 `wallet.exchange_points`（`exchange`） |
| `vendor_shop_ids()`（商户名下门店） | `app/shared.py` | `coupons` / `vendor` / `admin_v2` 里的内联列表推导、`redemption._vendor_shop_ids` 私有副本 | `scan` 商户校验、 `usage_data`、 `vendor_records`、 `redemption` 三处、 `admin_v2._merchant_admin_shop_ids`（店员店 + 自有店并集） |
| `paginate()`（唯一的分页包络） | `app/shared.py` | `admin._page` 与 `admin_v2._page` 两个字节相同的副本；钱包与评价列表各手写一份同样的切片 | 全部列表端点（见 §3） |
| `money()`（唯一的 JSON 金额风格） | `app/shared.py` | `admin_v2._money`（float 直转）与 `bargain._money`（Decimal 定点）两种拼法 | `admin_v2` 全部金额字段（`_money` 改为它的别名）；新接口一律用它 |
| `notify()`（唯一的站内信写入） | `app/notify.py`（自第 1 块原样采用） | 各处内联的 `Notification(...)` 构造 | 券到期提醒；第 1 块的入驻 / 审核 / 结算通知复用它 |
| `audit_log()`（同一事务内的审计行） | `app/shared.py` | `admin._log` 与 `admin_v2._log` 两个相同实现 | `admin` / `admin_v2` 全部 `_log` 调用点（别名引用，调用点不动） |
| `csv_response()`（唯一的 CSV 导出） | `app/shared.py` | 核销导出的手写 `",".join`（券名含逗号即错列）；`/admin/redemptions` 另写一份 `csv.writer` | 全部 `?format=csv` 出口（见 §4） |
| `live_status()` / `approve_status()` / `sync_package_coupon_status()` / `sweep_listing_schedule()` / `REFUND_POLICIES` | `app/listing.py`（自第 3 块采用，只改两行：`_now` 改引 `timeutil`，`REFUND_POLICIES` 改引 `models_packages`——它是三处重复元组的唯一来源，另两处在 `models_packages.py` 与已删的 `packages.py` 本地定义） | 套餐 `_sync_coupon_stock` 里的模板券状态同步、散落的 `active / scheduled / expired / sold_out` 判定 | 上架块的审批 / 编辑 / sweep；本片只采用模块，不新增上架端点 |
| `coupon_usage_stats()`（每券领 / 用 / 剩） | `app/routers/coupons.py` | ——（`usage_data` 内联构造；统计块的看板按券转化行应复用它，不再重算） | `usage_data`；统计块看板在 rebase 时接入 |

### 删掉的重复逻辑（删前已确认无其他调用方）

- `admin._page`、`admin_v2._page`（两份相同实现）→ `shared.paginate`。
- `admin._log`、`admin_v2._log`（两份相同实现）→ `shared.audit_log`。
- `redemption._vendor_shop_ids`（私有副本）→ `shared.vendor_shop_ids`。
- `packages.REFUND_POLICIES`（与 `models_packages.REFUND_POLICIES`
  相同的元组）→ 直接引 `models_packages` 的；`listing.py` 同样改引它。
- 18 个 `_now()` 定义 → `timeutil.utcnow`（各文件以 `utcnow as _now`
  引用，含 `models.py` 等用作列 `default` 的）。
- `coupons.scan_coupon` 内联消费块 → `_redeem_item`（`scan_coupon`
  只剩重放查找 + prepare token 校验 + 响应组装）。
- 撤销与评价隐藏里两份“找最近 award 再取负” → `points.clawback_points`。
- 四处内联 `WalletItem(...)` 构造 → `shared.issue_wallet_item`。
- 核销导出手写 `",".join` → `shared.csv_response`。
- 钱包列表与评价列表手写分页切片 → `shared.paginate`。

### 行为变化（有意的两处，其余一律不动）

1. 积分兑换的券现在 `source="exchange"`：以前兑换走列默认值误记为
   `claim`（模型注释写明 `source` 应为 `claim | purchase | bargain |
   exchange`）。没有任何逻辑按 `source == "claim"` 分支（只有
   `purchase` 被 `ensure_paid_voucher` 使用），所以只影响钱包明细的
   来源标注。
2. 金额字段统一为 2 位小数 HALF_UP 的 float：结算金额本身已是按分
   定点的 Decimal，输出不变；唯一理论变化是佣金率（不是金额）若配了
   超过 2 位小数的值，展示时会按 2 位取整——费率配置目前均为 2 位。

## 2. 错误码（统一风格）

业务错误 body 在 FastAPI 原有 `{"detail": "<中文文案>"}` 之上**只加
一个字段** `code`：`{"detail": "...", "code": "coupon_not_found"}`。
老客户端按 `detail` / 状态码匹配不受影响；新客户端按 `code` 分支。
`code` 与文案的对应表在 `app/errors.py`（`ApiError` + 工厂函数，
`main.py` 注册了处理器）。已迁移的面：领券 / 出示码 / 核销 / 撤销 /
钱包 / 积分兑换（`coupons.py`、`redemption.py`、`wallet.py`）。套餐
购买 / 退款与砍价（`packages.py`、`bargain.py`）仍用 plain
`HTTPException`，文案不变，按同一张表迁移是后续一小步。

| code | 状态 | 文案 | 触发点 |
|---|---|---|---|
| `coupon_not_found` | 404 | 找不到此優惠券 | 领券 / 出示码 / 钱包明细 / 评价查券 |
| `coupon_expired` | 400 | 此優惠券已失效 | 领券时已过期或已停用 |
| `coupon_inactive` | 400 | 此優惠券已停用 | 核销时券非 `active` |
| `coupon_not_started` | 400 | 此優惠券尚未到使用日期 | 核销时未到 `valid_from` |
| `coupon_package_must_purchase` | 400 | 套餐券請購買，不可免費領取 | 免费领套餐券 |
| `coupon_points_only` | 400 | 積分券請用積分兌換 | 免费领积分券 |
| `coupon_already_claimed` | 409 | 您已領取此 Coupon | 重复领券 |
| `coupon_sold_out` | 400 | 優惠券已達下載上限 | 库存为 0 |
| `coupon_unavailable` | 400 | 此優惠券已不能領取 | 并发领券抢完后的回退 |
| `coupon_not_usable` | 400 | 此優惠券不能使用 | 出示已用 / 已过期券 |
| `qr_generate_failed` | 503 | 暫時未能產生兌換碼，請重試 | 12 次抢码失败 |
| `qr_invalid` | 400 | 無效的 QR 碼！ | 扫码无此码 |
| `qr_wrong_shop` | 400 | 無效的 QR 碼！此碼屬於其他商店。 | 跨店扫码 |
| `qr_expired` | 400 | QR 碼已過期，請重新出示 | 码过期 |
| `code_expired` | 400 | 兌換碼已失效，請重新出示 | 消费时条件更新失败 |
| `prepare_expired` | 400 | 預核銷已失效，請重新預核銷 | prepare token 失效 |
| `voucher_unpaid` | 400 | 套餐券付款或退款狀態不允許核銷 | 套餐券未付款 / 退款中 |
| `voucher_used` | 400 | 此優惠券已使用 | 核销已用券 |
| `voucher_expired` | 400 | 此優惠券已失效 | 核销已失效券 |
| `scan_daily_limit` | 400 | 今日掃碼已達上限 | 单商户日扫码超限 |
| `redemption_replay_mismatch` | 409 | 核銷記錄不符，請重新掃碼 | 幂等键重放但商户 / 已撤销不符 |
| `redemption_not_found` | 404 | 找不到此核銷記錄 | 撤销 / 钱包明细查核销 |
| `redemption_already_revoked` | 400 | 此核銷已被撤销 | 重复撤销 |
| `revoke_window_expired` | 400 | 已超過 10 分鐘撤销窗口，請聯絡客服處理 | 超 10 分钟撤销 |
| `forbidden` | 403 | 沒有權限 | 跨店撤销 |
| `points_coupon_not_found` | 404 | 找不到此積分券 | 兑换非积分券 |
| `points_insufficient` | 400 | 積分不足 | 兑换时余额 < 9 |
| `points_coupon_already_exchanged` | 400 | 已經兌換過此券 | 重复兑换 |
| `review_not_redeemed` | 400 | 只有已使用的優惠券可以評價 | legacy `/review` 未核销 |
| `review_already_done` | 400 | 已經評價過 | legacy `/review` 重复评价 |
| `bad_request` | 400 | （具体原因） | 无专用码的入参错误，如日期格式 |

已知文案漂移（未动，等 Dot / Miso 定一句 copy 再统一）：
`/reviews` 的“只有已核銷的優惠券可以評價”与 legacy `/review` 的
“只有已使用的優惠券可以評價”是同一条件的两种说法。

## 3. 分页（统一风格）

- 查询参数一律 `page`（`Query(1, ge=1)`）+ `page_size`
  （`Query(20, ge=1, le=100)`，默认 20）。本次补上 `page_size` 的
  端点：`/admin/merchant-applications`、`/admin/shops`、
  `/admin/coupons`、`/admin/redemptions`、`/admin/users`、
  `/admin/audit-logs`、`/admin/settlements`、
  `/get-favorite-coupons-list`、`/shops/{shop_id}/reviews`；
  `/admin/orders` 原已有。未补的列表（佣金率、对账报告、发票、
  Banner、公告、商户自有结算单）仍默认 20，包络不变，后续同法补。
- 响应包络一律 `{"current_page", "total_pages", "total", <资源名>}`，
  资源名按端点（`shops` / `coupons` / `logs` / `items` / `reviews`
  等）。`total_pages` 为 `max(1, ceil(total / page_size))`。

## 4. 统一导出与审计（GAP §5.9）

### `GET /vendor/redemptions/export`（改动）

新增筛选（GAP §4.5）：`from` / `to`（`YYYY-MM-DD`，按 `scanned_at`
含端）、`coupon_id`、`revoked`（`true` / `false`）。日期格式错
返回 400 `bad_request`。CSV 改走 `shared.csv_response`（正确转义，
含逗号的券名不再错列）；列顺序与以前完全相同：

```
redemption_id,wallet_item_id,coupon_name,shop_name,scanned_at,revoked,revoked_at,idempotency_key
```

JSON 响应不变。

### `GET /admin/settlements?format=csv`（新增）

与 JSON 列表同一批行（同样受 `shop_id` / `status` 筛选），列：

```
id,shop_id,shop_name,period,status,redemption_count,gross_amount,commission_rate,commission,net_amount,transferred_net,outstanding_net,dispute_reason,decided_by,decided_at,paid_at,created_at,updated_at
```

### `GET /admin/orders?format=csv`（新增）

与 JSON 列表同一批行（同样受 `status` 与商户可见范围筛选），列：

```
id,order_id,order_no,user_id,user_name,user_email,order_type,package_id,package_name,shop_id,shop_name,quantity,amount,paid_amount,payment_method,payment_status,refund_status,refundable,settlement_status,commission_amount,net_amount,status,created_at,paid_at
```

嵌套的 `payments` 明细不进 CSV（逐单查 `GET /admin/orders/{id}`）。

### `GET /admin/redemptions?format=csv`（改动：只换 writer）

列与以前相同，改走 `shared.csv_response`。另见
`GET /admin/audit-logs`。

### `GET /admin/audit-logs`（改动）

新增三个筛选参数：`admin_id`、`action`、`target_type`（均精确匹配，
可组合）。响应仍是 `{"current_page", "total_pages", "total", "logs"}`。

```json
// GET /admin/audit-logs?action=coupon.approve
{"current_page": 1, "total_pages": 1, "total": 1,
 "logs": [{"id": 2, "admin_id": 2, "action": "coupon.approve",
           "target_type": "coupon", "target_id": 6, "detail": "",
           "created_at": "2026-11-21T10:00:00"}]}
```

## 5. 券到期提醒与自动过期（GAP §5.6 ①，调度器第 5 个任务）

### `POST /admin/coupon-expiry-sweep`（新增，仅 admin）

手动补跑（与 M1 手动端点同款：调同一份 sweep 逻辑）。

```json
{"message": "已處理即將到期與已過期優惠券",
 "reminded": 1, "expired": 1, "failed": 0}
```

### 后台任务 `coupon-expiry`

- 每周期跑一次（与 M1 同款：Postgres advisory lock key
  `2026111706`，SQLite 走 `scheduler_locks` 租约行；单条失败不阻塞
  其他，下周期重试；幂等）。手动跑：
  `python -m app.scheduler run-once --job coupon-expiry`。
- 提醒：未核销的券在到期前 3 天内，给持券人写一条站内信
  （`notification_type="coupon_expiring"`，`target_coupon_id` /
  `target_shop_id` 指向券与门店；标题“您領取的優惠券即將到期”，
  正文含向上取整的剩余天数，如“將於 2 天後到期”）。同一
  （用户，券）24 小时内只提醒一次，每日跑也不会重复打扰。
- 自动过期：已过 `valid_to` 仍未核销的券，批量翻为已失效（`status=3`，
  与 `_refresh_expiry` 同样的“仅当 `status == 1` 才写”条件更新，
  核销中途提交的不会被覆盖）。
- 积分过期（§5.1②）不在此任务：它要 `PointTransaction.expires_at`
  列，是 P1-C 的范围，列就位后再加。

## 6. 与在途工作的衔接点（不要重复立项）

| 在途项 | 本片留的衔接点 |
|---|---|
| P1-C 积分按消费 | 只改 `app/points.py::award_redeem_points` 一个函数：付费券按实付 HK$1 = 1 分（读 `WalletItem.order_id` 背后的 `Order.paid_amount`），免费券沿用现值。撤销侧不用改——`clawback_points` 冲的是账上实际发的那笔，不是按规则重算的。`POINTS_REDEEM` 常量与 `award_claim_points` / `award_review_points` 不动 |
| P1-A 付费券购买 | 购买链路仍是 P1-A 的；本片只保证 `Coupon.sale_price` / `refund_policy` 的单一来源是 `models_packages.REFUND_POLICIES`（第 3 块的 `listing.py` 同样改引它），且付费券发券走 `packages._issue_voucher` → `issue_wallet_item` |
| P1-B Stripe Connect | 不碰。商户提款打款仍是统计块的 `payouts.execute_merchant_payout()` 占位；本片不新增任何 Stripe 调用 |
| M9-7 真删号 | 不碰。删号后的积分 / 券处置等 M9-7 的数据处置定义落定后再接 |
| 第 1 块（商家管理） | `app/notify.py` 已采用（原样）；它的通知类型表见 `DIANPING_MERCHANT_API.md` §1.5，本片新增 `coupon_expiring` 一种。它的 `coupon_branches` / 店员权限与本片无交集 |
| 第 2 块（统计） | 余额 / 看板 / 提款 / 结算单口径说明（`basis_note`）已是第 2 块的；本片只补了结算单与订单的 CSV 导出与审计筛选。看板按券转化行 rebase 时复用 `coupon_usage_stats()`；`money()` 已是它 `_settlement_out` 的金额风格 |
| 第 3 块（上架） | `app/listing.py` 已采用（只改 `_now` 与 `REFUND_POLICIES` 两处引用）；`sweep_listing_schedule` 未接调度器——上架定时任务仍是第 3 块的 `listing-schedule`，不要与本片的 `coupon-expiry` 混淆。注意：它的 advisory key `2026111705` 与第 2 块的 `monthly-settlements` 撞号（见下） |
| 第 4 块（核销） | `_redeem_item` 已按它的设计抽出（同名、同语义，缺 `method` / `note` 两列——那是它迁移 `0021_dianping_redemption` 的内容，合入后补核销 / 批量核销直接调 `_redeem_item`，再传 `method` / `note`）。导出筛选（§4.5）已在本片实现 |

### 已发现的在途冲突（请 Miso 裁决，合入前核对）

- **迁移号 `0021` 七方撞号**：M9-7、P1-A、P1-B、P1-C、第 2 块
  （`0021_merchant_withdrawals`）、第 3 块（`0021_dianping_listing`）、
  第 4 块（`0021_dianping_redemption`）都认领 `0021`；`0022` 是
  P1-D，`0023` 是第 1 块。**本片无迁移**，不参与抢号。
- **调度器 advisory key `2026111705` 两方撞号**：第 2 块的
  `monthly-settlements` 与第 3 块的 `listing-schedule` 都用了
  `2026111705`；本片的 `coupon-expiry` 取了下一个空号 `2026111706`。
- **错误码迁移未覆盖**：`packages.py` / `bargain.py` 仍用 plain
  `HTTPException`（文案不变），按 `app/errors.py` 的表迁移是后续
  一小步，不要在别的块里另起一套码表。

## 7. 积分商城（GAP §5.2，留待后续——本片未做）

现状仍是 `wallet.py:exchange_points` 一个固定 9 分换券口
（`POINTS_EXCHANGE_COST = 9` 硬编码，只能换 `is_points_exchange`
的券，每人每券一张，无目录 / 库存 / 订单）。本片没有把它做进
同一个 PR，原因：

1. **要新迁移**：`points_products`（分值 / 库存 / 上下架 / 关联券）
   + `points_orders`（兑换记录）两张表。迁移号已被七方撞号占满，
   需等 Miso 裁决合入顺序后才能取号（本片无迁移，见 §6）。
2. **定价口径未定**：§3.4 只定了“首期只做电子礼券，兑换后进
   Q Wallet，不寄实物、不收地址”；每件礼品的分值、库存扣减时点
   （下单扣还是兑换成功扣）、兑换后是否可退款，未定——需 Miso 定。
3. **本片已铺好的衔接点**（下一步直接用，不用重搭）：
   `points.spend_points()`（扣分唯一入口）、
   `shared.issue_wallet_item(..., "exchange")`（发券唯一入口）、
   `points_balance()`（余额唯一来源）、`shared.paginate()`（目录分页）、
   `shared.csv_response()`（兑换订单导出）。
4. 下一步做法（供排期）：迁移（两张表）→ `POST /points/products`
  （admin 上架）→ `GET /points/products`（目录，分页）→
   `POST /points/exchange` 改为按商品行扣分发券（复用现有兑换的
   扣分 + 发券逻辑，只把“固定 9 分、固定券”换成按商品行）→
   `GET /points/orders`（兑换记录）→ 测试（并发兑换不超卖：库存扣减
   走条件 UPDATE，与领券同款）。

## 8. 测试

- `apps/api/tests/test_dianping_unify.py`（21 条）：单一时钟、分页 /
  金额 / 店铺查询、积分政策层（含撤销冲回）、`_redeem_item` 单链
  （领 → 出示 → 核销 → 幂等重放 → 撤销冲回，`face_value` 快照
  = 价格券 `discount_price`）、`listing` 单一来源、`notify`、
  发券入口、错误码包络、积分兑换（余额不足 / 兑换后 `source`
  = `exchange`）、CSV 导出（转义、列顺序、结算单 / 订单）、审计筛
  选、券到期 sweep（提醒一次、过期翻转）、调度器注册与 `run_all_jobs`。
- 全量 pytest：见 PR 汇报（含真实 Postgres 那次）。
- 迁移：无（`alembic/versions/` 未动）。
