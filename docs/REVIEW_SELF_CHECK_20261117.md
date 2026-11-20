# M4 自查：评价积分与星级拆分（2026-11-17）

自查，不改口径。星级口径的修改（所有 visible 评价计入星级、回填、
撤销校验与积分回收）是 M7，本文件只记录 M4 核实的现状与一处已修的
真缺口。`counts_toward_rating` 的逻辑、`recompute_rating` 的汇总口径、
评价积分发放规则均未动；无迁移；无接口字段变更。

## 结论一览

| # | 自查项 | 结论 |
|---|--------|------|
| 1 | 星级只由评分字段计算；评价积分与返现是两本账 | 通过（现状即如此，见下） |
| 2 | 只有已核销的券或订单才能评价，同一核销只能评一次，接口层校验 | 通过；另发现一处真缺口已修（并发双提交 500 → 400，见下） |
| 3 | 评价图片 EXIF 已清理 + 补一条测试；README 注明本地存储仅为开发占位 | 通过（EXIF 已有实现，新补端到端测试；README 说明早已存在，无需改动） |

## 1. 星级 / 积分 / 返现三本账——通过

- `recompute_rating`（`apps/api/app/routers/reviews.py:121`）只汇总
  `status == "visible"` 且 `counts_toward_rating == True` 的评价，
  取 `taste / ambience / service / overall` 四个评分字段的均值；
  积分不进入任何一处汇总。
- `create_review`（同文件 :175）：`claim_points=True` →
  `points_given=True`、`counts_toward_rating=False`、发 +15 积分
  （`config.POINTS_REVIEW`，:28）；`claim_points=False` → 计入星级、
  不发积分。领积分的评价不计星级、放弃积分的评价才计星级——这正是
  M7 要改的口径，M4 不动。
- 旧接口 `POST /review`（`apps/api/app/routers/wallet.py:72`）：
  只加分、不写 Review 行、不影响星级；校验 `item.status == 2`
 （已使用）与 `item.reviewed` 去重标志。
- 积分与返现分属两个账本：积分是 `PointTransaction`
  （`app/models.py:200`，经 `deps.add_points` 写入）；
  返现是 `CashbackTransaction`（`app/models_cashback.py`），唯一写入
  者是 `cashback_service.record_spend`（核销时）。评价流程不碰返现。
  测试 `test_m4_points_review_moves_points_not_rating_nor_cashback`
  锁定了这一点：+15 积分评价后，积分账多一条 +15、返现账行数不变、
  星级 `review_count == 0`。
- 记录一个现状细节（未改）：星级**分布**（`_rating_distribution`）
  覆盖全部 visible 评价（含领积分的），而均值与 `review_count` 只
  覆盖计入星级的评价。M7 把汇总口径改成全部 visible 后，两者自然
  对齐；M4 不动它。

## 2. 评价资格——通过，修一处真缺口

接口层校验已存在且覆盖套餐订单：

- `_resolve_redemption`（`reviews.py:146`）：`redemption_id` 不存在
  → 404、非本人 → 403、无核销记录 → 400；`wallet_item_id` 路径同理。
- `create_review`（:179）：`item.status != 2`（未使用）→ 400
  「只有已使用的優惠券可以評價」；已有同 `redemption_id` 的 Review
  或 `item.reviewed` → 400「已經評價過」。
- 套餐订单没有单独的评价路径：`routers/packages.py` 里无 review
  代码；购买成功经 `_issue_voucher` 只发一张 `WalletItem`，核销走
  与普通券同一套扫码流程（`routers/coupons.py:142` 置 `status = 2`
  并建 `Redemption` 行），评价走同一 `POST /reviews`。
  测试 `test_m4_package_order_review_uses_same_path` 覆盖：未核销
  套餐券 → 400；核销后 → 200 且计入星级；重复评 → 400；
  旧接口 → 400。

**纠正准备清单的一处说法**：同一核销防重评并非只有应用层。
`reviews.redemption_id` 在模型（`app/models_line_b.py:57-58`）与
迁移（`alembic/versions/0002_line_b.py:42-43`）里都是 `unique=True`，
DB 唯一约束自 F6 v1 起就存在。测试
`test_m4_redemption_id_unique_constraint_backstop` 直接验证：
第二条同 `redemption_id` 的 Review 行写入即报 `IntegrityError`。

**发现并已修的真缺口——并发双提交返回 500**：
应用层查重与 DB 约束之间存在竞态：两个并发提交同时通过查重，
第二个在 `flush` 时撞上唯一约束，而原代码未捕获
`IntegrityError`，请求以 500 告终（复现：修复前
`test_m4_concurrent_duplicate_review_returns_400` 单独跑 6/6 失败，
`sqlite3.IntegrityError: UNIQUE constraint failed:
reviews.redemption_id` 未被处理）。
修法（只修缺口本身）：`create_review` 里 `add_points` + `flush`
包进 `try/except IntegrityError`（`reviews.py:200-209`），回滚后
返回 400「已經評價過」。修复后同一测试 6/6 通过，全文件 5/5
通过。无迁移（约束早已存在），无口径变化。

**已知缺口，留给 M7（M4 不修）**：
- `create_review` 未检查 `red.revoked_at`：已撤销的核销仍可评价
  并领积分；撤销后已发积分也未回收（M7 任务书第 2 条）。
- 旧接口 `POST /review` 的去重只靠 `item.reviewed` 标志，无 DB
  约束背书；并发双提交理论上可重复发 +15（旧接口，影响小，
  M7/后续再定是否处理）。

## 3. EXIF 与 README——通过

- EXIF 清理已实现（`app/routers/uploads.py:48-136`）：JPEG 丢 APP1
  (Exif) 段、PNG 丢 eXIf 块、WebP 丢 EXIF 块，均无损（其余字节原样
  保留）；GIF 无 EXIF 直接通过；无法解析的文件原样存储、永不拒收。
  已有测试 `tests/test_p1_upload_exif.py`（JPEG/PNG/WebP/GIF 各一）。
- M4 新补一条端到端测试
  `test_m4_exif_stripped_end_to_end_upload_review_stored`：带 EXIF
  的 JPEG 上传 → 挂到评价上 → 存储文件仍无 EXIF 标记、JFIF 等非
  EXIF 段完好、评价返回同一图片路径。
- README 本地存储说明**早已存在，无需改动**：`README.md:150`
  「本地佔位儲存，未接對象儲存」；`README.md:273-274`
  「晒圖上傳是本地磁盤佔位儲存（開發佔位，上線前必須換對象儲存）」。
  按任务要求只补中性说明、不改写脱敏后写法——此处一句未改。

## 改动 / 测试 / 迁移 / 接口

- 改动文件（仅 M4 自己的 3 个）：
  - `apps/api/app/routers/reviews.py`——并发重评 `IntegrityError` →
    400（见 §2）。
  - `apps/api/tests/test_m4_review_self_check.py`——新增，5 条测试。
  - `docs/REVIEW_SELF_CHECK_20261117.md`——本文件。
- 新增测试（5 条，均在 `test_m4_review_self_check.py`）：
  `test_m4_points_review_moves_points_not_rating_nor_cashback`、
  `test_m4_package_order_review_uses_same_path`、
  `test_m4_redemption_id_unique_constraint_backstop`、
  `test_m4_concurrent_duplicate_review_returns_400`、
  `test_m4_exif_stripped_end_to_end_upload_review_stored`。
- 迁移编号：无（未加约束、未改表）。
- 接口字段变更：无，`docs/API.md` 不动。转告 dot 的内容：无契约
  变化；仅一处行为修正顺带说明——并发重复提交评价过去返回 500，
  现在返回 400「已經評價過」（顺序提交一直是 400，不受影响）。
- 未动：FIX-1 支付验签、静态 MVP、README 脱敏写法、星级口径、
  积分发放规则。

## 2026-11-20 M8 补查：现金券 ≠ 平台实收

- `Cash` 券的 `cash_amount` 只用于展示优惠金额；核销可照常发生，但结算毛额、佣金及净额均不因现金券增加。历史正值 `face_value` 按 0 生成新账单或计算对账期望，原核销快照及已生效结算不回写。未付款的旧结算单存在未冲回正额现金券行时，暂阻登记付款（409）；已登记付款的相同凭证可幂等重放。
- 套餐仍以实付额、价格券仍以既有折扣价快照为准；订单已退款的核销仍跳过。无新迁移。
- 确认 #2 已把两份日志移到 `docs/evidence/`，M3 在没有 PostgreSQL 时明确跳过；有 PostgreSQL 时仍执行真实并发测试。
