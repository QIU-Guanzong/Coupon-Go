import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:coupon_go/screens/vendor_packages.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class MerchantPackageApi extends ApiClient {
  MerchantPackageApi({this.nextStatus = 'active', this.noShop = false});
  final String nextStatus;
  final bool noShop;
  bool failUncertain = false;
  int submissions = 0;
  Map<String, dynamic>? lastBody;
  final List<Map<String, dynamic>> entries = [];

  @override
  Future<Map<String, dynamic>> usageData() async => {};
  @override
  Future<List<dynamic>> vendorCoupons() async => [];
  @override
  Future<Map<String, dynamic>> vendorPackageWorkspace() async => {
        'shops': noShop
            ? []
            : [
                {'id': 8, 'name': '灣仔店'},
                {'id': 12, 'name': '中環店'}
              ],
        'packages': entries,
      };
  @override
  Future<Map<String, dynamic>> createVendorPackage(
      Map<String, dynamic> body) async {
    submissions++;
    lastBody = body;
    if (failUncertain) throw Exception('connection dropped');
    final row = {
      ...body,
      'id': 22,
      'shop_name': '灣仔店',
      'status': nextStatus,
      'stock_left': body['stock_total'],
      'sold_count': 0
    };
    entries.add(row);
    return {'package': row};
  }

  @override
  Future<Map<String, dynamic>> editVendorPackage(
      int id, Map<String, dynamic> body) async {
    lastBody = body;
    final index = entries.indexWhere((e) => e['id'] == id);
    entries[index] = {...entries[index], ...body, 'status': nextStatus};
    return {'package': entries[index]};
  }

  @override
  Future<Map<String, dynamic>> vendorPackageAction(
      int id, String action) async {
    final index = entries.indexWhere((e) => e['id'] == id);
    entries[index] = {
      ...entries[index],
      'status': action == 'pause' ? 'paused' : 'active'
    };
    return {'status': entries[index]['status']};
  }
}

Widget app(Widget screen) => LanguageScope(
    language: AppLanguage(),
    child: MaterialApp(theme: CgTheme.light(), home: screen));

Future<void> fillForm(WidgetTester tester) async {
  final fields = find.byType(TextFormField);
  await tester.enterText(fields.at(0), '雙人午餐');
  await tester.enterText(fields.at(1), '主菜 ×2\n茶');
  await tester.enterText(fields.at(2), '200');
  await tester.enterText(fields.at(3), '150');
  await tester.enterText(fields.at(4), '20');
  await tester.ensureVisible(find.text('儲存套餐'));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('merchant navigation opens an honest empty workspace',
      (tester) async {
    final api = MerchantPackageApi();
    await tester.pumpWidget(app(VendorHomeScreen(api: api)));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('套餐管理'));
    await tester.tap(find.text('套餐管理'));
    await tester.pumpAndSettle();
    expect(find.text('尚未建立套餐'), findsOneWidget);
    expect(find.text('建立套餐'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  for (final status in ['active', 'pending_review']) {
    testWidgets('creation displays authoritative $status status',
        (tester) async {
      final api = MerchantPackageApi(nextStatus: status);
      tester.view.resetPhysicalSize();
      tester.view.physicalSize = const Size(390, 844);
      tester.view.devicePixelRatio = 1;
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      await tester.pumpWidget(app(VendorPackagesScreen(api: api)));
      await tester.pumpAndSettle();
      await tester.tap(find.text('建立套餐'));
      await tester.pumpAndSettle();
      await fillForm(tester);
      await tester.tap(find.text('儲存套餐'));
      await tester.pumpAndSettle();
      expect(api.lastBody?['shop_id'], 8);
      expect(api.lastBody?['items'], [
        {'name': '主菜', 'qty': 2},
        {'name': '茶', 'qty': 1}
      ]);
      expect(find.text(status == 'active' ? '上架中' : '待審核'), findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('uncertain create cannot be blindly repeated', (tester) async {
    final api = MerchantPackageApi()..failUncertain = true;
    await tester.pumpWidget(app(VendorPackagesScreen(api: api)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('建立套餐'));
    await tester.pumpAndSettle();
    await fillForm(tester);
    await tester.tap(find.text('儲存套餐'));
    await tester.pumpAndSettle();
    expect(find.textContaining('無法確認是否已儲存'), findsOneWidget);
    expect(
        tester
            .widget<FilledButton>(find.widgetWithText(FilledButton, '儲存套餐'))
            .onPressed,
        isNull);
    expect(api.submissions, 1);
    await tester.tap(find.text('返回並重新載入'));
    await tester.pumpAndSettle();
    expect(find.text('尚未建立套餐'), findsOneWidget);
  });

  testWidgets('account with no store cannot create', (tester) async {
    await tester.pumpWidget(
        app(VendorPackagesScreen(api: MerchantPackageApi(noShop: true))));
    await tester.pumpAndSettle();
    expect(find.textContaining('尚未有可管理的商店'), findsOneWidget);
    expect(
        tester
            .widget<ButtonStyleButton>(find.ancestor(
                of: find.text('建立套餐'),
                matching:
                    find.byWidgetPredicate((w) => w is ButtonStyleButton)))
            .onPressed,
        isNull);
  });

  testWidgets('edit retains existing rules and unchanged date values',
      (tester) async {
    final api = MerchantPackageApi();
    api.entries.add({
      'id': 9,
      'shop_id': 8,
      'shop_name': '灣仔店',
      'name': '舊套餐',
      'items': [
        {'name': '主菜', 'qty': 2}
      ],
      'person_count': 2,
      'original_price': 200.0,
      'package_price': 150.0,
      'stock_total': 20,
      'stock_left': 18,
      'sold_count': 2,
      'status': 'active',
      'valid_from': '2026-11-01T00:00:00',
      'valid_to': '2030-12-31T15:59:59',
      'refund_policy': 'anytime',
      'usage_rules': {
        'need_booking': true,
        'includes': ['茶']
      },
    });
    await tester.pumpWidget(app(VendorPackagesScreen(api: api)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('編輯'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField).first, '更新套餐');
    tester.testTextInput.hide();
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('儲存套餐'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('儲存套餐'));
    await tester.pumpAndSettle();
    expect(api.lastBody?['name'], '更新套餐');
    expect(api.lastBody?.containsKey('valid_from'), isFalse);
    expect(api.lastBody?.containsKey('valid_to'), isFalse);
    expect(api.lastBody?.containsKey('items'), isFalse);
    expect(api.lastBody?.containsKey('usage_rules'), isFalse);
    expect(find.text('更新套餐'), findsOneWidget);
  });

  testWidgets('pause uses server status and draft is protected on back',
      (tester) async {
    final api = MerchantPackageApi();
    api.entries.add({
      'id': 9,
      'shop_id': 8,
      'shop_name': '灣仔店',
      'name': '套餐',
      'package_price': 150,
      'stock_left': 5,
      'sold_count': 0,
      'status': 'active'
    });
    await tester.pumpWidget(app(VendorPackagesScreen(api: api)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('暫停上架'));
    await tester.pumpAndSettle();
    expect(find.text('已暫停'), findsOneWidget);
    await tester.tap(find.text('建立套餐'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField).first, '未儲存');
    await tester.pumpAndSettle();
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.text('放棄尚未儲存的內容？'), findsOneWidget);
    await tester.tap(find.text('繼續編輯'));
    await tester.pumpAndSettle();
    expect(find.text('未儲存'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.text('放棄'));
    await tester.pumpAndSettle();
    expect(find.text('我的套餐'), findsOneWidget);
  });

  testWidgets('merchant selects the correct store', (tester) async {
    final api = MerchantPackageApi();
    await tester.pumpWidget(app(VendorPackagesScreen(api: api)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('建立套餐'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('灣仔店').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('中環店').last);
    await tester.pumpAndSettle();
    await fillForm(tester);
    await tester.tap(find.text('儲存套餐'));
    await tester.pumpAndSettle();
    expect(api.lastBody?['shop_id'], 12);
  });
}
