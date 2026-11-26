import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/customer.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _ClaimApi extends ApiClient {
  int claims = 0;
  final pages = <int>[];
  final qrRequests = <int>[];
  bool alreadyClaimed = false;
  String? claimError;
  Completer<Map<String, dynamic>>? pendingClaim;

  @override
  Future<Map<String, dynamic>> couponDetails(int id) async => {
        'is_claimed': alreadyClaimed,
        'coupon': {'remaining': 3}
      };

  @override
  Future<Map<String, dynamic>> claimCoupon(int couponId) async {
    claims++;
    if (claimError case final code?) {
      throw ApiException(409, 'Claim conflict', code: code);
    }
    return pendingClaim?.future ??
        {
          'wallet_item_id': 81,
          'coupon': {'remaining': 2},
        };
  }

  @override
  Future<Map<String, dynamic>> wallet({int? status, int page = 1}) async {
    expect(status, 1);
    pages.add(page);
    return {
      'total_pages': 2,
      'items': page == 1
          ? [_row(82, 32, 'Unrelated offer'), _row(80, 42, 'Older claim')]
          : [_row(81, 42, 'Just claimed')],
    };
  }

  @override
  Future<Map<String, dynamic>> generateQr(int walletItemId) async {
    qrRequests.add(walletItemId);
    return {
      'code': '043987',
      'qr_payload': 'test-issued-coupon-81',
      'expires_in_seconds': 60,
    };
  }
}

Map<String, dynamic> _row(int id, int couponId, String name) => {
      'id': id,
      'coupon_id': couponId,
      'coupon_name': name,
      'shop_name': 'Fixture store',
      'status': 1,
    };

Future<void> _open(WidgetTester tester, _ClaimApi api) async {
  tester.view.physicalSize = const Size(390, 844);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  api.activateTrialToken('test-account');
  await tester.pumpWidget(MaterialApp(
    home: CouponDetailScreen(
      api: api,
      coupon: Coupon.fromJson({
        'id': 42,
        'coupon_name': 'Original offer',
        'total': 3,
        'remaining': 3,
      }),
    ),
  ));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('claim then opens Q Wallet for the issued item on page two',
      (tester) async {
    final api = _ClaimApi();
    await _open(tester, api);
    await tester.tap(find.text('立即領取'));
    await tester.pumpAndSettle();
    expect(find.text('去錢包出示'), findsOneWidget);
    expect(api.qrRequests, isEmpty);
    await tester.tap(find.text('去錢包出示'));
    await tester.pumpAndSettle();
    expect(find.text('Q Wallet'), findsOneWidget);
    expect(find.text('Just claimed'), findsOneWidget);
    expect(find.text('Unrelated offer'), findsNothing);
    expect(find.text('Older claim'), findsNothing);
    expect(api.pages, [1, 2]);
    expect(api.claims, 1);
    expect(api.qrRequests, isEmpty);
    await tester.tap(find.text('出示'));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 350));
    expect(api.qrRequests, [81]);
    expect(find.text('043987'), findsOneWidget);
    api.activateTrialToken('replacement-account');
    await tester.pumpAndSettle();
    expect(find.text('043987'), findsNothing);
    expect(find.text('帳戶已切換，請返回。'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('an already claimed coupon reopens its unused Q Wallet item',
      (tester) async {
    final api = _ClaimApi()..alreadyClaimed = true;
    await _open(tester, api);
    expect(find.text('去錢包出示'), findsOneWidget);
    await tester.tap(find.text('去錢包出示'));
    await tester.pumpAndSettle();
    expect(find.text('Older claim'), findsOneWidget);
    expect(find.text('Unrelated offer'), findsNothing);
    expect(api.pages, [1]);
    expect(api.claims, 0);
  });

  testWidgets('only a verified duplicate claim enables the wallet action',
      (tester) async {
    final api = _ClaimApi()..claimError = 'coupon_unavailable';
    await _open(tester, api);
    await tester.tap(find.text('立即領取'));
    await tester.pumpAndSettle();
    expect(find.text('去錢包出示'), findsNothing);
    expect(api.pages, isEmpty);
    api.claimError = 'coupon_already_claimed';
    await tester.tap(find.text('立即領取'));
    await tester.pumpAndSettle();
    expect(find.text('去錢包出示'), findsOneWidget);
  });

  testWidgets('late claim after changing account never exposes the old item',
      (tester) async {
    final api = _ClaimApi()..pendingClaim = Completer<Map<String, dynamic>>();
    await _open(tester, api);
    await tester.tap(find.text('立即領取'));
    await tester.pump();
    api.activateTrialToken('another-account');
    api.pendingClaim!.complete({'wallet_item_id': 81});
    await tester.pumpAndSettle();
    expect(find.text('去錢包出示'), findsNothing);
    expect(api.pages, isEmpty);
    expect(api.qrRequests, isEmpty);
  });
}
