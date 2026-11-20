import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/packages.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class PackageApi extends ApiClient {
  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    if (path == '/get-favorite-coupons-list') return {'items': [], 'total_pages': 1};
    if (path != '/orders/13') throw ApiException(503, 'Offline');
    return {
      'order': {
        'id': 13, 'package_name': '雙人茶席', 'payment_status': 'paid',
        'paid_amount': 396, 'amount': 396, 'quantity': 2, 'refundable': true
      }
    };
  }
}

void main() {
  expiredOrderTests();
  testWidgets('paid result and order detail use the same server receipt', (tester) async {
    final api = PackageApi();
    await tester.pumpWidget(MaterialApp(home: PaymentResultScreen(
        api: api, initialOrder: PackageOrder.fromJson({'id': 13}))));
    await tester.pumpAndSettle();
    await tester.tap(find.text('查看券'));
    await tester.pumpAndSettle();
    expect(find.text('我的券'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.pumpWidget(MaterialApp(home: OrderDetailScreen(api: api, orderId: 13)));
    await tester.pumpAndSettle();
    expect(find.text('2 份'), findsOneWidget);
    expect(find.text('申請退款'), findsOneWidget);
  });
}

class ExpiredOrderApi extends ApiClient {
  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    if (path == '/get-favorite-coupons-list') return {'items': [], 'total_pages': 1};
    if (path != '/orders/99') throw ApiException(404, 'missing');
    return {
      'order': {
        'id': 99, 'package_name': '過期套餐', 'payment_status': 'paid',
        'paid_amount': 100, 'amount': 100, 'refund_status': 'requested',
        'refundable': false, 'wallet_status': 3,
        'latest_refund_reason': '過期自動退',
      }
    };
  }
}

void expiredOrderTests() {
  testWidgets('expired order shows voucher and automatic refund state',
      (tester) async {
    await tester.pumpWidget(MaterialApp(
        home: OrderDetailScreen(api: ExpiredOrderApi(), orderId: 99)));
    await tester.pumpAndSettle();
    expect(find.text('退款處理中'), findsOneWidget);
    expect(find.text('已失效'), findsOneWidget);
    expect(find.text('過期自動退'), findsOneWidget);
    expect(find.text('申請退款'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  test('expired and failed wallet refund states stay distinct', () {
    expect(WalletItem.fromJson({'status': 3, 'refund_status': 'requested'}).statusText,
        '退款處理中');
    expect(WalletItem.fromJson({'status': 3, 'refund_status': 'rejected'}).statusText,
        '已失效 · 退款未成功');
    expect(WalletItem.fromJson({'status': 3, 'refund_status': 'refunded'}).statusText,
        '已退款');
    expect(PackageOrder.fromJson({'payment_status': 'paid', 'wallet_status': 3})
        .canRefund, isFalse);
  });
}
