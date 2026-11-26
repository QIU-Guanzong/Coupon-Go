import 'dart:async';
import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/account.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'my_wallet_profile_test.dart' show MyFixtureApi, choose, key;

class ExpiryApi extends MyFixtureApi {
  int expiring = 8;
  int? issuedWalletId;
  bool timeOut = false;
  bool issueDuringTimeout = true;
  bool exposeLedger = true;
  Completer<void>? waitForExchange;
  int walletReads = 0;
  int pointReads = 0;

  @override
  bool get isLoggedIn => true;

  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, String>? query,
  }) async {
    if (path == '/points') {
      pointReads++;
      return {
        'balance': balance,
        'expiring_soon': expiring,
        'expiring_soon_days': 30,
        'transactions': [
          // A prior unrelated transaction must not confirm the new request.
          {'id': 10, 'reason': 'exchange', 'ref_id': 12, 'delta': -9},
          if (issuedWalletId != null && exposeLedger)
            {'id': 11, 'reason': 'exchange', 'ref_id': 91, 'delta': -9},
        ],
      };
    }
    if (path == '/get-favorite-coupons-list' && query?['status'] == '1') {
      walletReads++;
      return {
        'total': issuedWalletId == null ? 1 : 2,
        'total_pages': 1,
        'items': [
          {
            'id': 8,
            'coupon_id': 12,
            'status': 1,
            'coupon_name': 'Other coupon',
          },
          if (issuedWalletId != null)
            {
              'id': issuedWalletId,
              'coupon_id': 91,
              'status': 1,
              'source': 'exchange',
              'coupon_name': 'Tea reward',
              'shop_name': 'Tea Shop',
            },
        ],
      };
    }
    return super.get(path, query: query);
  }

  @override
  Future<Map<String, dynamic>> exchangePoints(int id) async {
    exchanges++;
    await waitForExchange?.future;
    if (!timeOut || issueDuringTimeout) {
      issuedWalletId = 22;
      // Deliberately distinct from subtracting the old numbers locally.
      balance = 13;
      expiring = 2;
    }
    if (timeOut) throw ApiException(0, 'timeout');
    return {'balance': balance, 'wallet_item_id': issuedWalletId};
  }
}

Future<void> wallet(
  WidgetTester tester,
  ExpiryApi api, {
  required bool english,
  required Size size,
  bool guest = false,
  VoidCallback? onSignIn,
}) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = size;
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
  final language = AppLanguage()..english = english;
  addTearDown(language.dispose);
  await tester.pumpWidget(
    LanguageScope(
      language: language,
      child: MaterialApp(
        theme: CgTheme.light(),
        home: Scaffold(
          body: WalletScreen(
            api: api,
            guest: guest,
            onRequireSignIn: onSignIn,
            onShowQr: (_) async {},
          ),
        ),
      ),
    ),
  );
  await tester.pumpAndSettle();
}

void main() {
  for (final english in [false, true]) {
    for (final size in [const Size(390, 844), const Size(1366, 860)]) {
      testWidgets('expiring entry and exact coupon ($english $size)', (
        tester,
      ) async {
        final api = ExpiryApi();
        addTearDown(api.dispose);
        await wallet(tester, api, english: english, size: size);
        expect(key('wallet-expiring-rewards'), findsOneWidget);
        expect(
          find.text(english ? 'View redeemable offers' : '查看可兌換優惠'),
          findsOneWidget,
        );
        await choose(tester, key('wallet-expiring-rewards'));
        await choose(tester, key('wallet-exchange-91'));
        expect(
          find.text(english ? 'Points balance: 13' : '積分餘額：13'),
          findsOneWidget,
        );
        await choose(tester, key('wallet-exchange-show-91'));
        expect(find.text('Tea reward'), findsOneWidget);
        expect(find.text('Other coupon'), findsNothing);
        await tester.pageBack();
        await tester.pumpAndSettle();
        await tester.pageBack();
        await tester.pumpAndSettle();
        expect(find.text(english ? '13 points' : '13 分'), findsOneWidget);
        expect(
          find.text(english ? '2 points expire within 30 days' : '30 日內到期：2 分'),
          findsOneWidget,
        );
        expect(api.pointReads, greaterThan(1));
        expect(api.walletReads, greaterThan(1));
        expect(tester.takeException(), isNull);
      });
    }
  }

  testWidgets('zero expiry keeps balance and guest reads none', (tester) async {
    final api = ExpiryApi()..expiring = 0;
    addTearDown(api.dispose);
    await wallet(tester, api, english: true, size: const Size(390, 844));
    expect(key('wallet-expiring-rewards'), findsNothing);
    expect(find.text('19 points'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    final guest = ExpiryApi();
    addTearDown(guest.dispose);
    int signedIn = 0;
    await wallet(
      tester,
      guest,
      english: true,
      size: const Size(390, 844),
      guest: true,
      onSignIn: () => signedIn++,
    );
    expect(key('wallet-expiring-rewards'), findsNothing);
    expect(guest.pointReads, 0);
    expect(guest.walletReads, 0);
    expect(find.text('Points rules and FAQs'), findsOneWidget);
    await choose(tester, key('wallet-points-history'));
    expect(signedIn, 1);
  });

  for (final hasLedger in [false, true]) {
    testWidgets(
      'timeout reconciles wallet AND new ledger; no auto retry ($hasLedger)',
      (tester) async {
        final api = ExpiryApi()
          ..timeOut = true
          ..exposeLedger = hasLedger;
        addTearDown(api.dispose);
        await wallet(tester, api, english: true, size: const Size(390, 844));
        await choose(tester, key('wallet-expiring-rewards'));
        await choose(tester, key('wallet-exchange-91'));
        expect(api.exchanges, 1);
        expect(api.pointReads, greaterThan(2));
        expect(api.walletReads, greaterThan(1));
        expect(
          key('wallet-exchange-show-91'),
          hasLedger ? findsOneWidget : findsNothing,
        );
        if (!hasLedger) {
          expect(
            find.text('Could not confirm redemption. Check your wallet.'),
            findsOneWidget,
          );
          final controls = tester.widgetList<ElevatedButton>(
            find.byType(ElevatedButton),
          );
          expect(controls.every((b) => b.onPressed == null), isTrue);
        }
      },
    );
  }

  testWidgets(
    'switching accounts discards delayed exchange and its wallet shortcut',
    (tester) async {
      final api = ExpiryApi()..waitForExchange = Completer<void>();
      addTearDown(api.dispose);
      await wallet(tester, api, english: true, size: const Size(390, 844));
      await choose(tester, key('wallet-expiring-rewards'));
      await tester.tap(key('wallet-exchange-91'));
      await tester.pump();
      api.switchAccount();
      api.waitForExchange!.complete();
      await tester.pumpAndSettle();
      expect(find.text('The account changed. Go back.'), findsOneWidget);
      expect(key('wallet-exchange-show-91'), findsNothing);
    },
  );
}
