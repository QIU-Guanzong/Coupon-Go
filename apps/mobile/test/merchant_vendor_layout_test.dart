import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'coupon_issue_v2_fields_test.dart' show IssueFieldsApi;

class _VendorApi extends ApiClient {
  _VendorApi() : super(baseUrl: 'https://example.test');

  final dateRequests = <(DateTime?, DateTime?)>[];
  int consumeCalls = 0;

  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, String>? query,
  }) async =>
      {
        if (path == '/merchant/shops')
          'shops': [
            {
              'id': 7,
              'name': 'Central Shop',
              'is_owner': true,
              'can_manage_coupons': true,
              'can_view_settlement': true,
            },
          ],
      };

  @override
  Future<List<Map<String, dynamic>>> vendorRedemptions({
    DateTime? from,
    DateTime? to,
  }) async {
    dateRequests.add((from, to));
    return [];
  }

  @override
  Future<Map<String, dynamic>> prepareScan(String code, {int? branchId}) async {
    if (branchId == null) {
      return {
        'can_redeem': false,
        'branch_selection_required': true,
        'allowed_branches': [
          {'id': 7, 'name': 'Central Branch', 'address': 'One Queen’s Road'},
        ],
      };
    }
    return {
      'can_redeem': true,
      'prepare_token': 'test-token',
      'coupon_name': 'Lunch coupon',
      'shop_name': 'Central Shop',
      'customer_phone_last4': '0123',
      'valid_to': '2027-01-03T00:00:00+08:00',
      'package_quantity': 3,
    };
  }

  @override
  Future<Map<String, dynamic>> scanCoupon(
    String code, {
    String? idempotencyKey,
    String? prepareToken,
    int? branchId,
  }) async {
    consumeCalls++;
    return {'coupon_name': 'Lunch coupon'};
  }
}

Future<void>? _fonts;
Future<void> _loadFonts() => _fonts ??= Future.wait([
      for (final (family, file) in [
        ('Roboto', 'assets/fonts/Roboto-Regular.ttf'),
        ('Coupon UI Fallback', 'fonts/CouponUIFallback.ttf'),
        ('Coupon UI Supplemental', 'fonts/CouponUISupplemental.ttf'),
      ])
        (FontLoader(family)..addFont(rootBundle.load(file))).load(),
    ]);

Future<void> _phone(
  WidgetTester tester,
  Widget screen, {
  bool english = true,
  double scale = 1,
}) async {
  await tester.runAsync(_loadFonts);
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(390, 844);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final language = AppLanguage()..english = english;
  addTearDown(language.dispose);
  await tester.pumpWidget(
    LanguageScope(
      language: language,
      child: MaterialApp(
        theme: CgTheme.light(),
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context).copyWith(
            textScaler: TextScaler.linear(scale),
          ),
          child: child!,
        ),
        home: screen,
      ),
    ),
  );
}

void _readable(WidgetTester tester, Finder label) {
  expect(label, findsOneWidget);
  final text = tester.widget<Text>(label);
  final paragraph = tester.renderObject<RenderParagraph>(label);
  expect(paragraph.didExceedMaxLines, isFalse, reason: text.data);
  final boxes = paragraph.getBoxesForSelection(
    TextSelection(baseOffset: 0, extentOffset: text.data!.length),
  );
  expect(boxes, hasLength(1), reason: text.data);
}

void main() {
  for (final english in [false, true]) {
    for (final scale in [1.0, 1.3]) {
      testWidgets(
        '390px date controls stay aligned, $english and scale $scale',
        (tester) async {
          final api = _VendorApi();
          addTearDown(api.dispose);
          await _phone(
            tester,
            RedemptionRecordsScreen(api: api),
            english: english,
            scale: scale,
          );
          await tester.pumpAndSettle();
          final start = find.byKey(const ValueKey('vendor-date-start'));
          final end = find.byKey(const ValueKey('vendor-date-end'));
          final clear = find.byKey(const ValueKey('vendor-date-clear'));
          final width = tester.getSize(start).width;
          expect(tester.getSize(end), tester.getSize(start));
          for (final filter in [start, end]) {
            await tester.tap(filter);
            await tester.pumpAndSettle();
            await tester.tap(find.text('OK'));
            await tester.pumpAndSettle();
          }
          expect(api.dateRequests.last.$1, isNotNull);
          expect(api.dateRequests.last.$2, isNotNull);
          expect(tester.getSize(start).width, width);
          expect(tester.getTopLeft(start).dy, tester.getTopLeft(end).dy);
          expect(tester.getSize(end), tester.getSize(start));
          expect(tester.getTopLeft(clear).dy,
              greaterThanOrEqualTo(tester.getBottomLeft(start).dy));
          for (final filter in [start, end]) {
            final labels =
                find.descendant(of: filter, matching: find.byType(Text));
            expect(labels, findsNWidgets(2));
            _readable(tester, labels.at(0));
            _readable(tester, labels.at(1));
          }
          await tester.tap(clear);
          await tester.pumpAndSettle();
          expect(api.dateRequests.last, (null, null));
          expect(clear, findsNothing);
          expect(tester.takeException(), isNull);
        },
      );
    }
  }

  testWidgets('390px home tool labels are untruncated in two equal columns', (
    tester,
  ) async {
    final api = _VendorApi();
    addTearDown(api.dispose);
    await _phone(tester, VendorHomeScreen(api: api, showAdmin: true));
    await tester.pumpAndSettle();
    final grid = find.byKey(const ValueKey('vendor-tools'));
    final buttons =
        find.descendant(of: grid, matching: find.byType(OutlinedButton));
    final labels = find.descendant(of: grid, matching: find.byType(Text));
    final wrap = tester.widget<Wrap>(grid);
    final tiles = wrap.children.cast<SizedBox>();
    final columnWidth = (tester.getSize(grid).width - wrap.spacing) / 2;
    expect(tiles.length, greaterThanOrEqualTo(7));
    for (final tile in tiles) {
      expect(tile.width, columnWidth);
      expect(tester.getSize(find.byWidget(tile)).height, greaterThanOrEqualTo(44));
    }
    expect(buttons, findsWidgets);
    for (var i = 0; i < labels.evaluate().length; i++) {
      _readable(tester, labels.at(i));
    }
    expect(tester.takeException(), isNull);
  });

  testWidgets('390px scanner lines align after selecting branch, no write', (
    tester,
  ) async {
    final api = _VendorApi();
    addTearDown(api.dispose);
    await _phone(tester, ScanScreen(api: api));
    await tester.pump();
    await tester.enterText(find.byType(TextField), '123456');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pump();
    await tester.tap(find.text('Central Branch'));
    await tester.pump();
    final labels = [
      for (final name in [
        'Shop',
        'Branch',
        'Phone (last 4)',
        'Valid until',
        'Whole package',
        'Status'
      ])
        find.text(name),
    ];
    final x = tester.getTopLeft(labels.first).dx;
    for (final label in labels) {
      _readable(tester, label);
      expect(tester.getTopLeft(label).dx, x);
    }
    expect(tester.getTopLeft(find.text('0123')).dy,
        tester.getTopLeft(labels[2]).dy);
    expect(api.consumeCalls, 0);
    expect(tester.takeException(), isNull);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('390px coupon keeps three sections and an aligned submit', (
    tester,
  ) async {
    final api = IssueFieldsApi();
    addTearDown(api.dispose);
    await _phone(tester, IssueCouponScreen(api: api));
    await tester.pumpAndSettle();
    final details = find.text('Coupon details');
    final price = find.text('Price and quantity');
    expect(
        tester.getTopLeft(details).dy, lessThan(tester.getTopLeft(price).dy));
    final review = find.text('Review and submit');
    await tester.dragUntilVisible(
      review,
      find.byType(Scrollable).first,
      const Offset(0, -240),
    );
    await tester.pumpAndSettle();
    final validity = find.text('Validity and terms');
    expect(
        tester.getTopLeft(validity).dy, lessThan(tester.getTopLeft(review).dy));
    for (final name in [
      'Start time (Hong Kong time)',
      'Expiry date (Hong Kong time)'
    ]) {
      final row = find.widgetWithText(ListTile, name);
      expect(tester.widget<ListTile>(row).contentPadding, EdgeInsets.zero);
    }
    expect(
        tester.getSize(find.widgetWithText(FilledButton, 'Review and submit')),
        const Size(358, 56));
    expect(api.previewBody, isNull);
    expect(tester.takeException(), isNull);
  });
}
