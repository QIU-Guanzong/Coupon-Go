import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/bargain.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('bargain start stays actionable for in-stock offers', (tester) async {
    final language = AppLanguage();
    final api = ApiClient();
    final campaign = BargainCampaign.fromJson({
      'id': 1, 'name': '示例砍價', 'shop_name': '示例店',
      'stock_left': 5, 'original_price': 100, 'floor_price': 50,
    });
    await tester.pumpWidget(LanguageScope(language: language,
      child: MaterialApp(home: BargainCampaignScreen(api: api, campaign: campaign))));
    final action = find.widgetWithText(ElevatedButton,
      '立即發起砍價');
    await tester.scrollUntilVisible(action, 300);
    await tester.pumpAndSettle();
    expect(action, findsOneWidget);
    expect(tester.widget<ElevatedButton>(action).onPressed, isNotNull);
    expect(find.textContaining('不會扣款'), findsOneWidget);
    await tester.pumpWidget(const SizedBox.shrink());
    language.dispose();
    api.dispose();
  });
}
