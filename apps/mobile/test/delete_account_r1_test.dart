import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/account.dart';
import 'package:coupon_go/screens/cashback.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _Api extends ApiClient {
  _Api({this.loggedIn = true, this.balance = 0});
  bool loggedIn;
  final double balance;
  bool failDelete = false;
  ApiException? deleteConflict;
  bool failBalance = false;
  int deletes = 0;
  int cashbackReads = 0;

  @override
  bool get isLoggedIn => loggedIn;

  @override
  Future<Map<String, dynamic>> cashback() async {
    cashbackReads++;
    if (failBalance) throw ApiException(503, 'Unavailable');
    return {
      'withdrawable_balance': balance,
      'min_withdraw_hkd': 50,
      'daily_withdraw_limit_hkd': 500,
    };
  }

  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    expect(path, '/delete-account');
    expect(body, isEmpty);
    deletes++;
    if (failDelete) throw ApiException(503, 'Unavailable');
    if (deleteConflict != null) throw deleteConflict!;
    return {};
  }

  @override
  Future<void> clearToken() async => loggedIn = false;
}

Future<void> _mount(WidgetTester tester, _Api api, [VoidCallback? onLogout]) =>
    tester.pumpWidget(LanguageScope(
      language: AppLanguage()..english = true,
      child: MaterialApp(
        home: SettingsScreen(api: api, onLogout: onLogout ?? () {}),
        routes: {
          '/cashback': (_) => const Scaffold(body: Text('Cashback')),
          '/orders': (_) => const Scaffold(body: Text('My orders')),
        },
      ),
    ));

Future<void> _tap(WidgetTester tester, String text) async {
  await tester.tap(find.text(text).last);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('delete entry follows log out for signed-in accounts, not guests',
      (tester) async {
    await _mount(tester, _Api());
    expect(find.text('Delete account'), findsOneWidget);
    expect(tester.getTopLeft(find.text('Delete account')).dy,
        greaterThan(tester.getTopLeft(find.text('Log out')).dy));
    await _mount(tester, _Api(loggedIn: false));
    expect(find.text('Delete account'), findsNothing);
  });

  testWidgets('two explicit choices needed and cancelling does not submit',
      (tester) async {
    final api = _Api();
    var logouts = 0;
    await _mount(tester, api, () => logouts++);
    await _tap(tester, 'Delete account');
    await _tap(tester, 'Continue to delete');
    expect(api.deletes, 0);
    await _tap(tester, 'Cancel');
    expect(api.deletes, 0);
    await _tap(tester, 'Delete account');
    await _tap(tester, 'Continue to delete');
    await _tap(tester, 'Confirm deletion');
    expect(api.deletes, 1);
    expect(api.loggedIn, false);
    expect(logouts, 1);
  });

  testWidgets('withdrawable cashback offers withdrawal and does not delete',
      (tester) async {
    final api = _Api(balance: 75);
    await _mount(tester, api);
    await _tap(tester, 'Delete account');
    if (!cashbackEnabled) {
      expect(find.text('Withdraw cashback'), findsNothing);
      expect(find.text('Review cashback'), findsNothing);
      expect(find.textContaining('Withdraw any available cashback'), findsOneWidget);
      expect(api.cashbackReads, 0);
      expect(api.deletes, 0);
      return;
    }
    expect(api.cashbackReads, 1);
    expect(find.textContaining('Available cashback'), findsOneWidget);
    await _tap(tester, 'Withdraw cashback');
    final page = tester.widget<WithdrawScreen>(find.byType(WithdrawScreen));
    expect(page.balance, 75);
    expect(page.minWithdrawHkd, 50);
    expect(page.dailyWithdrawLimitHkd, 500);
    expect(api.deletes, 0);
  });

  testWidgets('below minimum and unavailable balances are not treated as zero',
      (tester) async {
    final api = _Api(balance: 25);
    await _mount(tester, api);
    await _tap(tester, 'Delete account');
    if (!cashbackEnabled) return;
    expect(find.textContaining('withdrawal minimum'), findsOneWidget);
    expect(find.text('Withdraw cashback'), findsNothing);
    expect(find.text('Review cashback'), findsOneWidget);
    await _tap(tester, 'Cancel');
    api.failBalance = true;
    await _tap(tester, 'Delete account');
    expect(find.textContaining('could not check your balance'), findsOneWidget);
    await _tap(tester, 'Review cashback');
    expect(find.text('Cashback'), findsOneWidget);
    expect(api.deletes, 0);
  });

  testWidgets('paid-voucher blocker offers refund or use and opens orders', (tester) async {
    final api = _Api()..deleteConflict = ApiException(
      409, '仍有未使用的付費優惠券，請先退款或使用', code: 'unused_paid_voucher',
    );
    await _mount(tester, api);
    await _tap(tester, 'Delete account');
    expect(find.text('Refund or use any unused paid vouchers first.'), findsOneWidget);
    await _tap(tester, 'Continue to delete');
    await _tap(tester, 'Confirm deletion');
    expect(find.text('Can’t delete account yet'), findsOneWidget);
    expect(find.text('Refund or use any unused paid vouchers first.'), findsOneWidget);
    await _tap(tester, 'View orders');
    expect(find.text('My orders'), findsOneWidget);
    expect(api.loggedIn, isTrue);
    expect(api.deletes, 1);
  });

  testWidgets('a failed confirmed request preserves the session', (tester) async {
    final api = _Api()..failDelete = true;
    var logouts = 0;
    await _mount(tester, api, () => logouts++);
    await _tap(tester, 'Delete account');
    await _tap(tester, 'Continue to delete');
    await tester.tap(find.text('Confirm deletion'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 350));
    expect(api.deletes, 1);
    expect(api.loggedIn, true);
    expect(logouts, 0);
    expect(find.text('Delete account'), findsOneWidget);
  });
}
