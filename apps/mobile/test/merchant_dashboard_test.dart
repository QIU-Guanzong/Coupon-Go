import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/merchant_dashboard.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class DashboardApi extends ApiClient {
  bool fail = false;
  @override
  Future<Map<String, dynamic>> merchantBalance() async {
    if (fail) throw ApiException(503, '無法讀取餘額');
    return {
      'balance_complete': false,
      'legacy_untracked_redemptions': 2,
      'unknown_transfer_count': 1,
      'unknown_transfer_amount': 90,
      'unknown_reversal_count': 1,
      'withdrawable': 0,
      'pending_settlement': 90,
      'reserved': 0,
      'withdrawn': 0
    };
  }

  @override
  Future<Map<String, dynamic>> merchantDashboard(int days) async => {
        'period': {'from': '2026-11-01', 'to': '2026-11-22'},
        'totals': {
          'redemptions': 1,
          'gross_amount': 100,
          'commission': 10,
          'net_amount': 90,
          'redeemed_orders': 1,
          'avg_order_value': 100,
          'claims': 2,
          'conversion_rate': 0.5
        },
        'shops': [
          {'shop_id': 5, 'shop_name': '測試店'}
        ],
      };
  @override
  Future<Map<String, dynamic>> merchantShopBalance(int id) => merchantBalance();
  @override
  Future<Map<String, dynamic>> merchantShopDashboard(int id, int days) async =>
      {
        'totals': {
          'redemptions': 1,
          'gross_amount': 100,
          'claims': 2,
          'conversion_rate': 0.5
        },
        'trend': [
          {'date': '2026-11-21', 'redemptions': 1, 'gross_amount': 100}
        ],
        'coupons': [
          {
            'coupon_name': '測試券',
            'claims': 2,
            'redemptions': 1,
            'gross_amount': 100
          }
        ],
      };
  @override
  Future<Map<String, dynamic>> merchantBalanceEntries(int id, int page) async =>
      {
        'entries': [
          {'coupon_name': '測試券', 'state': 'reversal_unknown', 'net_amount': 90}
        ],
        'total_pages': 1,
      };
}

void main() {
  testWidgets('缺歷史流水和未知轉帳均清楚標示，店舖流水與統計可查看', (tester) async {
    final api = DashboardApi();
    await tester
        .pumpWidget(MaterialApp(home: MerchantDashboardScreen(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('有 2 筆歷史核銷待核對，金額僅包含已接入流水。'), findsOneWidget);
    expect(find.text('已接入流水可提取'), findsOneWidget);
    expect(find.textContaining('涉及 HK\$90.00'), findsOneWidget);
    expect(find.textContaining('待確認金額不代表已到帳'), findsOneWidget);
    await tester.dragUntilVisible(
        find.text('測試店'), find.byType(Scrollable).first, const Offset(0, -300));
    await tester.ensureVisible(find.text('測試店'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('測試店'));
    await tester.pumpAndSettle();
    await tester.dragUntilVisible(find.text('狀態：沖回待核對'),
        find.byType(Scrollable).first, const Offset(0, -250));
    expect(find.text('狀態：沖回待核對'), findsOneWidget);
    expect(find.text('HK\$90.00'), findsOneWidget);
  });
  testWidgets('餘額讀取失敗時可重試，沒有假零餘額', (tester) async {
    final api = DashboardApi()..fail = true;
    await tester
        .pumpWidget(MaterialApp(home: MerchantDashboardScreen(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('無法讀取餘額'), findsOneWidget);
    expect(find.text('HK\$0.00'), findsNothing);
    api.fail = false;
    await tester.tap(find.text('重試'));
    await tester.pumpAndSettle();
    expect(find.text('已接入流水可提取'), findsOneWidget);
  });
}
