import 'dart:io';
import 'dart:ui' as ui;

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:coupon_go/screens/vendor_coupon_workspace.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:image_picker/image_picker.dart';

const _active = <String, dynamic>{
  'coupon_id': 9,
  'shop_id': 7,
  'shop_name': 'Central Shop',
  'coupon_name': 'Lunch voucher',
  'name_en': 'Lunch offer',
  'coupon_type': 'Price',
  'status': 'active',
  'sale_price': 88,
  'regular_price': 120,
  'discount_price': 100,
  'total': 50,
  'remaining': 47,
  'downloads': 3,
  'used_count': 0,
  'per_user_limit': 1,
  'min_spend': 0,
  'refund_policy': 'expiry_only',
  'category_id': 1,
  'valid_from': '2026-11-01T00:00:00',
  'valid_to': '2031-11-30T00:00:00',
  'additional_terms': 'Dine in only',
};

class _CouponFixture extends ApiClient {
  _CouponFixture() : super(baseUrl: 'https://example.test');
  final saves = <Map<String, dynamic>>[];
  final previews = <Map<String, dynamic>>[];
  final creates = <Map<String, dynamic>>[];
  int uploads = 0;
  bool existingOrder = false;
  bool wrongReceipt = false;
  bool imageFails = false;

  @override
  Future<Map<String, dynamic>> vendorCouponDetails(int id) async {
    expect(id, 9);
    return {'coupon': _active};
  }

  @override
  Future<Map<String, dynamic>> get(String path,
          {Map<String, String>? query}) async =>
      switch (path) {
        '/merchant/shops' => {
            'shops': [
              {'id': 6, 'name': 'Another Shop', 'can_manage_coupons': true},
              {'id': 7, 'name': 'Central Shop', 'can_manage_coupons': true},
            ]
          },
        '/merchant/shops/6/branches' || '/merchant/shops/7/branches' => {
            'branches': <Object>[]
          },
        '/get-category-list' => {
            'categories': [
              {'id': 1, 'name': '餐飲', 'name_en': 'Dining'}
            ]
          },
        _ => throw StateError('Unexpected GET $path'),
      };

  @override
  Future<Map<String, dynamic>> patch(String path,
      {Map<String, dynamic>? body}) async {
    expect(path, '/vendor/coupons/9');
    saves.add(Map.of(body!));
    if (existingOrder) {
      throw ApiException(409, '此優惠券已有訂單，請建立新券以修改條款');
    }
    return {
      'coupon': {
        ..._active,
        ...body,
        'coupon_id': wrongReceipt ? 5 : 9,
        'status': 'pending_review'
      }
    };
  }

  @override
  Future<String> uploadCouponImage(String filename, Uint8List bytes) async {
    uploads++;
    if (imageFails) throw ApiException(400, '圖片未能上傳');
    expect(filename, 'picture.webp');
    expect(bytes, [1, 2, 3]);
    return '/uploads/merchant-images/fixture.webp';
  }

  @override
  Future<Map<String, dynamic>> couponPreview(Map<String, dynamic> body) async {
    previews.add(Map.of(body));
    return {
      'coupon_name': body['coupon_name'],
      'shop_name': 'Another Shop',
      'total': body['total'],
      'amount_text': 'Coupon'
    };
  }

  @override
  Future<Map<String, dynamic>> couponCreate(Map<String, dynamic> body) async {
    creates.add(Map.of(body));
    return {
      'message': '已提交審核',
      'coupon': {'status': 'pending_review'}
    };
  }
}

final _baseline = Platform.environment['CG_COUPON_FORM_BASELINE'] == '1';
final _evidence = Platform.environment['CG_COUPON_FORM_CAPTURE'];
const _shotKey = ValueKey('form-capture');

Future<void> _open(WidgetTester t, Widget home, {bool en = false}) async {
  t.view.devicePixelRatio = 1;
  t.view.physicalSize = const Size(390, 844);
  addTearDown(t.view.resetDevicePixelRatio);
  addTearDown(t.view.resetPhysicalSize);
  final language = AppLanguage()..english = en;
  await t.pumpWidget(RepaintBoundary(
      key: _shotKey,
      child: LanguageScope(
          language: language,
          child: MaterialApp(
              debugShowCheckedModeBanner: false,
              theme: CgTheme.light(),
              home: home))));
  await t.pumpAndSettle();
}

Future<void> _bring(WidgetTester t, Finder finder) async {
  if (finder.evaluate().isEmpty) {
    final list = find.byType(ListView).first;
    final scrollable =
        find.descendant(of: list, matching: find.byType(Scrollable)).first;
    await t.drag(list, const Offset(0, 2400));
    await t.pumpAndSettle();
    await t.scrollUntilVisible(finder, 200, scrollable: scrollable);
  }
  await t.ensureVisible(finder);
  await t.pump();
}

Future<void> _tap(WidgetTester t, Finder finder) async {
  await _bring(t, finder);
  await t.tap(finder);
  await t.pumpAndSettle();
}

Future<void> _enter(WidgetTester t, Finder finder, String text) async {
  await _bring(t, finder);
  await t.enterText(finder, text);
  FocusManager.instance.primaryFocus?.unfocus();
  await t.pumpAndSettle();
}

Future<void> _image(WidgetTester t, String name) async {
  if (_evidence == null) return;
  await t.runAsync(() async {
    final boundary =
        t.renderObject<RenderRepaintBoundary>(find.byKey(_shotKey));
    final image = await boundary.toImage();
    final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
    await File('$_evidence/$name.png')
        .writeAsBytes(bytes!.buffer.asUint8List());
    image.dispose();
  });
}

void main() {
  setUpAll(() async {
    for (final (family, assets) in [
      ('MaterialIcons', ['fonts/MaterialIcons-Regular.otf']),
      (
        'Roboto',
        [
          'assets/fonts/Roboto-Regular.ttf',
          'assets/fonts/Roboto-Medium.ttf',
          'assets/fonts/Roboto-Bold.ttf'
        ]
      ),
      ('Coupon UI Fallback', ['fonts/CouponUIFallback.ttf']),
      ('Coupon UI Supplemental', ['fonts/CouponUISupplemental.ttf']),
    ]) {
      final loader = FontLoader(family);
      for (final asset in assets) {
        loader.addFont(rootBundle.load(asset));
      }
      await loader.load();
    }
  });

  for (final en in [false, true]) {
    final lg = en ? 'en' : 'zh';
    testWidgets(
        '390 edit active coupon from merchant detail and confirm only changed fields: $lg',
        (t) async {
      final api = _CouponFixture();
      await _open(t, VendorCouponDetail(api: api, couponId: 9), en: en);
      await _image(t, 'details-$lg');
      if (_baseline) return;
      await _tap(t, find.text(en ? 'Edit coupon' : '編輯優惠券'));
      expect(find.textContaining(en ? 'Saving sends' : '儲存後會重新提交審核'),
          findsOneWidget);
      await _image(t, 'edit-form-$lg');
      final editName = find.byType(TextField).first;
      await _enter(t, editName, 'Updated lunch voucher');
      final review = find.text(en ? 'Review and save' : '檢查並儲存');
      await _tap(t, review);
      expect(api.saves, isEmpty);
      expect(
          find.text('Lunch voucher → Updated lunch voucher'), findsOneWidget);
      await _image(t, 'edit-review-$lg');
      await _tap(t, find.text(en ? 'Keep editing' : '返回編輯'));
      expect(api.saves, isEmpty);
      await _tap(t, review);
      await _tap(t, find.text(en ? 'Confirm save' : '確認儲存'));
      expect(api.saves.single, {'coupon_name': 'Updated lunch voucher'});
      expect(api.previews, isEmpty);
      expect(find.text('Updated lunch voucher'), findsOneWidget);
      expect(find.text(en ? 'Pending review' : '待審核'), findsOneWidget);
      await _image(t, 'saved-$lg');
    });

    testWidgets('390 conflict preserves 409 and copy asks for fresh stock: $lg',
        (t) async {
      if (_baseline) return;
      final api = _CouponFixture()..existingOrder = true;
      await _open(t, VendorCouponDetail(api: api, couponId: 9), en: en);
      await _tap(t, find.text(en ? 'Edit coupon' : '編輯優惠券'));
      await _enter(t, find.byType(TextField).first, 'New lunch voucher');
      await _tap(t, find.text(en ? 'Review and save' : '檢查並儲存'));
      await _tap(t, find.text(en ? 'Confirm save' : '確認儲存'));
      expect(find.text('此優惠券已有訂單，請建立新券以修改條款'), findsOneWidget);
      expect(api.saves.single, {'coupon_name': 'New lunch voucher'});
      expect(api.creates, isEmpty);
      await _image(t, '409-$lg');
      await _tap(t,
          find.widgetWithText(FilledButton, en ? 'Create new coupon' : '建立新券'));
      expect(find.text('New lunch voucher'), findsOneWidget);
      expect(find.text('Central Shop'), findsWidgets);
      await _image(t, 'copy-$lg');
      await _tap(t, find.text(en ? 'Review and submit' : '檢查並提交'));
      expect(api.previews, isEmpty);
      expect(api.creates, isEmpty);
    });

    testWidgets(
        '390 paid create field values stay separate and are confirmed once: $lg',
        (t) async {
      if (_baseline) return;
      final api = _CouponFixture();
      await _open(
          t,
          Scaffold(
              body: Builder(
                  builder: (context) => TextButton(
                      onPressed: () => Navigator.of(context).push(
                          MaterialPageRoute(
                              builder: (_) => IssueCouponScreen(
                                  api: api,
                                  pickCouponImage: () async => XFile.fromData(
                                      Uint8List.fromList([1, 2, 3]),
                                      name: 'picture.webp',
                                      path: 'picture.webp')))),
                      child: const Text('Open fixture')))),
          en: en);
      await _tap(t, find.text('Open fixture'));
      await _enter(t, find.byType(TextField).first, 'Spring coupon');
      final type = find.byKey(const ValueKey('issue-coupon-type'));
      Future<void> choose(String option) async {
        await _tap(t, type);
        await _tap(t, find.text(option).last);
      }

      final priceKey = find.byKey(const ValueKey('issue-sale-price'));
      await choose(en ? 'Paid coupon' : '付費優惠券');
      await _enter(t, priceKey, '88');
      await choose(en ? 'Gift coupon' : '贈品優惠券');
      await choose(en ? 'Paid coupon' : '付費優惠券');
      expect(t.widget<TextField>(priceKey).controller!.text, isEmpty);
      await _enter(t, priceKey, '88.50');
      await _enter(t, find.byKey(const ValueKey('issue-regular-price')), '125');
      await _enter(
          t, find.byKey(const ValueKey('issue-discount-price')), '105');
      final image = find.byKey(const ValueKey('issue-coupon-image'));
      if (publicTrial) {
        expect(image, findsNothing);
        expect(api.uploads, 0);
      } else {
        api.imageFails = true;
        await _tap(t, image);
        expect(find.text('圖片未能上傳'), findsOneWidget);
        api.imageFails = false;
        await _tap(t, image);
        expect(api.uploads, 2);
      }
      await _image(t, 'paid-form-$lg');
      final refund = find.byKey(const ValueKey('issue-refund-policy'));
      await _tap(t, refund);
      await _tap(t, find.text(en ? 'Non-refundable' : '不可退款').last);
      await _tap(t, find.text(en ? 'Review and submit' : '檢查並提交'));
      expect(api.previews.single, containsPair('sale_price', 88.5));
      expect(api.previews.single, containsPair('regular_price', 125));
      expect(api.previews.single, containsPair('discount_price', 105));
      expect(
          api.previews.single, containsPair('refund_policy', 'non_refundable'));
      expect(api.previews.single,
          containsPair('image',
              publicTrial ? '' : '/uploads/merchant-images/fixture.webp'));
      expect(api.creates, isEmpty);
      await _image(t, 'paid-review-$lg');
      await _tap(t, find.text(en ? 'Submit for review' : '提交審核'));
      expect(api.creates.single, api.previews.single);
    });
  }

  testWidgets('trial copy with existing image submits without uploading',
      (t) async {
    final api = _CouponFixture();
    await _open(
        t,
        Scaffold(
            body: Builder(
                builder: (context) => TextButton(
                    onPressed: () => Navigator.of(context).push(
                        MaterialPageRoute(
                            builder: (_) => IssueCouponScreen(
                                api: api,
                                copyCoupon: const {
                                  ..._active,
                                  'image': '/uploads/merchant-images/existing.webp',
                                }))),
                    child: const Text('Open copy')))));
    await _tap(t, find.text('Open copy'));
    await _enter(t, find.widgetWithText(TextField, '數量'), '12');
    await _tap(t, find.text('檢查並提交'));
    expect(api.previews.single['image'], '');
    await _tap(t, find.text('提交審核'));
    expect(api.creates.single['image'], '');
    expect(api.uploads, 0);
  }, skip: !publicTrial);

  testWidgets('account switch during review sends zero PATCH requests',
      (t) async {
    final api = _CouponFixture();
    await _open(t, VendorCouponDetail(api: api, couponId: 9));
    await _tap(t, find.text('編輯優惠券'));
    await _enter(t, find.byType(TextField).first, 'Unsaved change');
    await _tap(t, find.text('檢查並儲存'));
    api.activateTrialToken('fixture-account-b');
    await t.pump();
    await _tap(t, find.text('確認儲存'));
    expect(api.saves, isEmpty);
    expect(find.text('Unsaved change'), findsNothing);
  });

  testWidgets('mismatched PATCH receipt cannot silently update the coupon',
      (t) async {
    final api = _CouponFixture()..wrongReceipt = true;
    await _open(t, VendorCouponDetail(api: api, couponId: 9));
    await _tap(t, find.text('編輯優惠券'));
    await _enter(t, find.byType(TextField).first, 'Cannot display success');
    await _tap(t, find.text('檢查並儲存'));
    await _tap(t, find.text('確認儲存'));
    expect(api.saves, hasLength(1));
    expect(find.text('編輯優惠券'), findsOneWidget);
    expect(find.textContaining('儲存結果未確認'), findsOneWidget);
    final review = find.widgetWithText(FilledButton, '檢查並儲存');
    expect(t.widget<FilledButton>(review).onPressed, isNull);
  });
  testWidgets(
      'legacy Price coupon without a sale price can be renamed without making it paid',
      (t) async {
    final api = _CouponFixture();
    final previous = <String, dynamic>{..._active, 'sale_price': null};
    await _open(
      t,
      Scaffold(
          body: Builder(
              builder: (context) => TextButton(
                    onPressed: () => Navigator.of(context).push(
                        MaterialPageRoute(
                            builder: (_) => IssueCouponScreen(
                                api: api, editCoupon: previous))),
                    child: const Text('Open edit'),
                  ))),
    );
    await _tap(t, find.text('Open edit'));
    await _enter(t, find.byType(TextField).first, 'Legacy price voucher');
    await _tap(t, find.text('檢查並儲存'));
    await _tap(t, find.text('確認儲存'));
    expect(api.saves.single, {'coupon_name': 'Legacy price voucher'});
  });

  testWidgets(
      'an inherited expiry farther than one year opens the HK picker without crashing',
      (t) async {
    final api = _CouponFixture();
    await _open(t, VendorCouponDetail(api: api, couponId: 9), en: true);
    await _tap(t, find.text('Edit coupon'));
    await _tap(
        t, find.widgetWithText(ListTile, 'Expiry date (Hong Kong time)'));
    expect(find.byType(DatePickerDialog), findsOneWidget);
    expect(t.takeException(), isNull);
    expect(api.saves, isEmpty);
  });
}
