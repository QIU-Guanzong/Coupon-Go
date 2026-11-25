# V7 merchant theme verification

Exact base `e1c4fb3bc6556df70e4ab0cdc74bb946a5edd615`,
exact reviewed PR #56 head `c603370e4d58f7d707590105a6ea35482170178b`.
The nine before/after source and test blobs were verified against both commits.

- 390×844, Chinese and English: [merchant](phone-merchant.webp) and [customer](phone-consumer.webp).
- 1440×980, Chinese and English: [merchant](desktop-merchant.webp) and [customer](desktop-consumer.webp).
- 390×844 / 200% text: [merchant](zoom-merchant.webp). Merchant captured without framework exception in either language. The pre-existing consumer English 200% visual probe reported framework exceptions on both base and head; not counted as a customer pass.
- All panels are local Flutter renderings using isolated seeded responses, not physical-device/live-merchant evidence.

## Actual test-output excerpts

```text
flutter analyze --no-pub lib test
Analyzing 2 items...
No issues found! (ran in 6.8s)

flutter test --no-pub  [default]
+253 ~22: All tests passed!

flutter test --no-pub --dart-define=PUBLIC_TRIAL=true [four targeted suites]
+22 ~1: All tests passed!
```

Targeted suites: `vendor_shared_theme`, `vendor_stack_integration`,
`navigation_d4`, `scan_branch_choice`. The theme suite checks contrast at 4.5:1,
tab continuity and 390px tool geometry at 100% and 200% text. No backend or payment changes,
no Actions run, no deployment or real camera verification.
