import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:coupon_go/vendor_coupon_lifecycle.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const _readOnly = publicPreview || bool.fromEnvironment('PUBLIC_TRIAL');
const _couponId = 3;
const _actionsKey = ValueKey('vendor-coupon-actions-3');
const _statusKey = ValueKey('vendor-coupon-status-3');

class _LifecycleApi extends ApiClient {
  final requests = <String>[];
  String stage = 'active';
  bool canManage = true;
  Map<String, dynamic>? badReceipt;
  Completer<void>? waitForMutation;
  Completer<void>? waitForList;

  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, String>? query,
  }) async {
    if (path == '/merchant/shops') {
      return {
        'shops': [
          {
            'id': 7,
            'name': 'Merchant',
            'is_owner': false,
            'can_manage_coupons': canManage,
          },
        ],
      };
    }
    if (path != '/get-vendor-coupon-list') throw StateError('Unexpected read');
    final snapshot = stage;
    final gate = waitForList;
    if (gate != null) await gate.future;
    final requested = query?['status'];
    return {
      'coupons': [
        if (requested == null || requested == snapshot)
          {
            'id': _couponId,
            'shop_id': 7,
            'coupon_name': 'Lunch',
            'status': snapshot,
            'remaining': 3,
            'used_count': 1,
          },
      ],
    };
  }

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
  }) async {
    requests.add(path);
    final gate = waitForMutation;
    if (gate != null) await gate.future;
    final receipt = badReceipt;
    if (receipt != null) return receipt;
    stage = switch (path) {
      '/vendor/coupons/3/pause' => 'paused',
      '/vendor/coupons/3/resume' => 'scheduled',
      '/vendor/coupons/3/unlist' => 'draft',
      '/vendor/coupons/3/submit' => 'pending_review',
      _ => throw StateError('Unexpected mutation'),
    };
    return {'coupon_id': _couponId, 'status': stage};
  }
}

Future<void> _open(
  WidgetTester tester,
  _LifecycleApi api, {
  bool english = false,
}) async {
  final language = AppLanguage()..english = english;
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(390, 844);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  addTearDown(language.dispose);
  addTearDown(api.dispose);
  api.activateTrialToken('local-fixture-only');
  await tester.pumpWidget(
    LanguageScope(
      language: language,
      child: MaterialApp(home: VendorHomeScreen(api: api)),
    ),
  );
  await tester.pumpAndSettle();
}

Future<void> _select(WidgetTester tester, String label) async {
  await tester.tap(find.byKey(_actionsKey));
  await tester.pumpAndSettle();
  await tester.tap(find.text(label));
  await tester.pumpAndSettle();
}

String _status(WidgetTester tester) =>
    tester.widget<Text>(find.byKey(_statusKey)).data!;

void main() {
  test(
    'menu limits actions to known listing states and API requires a scoped receipt',
    () async {
      expect(VendorCouponAction.forStatus('active'), [
        VendorCouponAction.pause,
        VendorCouponAction.unlist,
      ]);
      expect(VendorCouponAction.forStatus('paused'), [
        VendorCouponAction.resume,
        VendorCouponAction.unlist,
      ]);
      expect(VendorCouponAction.forStatus('scheduled'), [
        VendorCouponAction.unlist,
      ]);
      expect(VendorCouponAction.forStatus('rejected'), [
        VendorCouponAction.submit,
      ]);
      for (final state in [
        'expired',
        'sold_out',
        'pending_review',
        'unknown',
      ]) {
        expect(VendorCouponAction.forStatus(state), isEmpty);
      }
      final api = _LifecycleApi();
      addTearDown(api.dispose);
      api.badReceipt = {'coupon_id': 99, 'status': 'paused'};
      await expectLater(
        api.updateVendorCouponStatus(_couponId, VendorCouponAction.pause),
        throwsFormatException,
      );
      api.badReceipt = {'coupon_id': _couponId, 'status': 'active'};
      await expectLater(
        api.updateVendorCouponStatus(_couponId, VendorCouponAction.pause),
        throwsFormatException,
      );
    },
  );

  testWidgets(
    'pause, resume and submit reflect authoritative response states',
    (tester) async {
      final api = _LifecycleApi();
      await _open(tester, api, english: true);
      if (_readOnly) {
        expect(find.byKey(_actionsKey), findsNothing);
        expect(api.requests, isEmpty);
        return;
      }
      await _select(tester, 'Pause');
      expect(_status(tester), 'Paused');
      await _select(tester, 'Resume');
      expect(_status(tester), 'Scheduled');
      await _select(tester, 'Unlist');
      expect(find.text('Unlist coupon?'), findsOneWidget);
      expect(api.requests, hasLength(2));
      await tester.tap(find.text('Cancel'));
      await tester.pumpAndSettle();
      expect(api.requests, hasLength(2));
      expect(_status(tester), 'Scheduled');
      await _select(tester, 'Unlist');
      await tester.tap(find.text('Confirm unlist'));
      await tester.pumpAndSettle();
      expect(_status(tester), 'Draft');
      await _select(tester, 'Submit for review');
      expect(_status(tester), 'Pending review');
      expect(api.requests, [
        '/vendor/coupons/3/pause',
        '/vendor/coupons/3/resume',
        '/vendor/coupons/3/unlist',
        '/vendor/coupons/3/submit',
      ]);
      expect(tester.takeException(), isNull);
    },
  );

  testWidgets('unlist confirmation does not write after switching accounts', (
    tester,
  ) async {
    final api = _LifecycleApi();
    await _open(tester, api);
    if (_readOnly) return;
    await _select(tester, '下架');
    expect(find.text('下架優惠券？'), findsOneWidget);
    api.canManage = false;
    api.activateTrialToken('different-fixture');
    await tester.pumpAndSettle();
    await tester.tap(find.text('確認下架'));
    await tester.pumpAndSettle();
    expect(api.requests, isEmpty);
    expect(find.byKey(_actionsKey), findsNothing);
  });

  testWidgets(
    'pause acknowledgement replaces stale status before refresh completes',
    (tester) async {
      final api = _LifecycleApi();
      await _open(tester, api);
      if (_readOnly) return;
      api.waitForMutation = Completer<void>();
      final buttons = find.byKey(_actionsKey);
      final item = tester.widget<PopupMenuButton<VendorCouponAction>>(buttons);
      item.onSelected!(VendorCouponAction.pause);
      item.onSelected!(VendorCouponAction.pause);
      await tester.pump();
      expect(api.requests, ['/vendor/coupons/3/pause']);
      expect(_status(tester), '已上架');
      api.waitForList = Completer<void>();
      api.waitForMutation!.complete();
      await tester.pump();
      await tester.pump();
      expect(_status(tester), '已暫停');
      expect(
        tester.widget<PopupMenuButton<VendorCouponAction>>(buttons).enabled,
        isFalse,
      );
      api.waitForList!.complete();
      await tester.pumpAndSettle();
      expect(_status(tester), '已暫停');
      expect(
        tester.widget<PopupMenuButton<VendorCouponAction>>(buttons).enabled,
        isTrue,
      );
    },
  );

  testWidgets('confirmation clears the item from its previous status filter', (
    tester,
  ) async {
    final api = _LifecycleApi();
    await _open(tester, api, english: true);
    if (_readOnly) return;
    final filter = find.byKey(const ValueKey('vendor-coupon-status-filter'));
    tester.widget<DropdownButton<String>>(filter).onChanged!('active');
    await tester.pumpAndSettle();
    expect(_status(tester), 'Listed');
    await _select(tester, 'Pause');
    expect(find.byKey(_statusKey), findsNothing);
    expect(find.text('No coupons with this status.'), findsOneWidget);
    expect(api.requests, ['/vendor/coupons/3/pause']);
  });
}
