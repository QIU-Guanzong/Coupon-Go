/// Cashback is available by default. Build with
/// --dart-define=COUPON_CASHBACK_ENABLED=false for an emergency shutdown.
const cashbackEnabled = bool.fromEnvironment(
  'COUPON_CASHBACK_ENABLED',
  defaultValue: true,
);
