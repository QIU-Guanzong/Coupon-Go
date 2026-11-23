import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/merchant_branches.dart';
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
  testWidgets('trial branch and staff management explain unavailable writes', (tester) async {
    final api = TrialMerchantApi();
    await tester.pumpWidget(MaterialApp(home: MerchantBranchesScreen(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('體驗版可查看分店，暫不支援新增或修改。'), findsOneWidget);
    expect(tester.widget<FilledButton>(filledButton('新增分店')).onPressed, isNull);
    expect(tester.widget<TextButton>(find.widgetWithText(TextButton, '編輯')).onPressed, isNull);
    await tester.pumpWidget(MaterialApp(home: MerchantStaffScreen(api: api, shopId: 1)));
    await tester.pumpAndSettle();
    expect(find.text('體驗版可查看店員，暫不支援邀請或修改角色。'), findsOneWidget);
    expect(tester.widget<FilledButton>(filledButton('邀請店員')).onPressed, isNull);
    expect(api.writes, 0);
  }, skip: !trial);

  testWidgets('trial package list is readable but mutation controls are disabled', (tester) async {
    final api = TrialMerchantApi();
    await tester.pumpWidget(MaterialApp(home: VendorPackagesScreen(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('體驗版可查看套餐，暫不支援建立、編輯或調整上架狀態。'), findsOneWidget);
    expect(find.text('示例套餐'), findsOneWidget);
    expect(tester.widget<FilledButton>(filledButton('建立套餐')).onPressed, isNull);
    expect(tester.widget<TextButton>(find.widgetWithText(TextButton, '編輯')).onPressed, isNull);
    expect(tester.widget<TextButton>(find.widgetWithText(TextButton, '暫停上架')).onPressed, isNull);
    expect(api.writes, 0);
  }, skip: !trial);

  testWidgets('coupon draft survives back cancellation', (tester) async {
    final api = TrialMerchantApi();
    await tester.pumpWidget(MaterialApp(home: Builder(builder: (context) => Scaffold(
      body: TextButton(onPressed: () => Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => IssueCouponScreen(api: api))), child: const Text('開始'))))));
    await tester.tap(find.text('開始'));
    await tester.pumpAndSettle();
    if (trial) {
      expect(find.text('體驗版可填寫內容，暫不支援提交優惠券。'), findsOneWidget);
      await tester.scrollUntilVisible(find.text('檢查並提交'), 250,
          scrollable: find.descendant(of: find.byType(IssueCouponScreen),
              matching: find.byType(Scrollable)).first);
      expect(tester.widget<ElevatedButton>(find.widgetWithText(ElevatedButton, '檢查並提交')).onPressed, isNull);
    }
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
