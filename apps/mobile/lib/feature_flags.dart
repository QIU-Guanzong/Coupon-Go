/// Cashback entry points are hidden by default. Build with
/// --dart-define=COUPON_CASHBACK_ENABLED=true to enable them explicitly.
const cashbackEnabled = bool.fromEnvironment(
  'COUPON_CASHBACK_ENABLED',
  defaultValue: false,
);

/// A browse-only preview must not override the interactive trial when both
/// build flags are present.
const publicPreview = bool.fromEnvironment('PUBLIC_DEMO') &&
    !bool.fromEnvironment('PUBLIC_TRIAL');
