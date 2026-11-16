# Coupon GO! 补审报告（P1 之后）— 2026-11-16

- **审计性质**：只读审计，未写任何代码、未改任何文件、未推 GitHub。本报告是本次审计唯一新增文件。
- **基线**：任务给定为仓库 `QIU-Guanzong/Coupon-Go` main 最新提交 `36ed2dc76`（P1 已合入，全量 143 pytest 通过）。
  本地只读目录 `~/workspace/coupon-go-fullstack/` **不是 git 仓库**（`git log` 报错 `not a git repository`），故提交号 `36ed2dc76` 未能在本地核验；以下结论以本地目录现有文件为准。
- **审计范围**：`apps/api/app/routers/admin_v2.py`（1,119 行）、`apps/api/app/models_line_f.py`、`apps/api/app/permissions_line_f.py`、`apps/api/app/routers/admin.py`（v1）、`apps/api/app/schemas_line_f.py`、`apps/api/app/routers/reviews.py`、`apps/api/app/routers/wallet.py`、`apps/api/app/config.py`、`apps/api/app/models_line_b.py`、`apps/api/app/schemas_line_b.py`、`apps/api/app/routers/bargain.py`（仅核对退款失败说明）、`apps/api/app/payments_gateway.py`（仅核对网关说明）、`README.md`、`docs/API.md`、`docs/FEATURE_RESEARCH_20261116.md` §14。
- **未做的事**：未运行 pytest（143 通过为任务给定基线，未复跑）、未发任何 HTTP 请求、未做并发实测；并发类结论为代码级推导（行锁有无 + 状态检查位置），已在文中标明。
- **严重程度**：高 = 可直接造成资金/数据错误或越权写；中 = 越权写或审计留痕丢失，需 finance 批准或特定时序才生效；低 = 边界/可用性/文案不一致；观察 = 符合现有设计但值得产品确认。

## 修复状态（2026-11-16，F1/F2/F3 已修并合入 main）

- **F1（中）— 已修，提交 `16460762fe86273c385253470f2a08c4418fd63b`**：`create_invoice` 的回退由 `can_access_shop`（对 auditor 恒真）改为新增的 `can_request_invoice_for_shop`（仅店主或 `shop_members` 成员）；`can_access_shop` 本身未动，读接口仍需它。新增测试 `apps/api/tests/test_f1_invoice_perm.py`（7 条：auditor/support/customer → 403，finance → 201，vendor 自家店 → 201、他家店 → 403，店员自家店 → 201、他家店 → 403）。全量 pytest 150 通过（基线 143 + 7）。
- **F2（中）— 已修，提交 `79f1a6c626e7f4af03c0fe29d7c6a3ee61c927dd`**：`run_reconciliation` 重跑只删除/重建 `open` 行；`resolved` 行保留不动（含 resolution/resolved_by/resolved_at）；仍存在的底层差异若已有对应已处理行不再重复生成（去重键 `(discrepancy_type, redemption_id, settlement_item_id)`）；`discrepancy_count` 与报告状态按保留行 + 新生成行合计。新增测试 `apps/api/tests/test_f2_recon_rerun.py`（2 条）。三项合入后主目录全量 pytest 160 通过。
- **F3（中）— 已修，提交 `ffca20cac5abfb21e7a2ab55b150cfca45b4d9cf`**：`approve`/`dispute`/`resolve` 与 `pay` 对齐，对结算单行加 `.with_for_update().populate_existing()` 后再检查状态再转移；状态守卫与 400 文案未动。新增测试 `apps/api/tests/test_f3_settlement_locks.py`（8 条：pay–dispute 等并发 + 串行守卫 + 锁存在性）。并发用例跑在 SQLite fallback（`BEGIN IMMEDIATE` 串行化）下——本机无 Postgres；如有 Postgres 环境，建议以 `F3_POSTGRES_URL` 再跑一次做真验证。F1+F3 合入后主目录全量 pytest 158 通过。
- F4–F10（低）与 README 缺项未动，另批处理。

---

## 1. F 后台 v2（结算 / 对账 / 发票 / 角色分权）

### 1.0 结论先行

**不通过——有问题 10 项（中 3 项：F1、F2、F3；低 7 项：F4–F10），另有观察 3 项（O1–O3）。**

| 检查项 | 结论 |
|---|---|
| 结算：按核销结算 | 通过 |
| 结算：佣金默认 10%，与 README/报告一致 | 通过（数值一致；但费率无来源表，见 F5） |
| 结算：状态机 pending→approved→paid（含 disputed 分支），各转移有状态守卫 | 通过（单线程语义） |
| 结算：`pay_settlement` 行锁 + `status == approved` 检查（FIX-3） | **通过，已确认在** |
| 对账：三种差异类型都处理 | 通过 |
| 对账：差异处理后留痕 | 部分通过——单次处理留痕完整，但重跑会删除留痕（F2） |
| 发票：总额不得超净额，服务端强制（FIX-3 行锁 + 汇总检查） | **通过，已确认在** |
| 发票：状态机有无越权 | 状态守卫通过；但创建口有越权（F1） |
| 角色分权：各角色实际可调接口与 `require_role` / `require_perm` 对照 | 基本通过，1 处越权（F1）、2 处可用性缺口（F9、F10） |
| 接口有无漏鉴权 | 通过（除 F1 外；公开横幅/公告为有意公开） |
| merchant-admin 能否动别人的结算单 | 不能（通过） |
| auditor 能否改数据 | 除设计内的申诉解决与 F1 发票创建外，不能（见 1.4） |

### 1.1 通过项（证据）

**P1-1 结算按核销，佣金默认 10%。**
`apps/api/app/routers/admin_v2.py:183-235` `generate_settlement`：按 `shop_id + period（YYYY-MM）` 聚合该周期 `scanned_at` 落在区间内的核销；`_face_value`（`admin_v2.py:88-99`）取 `cash_amount`，否则 `discount_price`，否则 `regular_price`，否则 0；`commission = gross * rate`（`admin_v2.py:203-213`），`net = gross - commission`。默认值 `apps/api/app/schemas_line_f.py:13`：`commission_rate: float = Field(default=0.10, ge=0, le=1)`。与 `README.md:279`「结算佣金比例默认 0.10」、`docs/API.md:294,300`「默认 0.10；真实费率按商户逐个谈判」、`models_line_f.py:10-16` 一致。`SettlementItem.redemption_id` 唯一（`models_line_f.py:89-100`），一笔核销最多进一张结算单；重复生成同一商户同一周期返回 409（`admin_v2.py:189-193`）。

**P1-2 结算状态机守卫完整（单线程语义）。**
`approve` 仅 `pending`（`admin_v2.py:283`）；`dispute` 仅 `pending/approved`（`:303`）；`resolve` 仅 `disputed`，`decision=approve→approved / reopen→pending`（`:324,326`）；`pay` 仅 `approved`（`:347`）。模型状态 `pending | approved | disputed | paid`（`models_line_f.py:59-87`）。无通用 PUT/PATCH 可任意改结算单状态。

**P1-3 `pay_settlement` FIX-3 已确认在。**
`admin_v2.py:336-357`：
```python
# FIX-3: lock the settlement row before the status check so a
# second concurrent pay blocks here and sees status == "paid".
s = (db.query(Settlement).filter(Settlement.id == settlement_id)
     .with_for_update().populate_existing().first())   # :343-344
if s.status != "approved":                              # :347
    raise HTTPException(status_code=400, detail="只有已確認的結算單可以付款")
```
行锁 + 状态检查均在。并发双付有测试覆盖：`apps/api/tests/test_fix3_row_locks.py:508` `test_concurrent_pay_settlement_only_one_succeeds__row_lock`、`:531` 顺序双付拒绝。

**P1-4 对账三种差异类型全覆盖。**
`admin_v2.py:411-483` `run_reconciliation`：`missing_settlement_line`（有核销、无结算行，`:435-441`）、`missing_redemption`（有结算行、无此周期核销，`:442-446`）、`amount_mismatch`（结算行金额 ≠ 券面值，`:447-452`），每种都生成 `ReconciliationDiscrepancy` 行并带 `expected_amount / actual_amount`。报告状态 `no_settlement / resolved / open`（`:471-473`）。

**P1-5 差异处理留痕（单次处理）。**
`admin_v2.py:515-543` `resolve_discrepancy`：仅 `status == "open"` 可处理（`:525`）；写入 `resolution`、`resolved_by`、`resolved_at`（`:530-533`）；写 `admin_audit_logs`（`reconciliation.resolve`，`:534-536`）；全部差异处理完后报告自动变 `resolved`（`:537-541`）。模型字段见 `models_line_f.py:129-147`。

**P1-6 发票总额上限 FIX-3 已确认在。**
`admin_v2.py:563-603` `create_invoice`：
```python
# FIX-3: lock the settlement row before summing already-invoiced amounts ...
settlement = (db.query(Settlement).filter(Settlement.id == body.settlement_id)
    .with_for_update().populate_existing().first())      # :576-579
if settlement.shop_id != shop.id:                        # :581-582
    raise HTTPException(status_code=400, detail="結算單不屬於此商户")
already = db.query(func.coalesce(func.sum(Invoice.amount), 0)).filter(
    Invoice.settlement_id == settlement.id,
    Invoice.status != "rejected").scalar()               # :583-586
if Decimal(str(already or 0)) + Decimal(str(body.amount)) > \
        Decimal(str(settlement.net_amount)):              # :587-590
    raise HTTPException(status_code=400, detail="開票金額超過結算單可開票餘額…")
```
行锁 + 汇总（排除 `rejected`）+ 与 `net_amount` 比较均在。并发与顺序超额均有测试：`test_fix3_row_locks.py:541`、`571`；`test_line_f.py:259` 发票链与上限。

**P1-7 发票状态机守卫完整。**
`approve` 仅 `pending`（`admin_v2.py:640`）、`reject` 仅 `pending`（`:660`）、`issue` 仅 `approved`（`:680`），三者均要求 `invoices.write`（finance/admin，`:635,655,675`）。模型状态 `pending | approved | rejected | issued`（`models_line_f.py:149-168`）。批准时不改金额、净额生成后不变，故批准时无需重算上限（无缺口）。

**P1-8 merchant-admin 不能动别人的结算单。**
平台端结算/对账/发票读写均要求 `settlements.read/write`、`reconciliation.read/write`、`invoices.read/write`，而 `permissions_line_f.py:34-50` 的权限表里 `merchant-admin` 不在任何一项中（`has_perm` 对其返回 False，admin 除外）。商户端三个只读接口（`/merchant/shops/{shop_id}/settlements` `:359-373`、`/reconciliation` `:545-561`、`/invoices` `:690-704`）均经 `can_access_shop`（`permissions_line_f.py:77-88`）按 `shop_members` 表或店主身份限店：非本店成员调他人店铺返回 403。写操作（生成/确认/争议/付款/开票审批）没有商户端入口。

**P1-9 无接口漏鉴权（除 F1 外）。**
逐路由核对 `admin_v2.py` 全部端点：`overview` 用 `dashboard.read`（`:161`）；结算/对账/发票/CMS/分类均用对应 `require_perm`；角色清单与改角色用 `admin_only`（`:970,976`）；店员增删用自定义 `admin 或店主` 检查（`:1023,1058`，见 F8）；申诉解决用 `reviews.dispute.resolve`（`:1098`）。仅有的无登录端点是 `GET /banners`（`:775-791`）与 `GET /announcements`（`:868-879`），只返回 `status=active` 且在展示窗口内的内容，为有意公开。v1 全部 `admin_only`（`admin.py:33`），除提交入驻申请（任何登录用户，`admin.py:77`，设计内）。

**P1-10 角色变更有防呆。**
`admin_v2.py:974-1000` `change_user_role`：改自己 400（`:984`）、未知角色 400、降级最后一位 admin 时 400（`:987-992`）。

### 1.2 有问题清单

**F1（中）【已修，提交 16460762】— auditor 可越权创建发票：`create_invoice` 的权限回退把只读角色放进了写操作。**
- 位置：`apps/api/app/routers/admin_v2.py:563-571`；根因在 `apps/api/app/permissions_line_f.py:77-88`。
- 证据：
```python
# admin_v2.py:568-571
if not has_perm(user, "invoices.write"):        # auditor: False（invoices.write 仅 finance，permissions_line_f.py:41）
    if not can_access_shop(db, user, shop):
        raise HTTPException(status_code=403, ...)
# permissions_line_f.py:81-82 — can_access_shop 对 auditor 恒为 True：
if user.role in ("finance", "auditor"):
    return True
```
- 复现：auditor 账号（种子 `auditor@coupon-go.example`）登录后 `POST /api/v3/admin/invoices {"shop_id": <任意>, "amount": 100, "title": "…"}` → 201，发票进入 `pending`。`can_access_shop` 是读权限范围的辅助函数（平台财务/审核可看任意店），却被复用于写操作的回退判断。
- 影响：auditor 按权限表（`permissions_line_f.py:40-41`）只有 `invoices.read`，应为只读；此口子让其能为任意商户创建开票申请。需 finance 批准才能生效，故无直接资金损失，但属于越权写 + 可批量制造待审发票（骚扰/钓鱼式开票）。support 不受影响（`can_access_shop` 对 support 为 False → 403）；finance/admin 走 `has_perm` 直通，不受影响。
- 同类检查：其余 v2 写端点均直接用 `require_perm("*.write")`，无此回退模式，仅 `create_invoice` 一处。

**F2（中）【已修，提交 79f1a6c6】— 对账重跑删除已处理差异，处理留痕丢失；已处理差异会在重跑后以 open 重现。**
- 位置：`apps/api/app/routers/admin_v2.py:460-464`。
- 证据：
```python
else:
    # Re-run: drop the previous run's discrepancies and regenerate.
    db.query(ReconciliationDiscrepancy).filter(
        ReconciliationDiscrepancy.report_id == report.id).delete()
    db.flush()
```
重跑无条件删除该报告的**全部**差异行（含 `status=resolved`、带 `resolution / resolved_by / resolved_at` 的行），再重新生成。`_log`（`:477` 附近）只记录 `reconciliation.run … discrepancies=N`，不记录被删除的已处理记录。`docs/API.md` 对账表也写明「重跑会覆盖上次的差异记录」——行为与文档一致，但与「差异处理后留痕」的要求冲突：审计上一次处理结论无处可查。
- 复现：finance 处理某差异（resolve，留 `resolution`）→ 再次 `POST /admin/reconciliation/run` 同商户同周期 → 该差异行消失，若底层差异仍在则以全新 `open` 行重现，`resolved_by/at` 丢失。
- 关联：差异处理本身是纯文本结论（见 O3），不产生纠正动作；重跑后同一差异必然重现，处理结论形同作废。

**F3（中）【已修，提交 ffca20ca】— 除 `pay` 外，结算状态转移均无行锁；`pay` 与 `dispute`/`resolve`/`approve` 并发时会丢失更新。**
- 位置：`admin_v2.py:275-334`（`approve`/`dispute`/`resolve` 均为普通查询后赋值提交，无 `with_for_update`），对照 `pay` 的锁（`:343-344`）。
- 证据（时序推导，未实测并发）：`pay` 持有结算行 `FOR UPDATE` 锁直至提交；`dispute` 在 `pay` 提交前读到 `approved`，其提交时的 `UPDATE` 会等待行锁、随后执行，把已 `paid` 的结算单改回 `disputed`（`dispute` 只检查读入时的状态 `:303`，不重查提交时状态）。同理 `resolve`（读 `disputed` 后改 `approved/pending`）可覆盖并发 `pay` 的结果；`approve` 与 `dispute` 之间亦然。FIX-3 只锁了 `pay` 与发票创建这一对，状态机其余转移未覆盖。`test_fix3_row_locks.py` 只有 pay–pay（`:508`）与发票–发票（`:541`）并发测试，无 pay–dispute 交叉测试。生产用 PostgreSQL（`infra/docker-compose.yml`）时成立；开发 SQLite 的库级锁可能掩盖此问题。

**F4（低）— 并发生成结算单时，唯一约束兜底会以 500 而非 409 失败。**
- 位置：`admin_v2.py:183-235`（`existing` 检查 `:189-193` 无锁、无事务隔离说明）；兜底约束 `models_line_f.py:85-87` `uq_settlement_shop_period`。
- 复现：两请求并发 `POST /admin/settlements/generate` 同商户同周期，均通过 `existing is None` 检查后插入，一方违反唯一约束；代码无 `IntegrityError` 捕获，故返回 500 而非文档承诺的 409。脏数据不会产生（约束兜底有效），仅错误码与可重试性受损。另 `settled_ids`（`:195-197`）全表加载结算行 id，未按商户/周期过滤，仅为性能问题。

**F5（低）— 佣金率无来源表：生成时任意传入 0–1，finance 单人可把佣金定为 0%。**
- 位置：`schemas_line_f.py:13`（`ge=0, le=1`，默认 0.10）；`admin_v2.py:203` 直接采用传入值；`models_line_f.py:14-16` 明确「rate is a per-generation input — the real rate is negotiated per merchant」。
- 影响：无按商户配置的费率表、无复核、无费率变更审批；`Settlement` 行只记录最终所用费率，审计日志 `settlement.generate` 的 detail 含 `rate=`（`:226-228`），事后可查但事前无控制。与 README「需按商户谈判后逐个设定」一致，属于业务控制缺口而非代码错误。

**F6（低）— 面值取当前券价，无核销时快照；券改价后结算口径漂移、对账误报。**
- 位置：`_face_value`（`admin_v2.py:88-99`）在生成（`:204-212`）与对账（`:438,447-450`）两处均实时读当前 `Coupon` 行。
- 复现：某券核销后商户改价（`discount_price/regular_price` 变化）→ 此后生成结算单按新价计入；或结算单生成后改价 → 再跑对账即报 `amount_mismatch`（结算行金额取生成时价，`expected` 取当前价）。百分比券/赠品券无价基时面值为 0（`models_line_f.py:10-12` 已声明），结算毛额会低估该类核销。建议以核销时快照或 `Redemption` 行金额为准，现为口径风险。

**F7（低）— 无结算单的报告在差异全处理后仍停留 `no_settlement`，不转 `resolved`。**
- 位置：`admin_v2.py:537-541`：`if open_left == 0 and rep.status == "open": rep.status = "resolved"`。当报告因无结算单而为 `no_settlement` 时，即使全部差异都已处理，状态也不变。列表页按状态筛选会漏掉这类已处理报告。

**F8（低）— 店主可任意拉人并自动提权，无被加人同意校验；移除不降级。**
- 位置：`admin_v2.py:1021-1055`（加店员）、`:1056-1074`（移除）。
- 证据：加店员检查仅为 `user.role != "admin" and shop.vendor_id != user.id`（`:1026`），即店主可把任意 `user_id`（含陌生 customer）加为本店店员；`target.role == "customer"` 时自动升为 `merchant-admin`（`:1037-1040`），无邀请/确认环节；也可把已有 finance/auditor 账号加为店员（平台角色不变，`shop_members.role` 写死 `merchant-admin`，双角色并存易混淆）。移除时只删 `ShopMember` 行，不降级平台角色（文档 `docs/API.md` 已写明「不改该用户的平台角色」）；被移除者保留 `merchant-admin` 空头角色（该角色本身无平台权限，`permissions_line_f.py:34-50`，故无越权，仅角色标签残留）。

**F9（低）— 店主/店员看不到自家店员列表。**
- 位置：`admin_v2.py:1002-1019` `list_shop_members` 要求 `members.read`，而 `members.read` 仅 finance/auditor/admin（`permissions_line_f.py:46`）；商户端无对应的成员列表接口。店主能加/删店员（`:1021,1056`），却不能列出自家店员，需平台代查。可用性缺口，非安全问题。

**F10（低）— v1 用户列表的角色筛选对新角色静默失效。**
- 位置：`apps/api/app/routers/admin.py:275-279`：`if role in ("customer", "vendor", "admin")` 才过滤；传 `role=finance/auditor/support/merchant-admin` 时过滤被忽略，返回全部用户。v1 文件仍在用旧三角色白名单，未随 v2 角色扩展更新。

### 1.3 观察（非问题，产品确认用）

**O1 — `pay` 与 `issue` 只是状态翻转，无真实打款/开票凭证。**
`pay_settlement`（`:336-357`）置 `status=paid`、`paid_at`，无支付渠道、交易号、打款凭证字段；审计日志只记 `period/net`。与 README「没有真实 FPS / 银行打款集成」（`README.md:238-240`）一致：现阶段「已付款」是人工断言，需线下打款流程背书。发票 `issue`（`:672-688`）同理，只记 `issued_at`，无发票号码/税局回执字段。

**O2 — 发票可对任意状态结算单开具。**
`create_invoice`（`:563-603`）只校验结算单属于该商户、金额不超净额，不校验结算单状态；对 `pending/disputed/paid` 的结算单均可开票。是否应限 `approved/paid`，需产品定。

**O3 — 差异处理与争议解决的备注只进审计日志，不存业务行。**
`resolve_settlement` 的 `note`（`:315-334`）只写入 `_log` 的 detail，不存 `Settlement` 行（无对应列）；与之对比，`dispute_reason`（`:305`）与发票 `reject_reason`（`:660` 附近）均存行内。事后只能查审计日志。

### 1.4 角色分权总表（实测代码，非文档复述）

权限表来源 `permissions_line_f.py:34-50`（admin 恒有全部权限，`has_perm` `:53-60`）：

| 角色 | 可读 | 可写 | 说明 |
|---|---|---|---|
| admin | 全部 | 全部 + 改角色 + 内容管理 | 另保留 v1 全部 `admin_only` 端点 |
| finance | 看板、结算、对账、发票、内容、成员 | 结算、对账、发票（生成/确认/争议/付款/开票审批） | 不能改角色、不能写内容（`content.write` 为空集，仅 admin） |
| auditor | 看板、结算、对账、发票、内容、成员 | 仅评价申诉解决（`reviews.dispute.resolve`）+ **F1 的发票创建越权** | 其余写操作均 403 |
| support | 看板、内容 | 仅评价申诉解决 | 无结算/对账/发票读权限；商户端接口对其 403（`can_access_shop` 不含 support） |
| merchant-admin | 仅本店（`shop_members` 表）结算/对账/发票**只读**（商户端） | 本店发票申请（`create_invoice` 的 `can_access_shop` 分支） | 无平台端读写 |
| vendor（店主） | 自家店同 merchant-admin；另平台端无 | 自家店发票申请 + 加/删本店店员 | 加店员见 F8 |
| customer | 无后台权限 | 无 | 提交入驻申请除外（v1） |

补充：auditor/support 的「改数据」能力限于评价申诉解决（`admin_v2.py:1094-1119`：`uphold→hidden / dismiss→visible` 并重算星级，设计内，见 §2 R4）；除此与 F1 外，auditor 调用结算/对账/发票审批、CMS 写、角色变更均会被 `require_perm`/`admin_only` 以 403 拦截（测试 `test_line_f.py:163,302` 覆盖权限矩阵与争议解决）。

---

## 2. 评价积分与星级是否拆开

### 2.0 结论先行

**通过——逐条确认 6 项全部成立，0 项有问题。** 以下为独立复核（逐行读代码，非复述口头结论）。另有 1 条行为说明（N1，非星级问题）。

| # | 待确认项 | 结论 |
|---|---|---|
| R1 | 拿 +15 分的评价不计入星级（`counts_toward_rating=False`） | 通过 |
| R2 | 不拿分的评价计入星级（`counts_toward_rating=True`） | 通过 |
| R3 | `recompute_rating` 只汇总 `counts_toward_rating=True` 且 `visible` 的评价 | 通过 |
| R4 | 申诉/隐藏退出聚合，申诉解决后按 `counts_toward_rating` 恢复 | 通过 |
| R5 | 旧 `POST /review` 只加分，不写评价行、不影响星级 | 通过 |
| R6 | `POINTS_REVIEW = 15`，新评价默认拿分（`claim_points` 默认 True） | 通过 |

### 2.1 逐条证据

**R1/R2 — 创建评价的积分/星级二选一。**
`apps/api/app/routers/reviews.py:106-140` `create_review`：
```python
points_given = bool(body.claim_points)              # :117
review = Review(..., points_given=points_given,
    counts_toward_rating=not points_given,          # :125-126
    status="visible")
...
if points_given:
    add_points(db, user.id, config.POINTS_REVIEW, "review", ref_id=item.id)  # :131-132
db.flush()
recompute_rating(db, red.shop_id)                   # :134
```
`counts_toward_rating = not points_given` 为严格取反，无第三态。默认 `claim_points: bool = True`（`apps/api/app/schemas_line_b.py:26`，docstring `:13-16` 明写「拿分则不计入星级；设 False 放弃积分才计入」）。返回文案亦区分：「+15 分；此评价不计入商户星级」/「已计入商户星级」（`reviews.py:137-138`）。模型字段 `models_line_b.py:67-68`（`points_given`、`counts_toward_rating`，默认均为 False）与模块 docstring（`models_line_b.py:11-13`）一致。

**R3 — 聚合只取计入星级且可见的评价。**
`reviews.py:53-71` `recompute_rating`：
```python
counted = (db.query(Review)
           .filter(Review.shop_id == shop_id,
                   Review.status == "visible",
                   Review.counts_toward_rating.is_(True)).all())   # :56-58
```
随后按 `counted` 重算 `overall/taste/ambience/service/review_count`；无计入评价时分数置 `None`、`review_count=0`（`:63-70`）。全是积分评价时聚合为空，与 `docs/API.md` 评分节「全是积分评价时 `review_count=0`、`overall=null`」一致。测试覆盖：`apps/api/tests/test_line_b.py:116`（积分评价星级不变）、`:132`（不领积分才计入星级）。

**R4 — 申诉进出聚合正确，且恢复时仍尊重 `counts_toward_rating`。**
申诉：`reviews.py:199-218` `dispute_review` 置 `status=disputed` 后调 `recompute_rating`（`:215`），`disputed` 不满足 `visible` 条件即退出聚合。解决：`admin_v2.py:1094-1119` `resolve_review_dispute`，`uphold→hidden`（永久退出）、`dismiss→visible` 后 `recompute_rating`（`:1113-1114`）；因聚合同时要求 `counts_toward_rating=True`，被恢复的积分评价仍不计星级，被恢复的非积分评价才重新计入。逻辑无漏洞。

**R5 — 旧 `POST /review` 只加分。**
`apps/api/app/routers/wallet.py:72-90` `review`：
```python
def review(wallet_item_id: int, rating: int = Query(5, ge=1, le=5),
           comment: str = Query(""), ...):           # :73-76
    ...
    item.reviewed = True                              # :86
    add_points(db, user.id, config.POINTS_REVIEW, "review", ref_id=item.id)  # :87
    db.commit()
```
全程无 `Review` 行创建、无 `recompute_rating` 调用、无 `ShopRating` 触碰。更甚：传入的 `rating` 与 `comment` 参数在函数体内**一次也未被使用**（纯占位参数，调用方传 1–5 星与任意文字均被丢弃）。`docs/API.md`「旧版评价 +15 分（一次）；不写评价记录、不影响星级」属实。

**R6 — 分值 15。**
`apps/api/app/config.py:29`：`POINTS_REVIEW = int(os.environ.get("POINTS_REVIEW", "15"))`（可环境变量覆盖，默认 15）。新旧两端均引用同一常量（`reviews.py:132`、`wallet.py:87`），无两处 hardcode 不一致。

**N1（行为说明，非星级问题）— 新旧接口共用 `wallet_items.reviewed` 去重位。**
旧接口先调会置 `reviewed=True` 且不留 `Review` 行（`wallet.py:86`）；此后新接口对同一核销会因 `item.reviewed`（`reviews.py:113-114` 的 `existing is not None or item.reviewed` 检查）报「已经评价过」。即旧接口会消耗掉唯一一次评价机会且不留评价内容。星级不受影响（旧接口本就不写评价），但用户会失去补写文字评价的入口；是否保留此行为需产品确认。

---

## 3. README 是否写明默认假设

### 3.0 结论先行

**不通过——任务所列 11 组默认值中，两处皆有 7 组、仅一处有 2 组、两处皆缺 2 组；另 §14 的 6 个子项/整项未写入 README 与 API.md。缺项清单见 3.2。**

核对方法：逐项在 `README.md`（286 行）与 `docs/API.md`（390 行）全文检索关键词，并与 `docs/FEATURE_RESEARCH_20261116.md` §14（`:520-533`，12 个问题）逐项对照；同时核对 `apps/api/app/config.py` 的默认值是否与文档一致。

### 3.1 逐项核对表（任务所列 11 组）

| # | 默认假设 | README.md | docs/API.md | 结论 |
|---|---|---|---|---|
| 1 | 地图 Google Maps | 有（`:136-137`「Google Maps 导航跳转」；`:268-269` 说明瓦片视图未接） | 有（`:74`「地图预设 Google Maps（香港可用）」；`:72` 导航链接） | 通过（附注：导航跳转已写明，实景瓦片未接亦已写明） |
| 2 | 支付 FPS + 信用卡 / Stripe 测试模式 | 有（`:123`「FPS + 信用卡，Stripe 测试模式，未接真实商户号」） | 有（`:206`「Stripe 测试模式（FPS + 信用卡），未接真实商户号」） | 通过 |
| 3 | 返现资金商户佣金出 | 有（`:236` 表格「返现资金来源｜商户佣金里出｜`funded_by=merchant_commission`」） | **无**（返现节 `:130-150` 全文无「资金来源/商户佣金」） | 部分通过 |
| 4a | 线下固定比例 5% | 有（`:229`「线下餐饮返现比例（固定）｜5%」；`config.py:54` `CASHBACK_DEFAULT_RATE=0.05`） | **无**（返现节未写 5% 比例） | 部分通过 |
| 4b | 90 天解锁（线下再消费一次才解锁，过期作废） | 有（`:211-212`） | 有（`:135`、`:141`） | 通过 |
| 4c | 提现门槛 HK$50 | 有（`:232`） | 有（`:142`） | 通过 |
| 4d | 每日上限 HK$1,500 | 有（`:233`） | 有（`:142`） | 通过 |
| 5 | 套餐退款默认随时退 / 过期退 | 有（`:125`「未核销随时退、过期自动退（原路返回，1–3 个工作日），特价券可标不退」） | 有（`:225`「退款默认：未核销随时退、过期自动退」） | 通过 |
| 6 | 余额过期 1 年无交易、提醒后清零 | **无** | **无** | **缺** |
| 7 | 砍价分享助力 | 有（`:161`「砍价 2C（分享助力）」） | 有（`:182`「分享助力砍价（不是荷兰式降价）」） | 通过（README 未提荷兰式，见 §14 #9） |
| 8 | 结算佣金默认 10% | 有（`:279`「结算佣金比例默认 0.10，需按商户谈判后逐个设定」） | 有（`:294,300`「默认 0.10」） | 通过 |
| 9 | 返现无冷静期（待产品决定） | 有（`:256-258`「目前没有冷静期，确认后立即可以提现；保留现状，待产品决定」） | 有（`:150`「`confirmed → withdrawable` 没有冷静期（待产品决定）」） | 通过（明确标注待定） |
| 10 | 评价图片本地磁盘是开发占位 | 有（`:154`「本地占位储存，未接对象储存」；`:277-279`「本地磁盘占位储存（开发占位，上线前必须换对象储存）」） | 有（`:383-389`「本地磁盘是开发占位，上线前须换成对象储存（S3 / GCS 等）」） | 通过 |
| 11 | 砍价退款在真实 Stripe key 下会失败（预付模拟） | **未明写**（`:161` 只写「预付仍是模拟记账（未收真钱），退款改走支付网关」） | **未明写**（`:186` 只写「预付仍是模拟记账（未收真钱），网关调用用合成支付引用，开发版模拟器返回 `re_test_…`」） | **缺（未明示）** |

### 3.2 缺项清单（共 8 项，按优先级）

**缺-1（任务项 6）：余额过期「1 年无交易、提醒后清零」两处皆无。**
全文检索 `1 年 / 一年 / 365 / 清零 / 无交易 / 提醒` 在 README 与 API.md 均为 0 命中；代码亦无积分/返现余额过期逻辑（`points_balance` 只做 `SUM`，`deps.py`；`cashback_service.py` 只有 90 天 dine-in 解锁过期 `expired_90d`，那是另一规则）。§14 #10 原文只说「ShopBack 是 1 年无交易清零；Coupon GO 的积分 / 返现余额是否过期、过期前是否提醒，等 Miso 定」——即该默认值本身尚未定稿，文档未写是「未定未写」而非「已定漏写」；但任务要求核对的默认值在两处文档中不存在，需产品先定再补。

**缺-2（任务项 11）：砍价退款「真实 Stripe key 下会失败」两处皆未明示。**
失败结论只在代码注释写明：`apps/api/app/routers/bargain.py:26-30`（「a real Stripe key has no real payment_intent to refund against, so that call fails and stays retryable until a real prepay flow lands」）与 `:222-233` 的 `_attempt_gateway_refund` docstring。README（`:161`）与 API.md（`:186`）只写了「模拟预付 + 合成引用 + 模拟器返回 `re_test_…`」，谨慎读者可推断，但「接真实 key 会失败、退款停在 `failed` 可重试」这一运维关键结论未出现在任一文档。另注：网关层本身只接受 `sk_test_…`、拒绝正式 key（`payments_gateway.py`），「真实 key」在此指真实测试 key。

**缺-3（§14 #7）：分享佣金合规两处皆无。**
README 与 API.md 全文无「分享佣金 / 合规评估 / 传销」；两处只描述了助力降价，未写「不上分享佣金、须先过合规评估再做」这一决定（该决定只在研究报告 `FEATURE_RESEARCH_20261116.md:530` #7 与 `:514` 第三批清单中）。

**缺-4（§14 #12）：香港《商品说明条例》与退款两处皆无。**
README 与 API.md 全文无「商品说明条例 / 虚假宣传 / 券面须与实物一致」；套餐详情只列菜品组合、原价/套餐价等字段（`API.md:204-223`），未写券面展示与商户协议的合规要求。

**缺-5（§14 #2 子项）：砍价失败「是否也退助力次数」两处皆无。**
主规则（未成团/超时全额退款、1–3 工作日原路返回）两处皆有（README `:161`、API.md `:182,186`）；但 §14 #2 追问的「砍价失败是否只退钱还是也退助力次数」未在任何一处回答。

**缺-6（§14 #3 子项）：套餐部分使用是否按比例退，两处皆无。**
主规则（随时退/过期退、特价可标不退、已核销不可退）两处皆有；§14 #3 追问的「部分使用（套餐只吃了部分菜）是否按比例退，须逐券标注还是统一规则」未在任何一处回答。

**缺-7（§14 #6 子项）：结算周期「月结」未明示。**
按核销结算与默认 10% 两处皆有；但「T+1 / 周结 / 月结」的选择未在文字中定稿：月度只是代码事实（`Settlement.period` 为 `YYYY-MM`，`models_line_f.py:60-63`「Monthly」），README/API 均未出现「月结」及「T+1/周结已排除」的文字。

**缺-8（§14 #11）：试点商户名单与首批品类未定未写。**
README 已写种子数据是编造演示、非真实商户（`README.md:26,29`），但 §14 #11 要求的试点商户名单、首批品类（餐饮先行？）未定、未写。此为未决事项，建议文档明确标注「待定」而非留空。

### 3.3 §14 十二问对照总表

| §14 # | 问题 | README / API.md 是否写明已定默认值 |
|---|---|---|
| 1 | 返现资金由谁出 | 已定「商户佣金里出」，仅 README（缺-3 表第 3 项：API 缺） |
| 2 | 砍价成团失败怎么退 | 主规则两处有；助力次数子项缺（缺-5） |
| 3 | 套餐退款规则 | 主规则两处有；部分使用子项缺（缺-6） |
| 4 | 地图 Google Maps 还是高德 | 已定 Google Maps，两处有 |
| 5 | 支付用什么 | 已定 FPS + 信用卡 / Stripe 测试模式，两处有 |
| 6 | 佣金 / 结算模式 | 按核销 + 默认 10% 两处有；月结未明示（缺-7） |
| 7 | 分享佣金合规 | 未写（缺-3） |
| 8 | 评价积分与星级 | 已拆开，两处有（见 §2） |
| 9 | 砍价分享助力还是荷兰式降价 | 已定分享助力；API 明确「不是荷兰式降价」（`:182`），README 未提荷兰式备选 |
| 10 | 余额过期规则 | 未写（缺-1） |
| 11 | 商户数据从哪来 | 种子数据为演示已写；试点名单未定未写（缺-8） |
| 12 | 商品说明条例与退款 | 未写（缺-4） |

---

## 4. 汇总

| 节 | 通过 | 有问题 / 缺项 |
|---|---|---|
| §1 F 后台 v2 | 结算口径与 10% 默认、结算/发票状态机守卫、pay 与发票上限的 FIX-3 行锁、对账三种差异、差异单次处理留痕、角色矩阵主体、无漏鉴权端点、merchant-admin 限店、改角色防呆 | **中 3**：F1 auditor 越权创建发票；F2 对账重跑删除已处理差异；F3 结算状态转移除 pay 外无行锁。**低 7**：F4 并发生成 500 非 409；F5 佣金率任意传入；F6 面值无快照；F7 `no_settlement` 报告不转 `resolved`；F8 店主任意拉人自动提权；F9 店主看不到自家店员列表；F10 v1 用户角色筛选对新角色失效。**观察 3**：O1 付款/开票无凭证字段；O2 发票不限结算单状态；O3 解决备注只进审计日志 |
| §2 评价积分与星级拆开 | **6/6 通过**：R1–R6 全部成立 | 0 项有问题；行为说明 N1（旧接口消耗唯一评价位且不留评价行） |
| §3 README 默认假设 | 7 组两处皆有（地图、支付、90 天/HK$50/HK$1,500、套餐退款、砍价分享助力、佣金 10%、无冷静期、评价图片占位——计 7 组含 4a–4d 合并） | **缺 8 项**：缺-1 余额过期 1 年；缺-2 砍价退款真实 key 失败未明示；缺-3 分享佣金合规；缺-4 商品说明条例；缺-5 助力次数退不退；缺-6 部分使用按比例退；缺-7 月结未明示；缺-8 试点商户名单。另部分通过 2 组：返现资金来源与线下 5% 仅 README 有、API.md 缺 |

**最优先处理建议（审计方排序，不含修复代码）：** F1（auditor 发票创建越权，一行权限判断即可收紧）、F2（对账重跑改为保留已处理差异或归档后重建）、F3（`approve/dispute/resolve` 补 `with_for_update`，与 `pay` 对齐）、缺-1/缺-2（两处文档补写，缺-1 需产品先定）。
