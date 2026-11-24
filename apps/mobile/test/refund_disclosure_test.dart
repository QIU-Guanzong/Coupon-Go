import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/bargain.dart';
import 'package:coupon_go/screens/packages.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _RefundOrdersApi extends ApiClient {
  final filters = <String?>[];

  @override
  Future<List<dynamic>> orders({String? status}) async {
    filters.add(status);
    return [
      for (final (id, name, result) in [
        (1, 'Processing order', 'requested'),
        (2, 'Completed order', 'refunded'),
        (3, 'Declined order', 'rejected'),
        (4, 'Unrelated order', 'none'),
        (5, 'Unknown order', 'unrecognized'),
      ])
        {
          'id': id,
          'package_name': name,
          'refund_status': result,
          'payment_status': 'paid',
        }
    ];
  }
}

void main() {
  testWidgets('refunds open directly on three actual statuses and keep orders unfiltered at the API', (tester) async {
    final language = AppLanguage()..english = true;
    final api = _RefundOrdersApi();
    await tester.pumpWidget(LanguageScope(
      language: language,
      child: MaterialApp(home: OrdersScreen(api: api, initialTab: 3)),
    ));
    await tester.pumpAndSettle();
    expect(api.filters, [null]);
    expect(DefaultTabController.of(tester.element(find.byType(TabBar))).index, 3);
    expect(find.text('Processing order'), findsOneWidget);
    expect(find.text('Completed order'), findsOneWidget);
    expect(find.text('Declined order'), findsOneWidget);
    expect(find.text('Unrelated order'), findsNothing);
    expect(find.text('Unknown order'), findsNothing);
    await tester.pumpWidget(const SizedBox.shrink());
    language.dispose();
    api.dispose();
  });

  for (final entry in {
    'pending': '退款處理中',
    'failed': '退款未成功',
    'succeeded': '退款已完成',
    'other': '退款狀態待確認',
  }.entries) {
    testWidgets('bargain refund displays ${entry.key} truthfully', (tester) async {
      final language = AppLanguage();
      final api = ApiClient();
      final session = BargainSession.fromJson({'status': 'failed'});
      expect(session.statusText, '未成團');
      await tester.pumpWidget(LanguageScope(
        language: language,
        child: MaterialApp(home: BargainResultScreen(
          api: api,
          session: session,
          purchase: {'refund': {'amount': 10, 'status': entry.key}},
        )),
      ));
      expect(find.textContaining(entry.value), findsOneWidget);
      expect(find.text('退款只更新訂單記錄，不會退回款項。'), findsOneWidget);
      expect(find.textContaining('已退差價'), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
      language.dispose();
      api.dispose();
    });
  }

  testWidgets('package terms disclose refund rules without promising a transfer', (tester) async {
    final language = AppLanguage();
    await tester.pumpWidget(LanguageScope(
      language: language,
      child: MaterialApp(home: PackageTermsScreen(
        package: Package.fromJson({'id': 1, 'refund_policy': 'anytime'}),
        demoPayment: true,
      )),
    ));
    expect(find.text('退款只更新訂單記錄，不會退回款項。'), findsOneWidget);
    expect(find.textContaining('1–3'), findsNothing);
    if (const bool.fromEnvironment('PUBLIC_TRIAL')) {
      expect(find.textContaining('過期不會自動退款'), findsOneWidget);
    }
    await tester.pumpWidget(const SizedBox.shrink());
    language.dispose();
  });

  testWidgets('isolated terms do not promise a transfer before payment options load', (tester) async {
    final language = AppLanguage();
    await tester.pumpWidget(LanguageScope(
      language: language,
      child: MaterialApp(home: PackageTermsScreen(
        package: Package.fromJson({'id': 1, 'refund_policy': 'anytime'}),
      )),
    ));
    if (const bool.fromEnvironment('PUBLIC_TRIAL')) {
      expect(find.text('退款只更新訂單記錄，不會退回款項。'), findsOneWidget);
      expect(find.textContaining('退款退回原付款方式'), findsNothing);
    } else {
      expect(find.textContaining('退款退回原付款方式'), findsOneWidget);
    }
    await tester.pumpWidget(const SizedBox.shrink());
    language.dispose();
  });
}
