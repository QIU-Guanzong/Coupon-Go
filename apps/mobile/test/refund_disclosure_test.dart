import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/bargain.dart';
import 'package:coupon_go/screens/packages.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  for (final entry in {
    'pending': '模擬退款處理中',
    'failed': '模擬退款未成功',
    'succeeded': '模擬退款已完成',
    'other': '模擬退款狀態待確認',
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
      expect(find.text('模擬退款不涉及真實資金。'), findsOneWidget);
      expect(find.textContaining('已退差價'), findsNothing);
      await tester.pumpWidget(const SizedBox.shrink());
      language.dispose();
      api.dispose();
    });
  }

  testWidgets('package terms disclose simulated and trial refund rules', (tester) async {
    final language = AppLanguage();
    await tester.pumpWidget(LanguageScope(
      language: language,
      child: MaterialApp(home: PackageTermsScreen(
        package: Package.fromJson({'id': 1, 'refund_policy': 'anytime'}),
        demoPayment: true,
      )),
    ));
    expect(find.text('模擬退款不涉及真實資金。'), findsOneWidget);
    expect(find.textContaining('1–3'), findsNothing);
    if (const bool.fromEnvironment('PUBLIC_TRIAL')) {
      expect(find.textContaining('體驗版不執行過期自動退款'), findsOneWidget);
    }
    await tester.pumpWidget(const SizedBox.shrink());
    language.dispose();
  });

  testWidgets('trial terms remain simulated before payment options load', (tester) async {
    final language = AppLanguage();
    await tester.pumpWidget(LanguageScope(
      language: language,
      child: MaterialApp(home: PackageTermsScreen(
        package: Package.fromJson({'id': 1, 'refund_policy': 'anytime'}),
      )),
    ));
    if (const bool.fromEnvironment('PUBLIC_TRIAL')) {
      expect(find.text('模擬退款不涉及真實資金。'), findsOneWidget);
      expect(find.textContaining('如涉及實際付款'), findsNothing);
    } else {
      expect(find.textContaining('如涉及實際付款'), findsOneWidget);
    }
    await tester.pumpWidget(const SizedBox.shrink());
    language.dispose();
  });
}
