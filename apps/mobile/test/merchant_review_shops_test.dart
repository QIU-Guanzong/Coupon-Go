import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/merchant_reviews.dart';
import 'package:coupon_go/screens/reviews_admin.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const entry = Key('vendor-review-shops');
const first = {'id': 11, 'name': 'Central', 'is_owner': true};
const second = {'id': 22, 'name': 'Mong Kok', 'is_owner': true};
const staff = {
  'id': 33,
  'name': 'Staff branch',
  'is_owner': false,
  'can_manage_coupons': true,
};

Map<String, dynamic> _review(int shop, int id,
        {double stars = 4.3, String reply = '', String status = 'visible'}) =>
    {
      'id': id,
      'shop_id': shop,
      'overall': stars,
      'content': 'Review $id for shop $shop',
      'taste': 4,
      'ambience': 4,
      'service': 5,
      'status': status,
      'reply_text': reply,
      if (reply.isNotEmpty) 'replied_at': '2026-11-25T12:00:00Z',
      'created_at': '2026-11-24T11:00:00',
      'counts_toward_rating': true,
    };

class ReviewShopsApi extends ApiClient {
  ReviewShopsApi(this.shops);

  final List<Map<String, dynamic>> shops;
  final shopReads = <(int, int, String)>[];
  final replies = <(int, String)>[];
  final disputes = <(int, String)>[];
  Completer<Map<String, dynamic>>? delayFirstPage;
  ApiException? replyError;
  bool wrongReceipt = false;
  bool includeNonPublicReceipt = false;
  int revision = 0;

  @override
  int get sessionRevision => revision;

  void switchAccount() {
    revision++;
    notifyListeners();
  }

  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, String>? query,
  }) async {
    if (path == '/merchant/shops') return {'shops': shops};
    if (path == '/merchant/reviews' ||
        path == '/merchant/reviews/pending-count') {
      throw ApiException(404, 'Not Found');
    }
    if (path == '/get-vendor-coupon-list') return {'coupons': <dynamic>[]};
    if (path == '/get-vendor-usage-data-new') return {};
    throw StateError('Unexpected GET: $path');
  }

  Map<String, dynamic> page(int shop, int page) => {
        'current_page': page,
        'total_pages': 2,
        'total': 24,
        'reviews': [
          _review(shop, shop * 10 + page),
          if (includeNonPublicReceipt) _review(shop, 999, status: 'disputed'),
          if (page == 1) _review(shop, shop * 10 + 4, stars: 2),
          if (page == 1)
            _review(shop, shop * 10 + 5, stars: 4, reply: 'Earlier reply'),
        ],
      };

  @override
  Future<Map<String, dynamic>> shopReviews(
    int shopId, {
    int page = 1,
    bool hasImages = false,
    String sort = 'latest',
  }) async {
    shopReads.add((shopId, page, sort));
    if (shopId == 11 && page == 1 && delayFirstPage != null) {
      return delayFirstPage!.future;
    }
    return this.page(shopId, page);
  }

  @override
  Future<Map<String, dynamic>> shopRating(int shopId) async =>
      {'overall': 4.2, 'review_count': 24};

  @override
  Future<Map<String, dynamic>> replyReview(int reviewId, String reply) async {
    replies.add((reviewId, reply));
    if (replyError != null) throw replyError!;
    return {
      'review':
          _review(wrongReceipt ? 77 : reviewId ~/ 10, reviewId, reply: reply),
    };
  }

  @override
  Future<Map<String, dynamic>> disputeReview(
      int reviewId, String reason) async {
    disputes.add((reviewId, reason));
    return {'review': _review(reviewId ~/ 10, reviewId, status: 'disputed')};
  }
}

Future<void> _show(
  WidgetTester tester,
  Widget home, {
  bool english = true,
  double scale = 1,
}) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(390, 844);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final language = AppLanguage()..english = english;
  addTearDown(language.dispose);
  await tester.pumpWidget(LanguageScope(
    language: language,
    child: MaterialApp(
      theme: CgTheme.light(),
      builder: (context, child) => MediaQuery(
        data: MediaQuery.of(context)
            .copyWith(textScaler: TextScaler.linear(scale)),
        child: child!,
      ),
      home: home,
    ),
  ));
  await tester.pumpAndSettle();
}

Future<void> _tap(WidgetTester tester, Finder finder) async {
  final actual = finder.first;
  await tester.ensureVisible(actual);
  await tester.tap(actual);
  await tester.pumpAndSettle();
}

Finder _card(int id) => find.byKey(ValueKey('merchant-review-$id'));

Future<void> showReviewHome(WidgetTester tester, ReviewShopsApi api) =>
    _show(tester, VendorHomeScreen(api: api));

Future<void> openReviewCenter(WidgetTester tester) =>
    _tap(tester, find.byKey(entry));

void main() {
  testWidgets('vendor home only enters authorized stores; switch in one screen',
      (tester) async {
    final api = ReviewShopsApi([first, second, staff, second]);
    await _show(tester, VendorHomeScreen(api: api));
    await _tap(tester, find.byKey(entry));
    expect(find.byType(MerchantReviewsScreen), findsOneWidget);
    expect(find.text('Manage reviews'), findsOneWidget);
    expect(find.text('Review 111 for shop 11'), findsOneWidget);
    expect(find.text('Staff branch'), findsNothing);
    await _tap(tester, find.text('Central'));
    expect(find.text('Staff branch'), findsNothing);
    await _tap(tester, find.text('Mong Kok').last);
    expect(find.text('Review 221 for shop 22'), findsOneWidget);
    expect(find.text('Review 111 for shop 11'), findsNothing);
    expect(api.shopReads, [(11, 1, 'latest'), (22, 1, 'latest')]);
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(find.byType(Badge), findsOneWidget);
    expect(find.byTooltip('4 awaiting reply on loaded first review pages'),
        findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('membership alone does not make the entry or deep route eligible',
      (tester) async {
    final api = ReviewShopsApi([staff]);
    await _show(tester, VendorHomeScreen(api: api));
    expect(find.byKey(entry), findsNothing);
    expect(api.shopReads, isEmpty);
    await tester.pumpWidget(MaterialApp(home: MerchantReviewsScreen(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('沒有可管理的門店。'), findsOneWidget);
    expect(api.shopReads, isEmpty);
  });

  testWidgets(
      'admin roster is available, title localizes and expired view clears',
      (tester) async {
    final api = ReviewShopsApi([
      staff,
      {'id': -1, 'name': 'Invalid'}
    ]);
    await _show(tester, VendorHomeScreen(api: api, showAdmin: true),
        english: false);
    await _tap(tester, find.byKey(entry));
    expect(find.text('評價管理'), findsOneWidget);
    expect(find.text('Staff branch'), findsOneWidget);
    expect(find.text('Invalid'), findsNothing);
    expect(api.shopReads, [(33, 1, 'latest')]);
    api.switchAccount();
    await tester.pumpAndSettle();
    expect(find.text('Staff branch'), findsNothing);
    expect(find.text('帳戶已切換，請返回重試。'), findsOneWidget);
  });

  for (final english in [false, true]) {
    testWidgets('390px actual page, honest pending, and filters $english',
        (tester) async {
      final api = ReviewShopsApi([first]);
      await _show(tester, MerchantReviewsScreen(api: api),
          english: english, scale: 1.3);
      expect(find.text(english ? '2 awaiting reply on this page' : '本頁 2 條待回覆'),
          findsOneWidget);
      expect(_card(111), findsOneWidget);
      expect(_card(115), findsNothing);
      await _tap(tester, find.text(english ? 'Replied' : '已回覆'));
      expect(_card(115), findsOneWidget);
      expect(_card(111), findsNothing);
      await _tap(tester, find.text(english ? 'Low ratings (≤2)' : '差評（≤2）'));
      expect(_card(114), findsOneWidget);
      expect(_card(111), findsNothing);
      await _tap(tester, find.text(english ? 'Next' : '下一頁'));
      expect(api.shopReads.last, (11, 2, 'latest'));
      expect(
          find.text(
              english ? 'No matching reviews on this page.' : '本頁沒有符合條件的評價。'),
          findsOneWidget);
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets('old shop response cannot repopulate the next shop list',
      (tester) async {
    final api = ReviewShopsApi([first, second])
      ..delayFirstPage = Completer<Map<String, dynamic>>();
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(390, 844);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(home: MerchantReviewsScreen(api: api)));
    await tester.pump();
    await tester.tap(find.text('Central'));
    await tester.pump(const Duration(milliseconds: 400));
    await _tap(tester, find.text('Mong Kok').last);
    api.delayFirstPage!.complete(api.page(11, 1));
    await tester.pumpAndSettle();
    expect(_card(221), findsOneWidget);
    expect(_card(111), findsNothing);
  });

  testWidgets(
      'successful reply replaces this row without reload; edit survives failure',
      (tester) async {
    final api = ReviewShopsApi([first]);
    await _show(tester, MerchantReviewsScreen(api: api));
    await _tap(
        tester, find.descendant(of: _card(111), matching: find.text('Reply')));
    await tester.enterText(find.byType(TextField), 'First answer');
    await tester.pump();
    await _tap(tester, find.text('Send reply'));
    expect(api.replies, [(111, 'First answer')]);
    expect(api.shopReads, [(11, 1, 'latest')]);
    expect(_card(111), findsNothing);
    await _tap(tester, find.text('Replied'));
    expect(find.text('First answer'), findsOneWidget);
    await _tap(tester,
        find.descendant(of: _card(111), matching: find.text('Edit reply')));
    expect(tester.widget<TextField>(find.byType(TextField)).controller?.text,
        'First answer');
    await tester.enterText(find.byType(TextField), 'Edited answer');
    await tester.pump();
    api.replyError = ApiException(503, 'Please retry later');
    await _tap(tester, find.text('Send reply'));
    expect(find.text('Please retry later'), findsOneWidget);
    expect(tester.widget<TextField>(find.byType(TextField)).controller?.text,
        'Edited answer');
    api.replyError = null;
    await _tap(tester, find.text('Send reply'));
    expect(find.text('Edited answer'), findsOneWidget);
    expect(api.shopReads, [(11, 1, 'latest')]);
  }, skip: publicTrial);

  testWidgets(
      'successful dispute receipt exits public filters and shows disabled dispute',
      (tester) async {
    final api = ReviewShopsApi([first]);
    await _show(tester, MerchantReviewsScreen(api: api));
    await _tap(tester,
        find.descendant(of: _card(111), matching: find.text('Dispute')));
    expect(
        tester
            .widget<FilledButton>(
                find.widgetWithText(FilledButton, 'Submit dispute'))
            .onPressed,
        isNull);
    await tester.enterText(
        find.byType(TextField), 'Please verify the redemption');
    await tester.pump();
    await _tap(tester, find.text('Submit dispute'));
    expect(api.disputes, [(111, 'Please verify the redemption')]);
    expect(_card(111), findsNothing);
    await tester.drag(
        find.byType(SingleChildScrollView).first, const Offset(-540, 0));
    await tester.pumpAndSettle();
    await _tap(tester, find.text('Disputed'));
    expect(
        find.text(
            'This view shows disputes submitted while this workspace is open.'),
        findsOneWidget);
    expect(find.text('Under dispute'), findsOneWidget);
    expect(
        tester
            .widget<TextButton>(find.widgetWithText(TextButton, 'Dispute'))
            .onPressed,
        isNull);
    expect(api.disputes, hasLength(1));
  }, skip: publicTrial);

  testWidgets(
      'wrong-store reply receipt does not show success or remove original',
      (tester) async {
    final api = ReviewShopsApi([first])..wrongReceipt = true;
    await _show(tester, MerchantReviewsScreen(api: api));
    await _tap(
        tester, find.descendant(of: _card(111), matching: find.text('Reply')));
    await tester.enterText(find.byType(TextField), 'Reply');
    await tester.pump();
    await _tap(tester, find.text('Send reply'));
    expect(
        find.text('Could not confirm the result. Try again.'), findsOneWidget);
    expect(find.text('Reply sent'), findsNothing);
  }, skip: publicTrial);

  testWidgets('session change closes write affordances and removes old draft',
      (tester) async {
    final api = ReviewShopsApi([first]);
    await _show(tester, MerchantReviewsScreen(api: api));
    await _tap(
        tester, find.descendant(of: _card(111), matching: find.text('Reply')));
    await tester.enterText(find.byType(TextField), 'Old session text');
    api.switchAccount();
    await tester.pumpAndSettle();
    expect(find.byType(TextField), findsNothing);
    expect(find.text('Send reply'), findsNothing);
    await _tap(tester, find.text('Close'));
    expect(_card(111), findsNothing);
    expect(api.replies, isEmpty);
  }, skip: publicTrial);

  testWidgets('isolated review surface is read only', (tester) async {
    final api = ReviewShopsApi([first]);
    await _show(tester, MerchantReviewsScreen(api: api));
    expect(_card(111), findsOneWidget);
    expect(find.text('Reply'), findsNothing);
    expect(find.text('Dispute'), findsNothing);
  }, skip: !publicTrial);

  test('review HK redemption calendar date does not use the viewer clock', () {
    expect(Review.fromJson({'redeemed_on': '2026-11-12'}).redeemedOn,
        '2026-11-12');
    expect(Review.fromJson({'redeemed_on': '2026-02-30'}).redeemedOn, isNull);
    expect(
        Review.fromJson({'redeemed_on': '2026-11-12T00:30:00+08:00'})
            .redeemedOn,
        isNull);
    expect(
        Review.fromJson({'redeemed_at': '2026-11-11T16:30:00Z'}).redeemedOn,
        isNull);
  });

  testWidgets('customer sees HK date and label as provided by review API',
      (tester) async {
    final api = _CalendarReviewApi();
    await _show(
        tester,
        ShopReviewsScreen(
            api: api, shop: Shop.fromJson({'id': 11, 'shop_name': 'Central'})));
    await tester.scrollUntilVisible(
      find.text('Redeemed · Autumn offer · 2026-11-12'),
      180,
    );
    expect(find.text('Redeemed · Autumn offer · 2026-11-12'), findsOneWidget);
    expect(find.textContaining('16:30'), findsNothing);
  });

  testWidgets('customer sees server reply date, not invented metadata',
      (tester) async {
    final api = ReviewShopsApi([first])..includeNonPublicReceipt = true;
    await _show(
        tester,
        ShopReviewsScreen(
            api: api, shop: Shop.fromJson({'id': 11, 'name': 'Central'})));
    await tester.scrollUntilVisible(
      find.byKey(const ValueKey('customer-reply-115')),
      180,
    );
    expect(find.text('Merchant reply · 2026-11-25'), findsOneWidget);
    expect(find.text('Earlier reply'), findsOneWidget);
    expect(find.text('Review 999 for shop 11'), findsNothing);
    expect(find.textContaining('Redeemed ·'), findsNothing);
    expect(find.textContaining('(edited)'), findsNothing);
    expect(tester.takeException(), isNull);
  });
}

class _CalendarReviewApi extends ReviewShopsApi {
  _CalendarReviewApi() : super([first]);

  @override
  Map<String, dynamic> page(int shop, int page) => {
        ...super.page(shop, page),
        'reviews': [
          {
            ..._review(shop, 101),
            'coupon_name': 'Autumn offer',
            'redeemed_on': '2026-11-12',
          },
        ],
      };
}
