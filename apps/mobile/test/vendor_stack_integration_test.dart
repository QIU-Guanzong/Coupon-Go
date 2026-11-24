import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'merchant_review_shops_test.dart' as reviews;
import 'vendor_coupon_status_test.dart' as lifecycle;

class StackApi extends reviews.ReviewShopsApi {
  StackApi()
      : super([
          {...reviews.first, 'can_manage_coupons': true},
          reviews.second,
        ]);

  final filters = <String?>[];

  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, String>? query,
  }) async {
    if (path == '/get-vendor-coupon-list') {
      final status = query?['status'];
      filters.add(status);
      return lifecycle.CouponStatusApi.coupons([
        if (status == null)
          {
            ...lifecycle.CouponStatusApi.row(91, 'Listed coupon', 'active'),
            'shop_id': 11,
          }
        else if (status == 'pending_review')
          {
            ...lifecycle.CouponStatusApi.row(92, 'Pending coupon', status),
            'shop_id': 11,
          }
      ]);
    }
    return super.get(path, query: query);
  }
}

void main() {
  testWidgets(
      'brand survives and coupon filters never change review shop scope',
      (tester) async {
    final api = StackApi();
    await reviews.showHome(tester, api);
    final appBar = tester.widget<AppBar>(find.byType(AppBar).first);
    final logo = find.descendant(
      of: find.byWidget(appBar),
      matching: find.byWidgetPredicate((widget) =>
          widget is Image &&
          widget.image is AssetImage &&
          (widget.image as AssetImage).assetName ==
              'assets/images/img_logo.png'),
    );
    expect(logo, findsOneWidget);
    expect(CgTheme.pageInset, 16);
    await tester.ensureVisible(lifecycle.filter);
    await tester.tap(lifecycle.filter);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Pending review').last);
    await tester.pumpAndSettle();
    expect(api.filters, [null, 'pending_review']);
    expect(find.byKey(const ValueKey('vendor-coupon-status-91')), findsNothing);
    expect(
        find.byKey(const ValueKey('vendor-coupon-status-92')), findsOneWidget);
    await reviews.openPicker(tester);
    expect(find.text('Central'), findsOneWidget);
    expect(find.text('Mong Kok'), findsOneWidget);
    await tester.tap(find.text('Mong Kok'));
    await tester.pumpAndSettle();
    expect(api.shopReads, [22]);
    expect(api.replies, isEmpty);
    expect(tester.takeException(), isNull);
  });
}
