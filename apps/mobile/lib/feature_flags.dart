/// Cashback entry points are hidden by default. Build with
/// --dart-define=COUPON_CASHBACK_ENABLED=true to enable them explicitly.
const cashbackEnabled = bool.fromEnvironment(
  'COUPON_CASHBACK_ENABLED',
  defaultValue: false,
);
