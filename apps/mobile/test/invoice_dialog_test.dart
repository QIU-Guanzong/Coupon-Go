import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/admin_v2.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class InvoiceApi extends ApiClient {
  Map<String, dynamic>? submitted;
  bool failList = false;
  final statement = <String, dynamic>{
    'id': 1,
    'shop_id': 1,
    'shop_name': '測試商戶',
    'period': '2026-11',
    'gross_amount': 100.0,
    'commission': 10.0,
    'net_amount': 90.0,
    'status': 'pending',
    'outstanding_net': 90.0,
    'transferred_net': 0.0,
    'invoice_available_amount': 90.0,
    'invoice_issued_amount': 0.0,
    'invoice_credited_amount': 0.0,
    'invoice_net_issued_amount': 0.0,
    'invoice_reserved_amount': 0.0,
    'invoice_excess_amount': 0.0,
    'invoices': [],
    'items': [],
    'transfers': [],
  };

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    if (path == '/merchant/settlements') {
      if (failList) throw ApiException(503, '未能讀取資料');
      return {
        'settlements': [statement],
        'total': 1
      };
    }
    if (path == '/admin/settlements/1') return {'settlement': statement};
    throw ApiException(404, 'missing');
  }

  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    if (path != '/admin/invoices') throw ApiException(404, 'missing');
    submitted = body;
    return {'message': 'ok'};
  }
}

void main() {
  testWidgets('invoice dialog keeps input alive until it closes',
      (tester) async {
    final api = InvoiceApi();
    await tester.pumpWidget(
        MaterialApp(home: SettlementsScreen(api: api, merchant: true)));
    await tester.pumpAndSettle();
    await tester.tap(find.textContaining('測試商戶').first);
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('申請發票'));
    await tester.tap(find.text('申請發票'));
    await tester.pumpAndSettle();
    await tester.enterText(
        find.widgetWithText(TextField, '發票抬頭'), 'TEST COMPANY');
    await tester.enterText(find.widgetWithText(TextField, '稅號（選填）'), '123');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.tap(find.text('提交申請'));
    await tester.pumpAndSettle();
    expect(api.submitted?['title'], 'TEST COMPANY');
    expect(api.submitted?['amount'], 90.0);
    expect(api.submitted?['tax_id'], '123');
    expect(tester.takeException(), isNull);
  });

  testWidgets(
      'merchant statement distinguishes failed load and exports safe CSV',
      (tester) async {
    final api = InvoiceApi()..failList = true;
    api.statement['shop_name'] = '  = shop, "A"';
    addTearDown(api.dispose);
    await tester.pumpWidget(
      MaterialApp(home: SettlementsScreen(api: api, merchant: true)),
    );
    await tester.pumpAndSettle();
    expect(find.text('未能讀取資料'), findsOneWidget);
    expect(find.text('暫時沒有結算單'), findsNothing);
    expect(
        tester
            .widget<IconButton>(find.byWidgetPredicate(
              (widget) => widget is IconButton && widget.tooltip == '匯出本頁 CSV',
            ))
            .onPressed,
        isNull);

    api.failList = false;
    await tester.tap(find.text('重新載入'));
    await tester.pumpAndSettle();
    expect(find.text('商戶淨額'), findsOneWidget);
    await tester.tap(find.byTooltip('匯出本頁 CSV'));
    await tester.pumpAndSettle();
    final csv = tester.widget<SelectableText>(find.byType(SelectableText)).data;
    expect(csv, contains('"\'  = shop, ""A"""'));
  });
}
