/// Open only after the cashback backend is integrated and the release owner
/// confirms availability. An unset build flag must not expose the feature.
const cashbackEnabled = bool.fromEnvironment('COUPON_CASHBACK_ENABLED');
