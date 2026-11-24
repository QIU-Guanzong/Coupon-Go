import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/screens/personal_activity.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

class ReviewFixture extends ApiClient {
  final responses = <Completer<Map<String, dynamic>>>[];
  int revision = 0;
  bool active = true;

  @override
  int get sessionRevision => revision;
  @override
  bool get isLoggedIn => active;

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) {
    expect(path, '/reviews/mine');
    expect(query, isNull);
    final response = Completer<Map<String, dynamic>>();
    responses.add(response);
    return response.future;
  }

  void leaveAccount() {
    active = false;
    revision++;
    notifyListeners();
  }
}

void main() {
  test('history is bounded, de-duplicated and does not mix account keys', () async {
    SharedPreferences.setMockInitialValues({});
    final a = BrowsingHistory.keyFor(1)!;
    final b = BrowsingHistory.keyFor(2)!;
    expect(BrowsingHistory.keyFor(null), isNull);
    expect(a, startsWith(publicTrial ? 'coupon_trial_' : 'coupon_account_'));
    for (var id = 1; id <= 22; id++) {
      await BrowsingHistory.remember(a, RecentVisit(VisitKind.coupon, id, 'Offer $id'));
    }
    await BrowsingHistory.remember(a, const RecentVisit(VisitKind.coupon, 12, 'Offer 12'));
    await BrowsingHistory.remember(b, const RecentVisit(VisitKind.shop, 1, 'Other shop'));
    final recent = await BrowsingHistory.read(a);
    expect(recent, hasLength(20));
    expect(recent.first.id, 12);
    expect(recent.where((x) => x.id == 12), hasLength(1));
    expect((await BrowsingHistory.read(b)).single.name, 'Other shop');
  });

  testWidgets('history follows a visited item and hides itself on role change',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    final api = ReviewFixture();
    addTearDown(api.dispose);
    final key = BrowsingHistory.keyFor(1)!;
    final clicked = <int>[];
    await tester.pumpWidget(MaterialApp(home: BrowsingHistoryScreen(
      storageKey: key, api: api, onOpen: (row) async => clicked.add(row.id),
      load: (loadedKey) async {
        expect(loadedKey, key);
        return const [RecentVisit(VisitKind.coupon, 12, 'Tea voucher')];
      },
    )));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Tea voucher'));
    expect(clicked, [12]);
    api.leaveAccount();
    await tester.pumpAndSettle();
    expect(find.text('Tea voucher'), findsNothing);
    expect(find.text('請返回後再開啟。'), findsOneWidget);
  });

  testWidgets('own reviews display actual rows and never adopt prior-session results',
      (tester) async {
    final api = ReviewFixture();
    addTearDown(api.dispose);
    await tester.pumpWidget(MaterialApp(home: MyReviewsScreen(api: api)));
    api.responses.single.complete({'reviews': [
      {'id': 8, 'shop_name': 'Tea House', 'overall': 4, 'content': 'My real review fixture'},
    ]});
    await tester.pumpAndSettle();
    expect(find.text('Tea House'), findsOneWidget);
    expect(find.text('4 / 5 分'), findsOneWidget);
    final refresh = tester.widget<RefreshIndicator>(find.byType(RefreshIndicator));
    final pending = refresh.onRefresh();
    await tester.pump();
    api.leaveAccount();
    api.responses.last.complete({'reviews': [
      {'id': 9, 'shop_name': 'Prior shop', 'overall': 5, 'content': 'Prior review'},
    ]});
    await pending;
    await tester.pumpAndSettle();
    expect(find.text('Prior review'), findsNothing);
    expect(find.text('My real review fixture'), findsNothing);
    expect(find.text('暫時沒有評價。'), findsOneWidget);
  });
}
