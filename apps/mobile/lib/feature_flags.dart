/// Cashback entry points are hidden by default. Build with
/// --dart-define=COUPON_CASHBACK_ENABLED=true to enable them explicitly.
const cashbackEnabled = bool.fromEnvironment(
  'COUPON_CASHBACK_ENABLED',
  defaultValue: false,
);

/// Public web interactions use a browser-isolated gateway and identity.
const publicTrial = bool.fromEnvironment('PUBLIC_TRIAL');
