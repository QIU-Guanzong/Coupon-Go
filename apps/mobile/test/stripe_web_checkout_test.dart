import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/customer.dart';
import 'package:coupon_go/screens/packages.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

class CheckoutApi extends ApiClient {
  CheckoutApi(this.options);
  final Map<String, dynamic> options;
  int purchases = 0;
  int claims = 0;
  String? purchaseRef;
  @override
  bool get isLoggedIn => true;
  @override
  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) async {
    if (path == '/payment-options') return options;
    if (path == '/coupon-details') return {'coupon': {'remaining': 3}};
    throw ApiException(404, 'missing');
  }
  @override
  Future<Map<String, dynamic>> purchasePaidCoupon(int id, String method,
      {required String clientRef}) async {
    purchases++;
    purchaseRef = clientRef;
    return {'order': {'id': 31, 'amount': 45, 'payment_status': 'pending',
      'payment_method': method, 'demo_payment': true}};
  }
  @override
  Future<Map<String, dynamic>> claimCoupon(int id) async {
    claims++;
    return {};
  }
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));
  testWidgets('paid coupon purchases via server and cancel keeps same order reference', (tester) async {
    final api = CheckoutApi({'mode': 'demo', 'fps': true, 'card': true});
    await tester.pumpWidget(LanguageScope(language: AppLanguage(), child: MaterialApp(
      home: CouponDetailScreen(api: api, coupon: Coupon.fromJson({
        'id': 7, 'coupon_name': '付費優惠券', 'is_paid': true,
        'sale_price': 45.0, 'remaining': 3, 'total': 3,
      })))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('購買 HK\$45.00'));
    await tester.pumpAndSettle();
    expect(api.purchases, 1);
    expect(api.claims, 0);
    final firstRef = api.purchaseRef;
    expect(find.textContaining('訂單 #31'), findsOneWidget);
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('購買 HK\$45.00'));
    await tester.pumpAndSettle();
    expect(api.purchases, 2);
    expect(api.purchaseRef, firstRef);
  });

  testWidgets('Stripe test accepts card only; unavailable mode cannot place order', (tester) async {
    for (final options in [
      {'mode': 'stripe_test', 'fps': false, 'card': true},
      {'mode': 'unavailable', 'fps': false, 'card': false},
    ]) {
      final api = CheckoutApi(options);
      await tester.pumpWidget(LanguageScope(language: AppLanguage(), child: MaterialApp(
        home: PackageCheckoutScreen(api: api, package: Package.fromJson({
          'id': 2, 'name': '套餐', 'stock_left': 2, 'package_price': 100,
        })))));
      await tester.pumpAndSettle();
      final fps = tester.widget<RadioListTile<String>>(find.byType(RadioListTile<String>).first);
      expect(fps.enabled, isFalse);
      await tester.drag(find.byType(ListView).first, const Offset(0, -350));
      await tester.pumpAndSettle();
      if (options['mode'] == 'unavailable' || const bool.fromEnvironment('PUBLIC_TRIAL')) {
        expect(find.text('未能載入付款方式，請重試。'), findsOneWidget);
      } else {
        expect(find.text('確認訂單不會扣款。'), findsOneWidget);
      }
      await tester.pumpWidget(const SizedBox());
      api.dispose();
    }
  });
}
