import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/merchant_reviews.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'merchant_review_shops_test.dart' as existing;

class ServerReviewsApi extends existing.ReviewShopsApi {
  ServerReviewsApi() : super([existing.first, existing.second]);

  final privateReads = <(String, Map<String, String>)>[];
  int? deniedStatus;
  bool invalidEnvelope = false;
  bool missingPending = false;
  Completer<Map<String, dynamic>>? delayed;
  Completer<Map<String, dynamic>>? delayedPending;

  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, String>? query,
  }) async {
    if (!path.startsWith('/merchant/reviews')) {
      return super.get(path, query: query);
    }
    final args = Map<String, String>.of(query ?? {});
    privateReads.add((path, args));
    final shop = int.tryParse(args['shop_id'] ?? '');
    if (path == '/merchant/reviews/pending-count') {
      if (missingPending) throw ApiException(503, 'Unavailable');
      if (shop == 11 && delayedPending != null) return delayedPending!.future;
      return {
        'shop_id': shop,
        'total': shop == 11 ? 47 : (shop == 22 ? 4 : 51)
      };
    }
    if (deniedStatus != null) throw ApiException(deniedStatus!, 'Forbidden');
    if (shop == 11 && delayed != null) return delayed!.future;
    final page = int.parse(args['page']!);
    final privateStatus = args['status'] == 'disputed';
    return {
      'shop_id': invalidEnvelope ? 666 : shop,
      'total': privateStatus ? 46 : 50,
      'current_page': page,
      'total_pages': 3,
      'reviews': [
        row(shop!,
            '$shop · Page $page · ${privateStatus ? 'Archived dispute' : args['status']}',
            status: privateStatus ? 'disputed' : 'visible'),
        row(333, 'Mismatched owner'),
        row(shop, 'Hidden review', status: 'hidden'),
      ],
    };
  }
}

Map<String, dynamic> row(int shop, String content,
        {String status = 'visible'}) =>
    {
      'id': shop,
      'shop_id': shop,
      'status': status,
      'overall': 2.0,
      'taste': 2,
      'ambience': 2,
      'service': 2,
      'user_name': 'Chan',
      'content': content,
    };

Future<void> screen(WidgetTester tester, Widget home) async {
  tester.view.physicalSize = const Size(390, 844);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final language = AppLanguage()..english = true;
  addTearDown(language.dispose);
  await tester.pumpWidget(LanguageScope(
    language: language,
    child: MaterialApp(theme: CgTheme.light(), home: home),
  ));
  await tester.pumpAndSettle();
}

Future<void> tap(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.tap(finder);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
      'workspace uses authorized pending totals, filters and durable dispute pages',
      (tester) async {
    final api = ServerReviewsApi();
    await screen(tester, MerchantReviewsScreen(api: api));
    expect(api.privateReads.first.$2, {
      'shop_id': '11',
      'page': '1',
      'sort': 'latest',
      'status': 'visible',
      'unreplied': 'true',
    });
    expect(find.text('47 awaiting reply'), findsOneWidget);
    expect(find.text('50 matching reviews'), findsOneWidget);
    expect(find.text('Mismatched owner'), findsNothing);
    expect(find.text('Hidden review'), findsNothing);
    await tap(tester, find.text('Next'));
    expect(find.text('Page 2 of 3'), findsOneWidget);
    expect(find.textContaining('11 · Page 2'), findsOneWidget);
    await tap(tester, find.text('Replied'));
    expect(api.privateReads.where((r) => r.$1 == '/merchant/reviews').last.$2,
        containsPair('unreplied', 'false'));
    expect(find.text('Page 1 of 3'), findsOneWidget);
    await tester.drag(
        find.byType(SingleChildScrollView).first, const Offset(-700, 0));
    await tester.pumpAndSettle();
    await tap(tester, find.text('Low ratings (≤2)'));
    expect(api.privateReads.where((r) => r.$1 == '/merchant/reviews').last.$2,
        containsPair('max_stars', '2'));
    await tap(tester, find.text('Disputed'));
    expect(find.text('11 · Page 1 · Archived dispute'), findsOneWidget);
    expect(find.text('Under dispute'), findsOneWidget);
    expect(find.text('46 matching reviews'), findsOneWidget);
    expect(find.textContaining('while this workspace is open'), findsNothing);
    await tap(tester, find.text('Next'));
    expect(find.text('11 · Page 2 · Archived dispute'), findsOneWidget);
    expect(api.shopReads, isEmpty);
    expect(api.replies, isEmpty);
    expect(api.disputes, isEmpty);
    expect(tester.takeException(), isNull);
  }, skip: publicTrial);

  testWidgets('unavailable count is omitted, not guessed from server page',
      (tester) async {
    final api = ServerReviewsApi()..missingPending = true;
    await screen(tester, MerchantReviewsScreen(api: api));
    expect(find.textContaining('awaiting reply'), findsNothing);
    expect(find.text('50 matching reviews'), findsOneWidget);
  }, skip: publicTrial);

  testWidgets('merchant home gets the actual all-shops badge before entering',
      (tester) async {
    final api = ServerReviewsApi();
    await screen(tester, VendorHomeScreen(api: api));
    expect(find.byTooltip('51 reviews awaiting reply'), findsOneWidget);
    expect(api.privateReads.single.$1, '/merchant/reviews/pending-count');
    expect(api.privateReads.single.$2, isEmpty);
  }, skip: publicTrial);

  testWidgets('401/403 are not legacy fallback; mismatch is never displayed',
      (tester) async {
    for (final status in [401, 403, null]) {
      final api = ServerReviewsApi()
        ..deniedStatus = status
        ..invalidEnvelope = status == null;
      await screen(tester, MerchantReviewsScreen(key: UniqueKey(), api: api));
      expect(
          find.text(status == null
              ? 'Could not load reviews.'
              : 'You cannot manage reviews for this shop.'),
          findsOneWidget);
      expect(api.shopReads, isEmpty);
      expect(api.privateReads, hasLength(1));
      expect(find.textContaining('11 · Page'), findsNothing);
      expect(find.textContaining('47 awaiting reply'), findsNothing);
    }
  }, skip: publicTrial);

  testWidgets('account change clears data and ignores old private receipt',
      (tester) async {
    final api = ServerReviewsApi()..delayed = Completer();
    tester.view.physicalSize = const Size(390, 844);
    addTearDown(tester.view.resetPhysicalSize);
    await tester.pumpWidget(MaterialApp(home: MerchantReviewsScreen(api: api)));
    await tester.pump();
    await tester.pump();
    expect(api.privateReads, hasLength(1));
    api.revision++;
    api.notifyListeners();
    api.delayed!.complete({
      'shop_id': 11,
      'reviews': [row(11, 'Previous account feedback')],
      'current_page': 1,
      'total_pages': 1,
      'total': 1,
    });
    await tester.pumpAndSettle();
    expect(find.text('帳戶已切換，請返回重試。'), findsOneWidget);
    expect(find.text('Previous account feedback'), findsNothing);
    expect(api.privateReads, hasLength(1));
  }, skip: publicTrial);

  testWidgets('switching shops discards the previous shop pending receipt',
      (tester) async {
    final api = ServerReviewsApi()..delayedPending = Completer();
    await screen(tester, MerchantReviewsScreen(api: api));
    expect(find.text('47 awaiting reply'), findsNothing);
    await tap(tester, find.text('Central'));
    await tap(tester, find.text('Mong Kok').last);
    expect(find.text('4 awaiting reply'), findsOneWidget);
    api.delayedPending!.complete({'shop_id': 11, 'total': 47});
    await tester.pumpAndSettle();
    expect(find.text('4 awaiting reply'), findsOneWidget);
    expect(find.text('47 awaiting reply'), findsNothing);
    expect(find.textContaining('11 · Page'), findsNothing);
  }, skip: publicTrial);

  testWidgets('trial keeps pre-existing public read path and cannot write',
      (tester) async {
    final api = ServerReviewsApi();
    await screen(tester, MerchantReviewsScreen(api: api));
    expect(api.privateReads, isEmpty);
    expect(api.shopReads, [(11, 1, 'latest')]);
    expect(find.text('2 awaiting reply on this page'), findsOneWidget);
    expect(find.text('Reply'), findsNothing);
    expect(find.text('Dispute'), findsNothing);
  }, skip: !publicTrial);
}
