import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/customer.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  Future<void> open(WidgetTester tester, String? terms,
      {bool english = false}) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(LanguageScope(
      language: AppLanguage()..english = english,
      child: MaterialApp(
          home: CouponDetailScreen(
              api: ApiClient(),
              coupon: Coupon.fromJson({
                'id': 7,
                'coupon_name': 'Coupon',
                if (terms != null) 'additional_terms': terms,
              }))),
    ));
    await tester.pumpAndSettle();
  }

  for (final terms in [null, '', ' \n ']) {
    testWidgets(
        'hides terms section for ${terms == null ? "missing" : terms.isEmpty ? "empty" : "whitespace"} terms',
        (tester) async {
      await open(tester, terms);
      expect(find.text('條款和條件'), findsNothing);
      expect(find.text('每人限領一次；不可與其他優惠同時使用。'), findsNothing);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('displays actual terms with localized English heading',
      (tester) async {
    await open(tester, '  Weekdays only.  ', english: true);
    expect(find.text('Terms and conditions'), findsOneWidget);
    expect(find.text('Weekdays only.'), findsOneWidget);
  });
}
