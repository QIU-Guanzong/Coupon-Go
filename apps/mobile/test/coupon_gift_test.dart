import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/account.dart';
import 'package:coupon_go/screens/coupon_gift.dart';
import 'package:coupon_go/share_links.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'my_wallet_profile_test.dart' show MyFixtureApi;

const token =
    'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const _preview = <String, dynamic>{
  'coupon_name': 'Afternoon tea',
  'shop_name': 'Tea Shop',
  'expires_at': '2026-11-28T03:40:00+00:00',
};

class _GiftApi extends MyFixtureApi {
  final paths = <String>[];
  bool signedIn = true;
  bool pending = true;
  bool accepted = false;
  bool loseFirstReceipt = false;
  bool expired = false;
  Completer<Map<String, dynamic>>? delayedAcceptance;
  int accepting = 0;

  @override
  bool get isLoggedIn => signedIn;

  void logIn() {
    signedIn = true;
    switchAccount();
  }

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
  }) async {
    paths.add(path);
    if (path == '/wallet/items/7/gift') {
      pending = true;
      return {'token': token, 'expires_at': _preview['expires_at']};
    }
    expect(body, {'token': token});
    if (expired) {
      throw ApiException(404, 'Unavailable', code: 'gift_unavailable');
    }
    if (path == '/wallet/gifts/preview') {
      return {..._preview, if (accepted) 'wallet_item_id': 71};
    }
    if (path == '/wallet/gifts/accept') {
      accepting++;
      if (delayedAcceptance case final delayed?) return delayed.future;
      accepted = true;
      if (loseFirstReceipt && accepting == 1) {
        throw ApiException(503, 'Response lost');
      }
      return {'wallet_item_id': 71};
    }
    throw StateError('Unexpected path');
  }

  @override
  Future<Map<String, dynamic>> delete(String path,
      {Map<String, String>? query}) async {
    paths.add('DELETE $path');
    pending = false;
    return {};
  }

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    if (path != '/get-favorite-coupons-list' || query?['status'] != '1') {
      return super.get(path, query: query);
    }
    return {
      'total': accepted ? 3 : 2,
      'total_pages': accepted ? 2 : 1,
      'items': query?['page'] == '2'
          ? [
              {
                'id': 71,
                'status': 1,
                'coupon_name': 'Gifted afternoon tea',
                'source': 'gift'
              },
            ]
          : [
              {
                'id': 7,
                'status': 1,
                'coupon_name': 'Afternoon tea',
                'gift_eligible': true,
                if (pending) 'gift_status': 'pending',
              },
              {
                'id': 8,
                'status': 1,
                'coupon_name': 'Paid tea voucher',
                'source': 'purchase',
              },
            ],
    };
  }
}

Future<void> _pump(
  WidgetTester tester,
  Widget screen, {
  Map<String, WidgetBuilder> routes = const {},
}) async {
  tester.view.physicalSize = const Size(360, 740);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(LanguageScope(
    language: AppLanguage()..english = true,
    child: MaterialApp(home: screen, routes: routes),
  ));
  await tester.pumpAndSettle();
}

CouponGiftReceiveScreen _receive(_GiftApi api) => CouponGiftReceiveScreen(
      api: api,
      token: token,
      onOpenWallet: (context, id) =>
          openClaimedCouponWallet(context, api, walletItemId: id),
    );

void main() {
  testWidgets(
      'wallet presents and cancels a pending gift only on the eligible voucher',
      (tester) async {
    final api = _GiftApi();
    await _pump(
        tester,
        Scaffold(
            body: WalletScreen(
          api: api,
          showPoints: false,
          onShowQr: (_) async {},
        )));

    expect(find.byKey(const ValueKey('wallet-gift-8')), findsNothing);
    if (!couponGiftAvailable) {
      expect(find.byKey(const ValueKey('wallet-gift-7')), findsNothing);
      return;
    }
    await tester.tap(find.byKey(const ValueKey('wallet-gift-7')));
    await tester.pumpAndSettle();
    expect(find.text('Gift a coupon'), findsOneWidget);
    await tester.tap(find.text('Cancel gift'));
    await tester.pumpAndSettle();
    expect(api.paths, ['DELETE /wallet/items/7/gift']);
    expect(find.text('Gift cancelled. The coupon is still in your wallet.'),
        findsOneWidget);
    await tester.tap(find.text('Close'));
    await tester.pumpAndSettle();
    expect(find.text('Gift to a friend'), findsOneWidget);
  });

  testWidgets('gift creation uses a configured share host', (tester) async {
    final api = _GiftApi()..pending = false;
    await _pump(
        tester,
        Scaffold(
            body: WalletScreen(
          api: api,
          showPoints: false,
          onShowQr: (_) async {},
        )));
    await tester.tap(find.byKey(const ValueKey('wallet-gift-7')));
    await tester.pumpAndSettle();
    if (!customerShareAvailable) {
      expect(find.text('Gift links are unavailable in this version.'),
          findsOneWidget);
      expect(find.text('Create link'), findsNothing);
      expect(api.paths, isEmpty);
      return;
    }
    await tester.tap(find.text('Create link'));
    await tester.pumpAndSettle();
    expect(api.paths, ['/wallet/items/7/gift']);
    expect(find.textContaining('Claim by'), findsOneWidget);
    await tester.tap(find.text('Share link'));
    await tester.pumpAndSettle();
    expect(find.text('Copy link'), findsOneWidget);
    expect(find.textContaining('#/gift/$token'), findsOneWidget);
  }, skip: !couponGiftAvailable);

  testWidgets(
      'sign in and confirm a gift, then open only the exact wallet item',
      (tester) async {
    final api = _GiftApi()
      ..signedIn = false
      ..loseFirstReceipt = true;
    await _pump(tester, _receive(api), routes: {
      '/login': (context) => Scaffold(
              body: TextButton(
            onPressed: () {
              api.logIn();
              Navigator.of(context).pop();
            },
            child: const Text('Finish sign-in'),
          )),
    });
    if (!couponGiftAvailable) {
      expect(
          find.textContaining('Open your friend’s gift link on the full site.'),
          findsOneWidget);
      expect(api.paths, isEmpty);
      return;
    }
    expect(api.paths, isEmpty);
    await tester.tap(find.text('Sign in'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Finish sign-in'));
    await tester.pumpAndSettle();
    expect(find.text('Afternoon tea'), findsOneWidget);
    expect(api.paths, ['/wallet/gifts/preview']);
    await tester.tap(find.text('Claim coupon'));
    await tester.pumpAndSettle();
    expect(find.text('Check claim'), findsOneWidget);
    await tester.tap(find.text('Check claim'));
    await tester.pumpAndSettle();
    expect(api.accepting, 2);
    await tester.tap(find.text('Open Q Wallet'));
    await tester.pumpAndSettle();
    expect(find.text('Gifted afternoon tea'), findsOneWidget);
    expect(find.text('Paid tea voucher'), findsNothing);
  });

  testWidgets('an old receipt is ignored if the customer account changes',
      (tester) async {
    final api = _GiftApi()..delayedAcceptance = Completer();
    await _pump(tester, _receive(api));
    await tester.tap(find.text('Claim coupon'));
    await tester.pump();
    api.expired = true;
    api.switchAccount();
    api.delayedAcceptance!.complete({'wallet_item_id': 71});
    await tester.pumpAndSettle();
    expect(find.text('Open Q Wallet'), findsNothing);
    expect(
        find.text('This gift link is unavailable or has already been claimed.'),
        findsOneWidget);
  }, skip: !couponGiftAvailable);
}
