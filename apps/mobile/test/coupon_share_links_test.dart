import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/customer.dart';
import 'package:coupon_go/share_links.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const trialShareKey = '0123456789abcdef01234567';

class ShareCatalog extends ApiClient {
  String? path;
  Map<String, String>? requestQuery;

  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, String>? query,
  }) async {
    this.path = path;
    requestQuery = query;
    return {
      if (publicTrial) 'share_key': trialShareKey,
      'coupon': {
        'coupon_id': 31,
        'coupon_name': 'Tea coupon',
        'shop_name': 'Tea Shop',
        'status': 'sold_out',
        'remaining': 0,
      },
    };
  }
}

void main() {
  test('share links keep the app mount and remove query and existing route',
      () {
    final base = Uri.parse(
        'https://example.com/coupongo/index.html?session=old#/settings');
    expect(customerShareUriFrom(base, 'coupon', '31').toString(),
        'https://example.com/coupongo/#/coupon/31');
    const gift = 'abcdefghijklmnopqrstuvwx12345678.'
        'abcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcdefabcd';
    final giftLink = customerShareUriFrom(base, 'gift', gift)!;
    expect(giftLink.query, isEmpty);
    expect(couponGiftToken(giftLink.fragment), gift);
    expect(customerShareUriFrom(base, 'gift', '../../settings'), isNull);
    expect(customerShareUriFrom(Uri.parse('file:///coupongo'), 'coupon', '31'),
        isNull);
    expect(sharedCouponRoute('/coupon/2147483648'), isNull);
    expect(sharedCouponRoute('/coupon/31-$trialShareKey'),
        (id: 31, key: trialShareKey));
  });

  testWidgets('shared coupon loads public details and shows sold-out status',
      (tester) async {
    final api = ShareCatalog();
    await tester.pumpWidget(LanguageScope(
      language: AppLanguage()..english = true,
      child: MaterialApp(
          home: SharedCouponScreen(
              api: api,
              couponId: 31,
              shareKey: publicTrial ? trialShareKey : null)),
    ));
    await tester.pumpAndSettle();
    expect(api.path,
        publicTrial ? '/trial/public-coupons/31' : '/public-coupons/31');
    expect(api.requestQuery, publicTrial ? {'key': trialShareKey} : null);
    expect(find.text('Tea coupon'), findsOneWidget);
    expect(find.text('This offer has sold out'), findsOneWidget);
    expect(find.text('Claim now'), findsNothing);
  });
}
