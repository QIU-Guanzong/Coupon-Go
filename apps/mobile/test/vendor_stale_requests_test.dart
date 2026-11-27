import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/trial_banner.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

class DeferredVendorApi extends ApiClient {
  final shops = Completer<Map<String, dynamic>>();
  final usage = Completer<Map<String, dynamic>>();
  int usageCalls = 0;
  int couponCalls = 0;
  bool failSwitch = false;
  bool failPersistence = false;

  @override
  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) {
    if (path == '/merchant/shops') return shops.future;
    throw StateError('Unexpected request: $path');
  }

  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    if (failSwitch) throw StateError('switch cancelled');
    return {'access_token': 'other-role'};
  }

  @override
  Future<void> prepareTrialToken(String? token) async {
    if (failPersistence) throw StateError('storage failed');
    await super.prepareTrialToken(token);
  }

  @override
  Future<Map<String, dynamic>> usageData() { usageCalls++; return usage.future; }
  @override
  Future<List<dynamic>> vendorCoupons({String? status}) async { couponCalls++; return []; }
}

const merchant = {'shops': [
  {'id': 8, 'name': '測試店', 'is_owner': true, 'can_manage_coupons': true},
]};

Widget shell(Widget child) => LanguageScope(language: AppLanguage(),
    child: MaterialApp(home: child));

Future<void> openTrialSelector(WidgetTester tester) async {
  final controller = tester.state<TrialBannerState>(find.byType(TrialBanner));
  unawaited(controller.open(tester.element(find.byType(Scaffold).first)));
  await tester.pump(const Duration(milliseconds: 300));
}

void main() {
  testWidgets('disposed merchant page does not request usage after shops completes', (tester) async {
    final api = DeferredVendorApi();
    await tester.pumpWidget(shell(VendorHomeScreen(api: api)));
    await tester.pumpWidget(const MaterialApp(home: Text('guest home')));
    api.activateTrialToken(null);
    api.shops.complete(merchant);
    await tester.pump();
    expect(api.usageCalls, 0);
    expect(api.couponCalls, 0);
    expect(tester.takeException(), isNull);
  });

  testWidgets('disposed merchant page ignores late usage after its parallel coupon read', (tester) async {
    final api = DeferredVendorApi();
    await tester.pumpWidget(shell(VendorHomeScreen(api: api)));
    api.shops.complete(merchant);
    await tester.pump();
    expect(api.usageCalls, 1);
    expect(api.couponCalls, 1);
    await tester.pumpWidget(const MaterialApp(home: Text('guest home')));
    api.activateTrialToken(null);
    api.usage.complete({});
    await tester.pump();
    expect(api.couponCalls, 1);
    expect(find.text('guest home'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  for (final failure in ['cancelled', 'storage']) {
    testWidgets('$failure identity switch leaves merchant load running', (tester) async {
      SharedPreferences.setMockInitialValues({});
      final api = DeferredVendorApi();
      if (failure == 'cancelled') api.failSwitch = true;
      if (failure == 'storage') api.failPersistence = true;
      var changed = false;
      await tester.pumpWidget(shell(Scaffold(body: Column(children: [
        Expanded(child: VendorHomeScreen(api: api)),
        TrialBanner(api: api, onRoleChanging: () async { changed = true; },
            onRoleChangeFailed: () {}, onRoleChanged: (_) {}),
      ]))));
      await openTrialSelector(tester);
      await tester.tap(find.text('顧客'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 300));
      await tester.pump();
      await tester.pump();
      expect(changed, isFalse);
      expect(find.text('切換失敗，請重試'), findsOneWidget);
      api.shops.complete(merchant);
      await tester.pump();
      expect(api.usageCalls, 1);
      api.usage.complete({});
      await tester.pump();
      expect(api.couponCalls, 1);
      expect(find.text('企業版'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }
}
