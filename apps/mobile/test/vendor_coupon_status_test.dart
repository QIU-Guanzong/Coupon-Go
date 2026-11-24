import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class CouponStatusApi extends ApiClient {
  final requests = <Map<String, String>?>[];
  final pending = <String, Completer<Map<String, dynamic>>>{};
  bool canManage = true;

  static Map<String, dynamic> row(int id, String name, String status) => {
        'id': id,
        'shop_id': 8,
        'shop_name': 'Merchant',
        'coupon_name': name,
        'status': status,
        'remaining': 3,
        'used_count': 1,
      };

  static Map<String, dynamic> coupons(Iterable<Map<String, dynamic>> rows) =>
      {'coupons': rows.toList()};

  @override
  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) async {
    if (path == '/merchant/shops') {
      return {'shops': [
        {'id': 8, 'name': 'Merchant', 'is_owner': false, 'can_manage_coupons': canManage},
      ]};
    }
    if (path != '/get-vendor-coupon-list') throw StateError('Unexpected GET $path');
    requests.add(query);
    final status = query?['status'];
    if (status == null) {
      return coupons([
        row(1, 'Listed lunch', 'active'),
        row(2, 'Rejected tea', 'rejected'),
        row(3, 'Tomorrow offer', 'scheduled'),
      ]);
    }
    if (pending.containsKey(status)) return pending[status]!.future;
    if (status == 'pending_review') {
      return coupons([row(4, 'New review result', 'pending_review')]);
    }
    return coupons([]);
  }
}

const filterKey = ValueKey('vendor-coupon-status-filter');
Finder get filter => find.byKey(filterKey);

Future<void> showHome(WidgetTester tester, CouponStatusApi api,
    {required bool english}) async {
  await tester.binding.setSurfaceSize(const Size(390, 844));
  addTearDown(() => tester.binding.setSurfaceSize(null));
  final language = AppLanguage()..english = english;
  addTearDown(language.dispose);
  addTearDown(api.dispose);
  await tester.pumpWidget(LanguageScope(
    language: language,
    child: MaterialApp(home: VendorHomeScreen(api: api)),
  ));
  await tester.pumpAndSettle();
}

String statusText(WidgetTester tester, int id) =>
    tester.widget<Text>(find.byKey(ValueKey('vendor-coupon-status-$id'))).data!;

void choose(WidgetTester tester, String status) =>
    tester.widget<DropdownButton<String>>(filter).onChanged!(status);

void main() {
  testWidgets('shows bilingual backend statuses and sends exact pending filter',
      (tester) async {
    final api = CouponStatusApi();
    await showHome(tester, api, english: true);
    expect(api.requests, [null]);
    expect(statusText(tester, 1), 'Listed');
    expect(statusText(tester, 2), 'Rejected');
    expect(statusText(tester, 3), 'Scheduled');

    await tester.tap(filter);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Pending review').last);
    await tester.pumpAndSettle();
    expect(api.requests, [null, {'status': 'pending_review'}]);
    // A new coupon returned only by the server query must show up.
    expect(find.text('New review result'), findsOneWidget);
    expect(statusText(tester, 4), 'Pending review');
    expect(find.text('Listed lunch'), findsNothing);
    expect(find.text('Rejected tea'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('late status response cannot replace a newer filter; reset shows all',
      (tester) async {
    final api = CouponStatusApi();
    final outstanding = Completer<Map<String, dynamic>>();
    api.pending['pending_review'] = outstanding;
    await showHome(tester, api, english: false);
    expect(statusText(tester, 2), '已拒絕');

    choose(tester, 'pending_review');
    await tester.pump();
    expect(find.byType(LinearProgressIndicator), findsOneWidget);
    choose(tester, 'paused');
    await tester.pumpAndSettle();
    expect(find.text('這個狀態沒有優惠券。'), findsOneWidget);
    outstanding.complete(CouponStatusApi.coupons([
      CouponStatusApi.row(88, 'Late old filter result', 'pending_review'),
    ]));
    await tester.pumpAndSettle();
    expect(find.text('Late old filter result'), findsNothing);
    expect(find.text('這個狀態沒有優惠券。'), findsOneWidget);
    choose(tester, '');
    await tester.pumpAndSettle();
    expect(find.text('Listed lunch'), findsOneWidget);
    expect(api.requests, [null, {'status': 'pending_review'}, {'status': 'paused'}]);
  });

  testWidgets('role switch clears filter and late prior-account data cannot render',
      (tester) async {
    final api = CouponStatusApi();
    final outstanding = Completer<Map<String, dynamic>>();
    api.pending['pending_review'] = outstanding;
    await showHome(tester, api, english: true);

    choose(tester, 'pending_review');
    await tester.pump();
    api.canManage = false;
    api.activateTrialToken(null);
    outstanding.complete(CouponStatusApi.coupons([
      CouponStatusApi.row(88, 'Previous merchant coupon', 'pending_review'),
    ]));
    await tester.pumpAndSettle();
    expect(filter, findsNothing);
    expect(find.text('Listed lunch'), findsNothing);
    expect(find.text('Previous merchant coupon'), findsNothing);
    expect(api.requests, [null, {'status': 'pending_review'}]);
  });

  testWidgets('filter error stays in the coupon section and can be retried',
      (tester) async {
    final api = CouponStatusApi();
    final first = Completer<Map<String, dynamic>>();
    api.pending['pending_review'] = first;
    await showHome(tester, api, english: true);
    choose(tester, 'pending_review');
    await tester.pump();
    first.completeError(StateError('offline fixture'));
    await tester.pumpAndSettle();

    expect(find.text('Could not load coupons.'), findsOneWidget);
    expect(find.text('No coupons with this status.'), findsNothing);
    expect(find.text('Listed lunch'), findsNothing);
    api.pending.remove('pending_review');
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(find.text('New review result'), findsOneWidget);
    expect(api.requests, [null, {'status': 'pending_review'}, {'status': 'pending_review'}]);
  });
}
