import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/account.dart';
import 'package:coupon_go/screens/customer.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:qr_flutter/qr_flutter.dart';

Widget _app(Widget screen, {bool english = true}) => LanguageScope(
      language: AppLanguage()..english = english,
      child: MaterialApp(home: screen),
    );

class _QrApi extends ApiClient {
  int? expiry;

  @override
  Future<Map<String, dynamic>> generateQr(int walletItemId) async => {
        'code': '123456',
        'qr_payload': 'coupon-test',
        if (expiry != null) 'expires_in_seconds': expiry,
      };
}

void main() {
  testWidgets('QR countdown is withheld until the server supplies expiry',
      (tester) async {
    final api = _QrApi();
    await tester.pumpWidget(_app(QrShowScreen(
      api: api,
      item: WalletItem.fromJson({'id': 7, 'coupon_name': 'Tea offer'}),
    )));
    await tester.pumpAndSettle();
    expect(find.text('Could not confirm when the code expires. Try again.'),
        findsOneWidget);
    expect(find.byType(QrImageView), findsNothing);

    api.expiry = 137;
    await tester.tap(find.text('Get a new code'));
    await tester.pumpAndSettle();
    expect(find.byType(QrImageView), findsOneWidget);
    expect(find.text('00:02:17'), findsOneWidget);
    expect(find.text('00:15:00'), findsNothing);

    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('FAQ avoids hard-coded points and QR duration', (tester) async {
    await tester.pumpWidget(_app(const GuideScreen()));
    await tester.pumpAndSettle();
    await tester.tap(find.text('What are points for?'));
    await tester.pumpAndSettle();
    expect(
        find.text(
            'Check Q Wallet for your points balance and available rewards.'),
        findsOneWidget);
    expect(find.textContaining('9 points'), findsNothing);
    await tester.scrollUntilVisible(
        find.text('How long is the QR code valid?'), 100);
    await tester.tap(find.text('How long is the QR code valid?'));
    await tester.pumpAndSettle();
    expect(find.textContaining('Use the countdown'), findsOneWidget);
    expect(find.textContaining('15:00'), findsNothing);
  });

  testWidgets(
      'percentage offer uses the same payable fraction in either locale',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    tester.platformDispatcher.textScaleFactorTestValue = 2;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    final coupon = Coupon.fromJson({
      'id': 7,
      'coupon_type': 'Discount',
      'percentage': 70,
      'valid_to': '2027-12-31',
    });
    for (final english in [false, true]) {
      await tester.pumpWidget(_app(
        CouponDetailScreen(api: ApiClient(), coupon: coupon),
        english: english,
      ));
      await tester.pumpAndSettle();
      expect(
          find.text(english ? '70% of regular price' : '7 折'), findsOneWidget);
      expect(tester.takeException(), isNull);
    }
  });
}
