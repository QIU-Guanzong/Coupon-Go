import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/main.dart';
import 'package:coupon_go/screens/account.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

class FixtureApi extends ApiClient {
  FixtureApi({this.reset = false});

  final bool reset;
  final roles = <String>[];
  final reads = <String>[];
  final writes = <String>[];

  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    writes.add(path);
    switch (path) {
      case '/trial/session':
        return {'mode': 'simulated', 'reset': reset};
      case '/trial/role':
        final role = body!['role'] as String;
        roles.add(role);
        return {'mode': 'simulated', 'role': role, 'access_token': '$role-token'};
      default:
        throw StateError('Unexpected write $path');
    }
  }

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    reads.add(path);
    switch (path) {
      case '/me':
        return {
          'id': paymentAccessToken == 'vendor-token' ? 2 : 1,
          'role': paymentAccessToken == 'vendor-token' ? 'vendor' : 'customer',
          'name': 'Harbour guest',
        };
      case '/merchant/shops':
        return {
          'shops': [
            {'id': 5, 'is_owner': true, 'can_redeem': true, 'can_manage_coupons': true},
          ],
        };
      case '/get-vendor-coupon-list': return {'coupons': []};
      case '/points': return {'balance': 17, 'transactions': []};
      case '/get-favorite-coupons-list': return {'coupons': []};
      case '/orders': return {'orders': []};
      default: return {};
    }
  }
}

Future<void> frame(WidgetTester tester) async {
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 500));
  await tester.pump();
}

Future<void> fixtureScreen(WidgetTester tester, FixtureApi api) async {
  tester.view.physicalSize = const Size(390, 844);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(api.dispose);
  await tester.pumpWidget(CouponGoApp(api: api));
  await frame(tester);
}

void main() {
  test('trial only permits the exact same-origin gateway prefix', () {
    expect(ApiClient.resolveTrialBase('/coupongo-trial'), '/coupongo-trial');
    for (final unsafe in [
      '/api/v3', '', 'https://real.example', '//other.example/coupongo-trial',
      '/coupongo-trial/../',
    ]) {
      expect(() => ApiClient.resolveTrialBase(unsafe), throwsStateError);
    }
  });

  testWidgets('public home starts a real isolated customer and My has no login wall',
      (tester) async {
    SharedPreferences.setMockInitialValues({
      'access_token': 'saved-normal-token',
      'coupon_trial_access_token': 'stale-vendor-token',
      'coupon_trial_recent_v1_1': '[{"kind":"shop","id":1,"name":"Stale"}]',
      'coupon_account_recent_v1_1': '[]',
    });
    final api = FixtureApi(reset: true);
    await fixtureScreen(tester, api);
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('access_token'), 'saved-normal-token');
    expect(prefs.getString('coupon_trial_access_token'), 'customer-token');
    expect(prefs.getString('coupon_trial_recent_v1_1'), isNull);
    expect(prefs.getString('coupon_account_recent_v1_1'), '[]');
    expect(api.writes.take(2), ['/trial/session', '/trial/role']);
    expect(api.roles, ['customer']);
    expect(find.textContaining('僅供瀏覽'), findsNothing);
    await tester.tap(find.descendant(of: find.byType(BottomNavigationBar), matching: find.text('我的')));
    await frame(tester);
    expect(find.byType(WalletScreen), findsOneWidget);
    expect(find.text('Harbour guest'), findsOneWidget);
    expect(find.byType(AuthScreen), findsNothing);
    expect(api.reads, contains('/points'));
    expect(api.reads, contains('/orders'));
    expect(find.textContaining('登入後'), findsNothing);
    await tester.pumpWidget(const SizedBox());
  }, skip: !publicTrial);

  testWidgets('corporate entry and return use scoped roles without a login sheet',
      (tester) async {
    SharedPreferences.setMockInitialValues({'access_token': 'saved-normal-token'});
    final api = FixtureApi();
    await fixtureScreen(tester, api);
    tester.state<ScaffoldState>(find.byType(Scaffold).first).openDrawer();
    await frame(tester);
    await tester.tap(find.text('企業服務'));
    await frame(tester);
    await frame(tester);
    final shell = tester.widget<HomeShell>(find.byType(HomeShell));
    expect(shell.vendorMode, isTrue);
    expect(api.roles, ['customer', 'vendor']);
    expect(api.paymentAccessToken, 'vendor-token');
    expect(find.byType(AuthScreen), findsNothing);
    expect(find.byType(TextField), findsNothing);
    expect(find.widgetWithText(BottomNavigationBar, '掃碼'), findsOneWidget);
    expect(find.widgetWithText(BottomNavigationBar, '數據'), findsOneWidget);

    final nav = tester.state<NavigatorState>(find.byType(Navigator).first);
    nav.pushNamed('/admin/roles');
    await frame(tester);
    expect(find.text('找不到此頁面。'), findsOneWidget);
    expect(api.reads.where((path) => path.startsWith('/admin/')), isEmpty);
    nav.pop();
    await frame(tester);

    tester.state<ScaffoldState>(find.byType(Scaffold).first).openDrawer();
    await frame(tester);
    await tester.tap(find.text('切換到用戶版'));
    await frame(tester);
    await frame(tester);
    expect(tester.widget<HomeShell>(find.byType(HomeShell)).vendorMode, isFalse);
    expect(api.roles, ['customer', 'vendor', 'customer']);
    expect(api.writes, isNot(contains('/login')));
    expect((await SharedPreferences.getInstance()).getString('access_token'),
        'saved-normal-token');
    await tester.pumpWidget(const SizedBox());
  }, skip: !publicTrial);

  testWidgets('an existing isolated merchant is restored without a new role request',
      (tester) async {
    SharedPreferences.setMockInitialValues({
      'coupon_trial_access_token': 'vendor-token',
      'access_token': 'normal',
    });
    final api = FixtureApi();
    await fixtureScreen(tester, api);
    expect(api.roles, isEmpty);
    expect(tester.widget<HomeShell>(find.byType(HomeShell)).vendorMode, isTrue);
    expect(api.paymentAccessToken, 'vendor-token');
    await tester.pumpWidget(const SizedBox());
  }, skip: !publicTrial);

  testWidgets('ordinary mode retains email login and never starts a trial',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    tester.binding.platformDispatcher.defaultRouteNameTestValue = '/login';
    addTearDown(tester.binding.platformDispatcher.clearDefaultRouteNameTestValue);
    final api = FixtureApi();
    await fixtureScreen(tester, api);
    expect(find.byType(AuthScreen), findsOneWidget);
    await tester.tap(find.text('使用電子郵件帳戶繼續'));
    await frame(tester);
    expect(find.text('電子郵件'), findsOneWidget);
    expect(find.text('密碼'), findsOneWidget);
    expect(api.writes, isEmpty);
    await tester.pumpWidget(const SizedBox());
  }, skip: publicTrial);
}
