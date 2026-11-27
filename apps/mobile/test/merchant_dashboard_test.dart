import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/merchant_dashboard.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class DashboardApi extends ApiClient {
  bool fail = false;
  int balanceReads = 0;
  final weeklyRequests = <String>[];
  final requestedRanges = <int>[];
  @override
  Future<Map<String, dynamic>> merchantBalance() async {
    balanceReads++;
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
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    weeklyRequests.add(path);
    if (path != '/merchant/shops/5/weekly-report') {
      throw StateError('Unexpected report path: $path');
    }
    return {
      'shop_id': 5,
      'currency': 'HKD',
      'timezone': 'UTC',
      'current': {
        'period': {'from': '2026-11-16', 'to': '2026-11-22', 'days': 7},
        'totals': {
          'claims': 2,
          'redemptions': 1,
          'gross_amount': 200,
          'commission': 40,
          'net_amount': 160,
          'conversion_rate': 0.5,
        },
      },
      'previous': {
        'period': {'from': '2026-11-09', 'to': '2026-11-15', 'days': 7},
        'totals': {
          'claims': 0,
          'redemptions': 0,
          'gross_amount': 0,
          'commission': 0,
          'net_amount': 0,
          'conversion_rate': null
        },
      },
      'changes': {
        'gross_amount': {'delta': 200, 'percent': null},
        'commission': {'delta': 40, 'percent': null},
        'net_amount': {'delta': 160, 'percent': null},
        'conversion_percentage_points': null,
      },
    };
  }

  @override
  Future<Map<String, dynamic>> merchantDashboard(int days) async {
    requestedRanges.add(days);
    return {
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
  }

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
  testWidgets('390px English at 130% shows the date filter and applies it',
      (tester) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(390, 844);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final language = AppLanguage()..english = true;
    final api = DashboardApi();
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
          home: MerchantDashboardScreen(api: api),
        ),
      ),
    );
    await tester.pumpAndSettle();

    final heading = find.text('Business overview');
    await tester.scrollUntilVisible(heading, 240);
    final filter = find.byKey(const ValueKey('merchant-report-date-range'));
    await tester.ensureVisible(filter);
    await tester.pumpAndSettle();
    expect(tester.getTopLeft(filter).dx, greaterThanOrEqualTo(16));
    expect(tester.getBottomRight(filter).dx, lessThanOrEqualTo(374));
    expect(find.text('Last 30 days'), findsOneWidget);
    await tester.tap(filter);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Last 7 days').last);
    await tester.pumpAndSettle();
    expect(api.requestedRanges, [30, 7]);
    expect(api.balanceReads, 1);
    expect(find.text('Last 7 days'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

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
    final ledgerLine = find
        .ancestor(
          of: find.text('狀態：沖回待核對'),
          matching: find.byType(Row),
        )
        .first;
    expect(find.descendant(of: ledgerLine, matching: find.text('HK\$90.00')),
        findsOneWidget);
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

  testWidgets(
      'weekly report is read on demand for this shop and preserves a missing percentage',
      (tester) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(390, 844);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = DashboardApi();
    addTearDown(api.dispose);
    await tester.pumpWidget(MaterialApp(
      theme: CgTheme.light(merchant: true),
      home: MerchantShopDashboardScreen(
          api: api, shopId: 5, shopName: '測試店', days: 30),
    ));
    await tester.pumpAndSettle();
    expect(api.weeklyRequests, isEmpty);
    await tester.tap(find.text('經營週報'));
    await tester.pumpAndSettle();
    expect(api.weeklyRequests, ['/merchant/shops/5/weekly-report']);
    expect(find.text('最近一週  2026-11-16 – 2026-11-22 (UTC)'), findsOneWidget);
    expect(find.text('再前一週  2026-11-09 – 2026-11-15 (UTC)'), findsOneWidget);
    expect(find.text('+HK\$160.00'), findsOneWidget);
    expect(find.text('50.0%'), findsOneWidget);
    expect(find.text('0.0%'), findsNothing);
    expect(tester.takeException(), isNull);
  });
}
