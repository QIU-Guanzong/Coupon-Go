import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/account.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class JourneyApi extends ApiClient {
  final reply = Completer<Map<String, dynamic>>();

  @override
  Future<Map<String, dynamic>> post(String path,
          {Map<String, dynamic>? body, Map<String, String>? query}) =>
      reply.future;

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    if (path == '/get-favorite-coupons-list') {
      return {
        'items': [
          {'id': 8, 'coupon_name': 'Coffee coupon', 'status': 1}
        ],
        'total_pages': 1
      };
    }
    throw ApiException(503, 'Offline');
  }
}

class ReceiptApi extends JourneyApi {
  bool revoked = false;
  String? receiptId;

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    receiptId = query?['redemption_id'];
    return {'status': 2, 'revoked': revoked, 'reviewed': false};
  }
}

void main() {
  testWidgets(
      'the original receipt changes after reversal even if the coupon is used again',
      (tester) async {
    final api = ReceiptApi();
    await tester.pumpWidget(MaterialApp(
        home: RedemptionSuccessScreen(
      api: api,
      item: WalletItem.fromJson({'id': 8}),
      redemptionId: 24,
    )));
    expect(find.text('去評價'), findsOneWidget);
    api.revoked = true;
    await tester.pump(const Duration(seconds: 5));
    await tester.pump();
    expect(api.receiptId, '24');
    expect(find.text('商家已撤銷核銷'), findsOneWidget);
    expect(find.text('這張券其後已再次使用。'), findsOneWidget);
    expect(find.text('去評價'), findsNothing);
  });

  testWidgets('cleared conversation ignores a pending reply', (tester) async {
    final api = JourneyApi();
    await tester.pumpWidget(LanguageScope(
        language: AppLanguage(),
        child: MaterialApp(home: QBotScreen(api: api))));
    await tester.enterText(find.byType(TextField), '找咖啡券');
    await tester.tap(find.byTooltip('送出'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('同意並送出'));
    await tester.pump();
    expect(find.text('Q仔正在回覆…'), findsOneWidget);
    await tester.tap(find.byTooltip('清除對話'));
    api.reply.complete({'answer': 'late reply'});
    await tester.pumpAndSettle();
    expect(find.text('late reply'), findsNothing);
    expect(find.text('找咖啡券'), findsNothing);
  });

  testWidgets('wallet can present a coupon when balances are unavailable',
      (tester) async {
    int? presented;
    await tester.pumpWidget(MaterialApp(
        home: Scaffold(
            body: WalletScreen(
      api: JourneyApi(),
      onShowQr: (item) async {
        presented = item.id;
      },
    ))));
    await tester.pumpAndSettle();
    expect(find.text('Coffee coupon'), findsOneWidget);
    await tester.tap(find.text('出示'));
    await tester.pumpAndSettle();
    expect(presented, 8);
  });
  testWidgets('wallet respects the status bar with or without an app bar',
      (tester) async {
    tester.view.devicePixelRatio = 1;
    tester.view.padding = const FakeViewPadding(top: 30);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPadding);

    Widget wallet() => WalletScreen(api: JourneyApi(), onShowQr: (_) async {});
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: wallet())));
    await tester.pumpAndSettle();
    expect(tester.getTopLeft(find.text('Q Wallet 積分')).dy, 42);

    await tester.pumpWidget(MaterialApp(
        home: Scaffold(appBar: AppBar(title: const Text('我的券')), body: wallet())));
    await tester.pumpAndSettle();
    expect(tester.getTopLeft(find.text('Q Wallet 積分')).dy,
        30 + kToolbarHeight + 12);
    expect(tester.takeException(), isNull);
  });

  testWidgets('cashback entry is interactive only for signed-in wallets',
      (tester) async {
    final api = JourneyApi();
    Widget wallet({required bool guest}) => MaterialApp(
      home: Scaffold(body: WalletScreen(
        api: api, guest: guest, onShowQr: (_) async {},
      )),
    );

    await tester.pumpWidget(wallet(guest: true));
    await tester.pumpAndSettle();
    final entry = find.text('返現（可提現）');
    expect(find.byIcon(Icons.chevron_right), findsNothing);
    expect(entry, cashbackEnabled ? findsOneWidget : findsNothing);
    if (cashbackEnabled) {
      final guestRow = tester.widget<InkWell>(find.ancestor(
          of: entry, matching: find.byType(InkWell)).first);
      expect(guestRow.onTap, isNull);
    }

    await tester.pumpWidget(wallet(guest: false));
    await tester.pumpAndSettle();
    expect(find.byIcon(Icons.chevron_right),
        cashbackEnabled ? findsOneWidget : findsNothing);
    expect(entry, cashbackEnabled ? findsOneWidget : findsNothing);
    if (cashbackEnabled) {
      final memberRow = tester.widget<InkWell>(find.ancestor(
          of: entry, matching: find.byType(InkWell)).first);
      expect(memberRow.onTap, isNotNull);
    }
  });

}
