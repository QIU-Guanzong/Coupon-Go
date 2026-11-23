import 'dart:convert';

import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/main.dart';
import 'package:coupon_go/screens/guest_auth.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

const isTrial = bool.fromEnvironment('PUBLIC_TRIAL');
const isDemo = bool.fromEnvironment('PUBLIC_DEMO');

http.Client _client(List<http.Request> requests) => MockClient((r) async {
  requests.add(r);
  if (r.url.path.endsWith('/trial/session')) {
    return http.Response(jsonEncode({'reset': false}), 200);
  }
  if (r.url.path.endsWith('/me')) return http.Response('{"role":"customer"}', 200);
  return http.Response('{}', 200);
});

void main() {
  test('interactive trial takes priority over browse-only preview', () {
    expect(publicPreview, isDemo && !isTrial);
  });

  testWidgets('browse preview does not read saved account or ask for credentials', (tester) async {
    final requests = <http.Request>[];
    await http.runWithClient(() async {
      SharedPreferences.setMockInitialValues({'access_token': 'saved-normal-token'});
      tester.binding.platformDispatcher.defaultRouteNameTestValue = '/login';
      addTearDown(tester.binding.platformDispatcher.clearDefaultRouteNameTestValue);
      await tester.pumpWidget(const CouponGoApp());
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(find.byType(PreviewWelcomeScreen), findsOneWidget);
      expect(find.byType(TextField), findsNothing);
      expect((await SharedPreferences.getInstance()).getString('access_token'), 'saved-normal-token');
      expect(tester.widget<HomeShell>(find.byType(HomeShell, skipOffstage: false)).api.isLoggedIn, false);
      expect(requests.every((r) => !r.headers.containsKey('Authorization')), isTrue);
      expect(requests.every((r) => r.method == 'GET'), isTrue);
      await tester.tap(find.widgetWithText(FilledButton, '繼續瀏覽'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(find.byType(PreviewWelcomeScreen), findsNothing);
      await tester.tap(find.text('Q Wallet'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(find.byType(HomeShell), findsOneWidget);
      expect(requests.every((r) => !r.headers.containsKey('Authorization')), isTrue);
      final nav = tester.state<NavigatorState>(find.byType(Navigator).first);
      for (final path in ['/admin', '/admin/apply', '/vendor/scan']) {
        nav.pushNamed(path);
        await tester.pump();
        await tester.pump(const Duration(milliseconds: 500));
        expect(find.byType(PreviewWelcomeScreen), findsOneWidget, reason: path);
        nav.pop();
        await tester.pump();
        await tester.pump(const Duration(milliseconds: 500));
      }
      expect(requests.every((r) => !r.headers.containsKey('Authorization')), isTrue);
      await tester.pumpWidget(const SizedBox());
    }, () => _client(requests));
  }, skip: !publicPreview);

  testWidgets('trial keeps its own token and merchant/admin routes', (tester) async {
    final requests = <http.Request>[];
    await http.runWithClient(() async {
      SharedPreferences.setMockInitialValues({
        'access_token': 'saved-normal-token',
        'coupon_trial_access_token': 'saved-trial-token',
      });
      await tester.pumpWidget(const CouponGoApp());
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      final shell = tester.widget<HomeShell>(find.byType(HomeShell));
      expect(shell.api.paymentAccessToken, 'saved-trial-token');
      expect((await SharedPreferences.getInstance()).getString('access_token'), 'saved-normal-token');
      final nav = tester.state<NavigatorState>(find.byType(Navigator).first);
      nav.pushNamed('/login');
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(find.byType(PreviewWelcomeScreen), findsNothing);
      expect(find.text('使用示例顧客'), findsOneWidget);
      nav.pop();
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      nav.pushNamed('/vendor/scan');
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(find.byType(PreviewWelcomeScreen), findsNothing);
      expect(find.text('掃碼核銷'), findsWidgets);
      nav.pop();
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      nav.pushNamed('/admin');
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(find.byType(PreviewWelcomeScreen), findsNothing);
      await tester.pumpWidget(const SizedBox());
    }, () => _client(requests));
  }, skip: !isTrial);

  testWidgets('ordinary mode retains email login', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final requests = <http.Request>[];
    await http.runWithClient(() async {
      tester.binding.platformDispatcher.defaultRouteNameTestValue = '/login';
      addTearDown(tester.binding.platformDispatcher.clearDefaultRouteNameTestValue);
      await tester.pumpWidget(const CouponGoApp());
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 500));
      expect(find.byType(PreviewWelcomeScreen), findsNothing);
      await tester.tap(find.text('使用電子郵件帳戶繼續'));
      await tester.pump();
      expect(find.text('電子郵件'), findsOneWidget);
      expect(find.text('密碼'), findsOneWidget);
      await tester.pumpWidget(const SizedBox());
    }, () => _client(requests));
  }, skip: isTrial || publicPreview);
}
