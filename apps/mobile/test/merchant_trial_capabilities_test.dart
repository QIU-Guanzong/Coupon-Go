import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/merchant_branches.dart';
import 'package:coupon_go/screens/merchant_onboarding.dart';
import 'package:coupon_go/screens/merchant_staff.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:coupon_go/screens/vendor_packages.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const trial = bool.fromEnvironment('PUBLIC_TRIAL');

Finder filledButton(String text) => find.ancestor(
    of: find.text(text), matching: find.byWidgetPredicate((widget) => widget is FilledButton));

class TrialMerchantApi extends ApiClient {
  int writes = 0;
  int _session = 0;
  Map<String, dynamic>? application;
  Completer<void>? submitGate;

  @override
  int get sessionRevision => _session;

  void switchIdentity() {
    _session++;
    notifyListeners();
  }

  @override
  bool get isLoggedIn => true;

  @override
  Future<Map<String, dynamic>> myMerchantApplications() async => {
    'applications': [if (application != null) application],
  };

  @override
  Future<Map<String, dynamic>> submitMerchantApplication(Map<String, dynamic> body) async {
    expect(body, {'shop_name': '海港小店'});
    writes++;
    if (submitGate != null) await submitGate!.future;
    application = {'id': 77, ...body, 'status': 'pending'};
    return {'application': application};
  }

  @override
  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) async {
    if (path == '/merchant/shops') {
      return {'shops': [{'id': 1, 'name': '示例店', 'can_manage_branches': true,
        'can_manage_members': true, 'can_manage_coupons': true}]};
    }
    if (path == '/merchant/shops/1/branches') {
      return {'branches': [{'id': 11, 'name': '示例分店'}]};
    }
    if (path == '/admin/shops/1/members') return {'members': []};
    throw ApiException(404, '找不到');
  }

  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    writes++;
    throw ApiException(403, '體驗版不提供此操作');
  }

  @override
  Future<Map<String, dynamic>> vendorPackageWorkspace() async => {
    'shops': [{'id': 1, 'name': '示例店'}],
    'packages': [{'id': 9, 'name': '示例套餐', 'shop_name': '示例店',
      'status': 'active', 'package_price': 100, 'stock_left': 5, 'sold_count': 0}],
  };
}

void main() {
  testWidgets('trial hides branch contact and staff mutations', (tester) async {
    final api = TrialMerchantApi();
    await tester.pumpWidget(MaterialApp(home: MerchantBranchesScreen(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('示例分店'), findsOneWidget);
    expect(find.text('新增分店'), findsNothing);
    expect(find.text('編輯'), findsNothing);
    await tester.pumpWidget(MaterialApp(home: MerchantStaffScreen(api: api, shopId: 1)));
    await tester.pumpAndSettle();
    expect(find.text('邀請店員'), findsNothing);
    expect(api.writes, 0);
  }, skip: !trial);

  testWidgets('trial reads and exposes package mutation controls', (tester) async {
    final api = TrialMerchantApi();
    await tester.pumpWidget(MaterialApp(home: VendorPackagesScreen(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('示例套餐'), findsOneWidget);
    expect(tester.widget<FilledButton>(filledButton('建立套餐')).onPressed, isNotNull);
    expect(tester.widget<TextButton>(find.widgetWithText(TextButton, '編輯')).onPressed, isNotNull);
    expect(tester.widget<TextButton>(find.widgetWithText(TextButton, '暫停上架')).onPressed, isNotNull);
    expect(api.writes, 0);
  }, skip: !trial);

  testWidgets('trial application has preset shop only and displays gateway status',
      (tester) async {
    final api = TrialMerchantApi();
    await tester.pumpWidget(MaterialApp(home: MerchantOnboardingScreen(api: api)));
    await tester.pumpAndSettle();
    final selector = find.byKey(const ValueKey('preset-merchant-shop'));
    final submit = find.byKey(const ValueKey('preset-merchant-submit'));
    expect(find.byType(TextField), findsNothing);
    expect(tester.widget<FilledButton>(submit).onPressed, isNull);
    await tester.tap(selector);
    await tester.pumpAndSettle();
    await tester.tap(find.text('海港小店').last);
    await tester.pumpAndSettle();
    await tester.tap(submit);
    await tester.pumpAndSettle();
    expect(api.writes, 1);
    expect(find.text('待審核'), findsOneWidget);
    expect(submit, findsNothing);
  }, skip: !trial);

  testWidgets('late application receipt cannot be shown to a different identity',
      (tester) async {
    final api = TrialMerchantApi()..submitGate = Completer<void>();
    await tester.pumpWidget(MaterialApp(home: MerchantOnboardingScreen(api: api)));
    await tester.pumpAndSettle();
    final selector = find.byKey(const ValueKey('preset-merchant-shop'));
    final submit = find.byKey(const ValueKey('preset-merchant-submit'));
    await tester.tap(selector);
    await tester.pumpAndSettle();
    await tester.tap(find.text('海港小店').last);
    await tester.pumpAndSettle();
    await tester.tap(submit);
    await tester.pump();
    expect(api.writes, 1);
    api.switchIdentity();
    api.submitGate!.complete();
    await tester.pumpAndSettle();
    expect(find.text('請從企業服務重新開啟申請。'), findsOneWidget);
    expect(find.text('待審核'), findsNothing);
    expect(submit, findsNothing);
  }, skip: !trial);

  testWidgets('coupon draft survives back cancellation', (tester) async {
    final api = TrialMerchantApi();
    await tester.pumpWidget(MaterialApp(home: Builder(builder: (context) => Scaffold(
      body: TextButton(onPressed: () => Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => IssueCouponScreen(api: api))), child: const Text('開始'))))));
    await tester.tap(find.text('開始'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.byType(TextField).first);
    await tester.enterText(find.byType(TextField).first, '示例草稿');
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.text('放棄尚未儲存的內容？'), findsOneWidget);
    await tester.tap(find.text('繼續編輯'));
    await tester.pumpAndSettle();
    expect(find.text('示例草稿'), findsOneWidget);
    expect(api.writes, 0);
  });
}
