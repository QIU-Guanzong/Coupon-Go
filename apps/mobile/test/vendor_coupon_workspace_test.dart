import 'dart:async';
import 'dart:io';
import 'dart:ui' as ui;

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:coupon_go/screens/vendor_coupon_workspace.dart';
import 'package:coupon_go/theme.dart';
import 'package:coupon_go/vendor_coupon_lifecycle.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

Map<String, dynamic> _coupon(int id, String status) => {
      'id': id,
      'shop_id': 7,
      'shop_name': '海港商店',
      'coupon_name': '海港午餐券 $id',
      'name_en': 'Lunch coupon $id',
      'status': status,
      'coupon_type': 'Discount',
      'percentage': 90,
      'sale_price': id.isOdd ? 20 : 0,
      'min_spend': 50,
      'per_user_limit': 2,
      'remaining': 7,
      'downloads': 5,
      'used_count': 2,
      'branch_ids': [],
      'valid_from': '2026-11-30T01:00:00',
      'valid_to': '2026-12-15T12:00:00',
      if (status == 'rejected') 'rejection_reason': '請更新優惠條款。',
      'refund_policy': 'anytime',
    };

class WorkspaceApi extends ApiClient {
  final rows = [
    for (final (index, status) in couponListingStatuses.indexed)
      _coupon(index + 1, status),
  ];
  final reads = <(String, Map<String, String>?)>[];
  final writes = <String>[];
  final allowPage = <String, Completer<Map<String, dynamic>>>{};
  bool fakeMorePages = false;
  bool malformed = false;

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    reads.add((path, query));
    if (path == '/merchant/shops') {
      return {
        'shops': [
          {
            'id': 7,
            'name': '海港商店',
            'is_owner': false,
            'can_manage_coupons': true
          }
        ],
      };
    }
    if (path.startsWith('/vendor/coupons/')) {
      final id = int.parse(path.split('/').last);
      return {'coupon': rows.singleWhere((row) => row['id'] == id)};
    }
    if (path != '/get-vendor-coupon-list') {
      throw StateError('Unexpected read $path');
    }
    final status = query?['status'];
    final term = query?['q'];
    if (status != null && allowPage.containsKey(status)) {
      return allowPage[status]!.future;
    }
    final page = int.tryParse(query?['page'] ?? '');
    final visible = rows
        .where((row) =>
            (status == null || row['status'] == status) &&
            (term == null ||
                row['name_en']
                    .toString()
                    .toLowerCase()
                    .contains(term.toLowerCase())))
        .toList();
    return {
      'coupons': visible,
      if (page != null) ...{
        'page': page,
        'page_size': 20,
        'total': fakeMorePages && status == null ? 21 : visible.length,
      },
    };
  }

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
  }) async {
    writes.add(path);
    final fields = path.split('/');
    final id = int.parse(fields[3]);
    final action = fields.last;
    final status = switch (action) {
      'pause' => 'paused',
      'resume' => 'scheduled',
      'unlist' => 'draft',
      'submit' => 'pending_review',
      _ => throw StateError('Unexpected write'),
    };
    if (malformed) return {'coupon_id': id + 1, 'status': status};
    final at = rows.indexWhere((row) => row['id'] == id);
    rows[at] = {...rows[at], 'status': status};
    return {'coupon_id': id, 'status': status};
  }
}

Future<void> _pump(WidgetTester t, WorkspaceApi api,
    {bool english = true, bool home = false, GlobalKey? shot}) async {
  api.activateTrialToken('merchant-workspace-fixture');
  final language = AppLanguage()..english = english;
  t.view.physicalSize = const Size(390, 844);
  t.view.devicePixelRatio = 1;
  addTearDown(() {
    t.view.resetPhysicalSize();
    t.view.resetDevicePixelRatio();
  });
  await t.pumpWidget(RepaintBoundary(
    key: shot,
    child: LanguageScope(
        language: language,
        child: MaterialApp(
          theme: CgTheme.light(),
          debugShowCheckedModeBanner: false,
          home: home
              ? VendorHomeScreen(api: api)
              : VendorCouponWorkspace(api: api),
          onGenerateRoute: (settings) {
            final route = settings.name!;
            if (route == '/vendor/coupons') {
              return MaterialPageRoute<void>(
                  settings: settings,
                  builder: (_) => VendorCouponWorkspace(api: api));
            }
            final id = int.parse(route.split('/').last);
            return MaterialPageRoute<String>(
                settings: settings,
                builder: (_) => VendorCouponDetail(api: api, couponId: id));
          },
        )),
  ));
  await t.pumpAndSettle();
}

Future<void> _tap(WidgetTester t, Finder finder) async {
  await t.ensureVisible(finder);
  await t.tap(finder);
  await t.pumpAndSettle();
}

const _capturePath = String.fromEnvironment('COUPON_CAPTURE_DIR');
Future<void> _snap(WidgetTester t, GlobalKey key, String name) async {
  if (_capturePath.isEmpty) return;
  await t.runAsync(() async {
    final box =
        key.currentContext!.findRenderObject()! as RenderRepaintBoundary;
    final image = await box.toImage(pixelRatio: 1);
    final png = await image.toByteData(format: ui.ImageByteFormat.png);
    await File('$_capturePath/$name.png')
        .writeAsBytes(png!.buffer.asUint8List());
    image.dispose();
  });
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    for (final entry in {
      'Coupon UI Fallback': 'fonts/CouponUIFallback.ttf',
      'Coupon UI Supplemental': 'fonts/CouponUISupplemental.ttf',
      'Roboto': 'assets/fonts/Roboto-Regular.ttf',
      'MaterialIcons': 'fonts/MaterialIcons-Regular.otf',
    }.entries) {
      final loader = FontLoader(entry.key)
        ..addFont(rootBundle.load(entry.value));
      await loader.load();
    }
  });

  test('management status table exactly matches package workspace', () {
    const expected = {
      'active': ['edit', 'pause', 'unlist'],
      'pending_review': ['edit', 'unlist'],
      'paused': ['edit', 'resume', 'unlist'],
      'draft': ['edit', 'submit'],
      'rejected': ['edit', 'unlist', 'submit'],
      'expired': <String>[],
      'sold_out': <String>[],
      'scheduled': ['edit', 'unlist'],
    };
    for (final row in expected.entries) {
      expect([
        if (VendorCouponAction.canEditListing(row.key)) 'edit',
        ...VendorCouponAction.forManagementStatus(row.key)
            .map((a) => a.endpoint),
      ], row.value);
    }
  });

  for (final en in [false, true]) {
    testWidgets(
        'merchant home opens paged list, eight state cards, and detail ($en)',
        (t) async {
      final api = WorkspaceApi();
      final key = GlobalKey();
      final lng = en ? 'en' : 'zh';
      await _pump(t, api, home: true, english: en, shot: key);
      await _snap(t, key, 'home-entry-$lng');
      await _tap(t, find.text(en ? 'My coupons' : '我的優惠券'));
      expect(find.text(en ? 'Coupon management' : '券管理'), findsOneWidget);
      expect(api.reads.last.$2, {'page': '1', 'page_size': '20'});
      await _snap(t, key, 'workspace-$lng');
      for (final status in couponListingStatuses) {
        await _tap(t, find.byKey(ValueKey('coupon-filter-$status')));
        expect(api.reads.last.$2?['status'], status);
        expect(
            find.byKey(ValueKey(
                'managed-coupon-${couponListingStatuses.indexOf(status) + 1}')),
            findsOneWidget);
        await _snap(t, key, '$status-$lng');
      }
      await _tap(t, find.text('海港午餐券 8'));
      expect(find.text(en ? 'Coupon details' : '優惠券詳情'), findsOneWidget);
      expect(find.textContaining('2026-11-30 09:00'), findsOneWidget);
      await _snap(t, key, 'detail-$lng');
      await t.pageBack();
      await t.pumpAndSettle();
      expect(
          t
              .widget<ChoiceChip>(
                  find.byKey(const ValueKey('coupon-filter-scheduled')))
              .selected,
          isTrue);
      expect(t.takeException(), isNull);
    });
  }

  for (final en in [false, true]) {
    testWidgets(
        'four actions confirm and update acknowledged state without loading a new list ($en)',
        (t) async {
      final api = WorkspaceApi();
      final key = GlobalKey();
      await _pump(t, api, shot: key, english: en);
      final lang = en ? 'en' : 'zh';
      final initial = api.reads.length;
      for (final action in ['pause', 'resume', 'unlist', 'submit']) {
        await _tap(t, find.byKey(ValueKey('coupon-1-$action')));
        expect(api.writes.length,
            ['pause', 'resume', 'unlist', 'submit'].indexOf(action));
        await _snap(t, key, 'action-$action-before-$lang');
        await _tap(
            t,
            find.widgetWithText(
                FilledButton,
                action == 'unlist'
                    ? (en ? 'Confirm unlist' : '確認下架')
                    : (en ? 'Confirm' : '確認')));
        await _snap(t, key, 'action-$action-after-$lang');
        expect(api.writes.last, '/vendor/coupons/1/$action');
      }
      expect(api.reads.length, initial);
      expect(api.rows.first['status'], 'pending_review');
      expect(t.takeException(), isNull);
    });
  }

  testWidgets('account change while confirming never posts and clears rows',
      (t) async {
    final api = WorkspaceApi();
    await _pump(t, api);
    await _tap(t, find.byKey(const ValueKey('coupon-1-pause')));
    api.activateTrialToken('different-account');
    await t.pumpAndSettle();
    await _tap(t, find.widgetWithText(FilledButton, 'Confirm'));
    expect(api.writes, isEmpty);
    expect(find.text('海港午餐券 1'), findsNothing);
  });

  testWidgets('mismatched receipt cannot change row or allow immediate retry',
      (t) async {
    final api = WorkspaceApi()..malformed = true;
    await _pump(t, api);
    await _tap(t, find.byKey(const ValueKey('coupon-1-pause')));
    await _tap(t, find.widgetWithText(FilledButton, 'Confirm'));
    expect(api.rows.first['status'], 'active');
    expect(
        t
            .widget<TextButton>(find.byKey(const ValueKey('coupon-1-pause')))
            .onPressed,
        isNull);
    expect(find.text('Could not confirm the coupon status. Reload the page.'),
        findsOneWidget);
  });

  testWidgets('new filter wins a late reply; search resets page', (t) async {
    final api = WorkspaceApi()..fakeMorePages = true;
    await _pump(t, api);
    final next = find.byTooltip('Next page');
    await t.scrollUntilVisible(next, 300,
        scrollable: find.byType(Scrollable).first);
    await _tap(t, next);
    expect(api.reads.last.$2?['page'], '2');
    await t.scrollUntilVisible(
        find.byKey(const ValueKey('coupon-workspace-search')), -300,
        scrollable: find.byType(Scrollable).first);
    final search = find.byKey(const ValueKey('coupon-workspace-search'));
    await t.enterText(search, 'Lunch coupon 3');
    await t.testTextInput.receiveAction(TextInputAction.search);
    await t.pumpAndSettle();
    expect(api.reads.last.$2,
        {'page': '1', 'page_size': '20', 'q': 'Lunch coupon 3'});
    expect(find.byKey(const ValueKey('managed-coupon-3')), findsOneWidget);
  });

  testWidgets('stale filter result does not replace the later filter',
      (t) async {
    final api = WorkspaceApi();
    final start = Completer<Map<String, dynamic>>();
    api.allowPage['active'] = start;
    await _pump(t, api);
    await t.tap(find.byKey(const ValueKey('coupon-filter-active')));
    await t.pump();
    final paused = find.byKey(const ValueKey('coupon-filter-paused'));
    await t.ensureVisible(paused);
    await t.tap(paused);
    await t.pumpAndSettle();
    expect(find.byKey(const ValueKey('managed-coupon-3')), findsOneWidget);
    start.complete({
      'coupons': [_coupon(1, 'active')],
      'page': 1,
      'page_size': 20,
      'total': 1
    });
    await t.pumpAndSettle();
    expect(find.byKey(const ValueKey('managed-coupon-3')), findsOneWidget);
    expect(find.byKey(const ValueKey('managed-coupon-1')), findsNothing);
  });

  testWidgets(
      'detail change returns an acknowledged state without losing list context',
      (t) async {
    final api = WorkspaceApi();
    await _pump(t, api);
    await _tap(t, find.text('海港午餐券 1'));
    await _tap(t, find.byKey(const ValueKey('coupon-1-pause')));
    await _tap(t, find.widgetWithText(FilledButton, 'Confirm'));
    expect(find.byKey(const ValueKey('coupon-1-resume')), findsOneWidget);
    await t.pageBack();
    await t.pumpAndSettle();
    expect(find.text('Coupon management'), findsOneWidget);
    expect(find.byKey(const ValueKey('coupon-1-resume')), findsOneWidget);
    expect(find.byKey(const ValueKey('coupon-1-pause')), findsNothing);
  });
}
