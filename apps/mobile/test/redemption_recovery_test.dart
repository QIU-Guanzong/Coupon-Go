import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/redemption_recovery.dart';
import 'package:coupon_go/widgets/merchant_layout.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class RecoveryApi extends ApiClient {
  bool failFirst = false;
  bool restricted = false;
  bool revoked = false;
  int revision = 0;
  final calls = <Map<String, dynamic>>[];
  @override
  int get sessionRevision => revision;
  void changeSession() {
    revision++;
    notifyListeners();
  }

  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, String>? query,
  }) async {
    if (path.endsWith('/lookup')) {
      return {
        'items': [
          {
            'wallet_item_id': 42,
            'coupon_name': '測試券',
            'shop_name': '測試店',
            'customer_phone_last4': '1234',
            'valid_to': '2026-12-31',
            'branch_required': restricted,
            'branches': restricted
                ? [
                    {'id': 9, 'name': '中環分店', 'address': '中環'},
                  ]
                : [],
          },
        ],
      };
    }
    if (path == '/admin/redemptions/7/force-review') {
      return {
        'redemption_id': 7,
        'coupon_name': '測試券',
        'shop_name': '測試店',
        'scanned_at': '2026-11-22T10:00:00',
        'revoked': revoked,
      };
    }
    throw ApiException(404, '找不到記錄');
  }

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
  }) async {
    calls.add({'path': path, ...?body});
    if (failFirst) {
      failFirst = false;
      throw ApiException(502, '結果未確認');
    }
    if (path.endsWith('/force-revoke')) revoked = true;
    return {'redemption_id': 7};
  }
}

Future<void> searchVoucher(WidgetTester tester) async {
  await tester.enterText(find.byType(TextField).first, '91234567');
  await tester.tap(find.text('查詢'));
  await tester.pumpAndSettle();
  expect(find.text('測試券'), findsOneWidget);
}

void main() {
  testWidgets('補錄失聯後使用同一請求編號重試', (tester) async {
    final api = RecoveryApi()
      ..failFirst = true
      ..restricted = true;
    await tester.pumpWidget(
      MaterialApp(home: MerchantRedemptionRecoveryScreen(api: api)),
    );
    await searchVoucher(tester);
    await tester.tap(find.text('補記這張券'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('核銷分店（必選）'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('中環分店').last);
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, '顧客離店後補記');
    await tester.tap(find.text('確認補記'));
    await tester.pumpAndSettle();
    expect(find.textContaining('結果未確認'), findsOneWidget);
    expect(
        tester.widget<TextField>(find.byType(TextField).first).enabled, false);
    expect(find.text('補記這張券'), findsNothing);
    await tester.tap(find.text('使用原請求重試'));
    await tester.pumpAndSettle();
    expect(api.calls.length, 2);
    expect(api.calls[0]['branch_id'], 9);
    expect(api.calls[0]['reason'], '顧客離店後補記');
    expect(api.calls[0], api.calls[1]);
    expect(find.text('已補記核銷，記錄編號 7。'), findsOneWidget);
  });

  testWidgets('受限券必須選擇後端返回的允許分店', (tester) async {
    final api = RecoveryApi()..restricted = true;
    await tester.pumpWidget(
      MaterialApp(home: MerchantRedemptionRecoveryScreen(api: api)),
    );
    await searchVoucher(tester);
    await tester.tap(find.text('補記這張券'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('確認補記'));
    await tester.pumpAndSettle();
    expect(find.text('請選擇此券可使用的分店。'), findsOneWidget);
    expect(api.calls, isEmpty);
    await tester.tap(find.text('核銷分店（必選）'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('中環分店').last);
    await tester.pumpAndSettle();
    await tester.tap(find.text('確認補記'));
    await tester.pumpAndSettle();
    expect(api.calls.single['branch_id'], 9);
  });

  testWidgets('客服先核對記錄並填原因才可撤銷', (tester) async {
    final api = RecoveryApi();
    await tester.pumpWidget(
      MaterialApp(home: ForceRevokeRedemptionScreen(api: api)),
    );
    await tester.enterText(find.byType(TextField).first, '7');
    await tester.tap(find.text('查詢'));
    await tester.pumpAndSettle();
    expect(find.text('店舖：測試店'), findsOneWidget);
    await tester.tap(find.text('繼續撤銷'));
    await tester.pumpAndSettle();
    expect(find.text('請填寫撤銷原因。'), findsOneWidget);
    expect(api.calls, isEmpty);
    await tester.enterText(find.byType(TextField).last, '顧客提供正確記錄');
    await tester.tap(find.text('繼續撤銷'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('確認撤銷'));
    await tester.pumpAndSettle();
    expect(api.calls.single['reason'], '顧客提供正確記錄');
    expect(find.widgetWithText(MerchantStatusPill, '已撤銷'), findsOneWidget);
  });

  testWidgets('切換帳號時清除上一帳號的查詢結果', (tester) async {
    final api = RecoveryApi();
    await tester.pumpWidget(
      MaterialApp(home: MerchantRedemptionRecoveryScreen(api: api)),
    );
    await searchVoucher(tester);
    api.changeSession();
    await tester.pumpAndSettle();
    expect(find.text('測試券'), findsNothing);
  });
  testWidgets('撤銷失聯後須重新查詢才可再次操作', (tester) async {
    final api = RecoveryApi()..failFirst = true;
    await tester.pumpWidget(
      MaterialApp(home: ForceRevokeRedemptionScreen(api: api)),
    );
    await tester.enterText(find.byType(TextField).first, '7');
    await tester.tap(find.text('查詢'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, '顧客確認');
    await tester.tap(find.text('繼續撤銷'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('確認撤銷'));
    await tester.pumpAndSettle();
    expect(find.textContaining('請重新查詢核銷狀態'), findsOneWidget);
    expect(find.text('繼續撤銷'), findsNothing);
    api.revoked = true;
    await tester.tap(find.text('查詢'));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(MerchantStatusPill, '已撤銷'), findsOneWidget);
    expect(api.calls, hasLength(1));
  });
  testWidgets('確認補錄期間切換帳號不會提交舊券', (tester) async {
    final api = RecoveryApi();
    await tester.pumpWidget(
        MaterialApp(home: MerchantRedemptionRecoveryScreen(api: api)));
    await searchVoucher(tester);
    await tester.tap(find.text('補記這張券'));
    await tester.pumpAndSettle();
    api.changeSession();
    await tester.pump();
    await tester.tap(find.text('確認補記'));
    await tester.pumpAndSettle();
    expect(api.calls, isEmpty);
  });

  testWidgets('確認撤銷期間切換帳號不會提交舊記錄', (tester) async {
    final api = RecoveryApi();
    await tester
        .pumpWidget(MaterialApp(home: ForceRevokeRedemptionScreen(api: api)));
    await tester.enterText(find.byType(TextField).first, '7');
    await tester.tap(find.text('查詢'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, '核對後撤銷');
    await tester.tap(find.text('繼續撤銷'));
    await tester.pumpAndSettle();
    api.changeSession();
    await tester.pump();
    await tester.tap(find.text('確認撤銷'));
    await tester.pumpAndSettle();
    expect(api.calls, isEmpty);
  });
}
