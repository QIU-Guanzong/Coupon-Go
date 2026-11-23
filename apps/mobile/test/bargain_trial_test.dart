import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/bargain.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('bargain action reflects whether the trial supports it', (tester) async {
    final language = AppLanguage();
    final api = ApiClient();
    final campaign = BargainCampaign.fromJson({
      'id': 1, 'name': '示例砍價', 'shop_name': '示例店',
      'stock_left': 5, 'original_price': 100, 'floor_price': 50,
    });
    await tester.pumpWidget(LanguageScope(language: language,
      child: MaterialApp(home: BargainCampaignScreen(api: api, campaign: campaign))));
    const trial = bool.fromEnvironment('PUBLIC_TRIAL');
    final action = find.widgetWithText(ElevatedButton,
      publicPreview ? '只供瀏覽' : trial ? '體驗版暫不支援發起' : '立即發起砍價');
    await tester.scrollUntilVisible(action, 300);
    await tester.pumpAndSettle();
    expect(action, findsOneWidget);
    expect(tester.widget<ElevatedButton>(action).onPressed == null, trial || publicPreview);
    await tester.pumpWidget(const SizedBox.shrink());
    language.dispose();
    api.dispose();
  });
}
