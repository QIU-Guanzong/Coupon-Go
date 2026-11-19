import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/main.dart';
import 'package:coupon_go/screens/account.dart' show WalletScreen;
import 'package:coupon_go/screens/customer.dart';
import 'package:coupon_go/screens/guest_auth.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _NavApi extends ApiClient {
  _NavApi({this.role, this.name = 'Tea House'});
  String? role;
  final String name;
  bool failBargains = false;
  bool hasBargains = false;
  int meCalls = 0, cashbackCalls = 0;
  Completer<Map<String, dynamic>>? pendingMe;

  @override
  bool get isLoggedIn => role != null;

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    if (path == '/me') {
      meCalls++;
      return pendingMe?.future ?? {'role': role, 'name': name};
    }
    if (path == '/get-limited-time-coupons') {
      return {'coupons': <dynamic>[]};
    }
    return {};
  }

  @override
  Future<Map<String, dynamic>> wallet({int? status, int page = 1}) async =>
      {'items': <dynamic>[], 'total_pages': 1};

  @override
  Future<Map<String, dynamic>> cashback() async {
    cashbackCalls++;
    return {'pending_balance': 0, 'withdrawable_balance': 0};
  }

  @override
  Future<List<dynamic>> vendorCoupons() async => [];

  @override
  Future<List<dynamic>> bargainCampaigns() async {
    if (failBargains) throw ApiException(503, 'Unavailable');
    return [
      if (hasBargains)
        {
          'id': 3,
          'name': 'Tea set',
          'original_price': 80,
          'floor_price': 25,
          'stock_left': 3,
          'stock_total': 5,
          'status': 'active',
        },
      {'id': 9, 'name': 'Sold out set', 'stock_left': 0},
    ];
  }
}

Widget _app(Widget screen, {bool english = true}) => LanguageScope(
      language: AppLanguage()..english = english,
      child: MaterialApp(
        theme: CgTheme.light(),
        home: screen,
        routes: {
          '/vendor/plans': (_) =>
              Scaffold(appBar: AppBar(title: const Text('Plan page')))
        },
      ),
    );

Widget _home(_NavApi api, {bool vendorMode = true}) => HomeShell(
      api: api,
      authed: api.isLoggedIn,
      vendorMode: vendorMode,
      onAuthed: () {},
      onLogout: () {},
      onToggleMode: () {},
    );

void main() {
  testWidgets(
      'only a verified admin sees Administration; the application remains',
      (tester) async {
    for (final role in ['vendor', 'admin']) {
      final api = _NavApi(role: role)..pendingMe = Completer();
      await tester.pumpWidget(_app(_home(api)));
      final outer = find.byType(Scaffold).first;
      tester.state<ScaffoldState>(outer).openDrawer();
      await tester.pumpAndSettle();
      expect(find.text('Administration'), findsNothing);
      await tester.scrollUntilVisible(find.text('Merchant application'), 150,
          scrollable: find.descendant(
              of: find.byType(Drawer), matching: find.byType(Scrollable)));
      expect(find.text('Merchant application'), findsOneWidget);
      expect(find.text('Invite Friends'), findsNothing);
      api.pendingMe!.complete({'role': role, 'name': 'Tea House'});
      await tester.pumpAndSettle();
      expect(find.text('Administration'),
          role == 'admin' ? findsOneWidget : findsNothing);
      await tester.pumpWidget(const SizedBox());
    }
  });

  testWidgets(
      'merchant tabs select Account and put its subscription link there',
      (tester) async {
    await tester
        .pumpWidget(_app(_home(_NavApi(role: 'vendor', name: 'Tea House'))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Account'));
    await tester.pumpAndSettle();
    final nav =
        tester.widget<BottomNavigationBar>(find.byType(BottomNavigationBar));
    expect(nav.currentIndex, 3);
    expect(nav.items.map((e) => e.label),
        ['Home', 'Scan', 'Analytics', 'Account']);
    expect(find.text('Merchant account'), findsOneWidget);
    expect(find.text('Tea House'), findsOneWidget);
    await tester.tap(find.text('Subscription plans'));
    await tester.pumpAndSettle();
    expect(find.text('Plan page'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.text('Merchant account'), findsOneWidget);
  });

  testWidgets('limited bargains are optional, retryable and active-only',
      (tester) async {
    final api = _NavApi()..failBargains = true;
    await tester
        .pumpWidget(_app(LimitedTimeScreen(api: api, onOpenCoupon: (_) {})));
    await tester.pumpAndSettle();
    expect(find.text('Could not load bargains'), findsOneWidget);
    api.failBargains = false;
    api.hasBargains = true;
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.text('Flash bargains'), findsOneWidget);
    expect(find.text('Tea set'), findsOneWidget);
    expect(find.text('Sold out set'), findsNothing);
    await tester.pumpWidget(const SizedBox());
    await tester.pumpWidget(
        _app(LimitedTimeScreen(api: _NavApi(), onOpenCoupon: (_) {})));
    await tester.pumpAndSettle();
    expect(find.text('Flash bargains'), findsNothing);
  });

  testWidgets('cashback flag controls wallet entry and balance reads',
      (tester) async {
    final api = _NavApi(role: 'customer');
    await tester.pumpWidget(_app(Scaffold(
      body: WalletScreen(api: api, onShowQr: (_) async {}),
    )));
    await tester.pumpAndSettle();
    expect(
        find.text('返現（可提現）'), cashbackEnabled ? findsOneWidget : findsNothing);
    expect(api.cashbackCalls, cashbackEnabled ? 1 : 0);
  });

  testWidgets('cashback direct route is not built when its flag is off',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const CouponGoApp());
    await tester.pump();
    final app = tester.widget<MaterialApp>(find.byType(MaterialApp));
    expect(app.onGenerateRoute!(const RouteSettings(name: '/cashback')),
        cashbackEnabled ? isA<MaterialPageRoute>() : isNull);
  });

  testWidgets('unavailable social options are hidden', (tester) async {
    await tester.pumpWidget(_app(AuthScreen(api: _NavApi(), onAuthed: () {})));
    await tester.pumpAndSettle();
    expect(find.text('Continue with Email account'), findsOneWidget);
    expect(find.textContaining('Google'), findsNothing);
    expect(find.textContaining('Facebook'), findsNothing);
  });
}
