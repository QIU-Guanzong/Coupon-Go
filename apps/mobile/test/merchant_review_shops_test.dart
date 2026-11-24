import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/reviews_admin.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const entry = Key('vendor-review-shops');

class ReviewShopsApi extends ApiClient {
  ReviewShopsApi(this.shops);

  final List<Map<String, dynamic>> shops;
  final shopReads = <int>[];
  final replies = <int>[];
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
    if (path == '/get-vendor-coupon-list') return {'coupons': <dynamic>[]};
    if (path == '/get-vendor-usage-data-new') return {};
    throw StateError('Unexpected GET: $path');
  }

  @override
  Future<Map<String, dynamic>> shopReviews(
    int shopId, {
    int page = 1,
    bool hasImages = false,
    String sort = 'latest',
  }) async {
    shopReads.add(shopId);
    return {
      'reviews': [
        {
          'id': 100 + shopId,
          'shop_id': shopId,
          'overall': 1,
          'content': 'Review for shop $shopId',
        },
      ],
    };
  }

  @override
  Future<Map<String, dynamic>> replyReview(int reviewId, String reply) async {
    replies.add(reviewId);
    return {};
  }
}

const first = {'id': 11, 'name': 'Central', 'is_owner': true};
const second = {'id': 22, 'name': 'Mong Kok', 'is_owner': true};
const staff = {
  'id': 33,
  'name': 'Staff branch',
  'is_owner': false,
  'can_manage_coupons': true,
};

Future<void> showHome(
  WidgetTester tester,
  ReviewShopsApi api, {
  bool admin = false,
  bool english = true,
}) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(390, 844);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final language = AppLanguage()..english = english;
  addTearDown(language.dispose);
  addTearDown(api.dispose);
  await tester.pumpWidget(LanguageScope(
    language: language,
    child: MaterialApp(home: VendorHomeScreen(api: api, showAdmin: admin)),
  ));
  await tester.pumpAndSettle();
}

Future<void> openPicker(WidgetTester tester) async {
  await tester.ensureVisible(find.byKey(entry));
  await tester.tap(find.byKey(entry));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('merchant can choose either owned shop without any coupons',
      (tester) async {
    final api = ReviewShopsApi([first, second, staff, second]);
    await showHome(tester, api);
    await openPicker(tester);
    expect(find.text('Choose a shop'), findsOneWidget);
    expect(find.text('Staff branch'), findsNothing);
    expect(find.text('Mong Kok'), findsOneWidget);
    expect(api.shopReads, isEmpty);

    await tester.tap(find.text('Mong Kok'));
    await tester.pumpAndSettle();
    expect(find.text('Mong Kok · Review replies'), findsOneWidget);
    expect(find.text('Review for shop 22'), findsOneWidget);
    expect(api.shopReads, [22]);

    await tester.pageBack();
    await tester.pumpAndSettle();
    await tester.tap(find.text('Central'));
    await tester.pumpAndSettle();
    expect(find.text('Review for shop 11'), findsOneWidget);
    expect(find.text('Review for shop 22'), findsNothing);
    expect(api.shopReads, [22, 11]);
    expect(api.replies, isEmpty);
  });

  testWidgets('membership alone does not make the reply entry available',
      (tester) async {
    final api = ReviewShopsApi([staff]);
    await showHome(tester, api);
    expect(find.text('Reply to low-rated reviews'), findsNothing);
    expect(api.shopReads, isEmpty);
    expect(api.replies, isEmpty);
  });

  testWidgets('existing admin grant matches server-owned scope and localizes',
      (tester) async {
    final api = ReviewShopsApi([staff, {'id': -1, 'name': 'Invalid'}]);
    await showHome(tester, api, admin: true, english: false);
    await openPicker(tester);
    expect(find.text('選擇門店'), findsOneWidget);
    expect(find.text('Invalid'), findsNothing);
    await tester.tap(find.text('Staff branch'));
    await tester.pumpAndSettle();
    expect(find.text('Staff branch · 差評回覆'), findsOneWidget);
    expect(api.shopReads, [33]);
  });

  testWidgets('account switch removes the previous account shop choices',
      (tester) async {
    final api = ReviewShopsApi([first, second]);
    await showHome(tester, api);
    await openPicker(tester);
    api.switchAccount();
    await tester.pumpAndSettle();
    expect(find.text('Mong Kok'), findsNothing);
    expect(find.text('The account changed. Go back and try again.'),
        findsOneWidget);
    expect(api.shopReads, isEmpty);
  });

  testWidgets('trial reviews explain that replies are unavailable',
      (tester) async {
    final api = ReviewShopsApi([first]);
    await showHome(tester, api);
    await openPicker(tester);
    await tester.tap(find.text('Central'));
    await tester.pumpAndSettle();
    expect(find.text('Review replies are unavailable in the trial.'), findsOneWidget);
    final button = tester.widget<ElevatedButton>(find.widgetWithText(ElevatedButton, 'Reply'));
    expect(button.onPressed, isNull);
    expect(api.replies, isEmpty);
  }, skip: !const bool.fromEnvironment('PUBLIC_TRIAL'));

  testWidgets('reply targets the chosen shop review and reloads only that shop',
      (tester) async {
    final api = ReviewShopsApi([first, second]);
    await showHome(tester, api);
    await openPicker(tester);
    await tester.tap(find.text('Mong Kok'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Reply'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'Synthetic test reply');
    await tester.tap(find.text('Send reply'));
    await tester.pumpAndSettle();
    expect(api.replies, [122]);
    expect(api.shopReads, [22, 22]);
  }, skip: const bool.fromEnvironment('PUBLIC_TRIAL'));

  testWidgets('an open reply dialog cannot send using the next account',
      (tester) async {
    final api = ReviewShopsApi([first]);
    await showHome(tester, api);
    await openPicker(tester);
    await tester.tap(find.text('Central'));
    await tester.pumpAndSettle();
    expect(find.byType(MerchantReviewReplyScreen), findsOneWidget);
    await tester.tap(find.text('Reply'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'Synthetic test reply');
    api.switchAccount();
    await tester.pumpAndSettle();
    expect(find.text('Send reply'), findsNothing);
    expect(find.byType(TextField), findsNothing);
    await tester.tap(find.text('Close'));
    await tester.pumpAndSettle();
    expect(api.replies, isEmpty);
    expect(api.shopReads, [11]);
    expect(find.text('Review for shop 11'), findsNothing);
  }, skip: const bool.fromEnvironment('PUBLIC_TRIAL'));
}
