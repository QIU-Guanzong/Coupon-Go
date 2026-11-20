import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/app_config.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/account.dart';
import 'package:coupon_go/screens/customer.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _app(Widget screen) => LanguageScope(
      language: AppLanguage()..english = true,
      child: MaterialApp(home: screen),
    );

class _ConfigApi extends ApiClient {
  _ConfigApi(this.data);
  final Future<Map<String, dynamic>> data;
  int reads = 0;
  @override
  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) {
    expect(path, '/app-config');
    reads++;
    return data;
  }
}

class _ClaimApi extends ApiClient {
  @override
  bool get isLoggedIn => true;
  @override
  Future<Map<String, dynamic>> couponDetails(int id) async => {
        'coupon': {'id': id, 'remaining': 3}
      };
  @override
  Future<Map<String, dynamic>> claimCoupon(int id) async => {
        'coupon': {'id': id, 'remaining': 2},
        'wallet_item_id': 21
      };
}

void main() {
  testWidgets('FAQ renders cached live rules but not exchange cost',
      (tester) async {
    final data = Completer<Map<String, dynamic>>();
    final api = _ConfigApi(data.future);
    addTearDown(api.dispose);
    final inFlight = api.appConfig.load();
    unawaited(api.appConfig.load());

    await tester.pumpWidget(_app(GuideScreen(api: api)));
    await tester.tap(find.text('What are points for?'));
    await tester.pumpAndSettle();
    expect(find.textContaining('+'), findsNothing);
    expect(api.reads, 1);

    data.complete({
      'points_claim': 23,
      'points_redeem': 34,
      'points_review': 45,
      'qr_ttl_seconds': 715,
      'cashback_unlock_days': 61,
    });
    await inFlight;
    await api.appConfig.load();
    await tester.pumpAndSettle();
    expect(api.reads, 1);
    expect(find.text('Claiming a coupon: +23 points'), findsOneWidget);
    expect(find.text('Redeeming a coupon: +34 points'), findsOneWidget);
    expect(find.text('Eligible review: +45 points'), findsOneWidget);
    await tester.scrollUntilVisible(
        find.text('How long is the QR code valid?'), 100);
    await tester.tap(find.text('How long is the QR code valid?'));
    await tester.pumpAndSettle();
    expect(find.text('Maximum code lifetime: 715 sec.'), findsOneWidget);
    expect(find.textContaining('9 points'), findsNothing);
    expect(find.textContaining('61'), findsNothing);
  });

  testWidgets('old API failure leaves guide usable with no guessed values',
      (tester) async {
    final data = Completer<Map<String, dynamic>>();
    final api = _ConfigApi(data.future);
    final pending = api.appConfig.load();
    data.completeError(ApiException(404, 'Not available'));
    await pending;
    await tester.pumpWidget(_app(GuideScreen(api: api)));
    await tester.tap(find.text('What are points for?'));
    await tester.pumpAndSettle();
    expect(
        find.text(
            'Check Q Wallet for your points balance and available rewards.'),
        findsOneWidget);
    expect(find.textContaining('points: +'), findsNothing);
    await tester.scrollUntilVisible(
        find.text('How long is the QR code valid?'), 100);
    await tester.tap(find.text('How long is the QR code valid?'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Maximum code lifetime'), findsNothing);
    expect(find.textContaining('Use the countdown'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    api.dispose(); // cancel retries inside this fake-clock widget test
    // Dispose-on-exit is handled by the app normally.
  });

  testWidgets('retry recovers without defaults and keeps valid partial fields',
      (tester) async {
    var reads = 0;
    final store = AppConfigStore(() async {
      if (++reads == 1) throw ApiException(503, 'Offline');
      return {
        'points_claim': 28,
        'points_redeem': '20',
        'points_review': -1,
        'qr_ttl_seconds': 0
      };
    }, retryDelay: const Duration(seconds: 1));
    await store.load();
    expect(store.value.pointsClaim, isNull);
    await tester.pump(const Duration(seconds: 1));
    await tester.pump();
    expect(store.value.pointsClaim, 28);
    expect(store.value.pointsRedeem, isNull);
    expect(store.value.pointsReview, isNull);
    expect(store.value.qrTtlSeconds, isNull);
    expect(reads, 2);
    store.dispose();
  });

  testWidgets('claim toast adds points only when rule is available',
      (tester) async {
    for (final points in [null, 27]) {
      final api = _ClaimApi();
      api.appConfig.value = AppConfig(pointsClaim: points);
      await tester.pumpWidget(_app(CouponDetailScreen(
        key: ValueKey(points),
        api: api,
        coupon: Coupon.fromJson({'id': 7, 'name': 'Example coupon'}),
      )));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Claim now'));
      await tester.pumpAndSettle();
      expect(
          find.text(points == null
              ? 'Saved to Q Wallet'
              : 'Saved to Q Wallet, +27 points'),
          findsOneWidget);
      if (points == null) expect(find.textContaining('+10'), findsNothing);
      await tester.pumpWidget(const SizedBox());
      api.dispose();
    }
  });
}
