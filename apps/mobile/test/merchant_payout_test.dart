import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/merchant_payout.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

class PayoutApi extends ApiClient {
  PayoutApi() : super(baseUrl: 'https://isolated.example');
  List<Map<String, dynamic>> active = [];
  final List<String> requestIds = [];
  bool timeoutFirst = false;
  Completer<Map<String, dynamic>>? preflight;
  int activeReads = 0;
  @override
  Future<Map<String, dynamic>> get(String path,
          {Map<String, String>? query}) async =>
      {'id': 123};
  @override
  Future<Map<String, dynamic>> merchantConnectBalance(int id) async => {
        'connect_mode': 'simulated',
        'account_ready': true,
        'connect_configured': true,
        'available': 100,
      };
  @override
  Future<Map<String, dynamic>> activeMerchantPayouts(int id) async {
    activeReads++;
    if (activeReads > 1 && preflight != null) return preflight!.future;
    return {'payouts': active};
  }

  @override
  Future<Map<String, dynamic>> createMerchantPayout(
      int shopId, String amount, String requestId) async {
    requestIds.add(requestId);
    if (timeoutFirst && requestIds.length == 1) {
      throw TimeoutException('test timeout');
    }
    active = [
      {
        'request_id': requestId,
        'amount': double.parse(amount),
        'status': 'submitted'
      }
    ];
    return {'status': 'submitted'};
  }
}

Future<void> showPayout(WidgetTester tester, PayoutApi api) async {
  await tester.pumpWidget(MaterialApp(
      home: MerchantPayoutScreen(api: api, shopId: 5, shopName: '測試店')));
  await tester.pumpAndSettle();
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));
  testWidgets('斷線後重新進入並重試，使用同一請求編號', (tester) async {
    final api = PayoutApi()..timeoutFirst = true;
    await showPayout(tester, api);
    await tester.enterText(find.byType(TextField), '30.50');
    await tester.tap(find.text('提交提款核對'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('確認提交'));
    await tester.pumpAndSettle();
    expect(api.requestIds, hasLength(1));
    expect(find.textContaining('請保留原請求'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
    await showPayout(tester, api);
    expect(find.textContaining('本機保留 HK\$30.50'), findsOneWidget);
    await tester.tap(find.text('用同一請求核對或重試'));
    await tester.pumpAndSettle();
    expect(api.requestIds, hasLength(2));
    expect(api.requestIds[1], api.requestIds[0]);
    expect(find.text('處理中'), findsOneWidget);
  });
  testWidgets('跨設備只讀待核對提款，恢復時沿用服務端原編號', (tester) async {
    final api = PayoutApi()
      ..active = [
        {
          'request_id': 'existing_request_123456',
          'amount': 24.5,
          'status': 'payout_unknown'
        }
      ];
    await showPayout(tester, api);
    expect(find.text('結果待核對，請勿重複提款'), findsOneWidget);
    expect(find.text('提交提款核對'), findsNothing);
    await tester.tap(find.text('恢復原提款請求'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('用同一請求核對或重試'));
    await tester.pumpAndSettle();
    expect(api.requestIds.single, 'existing_request_123456');
  });
  testWidgets('舊提款缺編號時阻止重發', (tester) async {
    final api = PayoutApi()
      ..active = [
        {'request_id': null, 'amount': 10, 'status': 'payout_unknown'}
      ];
    await showPayout(tester, api);
    expect(find.text('舊提款缺少請求編號，請聯絡平台核對。'), findsOneWidget);
    expect(find.text('恢復原提款請求'), findsNothing);
    expect(find.text('提交提款核對'), findsNothing);
  });
  testWidgets('確認框打開時切換帳號不提交舊請求', (tester) async {
    final api = PayoutApi();
    await api.saveToken('first', persist: false);
    await showPayout(tester, api);
    await tester.enterText(find.byType(TextField), '20');
    await tester.tap(find.text('提交提款核對'));
    await tester.pumpAndSettle();
    await api.saveToken('second', persist: false);
    await tester.pump();
    await tester.tap(find.text('確認提交'));
    await tester.pumpAndSettle();
    expect(api.requestIds, isEmpty);
    expect(find.textContaining('登入狀態已變更'), findsOneWidget);
  });
  testWidgets('提交前查詢期間切換帳號不發起提款', (tester) async {
    final api = PayoutApi()..preflight = Completer<Map<String, dynamic>>();
    await api.saveToken('first', persist: false);
    await showPayout(tester, api);
    await tester.enterText(find.byType(TextField), '20');
    await tester.tap(find.text('提交提款核對'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('確認提交'));
    await tester.pump();
    await api.saveToken('second', persist: false);
    api.preflight!.complete({'payouts': []});
    await tester.pumpAndSettle();
    expect(api.requestIds, isEmpty);
    expect(find.textContaining('登入狀態已變更'), findsOneWidget);
  });
  testWidgets('確認前發現其他設備有待處理提款時停止', (tester) async {
    final api = PayoutApi()..preflight = Completer<Map<String, dynamic>>();
    await showPayout(tester, api);
    await tester.enterText(find.byType(TextField), '20');
    await tester.tap(find.text('提交提款核對'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('確認提交'));
    await tester.pump();
    api.preflight!.complete({
      'payouts': [
        {
          'request_id': 'existing_request_123456',
          'amount': 20,
          'status': 'submitted'
        }
      ]
    });
    await tester.pumpAndSettle();
    expect(api.requestIds, isEmpty);
    expect(find.textContaining('已有未完成提款'), findsOneWidget);
  });
}
