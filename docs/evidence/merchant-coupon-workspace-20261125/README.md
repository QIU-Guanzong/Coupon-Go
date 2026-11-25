# Merchant coupon workspace (C1/C2/C3/C6)

New pages: **Merchant home → My coupons / Manage coupons → coupon list → detail**. The dedicated list retains search, status, and scroll position when the detail is closed.

- [Traditional Chinese before/after](journey-zh.webp) · [English before/after](journey-en.webp), Flutter widget-rendered 390×844 panels, with the same synthetic roster and coupon data on both sides.
- Eight selected-state cards: [live](state-active.webp), [pending review](state-pending_review.webp), [paused](state-paused.webp), [draft](state-draft.webp), [rejected](state-rejected.webp), [expired](state-expired.webp), [sold out](state-sold_out.webp), [scheduled](state-scheduled.webp).
- Before confirmation and after the acknowledgement: [pause](action-pause.webp), [resume](action-resume.webp), [unlist](action-unlist.webp), [submit](action-submit.webp). These exercise mocked writes, not live merchant data.

The three C3 cases are documented in the merchant section of [API.md](../../API.md), based on the same eligibility check used by wallet QR issuance, read-only preparation, redemption, and batch redemption. Paused, withdrawn to draft, and awaiting re-review: non-purchased coupons cannot be redeemed; existing paid coupons remain subject to order, refund, validity, branch and other redemption checks. The operation dialog does not imply existing paid orders become refundable or voided.

The API adds opt-in SQL pagination/search to the legacy list and a scoped detail endpoint. Clients that do not send a page get the unchanged legacy response. This batch does not add/edit prices, change money movement, or migrate the database. The full coupon edit workflow belongs to C5; the current detail reuses the existing eligible-branches editor. Coupon-scoped review/history/ranking cross-links wait for the relevant later batches; no fabricated financial or review data is shown.
