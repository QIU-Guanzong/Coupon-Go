import 'dart:convert';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/main.dart';
import 'package:coupon_go/screens/packages.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _slug = 'shu-xiang-hotpot-for-four';
const _path = '/api/v3/trial/public-packages/';

class _AppApi extends ApiClient {
  _AppApi() : super(baseUrl: 'https://example.test/coupongo-trial');
  final writes = <String>[];
  final reads = <String>[];

  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    writes.add(path);
    return switch (path) {
      '/trial/session' => {'mode': 'simulated', 'reset': false},
      '/trial/role' => {
          'mode': 'simulated',
          'role': 'customer',
          'access_token': 'synthetic'
        },
      _ => throw StateError('Unexpected write $path'),
    };
  }

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    reads.add(path);
    return path == '/me' ? {'role': 'customer'} : {};
  }
}

http.Response _response(Object value, [int status = 200]) => http.Response(
      jsonEncode(value),
      status,
      headers: {'content-type': 'application/json; charset=utf-8'},
    );

Map<String, Object> _public({String state = 'active', String slug = _slug}) => {
      'slug': slug,
      'package': {
        'name': '麻辣火鍋四人特價套餐',
        'shop_name': '蜀香麻辣火鍋城',
        'package_price': 588,
        'original_price': 820,
        'person_count': 4,
        'status': state,
        'valid_from': '2026-11-25T00:00:00+00:00',
        'valid_to': '2026-12-03T00:00:00+00:00',
        'refund_policy': 'non_refundable',
        'usage_rules': {
          'need_booking': true,
          'weekday_only': true,
          'holiday': false,
          'includes': ['鍋底'],
          'excludes': ['飲品'],
        },
        'items': [
          {'name': '牛小排拼盤', 'qty': 1}
        ],
        'branches': [
          {'branch_name': '蜀香麻辣火鍋城 · 總店', 'address': '旺角彌敦道 610 號'}
        ],
      },
    };

Future<void>? _fonts;
Future<void> _font() => _fonts ??= Future.wait([
      for (final (family, file) in [
        ('Roboto', 'assets/fonts/Roboto-Regular.ttf'),
        ('Coupon UI Fallback', 'fonts/CouponUIFallback.ttf'),
        ('Coupon UI Supplemental', 'fonts/CouponUISupplemental.ttf'),
        ('MaterialIcons', 'fonts/MaterialIcons-Regular.otf'),
      ])
        (FontLoader(family)..addFont(rootBundle.load(file))).load(),
    ]);

Future<void> _phone(WidgetTester tester, String route) async {
  await tester.runAsync(_font);
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(390, 844);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  tester.binding.platformDispatcher.defaultRouteNameTestValue = route;
  addTearDown(tester.binding.platformDispatcher.clearDefaultRouteNameTestValue);
}

Future<void> _boot(WidgetTester tester, _AppApi api) async {
  addTearDown(api.dispose);
  await tester.pumpWidget(CouponGoApp(api: api));
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 700));
  await tester.pumpAndSettle();
}

void main() {
  test('canonical share retains ingress base and excludes query/account data',
      () {
    expect(publicTrialPackageSlug('/p/$_slug'), _slug);
    expect(publicTrialPackageSlug('/p/2'), isNull);
    expect(publicTrialPackageSlug('/p/$_slug/extra'), isNull);
    expect(publicTrialPackageSlug('/p/$_slug?customer=1'), isNull);
    for (final base in [
      'https://example.test/coupongo/',
      'https://second.example/coupongo/',
    ]) {
      final url =
          publicTrialPackageShareUri(Uri.parse('$base?private=1#/old'), _slug);
      expect(url.toString(), '$base#/p/$_slug');
    }
    for (final path in ['/coupongo', '/coupongo/index.html']) {
      expect(
          publicTrialPackageShareUri(
                  Uri.parse('https://example.test$path'), _slug)
              .toString(),
          'https://example.test/coupongo/#/p/$_slug');
    }
    expect(
        publicTrialPackageShareUri(
            Uri.parse('https://person@example.test/'), _slug),
        isNull);
  });

  testWidgets(
      'cold public route and a fresh app reopen keep same offer and skip checkout',
      (tester) async {
    await _phone(tester, '/p/$_slug');
    SharedPreferences.setMockInitialValues({'english': true});
    final requests = <http.Request>[];
    final mock = MockClient((request) async {
      requests.add(request);
      return _response(_public());
    });
    await http.runWithClient(() async {
      final first = _AppApi();
      await _boot(tester, first);
      expect(find.text('麻辣火鍋四人特價套餐'), findsOneWidget);
      expect(find.text('HK\$588.00'), findsWidgets);
      expect(find.text('蜀香麻辣火鍋城 · Serves 4'), findsOneWidget);
      expect(find.text('Refunds: Non-refundable'), findsOneWidget);
      expect(find.text('This link is for viewing package details'),
          findsOneWidget);
      expect(find.text('Buy now'), findsNothing);
      expect(find.byType(PackageCheckoutScreen), findsNothing);
      expect(first.reads, isNot(contains('/package-details')));
      expect(first.writes, ['/trial/session', '/trial/role']);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
      await tester.pump();

      // A second cold app restores its own trial identity and reuses the route.
      SharedPreferences.setMockInitialValues(
          {'coupon_trial_access_token': 'other-session'});
      final afterRefresh = _AppApi();
      await _boot(tester, afterRefresh);
      expect(find.text('麻辣火鍋四人特價套餐'), findsOneWidget);
      expect(find.text('此連結只供查看套餐'), findsOneWidget);
      expect(afterRefresh.reads, contains('/me'));
      expect(afterRefresh.writes, ['/trial/session']);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox.shrink());
    }, () => mock);
    expect(requests, hasLength(2));
    for (final req in requests) {
      expect(req.url.path, '/coupongo-trial$_path$_slug');
      expect(req.url.query, isEmpty);
      expect(req.method, 'GET');
      expect(req.headers['authorization'], isNull);
    }
  }, skip: !publicTrial);

  testWidgets('sold-out link retains details, without a purchase action',
      (tester) async {
    await _phone(tester, '/p/$_slug');
    SharedPreferences.setMockInitialValues({'english': true});
    final api = _AppApi();
    await http.runWithClient(() async {
      await _boot(tester, api);
      expect(find.text('麻辣火鍋四人特價套餐'), findsOneWidget);
      expect(find.text('Sold out'), findsOneWidget);
      expect(find.text('Buy now'), findsNothing);
      await tester.scrollUntilVisible(find.text('Branches (1)'), 280);
      await tester.tap(find.text('Branches (1)'));
      await tester.pumpAndSettle();
      expect(find.text('旺角彌敦道 610 號'), findsOneWidget);
      await tester.pumpWidget(const SizedBox.shrink());
    }, () => MockClient((_) async => _response(_public(state: 'sold_out'))));
  }, skip: !publicTrial);

  for (final (route, hits) in [('/p/unlisted-sample', 1), ('/p/2', 0)]) {
    testWidgets(
        'unknown/unlisted or numeric link has no numeric fallback: $route',
        (tester) async {
      await _phone(tester, route);
      SharedPreferences.setMockInitialValues({'english': true});
      final api = _AppApi();
      var requests = 0;
      final mock = MockClient((_) async {
        requests++;
        return _response({'detail': '此連結已失效'}, 404);
      });
      await http.runWithClient(() async {
        await _boot(tester, api);
        expect(find.text('This package link is unavailable'), findsOneWidget);
        expect(find.text('Buy now'), findsNothing);
        expect(api.reads, isNot(contains('/package-details')));
        await tester.pumpWidget(const SizedBox.shrink());
      }, () => mock);
      expect(requests, hits);
    }, skip: !publicTrial);
  }
}
