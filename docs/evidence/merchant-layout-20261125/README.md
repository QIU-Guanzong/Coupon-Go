# Merchant layout verification

The phone panels show the same route with the same read-only fixture before and after: **Traditional Chinese before → after, English before → after**. Every viewport is 390×844. Baseline: Coupon-Go PR #56 head `c603370e4d58f7d707590105a6ea35482170178b`. The images were rendered locally with the Flutter engine and bundled fonts, not captured from a production user account or a physical phone.

| Route / surface | Comparison | Checks |
| --- | --- | --- |
| Merchant home | [Tools](tools.webp) | English labels fit two equal-height columns at 390px; primary CTA uses shared 56px style. |
| Redemption history | [Date filters and records](records.webp) · [130% Chinese / English](records-130.webp) | Date picker actually selected in both controls; clear stays underneath. |
| Single redemption | [Preflight details](scan.webp) | Six labels and values; manual code and branch selection; no redemption request. |
| Issue coupon | [Top](issue.webp) · [Lower](issue-lower.webp) | Three headings; field order preserved. The third heading says “Validity and terms”: this form has no refund field. |
| Merchant account | [Balances and sections](account.webp) | Section titles, amount typography and state pill. |
| Dashboard | [Overview and filters](dashboard.webp) | Outlined date filter, imported values, section and amount styles. |
| Payout | [Payout form](payout.webp) | Layout only; no Stripe or payout call. |
| Shops and branches | [List](branches.webp) | Side inset and shared primary action. |
| Merchant application | [Form](onboarding.webp) | Form spacing and heading hierarchy. |
| Staff | [Staff workspace](staff.webp) | Primary action; the full suite additionally exercises staff rows and role/scope rules. |
| Usage | [Metrics and CSV entry](usage.webp) | Cards and AppBar export. |
| Packages | [List](packages.webp) | Desktop/list width and shared creation CTA. |
| Admin link | [Admin home](admin.webp) | Shared red AppBar and translated menu. |
| Desktop 1440×980 | [Home / account / dashboard](desktop-1.webp) · [Issue / payout](desktop-2.webp) | Rendered bounds measured at 880px (list) and 720px (form). Panels scaled to fit. |

The fixtures contain no customer data. Tests also cover request order, role boundaries and preserved form payloads. Approval here does not imply hardware camera, real merchant/financial flows, native browser integration or deployment verification.
