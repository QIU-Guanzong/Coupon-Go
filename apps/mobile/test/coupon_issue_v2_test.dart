import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _priceKey = ValueKey('issue-sale-price');
const _giftKey = ValueKey('issue-gift');
const _startKey = ValueKey('issue-valid-from');

class _IssueApi extends ApiClient {
  _IssueApi() : super(baseUrl: 'https://fixture.invalid');
  final previews = <Map<String, dynamic>>[];
  final creates = <Map<String, dynamic>>[];
  Completer<void>? previewGate;

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    switch (path) {
      case '/merchant/shops':
        return {
          'shops': [
            {'id': 7, 'name': '測試商店', 'can_manage_coupons': true},
          ],
        };
      case '/merchant/shops/7/branches':
        return {
          'branches': [
            {'id': 21, 'name': '測試分店', 'address': '測試路'},
          ],
        };
      default:
        throw StateError('Unexpected GET: $path');
    }
  }

  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    throw StateError('Unexpected POST: $path');
  }

  @override
  Future<Map<String, dynamic>> couponPreview(Map<String, dynamic> body) async {
    previews.add(Map.of(body));
    final gate = previewGate;
    if (gate != null) await gate.future;
    return {
      'coupon_name': body['coupon_name'],
      'amount_text': body['coupon_type'] == 'Gift' ? '送${body['gift']}' : 'Price',
      'total': body['total'],
      'shop_name': '測試商店',
    };
  }

  @override
  Future<Map<String, dynamic>> couponCreate(Map<String, dynamic> body) async {
    creates.add(Map.of(body));
    return {'message': '已提交審核', 'coupon': {'status': 'pending_review'}};
  }
}

Future<void> _open(WidgetTester tester, _IssueApi api,
    {DateTime Function()? now}) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(800, 1300);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(MaterialApp(
    home: Builder(builder: (context) => Scaffold(
      body: TextButton(
        onPressed: () => Navigator.of(context).push(MaterialPageRoute<void>(
          builder: (_) => IssueCouponScreen(api: api, now: now),
        )),
        child: const Text('open'),
      ),
    )),
  ));
  await tester.tap(find.text('open'));
  await tester.pumpAndSettle();
  await _fill(tester, find.byType(TextField).first, '測試優惠券');
}

Future<void> _fill(WidgetTester tester, Finder finder, String text) async {
  await tester.enterText(finder, text);
  FocusManager.instance.primaryFocus?.unfocus();
  await tester.pumpAndSettle();
}

Future<void> _chooseType(WidgetTester tester, String name) async {
  final selector = find.byWidgetPredicate(
    (widget) => widget is DropdownButtonFormField<String>,
  );
  await tester.tap(selector);
  await tester.pumpAndSettle();
  await tester.tap(find.text(name).last);
  await tester.pumpAndSettle();
}

Finder get _reviewButton =>
    find.widgetWithText(FilledButton, '檢查並提交');

Future<void> _review(WidgetTester tester) async {
  if (_reviewButton.evaluate().isEmpty) {
    await tester.scrollUntilVisible(_reviewButton, 260,
        scrollable: find.byType(Scrollable).first);
  }
  await tester.ensureVisible(_reviewButton);
  await tester.tap(_reviewButton);
  await tester.pumpAndSettle();
}

Future<void> _confirm(WidgetTester tester) async {
  await tester.tap(find.text('提交審核'));
  await tester.pumpAndSettle();
}

Future<void> _start(WidgetTester tester, DateTime day, TimeOfDay time) async {
  await tester.tap(find.byKey(_startKey));
  await tester.pumpAndSettle();
  final datePicker = find.byType(DatePickerDialog);
  expect(datePicker, findsOneWidget);
  // Supply just the picker's selected values, as its confirm buttons would.
  Navigator.of(tester.element(datePicker)).pop(day);
  await tester.pumpAndSettle();
  final timePicker = find.byType(TimePickerDialog);
  expect(timePicker, findsOneWidget);
  Navigator.of(tester.element(timePicker)).pop(time);
  await tester.pumpAndSettle();
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('Price blocks invalid sale prices; preview and create use the exact paid draft',
      (tester) async {
    final api = _IssueApi();
    await _open(tester, api);
    await _chooseType(tester, '付費優惠券');
    final price = find.byKey(_priceKey);
    for (final invalid in ['', '0.00', '-2', '1.234', 'Infinity', '1e2', '100000000.00']) {
      await _fill(tester, price, invalid);
      await _review(tester);
      expect(find.textContaining('請輸入有效售價'), findsOneWidget, reason: invalid);
      expect(api.previews, isEmpty, reason: invalid);
      expect(api.creates, isEmpty, reason: invalid);
    }
    await _fill(tester, price, '128.50');
    final gate = Completer<void>();
    api.previewGate = gate;
    await _review(tester);
    expect(api.previews.single['coupon_type'], 'Price');
    expect(api.previews.single['sale_price'], 128.50);
    expect(api.previews.single.containsKey('discount_price'), isFalse);
    expect(api.previews.single.containsKey('gift'), isFalse);
    // The form remains visible while preview loads. A late field edit must
    // not silently alter the amount the merchant sees or the confirmed body.
    await _fill(tester, price, '999');
    gate.complete();
    await tester.pumpAndSettle();
    expect(find.textContaining('128.50'), findsOneWidget);
    expect(api.creates, isEmpty);
    await _confirm(tester);
    expect(api.creates.single, api.previews.single);
    expect(find.text('open'), findsOneWidget);
  });

  testWidgets('Gift keeps the entered gift and does not inherit stale paid price',
      (tester) async {
    final api = _IssueApi();
    await _open(tester, api);
    await _chooseType(tester, '付費優惠券');
    await _fill(tester, find.byKey(_priceKey), '88');
    await _chooseType(tester, '贈品優惠券');
    await _fill(tester, find.byKey(_giftKey), ' 咖啡一杯 ');
    await _review(tester);
    expect(api.previews.single['coupon_type'], 'Gift');
    expect(api.previews.single['gift'], '咖啡一杯');
    expect(api.previews.single.containsKey('sale_price'), isFalse);
    expect(find.textContaining('送咖啡一杯'), findsOneWidget);
    await _confirm(tester);
    expect(api.creates.single, api.previews.single);
  });

  testWidgets('HK start converts to UTC, checks expiry first and survives preview untouched',
      (tester) async {
    final api = _IssueApi();
    final now = DateTime.utc(2026, 12, 1, 17);
    await _open(tester, api, now: () => now);
    final hongKongToday = now.add(const Duration(hours: 8));
    DateTime dayOffset(int days) =>
        DateTime(hongKongToday.year, hongKongToday.month, hongKongToday.day + days);
    const localTime = TimeOfDay(hour: 1, minute: 15); // UTC previous day 17:15.
    await _start(tester, dayOffset(31), localTime);
    await _review(tester);
    expect(api.previews, isEmpty);
    expect(api.creates, isEmpty);
    expect(find.text('到期日期必須晚於生效時間'), findsOneWidget);

    final starts = dayOffset(2);
    await _start(tester, starts, localTime);
    await _review(tester);
    final expected = DateTime.utc(starts.year, starts.month, starts.day, 1, 15)
        .subtract(const Duration(hours: 8));
    final sent = api.previews.single;
    expect(sent['valid_from'], expected.toIso8601String());
    expect(DateTime.parse(sent['valid_to'] as String).isAfter(expected), isTrue);
    expect(find.textContaining('01:15（香港時間）'), findsOneWidget);
    expect(api.creates, isEmpty);
    await _confirm(tester);
    expect(api.creates.single, sent);
  });

  testWidgets('a planned start reached during review cannot become an immediate create',
      (tester) async {
    final api = _IssueApi();
    var now = DateTime.utc(2026, 12, 1, 17);
    await _open(tester, api, now: () => now);
    await _start(tester, DateTime(2026, 12, 2), const TimeOfDay(hour: 2, minute: 0));
    await _review(tester);
    expect(api.previews.single['valid_from'], '2026-12-01T18:00:00.000Z');
    now = DateTime.utc(2026, 12, 1, 18);
    await _confirm(tester);
    expect(api.creates, isEmpty);
    expect(find.text('定時上架時間已過，請重新選擇'), findsOneWidget);
  });
}
