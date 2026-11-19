import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/main.dart';
import 'package:coupon_go/screens/account.dart';
import 'package:coupon_go/screens/customer.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

class GuestApi extends ApiClient {
  final calls = <String>[];
  bool fail = false;
  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    calls.add(path);
    if (fail) throw ApiException(503, 'Offline');
    return switch (path) {
      '/get-nearest-branchs' => {
          'branches': [
            {
              'branch_id': 7,
              'branch_name': 'Central',
              'address': 'First Street'
            }
          ]
        },
      '/branch-details' => {'address': 'First Street', 'phone': '1234'},
      '/get-classification-coupons' || '/get-limited-time-coupons' => {
          'coupons': []
        },
      '/bargain-campaigns' => {'campaigns': []},
      '/new-filter-details' => {
          'categories': [
            {'id': 4, 'name': 'Coffee'}
          ]
        },
      _ => {},
    };
  }

  @override
  Future<List<dynamic>> packages({int? shopId}) async => [];
  @override
  Future<List<dynamic>> suggestions(String keyword) async => [
        {'id': 3, 'kind': 'branch', 'title': 'Three shops', 'has_branch': 1},
      ];
}

void main() {
  testWidgets('guest gates preserve the tab and never fetch private lists',
      (tester) async {
    final api = GuestApi();
    await tester.pumpWidget(MaterialApp(
        home: HomeShell(
            api: api,
            authed: false,
            vendorMode: false,
            onAuthed: () {},
            onLogout: () {},
            onToggleMode: () {})));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(
        tester
            .widget<BottomNavigationBar>(find.byType(BottomNavigationBar))
            .items
            .length,
        5);
    for (final label in ['收藏', 'Q Wallet']) {
      await tester.tap(find.text(label));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      expect(find.text('請登錄以繼續'), findsOneWidget);
      await tester.tap(find.text('取消'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      expect(
          tester
              .widget<BottomNavigationBar>(find.byType(BottomNavigationBar))
              .currentIndex,
          0);
    }
    await tester.tap(find.byTooltip('通知'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    await tester.tap(find.text('登錄'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.text('使用電子郵件帳戶繼續'), findsOneWidget);
    await tester.tap(find.text('使用電子郵件帳戶繼續'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    await tester.tap(find.text('忘記密碼？'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    expect(find.text('繼續'), findsOneWidget);
    expect(api.calls, isNot(contains('/favorite-coupon-list')));
  });

  testWidgets('filters recover and cancellation keeps the applied query',
      (tester) async {
    final api = GuestApi()..fail = true;
    await tester.pumpWidget(MaterialApp(
        home: DiscoveryScreen(
            api: api, onOpenCoupon: (_) {}, onOpenSearch: () {})));
    await tester.pumpAndSettle();
    api.fail = false;
    await tester.tap(find.text('重試'));
    await tester.pumpAndSettle();
    expect(find.text('找不到優惠券！'), findsOneWidget);
    await tester.tap(find.text('分類'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Coffee'));
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('分類'));
    await tester.pumpAndSettle();
    expect(
        tester
            .widget<ListTile>(find.widgetWithText(ListTile, 'Coffee'))
            .selected,
        isFalse);
  });

  testWidgets('branch suggestion opens branch list then selected branch',
      (tester) async {
    final api = GuestApi();
    await tester.pumpWidget(MaterialApp(
        home: SearchScreen(
            api: api,
            onOpenShop: (_) => fail('shop route'),
            onOpenCoupon: (_) => fail('coupon route'))));
    await tester.enterText(find.byType(TextField), 'shops');
    await tester.pumpAndSettle();
    await tester.tap(find.text('Three shops'));
    await tester.pumpAndSettle();
    expect(find.text('shops'), findsOneWidget);
    await tester.tap(find.text('Central'));
    await tester.pumpAndSettle();
    expect(find.text('1234'), findsOneWidget);
  });

  testWidgets('language draft commits on submit and survives relaunch',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    final language = AppLanguage();
    await tester.pumpWidget(LanguageScope(
        language: language,
        child: MaterialApp(
            home: SettingsScreen(api: GuestApi(), onLogout: () {}))));
    await tester.tap(find.text('語言'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('English'));
    await tester.pumpAndSettle();
    expect(language.english, isFalse);
    await tester.tap(find.text('提交'));
    await tester.pumpAndSettle();
    expect(find.text('Languages'), findsOneWidget);
    final next = AppLanguage();
    await next.load();
    expect(next.english, isTrue);
  });
}
