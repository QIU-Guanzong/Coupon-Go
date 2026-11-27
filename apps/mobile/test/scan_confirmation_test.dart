import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/scan_batch.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

typedef _Submission = ({
  List<String> codes,
  List<String> tokens,
  List<int?> branches,
  String key,
});

class _Api extends ApiClient {
  _Api() : super(baseUrl: 'https://example.test');

  final preflights = <(String, int?)>[];
  final batches = <_Submission>[];
  final singles = <(String, String?, String?, int?)>[];
  bool loseFirstBatchReply = false;
  bool loseFirstSingleReply = false;

  @override
  Future<Map<String, dynamic>> prepareScan(String code, {int? branchId}) async {
    preflights.add((code, branchId));
    if (code == '111111' && branchId == null) {
      return {
        'can_redeem': false,
        'branch_selection_required': true,
        'allowed_branches': [
          {'id': 7, 'name': 'Central'}
        ],
      };
    }
    return {
      'can_redeem': true,
      'coupon_name': {
        '111111': 'First voucher',
        '222222': 'Second voucher',
      }[code],
      'shop_name': 'Sample shop',
      'prepare_token': 'prepared-$code-$branchId',
    };
  }

  @override
  Future<Map<String, dynamic>> scanBatch(List<String> codes,
      List<String> tokens, List<int?> branchIds, String requestId) async {
    batches.add((
      codes: List.of(codes),
      tokens: List.of(tokens),
      branches: List.of(branchIds),
      key: requestId,
    ));
    if (loseFirstBatchReply && batches.length == 1) {
      throw TimeoutException('Synthetic lost reply');
    }
    return {'total': codes.length};
  }

  @override
  Future<Map<String, dynamic>> scanCoupon(String code,
      {String? idempotencyKey, String? prepareToken, int? branchId}) async {
    singles.add((code, idempotencyKey, prepareToken, branchId));
    if (loseFirstSingleReply && singles.length == 1) {
      throw TimeoutException('Synthetic lost reply');
    }
    return {'coupon_name': 'Second voucher'};
  }
}

Future<void>? _fonts;
Future<void> _loadFonts() => _fonts ??= Future.wait([
      for (final (family, file) in [
        ('Roboto', 'assets/fonts/Roboto-Regular.ttf'),
        ('Coupon UI Fallback', 'fonts/CouponUIFallback.ttf'),
        ('Coupon UI Supplemental', 'fonts/CouponUISupplemental.ttf'),
        ('MaterialIcons', 'fonts/MaterialIcons-Regular.otf'),
      ])
        (FontLoader(family)..addFont(rootBundle.load(file))).load(),
    ]);

Future<void> _phone(WidgetTester tester, Widget screen,
    {bool en = true}) async {
  await tester.runAsync(_loadFonts);
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(390, 844);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final language = AppLanguage()..english = en;
  addTearDown(language.dispose);
  await tester.pumpWidget(LanguageScope(
    language: language,
    child: MaterialApp(theme: CgTheme.light(), home: screen),
  ));
  await tester.pumpAndSettle();
}

Future<void> _tap(WidgetTester tester, Finder target) async {
  await tester.ensureVisible(target);
  await tester.tap(target);
  await tester.pumpAndSettle();
}

Future<void> _add(WidgetTester tester, String code) async {
  final input = find.byType(TextField);
  await tester.ensureVisible(input);
  await tester.enterText(input, code);
  await _tap(tester, find.text('Check and add'));
  if (code == '111111') await _tap(tester, find.text('Central'));
}

Finder _remove(String title) => find.descendant(
      of: find.ancestor(of: find.text(title), matching: find.byType(ListTile)),
      matching: find.byWidgetPredicate(
          (widget) => widget is IconButton && widget.tooltip == 'Remove'),
    );

void main() {
  testWidgets('single redemption keeps the original request after a lost reply',
      (tester) async {
    final api = _Api()..loseFirstSingleReply = true;
    addTearDown(api.dispose);
    await _phone(tester, ScanScreen(api: api));
    await tester.enterText(find.byType(TextField), '222222');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();
    await _tap(tester, find.text('Confirm redemption'));
    expect(api.singles, hasLength(1));
    expect(find.text('Cancel'), findsNothing);
    await _tap(tester, find.text('Retry this redemption'));
    expect(api.singles, hasLength(2));
    expect(api.singles.last, api.singles.first);
    expect(find.text('Redeemed'), findsOneWidget);
  });

  testWidgets(
      'merchant scanner batch: repeated tap opens only one confirmation',
      (tester) async {
    final api = _Api();
    addTearDown(api.dispose);
    await _phone(tester, ScanScreen(api: api));
    await _tap(tester, find.text('Batch redemption'));
    await _add(tester, '222222');
    final submit = find.ancestor(
      of: find.text('Confirm batch redemption'),
      matching: find.byWidgetPredicate((widget) => widget is FilledButton),
    );
    await tester.ensureVisible(submit);
    final go = tester.widget<FilledButton>(submit).onPressed!;
    // Two input callbacks in the same frame, before the modal has been built.
    go();
    go();
    await tester.pumpAndSettle();
    expect(find.byType(AlertDialog, skipOffstage: false), findsOneWidget);
    expect(api.batches, isEmpty);
    await _tap(tester, find.text('Cancel'));
    expect(find.byType(AlertDialog), findsNothing);
    expect(api.batches, isEmpty);
    expect(tester.widget<IconButton>(_remove('Second voucher')).onPressed,
        isNotNull);
  });

  testWidgets(
      'remove before confirming only sends remaining code, branch and token; retries exact request',
      (tester) async {
    final api = _Api()..loseFirstBatchReply = true;
    addTearDown(api.dispose);
    await _phone(tester, ScanBatchScreen(api: api));
    await _add(tester, '111111');
    await _add(tester, '222222');
    final staleRemove =
        tester.widget<IconButton>(_remove('First voucher')).onPressed!;
    await _tap(tester, _remove('Second voucher'));
    expect(find.text('Checked 1 / 20'), findsOneWidget);
    final submit = find.ancestor(
      of: find.text('Confirm batch redemption'),
      matching: find.byWidgetPredicate((widget) => widget is FilledButton),
    );
    await tester.ensureVisible(submit);
    tester.widget<FilledButton>(submit).onPressed!();
    // A remove callback queued before the new frame may not alter consented contents.
    staleRemove();
    await tester.pumpAndSettle();
    expect(find.textContaining('1 vouchers will be redeemed'), findsOneWidget);
    expect(api.batches, isEmpty);
    final accept = find.widgetWithText(FilledButton, 'Confirm redemption');
    final finish = tester.widget<FilledButton>(accept).onPressed!;
    finish();
    finish();
    await tester.pumpAndSettle();
    expect(find.byType(ScanBatchScreen), findsOneWidget);
    expect(api.batches, hasLength(1));
    final first = api.batches.single;
    expect(first.codes, ['111111']);
    expect(first.tokens, ['prepared-111111-7']);
    expect(first.branches, [7]);
    expect(first.key, isNotEmpty);
    expect(
        tester.widget<IconButton>(_remove('First voucher')).onPressed, isNull);
    expect(tester.widget<TextField>(find.byType(TextField)).enabled, isFalse);
    await _tap(tester, find.text('Retry original request'));
    expect(api.batches, hasLength(2));
    final retry = api.batches.last;
    expect(retry.codes, first.codes);
    expect(retry.tokens, first.tokens);
    expect(retry.branches, first.branches);
    expect(retry.key, first.key);
    expect(find.text('Redeemed 1 vouchers'), findsOneWidget);
    expect(find.text('Checked 0 / 20'), findsOneWidget);
  });

  for (final en in [true, false]) {
    testWidgets(
        'continue next voucher clears the old receipt and opens an empty scanner ($en)',
        (tester) async {
      final api = _Api();
      addTearDown(api.dispose);
      await _phone(tester, ScanScreen(api: api), en: en);
      await tester.ensureVisible(find.byType(TextField));
      await tester.enterText(find.byType(TextField), '222222');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();
      expect(api.singles, isEmpty);
      await _tap(tester, find.text(en ? 'Confirm redemption' : '確認核銷'));
      expect(api.singles, hasLength(1));
      final first = api.singles.single;
      expect(first.$1, '222222');
      expect(first.$2, isNotEmpty);
      await _tap(tester, find.text(en ? 'Scan next voucher' : '繼續掃下一張'));
      final field = find.byType(TextField);
      await tester.ensureVisible(field);
      expect(tester.widget<TextField>(field).controller?.text, '');
      expect(find.text('Second voucher'), findsNothing);
      await tester.enterText(field, '111111');
      await tester.testTextInput.receiveAction(TextInputAction.done);
      await tester.pumpAndSettle();
      expect(api.preflights, [('222222', null), ('111111', null)]);
      expect(api.singles, hasLength(1));
      await tester.pumpWidget(const SizedBox());
    });
  }
}
