import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/main.dart';
import 'package:coupon_go/screens/guest_auth.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('direct login opens a one-button entry without changing identity',
      (tester) async {
    final requests = <http.Request>[];
    await http.runWithClient(() async {
      // A preview sharing the same origin as an old app must not use that token.
      SharedPreferences.setMockInitialValues(
          {'access_token': 'saved-old-session'});
      tester.binding.platformDispatcher.defaultRouteNameTestValue = '/login';
      addTearDown(
          tester.binding.platformDispatcher.clearDefaultRouteNameTestValue);
      await tester.pumpWidget(const CouponGoApp());
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      expect(find.byType(PreviewWelcomeScreen), findsOneWidget);
      expect(find.byType(TextField), findsNothing);
      expect(find.text('創建帳戶'), findsNothing);
      await tester.tap(find.widgetWithText(FilledButton, '進入體驗'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      final shell = tester.widget<HomeShell>(find.byType(HomeShell));
      expect(shell.authed, isFalse);
      expect(shell.api.isLoggedIn, isFalse);
      expect(shell.vendorMode, isFalse);
      expect(find.byType(PreviewWelcomeScreen), findsNothing);
      final nav = tester.state<NavigatorState>(find.byType(Navigator).first);
      expect(nav.canPop(), isFalse);

      await tester.tap(find.text('Q Wallet'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      tester.state<ScaffoldState>(find.byType(Scaffold).first).openDrawer();
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      expect(find.text('企業服務'), findsNothing);
      expect(find.text('商戶入駐申請'), findsNothing);
      await tester.tap(find.text('進入體驗'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      expect(
          tester
              .widget<BottomNavigationBar>(find.byType(BottomNavigationBar))
              .currentIndex,
          0);
      expect(find.byType(AuthScreen), findsNothing);
      expect((await SharedPreferences.getInstance()).getString('access_token'),
          'saved-old-session');
      await tester.pumpWidget(const SizedBox());
    },
        () => MockClient((request) async {
              requests.add(request);
              return http.Response('{}', 200);
            }));
    expect(requests, isNotEmpty);
    for (final request in requests) {
      expect(request.method, 'GET');
      expect(request.headers.containsKey('Authorization'), isFalse);
      expect(request.url.path,
          isNot(matches(r'/(login|me|admin|orders|cashback)(/|$)')));
    }
  }, skip: !publicPreview);

  testWidgets('preview deep links cannot open admin or vendor screens',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.pumpWidget(const CouponGoApp());
    await tester.pump();
    final navigator = tester.state<NavigatorState>(find.byType(Navigator));
    for (final path in ['/admin', '/admin/apply', '/vendor/issue']) {
      navigator.pushNamed(path);
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      expect(find.byType(PreviewWelcomeScreen), findsOneWidget, reason: path);
      await tester.tap(find.widgetWithText(FilledButton, '進入體驗'));
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 400));
      expect(tester.widget<HomeShell>(find.byType(HomeShell)).authed, isFalse);
    }
  }, skip: !publicPreview);

  testWidgets('ordinary build keeps the email and password login screen',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    tester.binding.platformDispatcher.defaultRouteNameTestValue = '/login';
    addTearDown(
        tester.binding.platformDispatcher.clearDefaultRouteNameTestValue);
    await tester.pumpWidget(const CouponGoApp());
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    await tester.tap(find.text('使用電子郵件帳戶繼續'));
    await tester.pump();
    expect(find.text('電子郵件'), findsOneWidget);
    expect(find.text('密碼'), findsOneWidget);
    expect(find.byType(PreviewWelcomeScreen), findsNothing);
  }, skip: publicPreview);
}
