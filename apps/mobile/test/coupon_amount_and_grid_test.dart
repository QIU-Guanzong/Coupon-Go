import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/discovery.dart';
import 'package:coupon_go/screens/customer.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Coupon discount(double percentage) => Coupon.fromJson({
  'coupon_id': 1, 'coupon_name': 'Store offer',
  'coupon_type': 'Discount', 'percentage': percentage,
});

void main() {
  test('percentage is the amount payable, with Chinese and English units', () {
    for (final entry in <double, String>{
      0: '0 折', 70: '7 折', 85: '8.5 折', 85.5: '8.55 折',
      85.23: '8.523 折', 100: '10 折',
    }.entries) {
      expect(discount(entry.key).amountText, entry.value);
    }
    expect(discount(85).amountTextEnglish, '85% of regular price');
    expect(discount(85.23).amountTextEnglish, '85.23% of regular price');
  });

  test('money is presented in HKD without rounding away cents or sign', () {
    final cash = Coupon.fromJson({'coupon_type': 'Cash', 'cash_amount': 1234.5});
    expect(cash.amountText, '減 HK\$1,234.50');
    expect(cash.amountTextEnglish, 'HK\$1,234.50 off');
    final price = Coupon.fromJson({'coupon_type': 'Price', 'discount_price': 12.34});
    expect(price.amountText, 'HK\$12.34');
    expect(price.amountTextEnglish, 'HK\$12.34');
    expect(Coupon.fromJson({'coupon_type': 'Cash', 'cash_amount': -1.5}).amountText,
        '減 -HK\$1.50');
    expect(Coupon.fromJson({'coupon_type': 'Gift', 'gift': '贈品'}).amountText, '送贈品');
    expect(Coupon.fromJson({'coupon_type': 'Gift', 'gift': 'dessert'}).amountTextEnglish,
        'Free dessert');
    expect(Coupon.fromJson({'coupon_type': 'Other', 'coupon_name': '優惠'}).amountText, '優惠');
  });

  testWidgets('detail preserves exact original price and minimum spend', (tester) async {
    final coupon = Coupon.fromJson({
      'coupon_id': 4, 'coupon_type': 'Cash', 'coupon_name': '優惠',
      'cash_amount': 12.5, 'regular_price': 1234.56, 'min_spend': 99.99,
    });
    await tester.pumpWidget(MaterialApp(home: CouponDetailScreen(api: ApiClient(), coupon: coupon)));
    await tester.pumpAndSettle();
    expect(find.text('減 HK\$12.50'), findsOneWidget);
    expect(find.text('HK\$1,234.56'), findsOneWidget);
    expect(find.text('於本店惠顧滿 HK\$99.99'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  for (final width in [320.0, 390.0, 768.0, 1280.0]) {
    for (final scale in [1.0, 2.0]) {
      for (final english in [false, true]) {
        testWidgets('coupon grid fits $width px, text scale $scale, english=$english', (tester) async {
          tester.view.physicalSize = Size(width, 844);
          tester.view.devicePixelRatio = 1;
          addTearDown(tester.view.resetPhysicalSize);
          addTearDown(tester.view.resetDevicePixelRatio);
          final language = AppLanguage()..english = english;
          var opened = 0;
          final rows = [
            {
              'coupon_id': 101, 'coupon_type': 'Gift',
              'coupon_name': english ? 'A gift coupon with a long title' : '免費送麻辣牛小排一份',
              'shop_name': english ? 'A shop with a long name' : '蜀香麻辣火鍋城',
              'gift': english ? 'a long complimentary dessert selection' : '麻辣牛小排一份',
            },
            {
              'coupon_id': 102, 'coupon_type': 'Price', 'coupon_name': '套餐優惠',
              'shop_name': '商店', 'discount_price': 1234.56, 'regular_price': 1999.99,
            },
          ];
          await tester.pumpWidget(LanguageScope(
            language: language,
            child: MaterialApp(home: MediaQuery(
              data: MediaQueryData(size: Size(width, 844), textScaler: TextScaler.linear(scale)),
              child: Scaffold(body: CouponResults(
                api: ApiClient(), load: () async => rows, onOpenCoupon: (_) => opened++,
              )),
            )),
          ));
          await tester.pumpAndSettle();
          expect(tester.takeException(), isNull);
          final action = find.text(english ? 'Collect' : '領取');
          expect(action, findsNWidgets(2));
          expect(find.text('HK\$1,234.56'), findsOneWidget);
          expect(find.text('HK\$1,999.99'), findsOneWidget);
          for (final element in action.evaluate()) {
            final rect = tester.getRect(find.byWidget(element.widget));
            expect(rect.left, greaterThanOrEqualTo(0));
            expect(rect.right, lessThanOrEqualTo(width));
          }
          await tester.tap(action.first);
          await tester.pump();
          expect(opened, 1);
          expect(tester.takeException(), isNull);
        });
      }
    }
  }
}
