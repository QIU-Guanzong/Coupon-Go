import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/merchant_account_overview.dart';
import 'package:coupon_go/theme.dart';
import 'package:coupon_go/widgets/merchant_layout.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

typedef _Read = FutureOr<Map<String, dynamic>> Function(String path, int? shop);

class _Api extends ApiClient {
  _Api(this.read);

  final _Read read;
  final calls = <(String, int?)>[];

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    final shop = int.tryParse(query?['shop_id'] ?? '');
    calls.add((path, shop));
    return read(path, shop);
  }

  @override
  Future<Map<String, dynamic>> post(String path,
          {Map<String, dynamic>? body, Map<String, String>? query}) async =>
      throw StateError('Read-only account view attempted a POST');
}

const _status = '/merchant/connect/status';
const _balance = '/merchant/connect/balance';
const _ledger = '/merchant/connect/ledger';

Map<String, dynamic> _shop(int id, {bool grant = true}) => {
      'id': id,
      'name': '店舖 $id',
      'can_view_settlement': grant,
    };

Map<String, dynamic> _account(int id, {bool missing = false}) => {
      'shop_id': id,
      'stripe_account_id': missing ? null : 'acct_private_$id',
      'onboarding_status': missing ? 'pending' : 'active',
    };

Map<String, dynamic> _money(int id, {num? available}) => {
      'shop_id': id,
      'currency': 'HKD',
      'pending': 0,
      'available': available,
      'paid_out': 18,
      'account_ready': true,
    };

Map<String, dynamic> _readOwner(String path, int? shop) => switch (path) {
      '/me' => {'name': '店主', 'email': 'owner@example.test'},
      '/merchant/shops' => {
          'shops': [_shop(1)]
        },
      _status => _account(shop!),
      _balance => _money(shop!),
      _ledger => {
          'total': 1,
          'page': 1,
          'page_size': 20,
          'entries': [
            {
              'kind': 'payout',
              'status': 'paid_out',
              'net_amount': -18,
              'created_at': '2026-11-24T12:00:00',
              'stripe_payout_id': 'po_private_fixture',
            },
          ],
        },
      _ => throw StateError('Unexpected GET $path'),
    };

Future<void> _show(WidgetTester tester, _Api api,
    {bool signedIn = true}) async {
  if (signedIn) api.activateTrialToken('test-session');
  await tester.pumpWidget(MaterialApp(
    home: Scaffold(
        body: SingleChildScrollView(
      child: MerchantAccountOverview(api: api),
    )),
  ));
  await tester.pumpAndSettle();
}

Finder _textEither(String zh, String en) => find.byWidgetPredicate(
    (widget) => widget is Text && (widget.data == zh || widget.data == en));

Finder _balanceRow(String zh, String en) => find.ancestor(
      of: _textEither(zh, en),
      matching: find.byType(ListTile),
    );

void main() {
  testWidgets('390px English at 130% aligns account headings and ledger rows',
      (tester) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(390, 844);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final language = AppLanguage()..english = true;
    final api = _Api(_readOwner)..activateTrialToken('test-session');
    addTearDown(language.dispose);
    addTearDown(api.dispose);
    await tester.pumpWidget(
      LanguageScope(
        language: language,
        child: MaterialApp(
          theme: CgTheme.light(),
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(context)
                .copyWith(textScaler: const TextScaler.linear(1.3)),
            child: child!,
          ),
          home: Scaffold(
            body: SingleChildScrollView(
              padding: const EdgeInsets.all(CgTheme.pageInset),
              child: MerchantAccountOverview(api: api),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    final balanceHeading = find.text('Ledger balance');
    final ledgerHeading = find.text('Recent ledger entries');
    final row = find.text('Payout in progress');
    expect(tester.widget<Text>(balanceHeading).style?.fontSize, 16);
    expect(
        tester.widget<Text>(ledgerHeading).style?.fontWeight, FontWeight.bold);
    expect(tester.getTopLeft(balanceHeading).dx, closeTo(16, 1));
    expect(tester.getTopLeft(ledgerHeading).dx, closeTo(16, 1));
    expect(tester.getTopLeft(row).dx, closeTo(16, 1));
    expect(find.byType(MerchantAmountValue), findsNWidgets(5));
    expect(find.byType(MerchantStatusPill), findsNWidgets(2));
    expect(tester.takeException(), isNull);
  });

  testWidgets('signed-out screen issues no GET', (tester) async {
    final api = _Api(_readOwner);
    await _show(tester, api, signedIn: false);
    expect(_textEither('請登入查看商戶帳戶', 'Sign in to view your merchant account'),
        findsOneWidget);
    expect(api.calls, isEmpty);
  });

  testWidgets('profile 401 prevents reads and does not display server wording',
      (tester) async {
    final api = _Api((path, shop) => throw ApiException(401, 'private detail'));
    await _show(tester, api);
    expect(_textEither('請登入查看商戶帳戶', 'Sign in to view your merchant account'),
        findsOneWidget);
    expect(find.text('private detail'), findsNothing);
    expect(api.calls, [('/me', null)]);
  });

  testWidgets('staff without a settlement grant requests no protected status',
      (tester) async {
    final api = _Api((path, shop) => path == '/merchant/shops'
        ? {
            'shops': [_shop(7, grant: false)]
          }
        : _readOwner(path, shop));
    await _show(tester, api);
    expect(find.text('店主'), findsOneWidget);
    expect(find.text('店舖 7'), findsOneWidget);
    expect(_textEither('沒有權限查看此資料', 'You cannot view these details'),
        findsOneWidget);
    expect(api.calls, [('/me', null), ('/merchant/shops', null)]);
  });

  testWidgets('403 must not be treated as an account missing or as zero',
      (tester) async {
    final api = _Api((path, shop) {
      if (path == _status) throw ApiException(403, 'forbidden');
      return _readOwner(path, shop);
    });
    await _show(tester, api);
    expect(_textEither('沒有權限查看此資料', 'You cannot view these details'),
        findsOneWidget);
    expect(
        _textEither('未設定收款帳戶', 'Receiving account not set up'), findsNothing);
    expect(api.calls, [('/me', null), ('/merchant/shops', null), (_status, 1)]);
  });

  testWidgets('verified absent account displays no balance and reads no ledger',
      (tester) async {
    final api = _Api((path, shop) => path == _status
        ? _account(shop!, missing: true)
        : _readOwner(path, shop));
    await _show(tester, api);
    expect(
        _textEither('未設定收款帳戶', 'Receiving account not set up'), findsOneWidget);
    expect(_textEither('帳面餘額', 'Ledger balance'), findsNothing);
    expect(
        api.calls.map((call) => call.$1), ['/me', '/merchant/shops', _status]);
  });

  testWidgets('ledger reads show real zero, keep missing unknown and hide IDs',
      (tester) async {
    final api = _Api(_readOwner);
    await _show(tester, api);
    expect(find.text('HK\$ 0.00'), findsOneWidget);
    final available = _balanceRow('帳面可提取', 'Ledger available');
    final processing = _balanceRow('提款處理中', 'Payout in progress');
    expect(find.descendant(of: available, matching: find.text('—')),
        findsOneWidget);
    expect(find.descendant(of: processing, matching: find.text('—')),
        findsOneWidget);
    expect(find.text('HK\$ 18.00'), findsOneWidget);
    expect(find.text('acct_private_1'), findsNothing);
    expect(find.text('po_private_fixture'), findsNothing);
    expect(api.calls, [
      ('/me', null),
      ('/merchant/shops', null),
      (_status, 1),
      (_balance, 1),
      (_ledger, 1),
    ]);
  });

  testWidgets('reserved payout stays separate from available and paid out',
      (tester) async {
    final api = _Api((path, shop) => switch (path) {
          _balance => {
              ..._money(shop!, available: 600),
              'processing': 400,
              'paid_out': 0,
            },
          _ledger => {'total': 0, 'page': 1, 'page_size': 20, 'entries': []},
          _ => _readOwner(path, shop),
        });
    await _show(tester, api);

    expect(
        find.descendant(
            of: _balanceRow('帳面可提取', 'Ledger available'),
            matching: find.text(r'HK$ 600.00')),
        findsOneWidget);
    expect(
        find.descendant(
            of: _balanceRow('提款處理中', 'Payout in progress'),
            matching: find.text(r'HK$ 400.00')),
        findsOneWidget);
    expect(
        find.descendant(
            of: _balanceRow('已提取', 'Paid out'),
            matching: find.text(r'HK$ 0.00')),
        findsOneWidget);
  });

  testWidgets('switching shops fences old balances; signing out clears results',
      (tester) async {
    final oldBalance = Completer<Map<String, dynamic>>();
    final api = _Api((path, shop) {
      if (path == '/merchant/shops') {
        return {
          'shops': [_shop(1), _shop(2)]
        };
      }
      if (path == _balance) {
        return shop == 1 ? oldBalance.future : _money(shop!, available: 20);
      }
      return _readOwner(path, shop);
    });
    api.activateTrialToken('test-session');
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
      body: SingleChildScrollView(child: MerchantAccountOverview(api: api)),
    )));
    // A busy skeleton may animate while the first balance is in flight.
    await tester.pump();
    await tester.pump();
    await tester.tap(find.byType(DropdownButtonFormField<int>));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    await tester.tap(find.text('店舖 2').last);
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    await tester.pumpAndSettle();
    expect(find.text('HK\$ 20.00'), findsOneWidget);
    oldBalance.complete(_money(1, available: 91));
    await tester.pumpAndSettle();
    expect(find.text('HK\$ 91.00'), findsNothing);
    expect(api.calls, isNot(contains((_ledger, 1))));
    api.activateTrialToken(null);
    await tester.pumpAndSettle();
    expect(find.text('HK\$ 20.00'), findsNothing);
    expect(find.text('owner@example.test'), findsNothing);
  });
}
