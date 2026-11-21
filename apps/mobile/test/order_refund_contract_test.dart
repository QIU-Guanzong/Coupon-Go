import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/account.dart';
import 'package:coupon_go/screens/admin_v2.dart';
import 'package:coupon_go/screens/packages.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class ContractApi extends ApiClient {
  ContractApi({this.refundable = true, this.needsReview = false});
  final bool refundable;
  final bool needsReview;
  int? refundedOrder;

  @override
  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) async {
    if (path == '/get-favorite-coupons-list') {
      return {'items': [
        {'id': 1, 'coupon_name': '套餐券', 'shop_name': '商店', 'status': 1,
         'is_package': true, 'order_id': 13, 'quantity': 1, 'order_quantity': 2,
         'order_refundable': refundable, 'order_wallet_quantity_status': needsReview ? 'needs_review' : 'ok'}
      ], 'total_pages': 1};
    }
    if (path == '/orders/13') {
      return {'order': {'id': 13, 'package_name': '套餐', 'payment_status': 'paid',
        'paid_amount': 100, 'amount': 100, 'quantity': 2, 'refundable': refundable,
        'voucher_redeemed': !refundable}};
    }
    if (path == '/merchant/settlements') return {'settlements': [statement], 'total': 1};
    if (path == '/admin/settlements/1') return {'settlement': statement};
    throw ApiException(503, 'Offline');
  }

  final statement = <String, dynamic>{
    'id': 1, 'shop_id': 1, 'shop_name': '測試商戶', 'period': '2026-11',
    'gross_amount': 100.0, 'commission': 15.0, 'net_amount': 85.0,
    'status': 'pending', 'outstanding_net': 85.0, 'transferred_net': 0.0,
    'commission_rate': 0.1, 'commission_rate_mixed': true,
    'invoice_available_amount': 0.0, 'invoices': [], 'transfers': [],
    'items': [
      {'redemption_id': 5, 'amount': 50.0, 'commission': 6.25, 'commission_rate': 0.125},
      {'redemption_id': 6, 'amount': 50.0, 'commission': 8.75, 'commission_rate': 0.175},
    ],
  };

  @override
  Future<Map<String, dynamic>> refundOrder(int orderId, {String reason = ''}) async {
    refundedOrder = orderId;
    throw ApiException(409, '訂單已有券使用，無法退款');
  }
}

void main() {
  test('refund eligibility requires aggregate server fields', () {
    final base = {'id': 1, 'status': 1, 'is_package': true, 'order_id': 13,
      'quantity': 1, 'order_quantity': 2};
    expect(WalletItem.fromJson({...base, 'order_refundable': false}).canRefund, isFalse);
    expect(WalletItem.fromJson({...base, 'order_refundable': true}).canRefund, isTrue);
    expect(WalletItem.fromJson(base).canRefund, isFalse);
    expect(WalletItem.fromJson({...base, 'order_refundable': true,
      'order_wallet_quantity_status': 'needs_review'}).canRefund, isFalse);
    expect(WalletItem.fromJson({...base, 'order_refundable': true}).orderQuantity, 2);
    expect(WalletItem.fromJson({'quantity': 2}).orderQuantity, 2);
    expect(PackageOrder.fromJson({'payment_status': 'paid'}).canRefund, isFalse);
    expect(PackageOrder.fromJson({'payment_status': 'paid', 'refundable': false,
      'voucher_redeemed': true}).canRefund, isFalse);
    expect(PackageOrder.fromJson({'payment_status': 'paid', 'refundable': true}).canRefund, isTrue);
  });

  testWidgets('wallet warns about the whole order and reports server rejection', (tester) async {
    final api = ContractApi();
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: WalletScreen(api: api, onShowQr: (_) async {}))));
    await tester.pumpAndSettle();
    expect(find.textContaining('套餐券 ×1'), findsOneWidget);
    await tester.tap(find.text('退款'));
    await tester.pumpAndSettle();
    expect(find.textContaining('訂單共 2 份'), findsOneWidget);
    expect(find.textContaining('整筆訂單及其所有券'), findsOneWidget);
    await tester.tap(find.text('確認退款'));
    await tester.pumpAndSettle();
    expect(api.refundedOrder, 13);
    expect(find.text('訂單已有券使用，無法退款'), findsOneWidget);
  });

  testWidgets('partial use and abnormal historical quantity do not permit refund', (tester) async {
    final api = ContractApi(refundable: false, needsReview: true);
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: WalletScreen(api: api, onShowQr: (_) async {}))));
    await tester.pumpAndSettle();
    expect(find.text('退款'), findsNothing);
    expect(find.textContaining('券資料待核對'), findsOneWidget);
    expect(tester.widget<ElevatedButton>(find.widgetWithText(ElevatedButton, '出示')).onPressed, isNull);
    await tester.pumpWidget(MaterialApp(home: OrderDetailScreen(api: api, orderId: 13)));
    await tester.pumpAndSettle();
    expect(find.text('申請退款'), findsNothing);
  });

  testWidgets('order detail confirms full-order refund', (tester) async {
    await tester.pumpWidget(MaterialApp(home: OrderDetailScreen(api: ContractApi(), orderId: 13)));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('申請退款'));
    await tester.tap(find.text('申請退款'));
    await tester.pumpAndSettle();
    expect(find.textContaining('整筆訂單及其所有券'), findsOneWidget);
  });

  testWidgets('settlement displays each rate when rates differ', (tester) async {
    await tester.pumpWidget(MaterialApp(home: SettlementsScreen(api: ContractApi(), merchant: true)));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('測試商戶').first);
    await tester.pumpAndSettle();
    expect(find.text('此結算單包含不同佣金率，詳見各筆核銷。'), findsOneWidget);
    await tester.ensureVisible(find.textContaining('佣金率 12.5%'));
    expect(find.textContaining('佣金率 12.5%'), findsOneWidget);
    expect(find.textContaining('佣金率 17.5%'), findsOneWidget);
  });
}
