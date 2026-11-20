import 'dart:async';

import 'package:flutter/foundation.dart';

/// Public display rules only. Missing/malformed fields never become defaults.
@immutable
class AppConfig {
  const AppConfig({
    this.pointsClaim,
    this.pointsRedeem,
    this.pointsReview,
    this.qrTtlSeconds,
    this.cashbackUnlockDays,
  });

  factory AppConfig.fromJson(Map<String, dynamic> json) {
    int? rule(String key, {bool allowZero = true}) {
      final value = json[key];
      return value is int && value >= (allowZero ? 0 : 1) ? value : null;
    }

    return AppConfig(
      pointsClaim: rule('points_claim'),
      pointsRedeem: rule('points_redeem'),
      pointsReview: rule('points_review'),
      qrTtlSeconds: rule('qr_ttl_seconds', allowZero: false),
      cashbackUnlockDays: rule('cashback_unlock_days', allowZero: false),
    );
  }

  final int? pointsClaim, pointsRedeem, pointsReview;
  final int? qrTtlSeconds, cashbackUnlockDays;
}

/// One in-memory cache per API client, shared by all screens. Never holds up
/// app startup; transient and unsupported endpoint errors retry in background.
class AppConfigStore extends ValueNotifier<AppConfig> {
  AppConfigStore(this._fetch, {this.retryDelay = const Duration(seconds: 30)})
      : super(const AppConfig());

  final Future<Map<String, dynamic>> Function() _fetch;
  final Duration retryDelay;
  Timer? _retry;
  bool _loading = false, _loaded = false, _disposed = false;
  int _retries = 0;

  Future<void> load() async {
    if (_loading || _loaded || _disposed) return;
    _retry?.cancel();
    _loading = true;
    try {
      final data = await _fetch().timeout(const Duration(seconds: 8));
      if (_disposed) return;
      _loaded = true;
      value = AppConfig.fromJson(data);
    } catch (_) {
      if (!_disposed && _retries++ < 3) {
        _retry = Timer(retryDelay, load);
      }
    } finally {
      _loading = false;
    }
  }

  @override
  void dispose() {
    _disposed = true;
    _retry?.cancel();
    super.dispose();
  }
}
