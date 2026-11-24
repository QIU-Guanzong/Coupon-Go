import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/app_config.dart';
import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/account.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class MyFixtureApi extends ApiClient {
  MyFixtureApi() {
    appConfig.value = const AppConfig(
      pointsClaim: 10,
      pointsRedeem: 20,
      pointsReview: 15,
      pointsExpireMonths: 12,
    );
  }

  int revision = 0, balance = 19, exchanges = 0;
  bool reviewed = false;
  Completer<Map<String, dynamic>>? delayedPoints;
  final reads = <String>[];

  @override
  int get sessionRevision => revision;

  void switchAccount() {
    revision++;
    notifyListeners();
  }

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    reads.add(path);
    switch (path) {
      case '/me':
        return {'id': revision + 1, 'name': revision == 0 ? 'Ariel' : 'Bianca'};
      case '/points':
        if (delayedPoints != null) return delayedPoints!.future;
        return {
          'balance': balance,
          'expiring_soon': 8,
          'expiring_soon_days': 30,
          'transactions': [
            {
              'delta': 10,
              'reason': 'claim',
              'created_at': '2026-11-22T09:00:00'
            },
            {
              'delta': -9,
              'reason': 'exchange',
              'created_at': '2026-11-24T09:00:00'
            },
          ],
        };
      case '/points/exchange-options':
        return {
          'cost': 9,
          'coupons': [
            {'id': 91, 'coupon_name': 'Tea reward', 'shop_name': 'Tea Shop'},
            {'id': 92, 'coupon_name': 'Extra reward', 'shop_name': 'Tea Shop'},
            {'id': 93, 'coupon_name': 'Third reward', 'shop_name': 'Tea Shop'},
            {'id': 95, 'coupon_name': 'Paid coupon', 'sale_price': 5},
          ]
        };
      case '/get-favorite-coupons-list':
        final status = query?['status'];
        if (status == '1') {
          return {
            'total': balance < 19 ? 2 : 1,
            'total_pages': 1,
            'items': [
              {
                'id': 1,
                'status': 1,
                'coupon_name': 'Ready to use',
                'shop_name': 'Tea Shop'
              },
              if (balance < 19)
                {
                  'id': 9,
                  'status': 1,
                  'coupon_name': 'Tea reward',
                  'shop_name': 'Tea Shop'
                },
            ]
          };
        }
        if (status == '2') {
          return {
            'total': 2,
            'total_pages': 2,
            'items': [
              if (query?['page'] == '1')
                {
                  'id': 2,
                  'status': 2,
                  'reviewed': true,
                  'coupon_name': 'Reviewed coupon'
                },
              if (query?['page'] == '2')
                {
                  'id': 3,
                  'status': 2,
                  'reviewed': reviewed,
                  'coupon_name': 'Needs review'
                },
            ]
          };
        }
        return {'total': 0, 'total_pages': 1, 'items': []};
      case '/orders':
        return {
          'orders': [
            {'id': 1, 'refund_status': 'none'},
            {'id': 2, 'refund_status': 'requested'},
            {'id': 3, 'refund_status': 'refunded'},
            {'id': 4, 'refund_status': 'rejected'},
          ]
        };
      case '/get-user-record-list':
        return {
          'records': [
            {
              'coupon_name': 'Tea used',
              'shop_name': 'Tea Shop',
              'scanned_at': '2026-11-24T09:00:00',
              'revoked': true
            }
          ]
        };
      default:
        throw ApiException(503, 'Unavailable');
    }
  }

  @override
  Future<Map<String, dynamic>> submitReview({
    int? walletItemId,
    int? redemptionId,
    required int taste,
    required int ambience,
    required int service,
    String content = '',
    List<String> images = const [],
    List<String> tags = const [],
    bool claimPoints = true,
  }) async {
    expect(walletItemId, 3);
    expect(claimPoints, isTrue);
    expect(images, isEmpty);
    reviewed = true;
    balance += 15;
    return {
      'review': {'points_given': true}
    };
  }

  @override
  Future<Map<String, dynamic>> exchangePoints(int id) async {
    exchanges++;
    balance -= 9;
    return {'balance': balance};
  }
}

Future<void> show(WidgetTester tester, MyFixtureApi api,
    {bool guest = false, VoidCallback? onSignIn}) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(390, 844);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(tester.view.resetPhysicalSize);
  final language = AppLanguage()..english = true;
  addTearDown(language.dispose);
  await tester.pumpWidget(LanguageScope(
      language: language,
      child: MaterialApp(
        theme: CgTheme.light(),
        routes: {
          '/orders': (_) => Scaffold(
              appBar: AppBar(), body: const Text('Existing orders page'))
        },
        home: Scaffold(
            body: WalletScreen(
          api: api,
          showProfile: true,
          guest: guest,
          onShowQr: (_) async {},
          onOpenRefunds: () {},
          onRequireSignIn: onSignIn,
          onOpenCorporate: () {},
        )),
      )));
  await tester.pumpAndSettle();
}

Finder key(String value) => find.byKey(ValueKey(value));
Future<void> choose(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
      'My uses real profile, counts each relevant state and opens separate lists',
      (tester) async {
    final api = MyFixtureApi();
    addTearDown(api.dispose);
    await show(tester, api);
    expect(find.text('Ariel'), findsOneWidget);
    expect(tester.widget<Text>(key('wallet-order-count-all')).data, '4');
    expect(tester.widget<Text>(key('wallet-order-count-unused')).data, '1');
    expect(tester.widget<Text>(key('wallet-order-count-used')).data, '2');
    expect(tester.widget<Text>(key('wallet-order-count-review')).data, '1');
    expect(tester.widget<Text>(key('wallet-order-count-refund')).data, '3');
    await choose(tester, key('wallet-order-all'));
    expect(find.text('Existing orders page'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();
    await choose(tester, key('wallet-order-unused'));
    expect(find.text('Ready to use'), findsOneWidget);
    expect(find.text('Q Wallet points'), findsNothing);
    await tester.pageBack();
    await tester.pumpAndSettle();
    await choose(tester, key('wallet-order-used'));
    expect(find.text('Reviewed coupon'), findsOneWidget);
    expect(find.text('Needs review'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();
    await choose(tester, key('wallet-order-review'));
    expect(find.text('Reviewed coupon'), findsNothing);
    expect(find.text('Needs review'), findsOneWidget);
    expect(find.text('Review'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'nested Q Wallet activity and usage display sorted persisted records',
      (tester) async {
    final api = MyFixtureApi();
    addTearDown(api.dispose);
    await show(tester, api);
    await choose(tester, find.text('Usage history'));
    expect(find.text('Tea used'), findsOneWidget);
    expect(find.text('Reversed'), findsOneWidget);
    expect(find.text('Tea Shop · 2026-11-24 17:00'), findsOneWidget);
    await tester.pageBack();
    await tester.pumpAndSettle();
    await choose(tester, key('my-q-wallet'));
    expect(find.text('8 points expire within 30 days'), findsOneWidget);
    await choose(tester, key('wallet-points-history'));
    expect(find.text('-9'), findsOneWidget);
    expect(find.text('+10'), findsOneWidget);
    expect(tester.getTopLeft(find.text('-9')).dy,
        lessThan(tester.getTopLeft(find.text('+10')).dy));
  });

  testWidgets(
      'isolated points exchange updates balance and rejects paid or insufficient options',
      (tester) async {
    final api = MyFixtureApi();
    addTearDown(api.dispose);
    await show(tester, api);
    await choose(tester, key('my-q-wallet'));
    await choose(tester, find.text('Redeem points'));
    expect(find.text('9 points per coupon'), findsOneWidget);
    expect(find.text('Paid coupon'), findsNothing);
    await choose(tester, key('wallet-exchange-91'));
    expect(find.text('Points balance: 10'), findsOneWidget);
    await choose(tester, key('wallet-exchange-92'));
    expect(find.text('Points balance: 1'), findsOneWidget);
    await choose(tester, key('wallet-exchange-93'));
    expect(find.text('Not enough points.'), findsOneWidget);
    expect(api.exchanges, 2);
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.text('Tea reward'), findsOneWidget);
    expect(find.text('1 points'), findsOneWidget);
  }, skip: !publicTrial);

  testWidgets(
      'a submitted review leaves the pending list and credits the server points',
      (tester) async {
    final api = MyFixtureApi();
    addTearDown(api.dispose);
    await show(tester, api);
    await choose(tester, key('wallet-order-review'));
    await choose(tester, find.text('Review'));
    await tester.enterText(find.byType(TextField).first, 'Friendly service');
    await tester.pumpAndSettle();
    await choose(tester, find.text('Submit review'));
    expect(find.text('You earned 15 points'), findsOneWidget);
    await choose(tester, find.text('OK'));
    expect(find.text('Needs review'), findsNothing);
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(tester.widget<Text>(key('wallet-order-count-review')).data, '0');
    expect(find.text('34 points'), findsOneWidget);
  });

  testWidgets(
      'real guest cannot read private data and calls the existing login entry',
      (tester) async {
    final api = MyFixtureApi();
    addTearDown(api.dispose);
    int signedIn = 0;
    await show(tester, api, guest: true, onSignIn: () => signedIn++);
    expect(api.reads, isEmpty);
    await choose(tester, key('my-q-wallet'));
    expect(signedIn, 1);
  });

  testWidgets('late previous-account points cannot appear after session change',
      (tester) async {
    final api = MyFixtureApi();
    addTearDown(api.dispose);
    final stale = Completer<Map<String, dynamic>>();
    api.delayedPoints = stale;
    await show(tester, api);
    api.delayedPoints = null;
    api.switchAccount();
    stale.complete({'balance': 999, 'transactions': []});
    await tester.pumpAndSettle();
    expect(find.text('Bianca'), findsOneWidget);
    expect(find.text('Ariel'), findsNothing);
    expect(find.text('999 points'), findsNothing);
    expect(find.text('19 points'), findsOneWidget);
  });
}
