import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/main.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:geolocator/geolocator.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

Map<String, dynamic> homeData(String tag) => {
      'categories': [
        {'id': 1, 'name': '分類 $tag', 'name_en': 'Category $tag'},
      ],
      'popular_coupons': [couponData('Coupon $tag')],
    };
Map<String, dynamic> couponData(String title) => {
      'id': 42,
      'coupon_name': title,
      'shop_name': 'Fixture shop',
      'remaining': 2,
      'total': 3,
    };
List<Map<String, dynamic>> packagesData(String tag) => [
      {'id': 13, 'name': 'Package $tag', 'package_price': 50, 'stock_left': 2},
    ];
List<Map<String, dynamic>> bargainsData(String tag) => [
      {'id': 9, 'name': 'Bargain $tag', 'stock_left': 2, 'status': 'active'},
    ];

class DeniedLocation extends GeolocatorPlatform {
  @override
  Future<LocationPermission> checkPermission() async =>
      LocationPermission.denied;
}

class HomeApi extends ApiClient {
  final homeCalls = <Completer<Map<String, dynamic>>>[];
  final packageCalls = <Completer<List<dynamic>>>[];
  final bargainCalls = <Completer<List<dynamic>>>[];
  final regionCalls = <Completer<List<dynamic>>>[];
  final homeCoordinates = <(double?, double?)>[];
  final packageShops = <int?>[];
  final areas = <(int?, String?)>[];

  @override
  bool get isLoggedIn => false;

  @override
  Future<Map<String, dynamic>> home({double? lat, double? lng}) {
    homeCoordinates.add((lat, lng));
    final response = Completer<Map<String, dynamic>>();
    homeCalls.add(response);
    return response.future;
  }

  @override
  Future<List<dynamic>> packages({int? shopId}) {
    packageShops.add(shopId);
    final response = Completer<List<dynamic>>();
    packageCalls.add(response);
    return response.future;
  }

  @override
  Future<List<dynamic>> bargainCampaigns() {
    final response = Completer<List<dynamic>>();
    bargainCalls.add(response);
    return response.future;
  }

  @override
  Future<List<dynamic>> classification({int? categoryId, String? area}) {
    areas.add((categoryId, area));
    final response = Completer<List<dynamic>>();
    regionCalls.add(response);
    return response.future;
  }

  @override
  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) {
    throw StateError('Unexpected request: $path');
  }
}

class TimedHomeApi extends HomeApi {
  final calls = <String>[];

  @override
  Future<Map<String, dynamic>> home({double? lat, double? lng}) async {
    calls.add('home');
    await Future<void>.delayed(const Duration(milliseconds: 40));
    return homeData('timed');
  }

  @override
  Future<List<dynamic>> packages({int? shopId}) async {
    calls.add('packages');
    await Future<void>.delayed(const Duration(milliseconds: 400));
    return packagesData('timed');
  }

  @override
  Future<List<dynamic>> bargainCampaigns() async {
    calls.add('bargains');
    await Future<void>.delayed(const Duration(milliseconds: 600));
    return bargainsData('timed');
  }
}

Future<void> showHome(WidgetTester tester, HomeApi api) async {
  await tester.binding.setSurfaceSize(const Size(390, 880));
  addTearDown(() => tester.binding.setSurfaceSize(null));
  SharedPreferences.setMockInitialValues({'home_qbot_hint_seen_v1': true});
  await tester.pumpWidget(LanguageScope(
    language: AppLanguage()..english = true,
    child: MaterialApp(
      theme: CgTheme.light(),
      home: HomeShell(
        api: api,
        authed: false,
        vendorMode: false,
        onAuthed: () {},
        onLogout: () {},
        onToggleMode: () {},
      ),
    ),
  ));
  await tester.pump();
}

Future<void> refresh(WidgetTester tester) async {
  final control =
      tester.widget<RefreshIndicator>(find.byType(RefreshIndicator));
  unawaited(control.onRefresh());
  await tester.idle();
  await tester.pump();
}

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUpAll(() async {
    await (FontLoader('Roboto')
          ..addFont(rootBundle.load('assets/fonts/Roboto-Regular.ttf')))
        .load();
  });
  setUp(() {
    final original = GeolocatorPlatform.instance;
    GeolocatorPlatform.instance = DeniedLocation();
    addTearDown(() => GeolocatorPlatform.instance = original);
  });
  for (var sample = 1; sample <= 3; sample++) {
    testWidgets('sample $sample: home is visible before optional response',
        (tester) async {
      final api = TimedHomeApi();
      await showHome(tester, api);
      int? first;
      for (var elapsed = 0; elapsed <= 1200; elapsed += 20) {
        if (find.text('Category timed').evaluate().isNotEmpty &&
            find.text('Coupon timed').evaluate().isNotEmpty) {
          first ??= elapsed;
        }
        if (elapsed < 1200) await tester.pump(const Duration(milliseconds: 20));
      }
      debugPrint(
          'HOME_PRIMARY_SAMPLE sample=$sample fixture_elapsed_ms=$first');
      expect(first, isNotNull);
      expect(first!, lessThan(400));
      expect(api.calls, ['home', 'packages', 'bargains']);
    });
  }

  testWidgets('home and each lower section complete independently',
      (tester) async {
    final api = HomeApi();
    await showHome(tester, api);
    api.homeCalls.single.complete(homeData('first'));
    await tester.pump();
    expect(find.text('Category first'), findsOneWidget);
    expect(find.text('Coupon first'), findsWidgets);
    expect(find.text('No popular packages yet.'), findsNothing);
    expect(api.packageCalls, hasLength(1));
    expect(api.bargainCalls, hasLength(1));
    expect(api.homeCoordinates, [(null, null)]);
    expect(api.packageShops, [null]);
    api.bargainCalls.single.complete(bargainsData('first'));
    await tester.pump();
    expect(find.text('Bargain first'), findsOneWidget);
    expect(find.text('Category first'), findsOneWidget);
    api.packageCalls.single.completeError(ApiException(503, 'Unavailable'));
    await tester.pump();
    expect(find.text('No popular packages yet.'), findsOneWidget);
    expect(find.text('Category first'), findsOneWidget);
    expect(find.text('Connection problem. Please retry.'), findsNothing);
  });

  testWidgets(
      'refresh drops stale optional and region results; dispose is safe',
      (tester) async {
    final api = HomeApi();
    await showHome(tester, api);
    api.homeCalls[0].complete(homeData('old'));
    await tester.pump();
    await tester.tap(find.text('Hong Kong'));
    await tester.pump();
    expect(api.regionCalls, hasLength(1));
    await refresh(tester);
    api.homeCalls[1].complete(homeData('new'));
    await tester.pump();
    expect(api.packageCalls, hasLength(2));
    expect(api.bargainCalls, hasLength(2));
    expect(api.regionCalls, hasLength(2));
    expect(api.areas, [(null, '香港'), (null, '香港')]);
    api.packageCalls[1].complete(packagesData('new'));
    api.bargainCalls[1].completeError(ApiException(503, 'Unavailable'));
    api.regionCalls[1].complete([couponData('Regional new')]);
    await tester.pump();
    api.packageCalls[0].complete(packagesData('old'));
    api.bargainCalls[0].complete(bargainsData('old'));
    api.regionCalls[0].complete([couponData('Regional old')]);
    await tester.pump();
    expect(find.text('Category new'), findsOneWidget);
    expect(find.text('Package new'), findsOneWidget);
    expect(find.text('Package old'), findsNothing);
    expect(find.text('Bargain old'), findsNothing);
    expect(find.text('Regional new'), findsWidgets);
    expect(find.text('Regional old'), findsNothing);

    await refresh(tester);
    await refresh(tester);
    expect(api.homeCalls, hasLength(4));
    api.homeCalls[3].complete(homeData('dispose'));
    await tester.pump();
    api.homeCalls[2].complete(homeData('superseded'));
    await tester.pump();
    expect(find.text('Category dispose'), findsOneWidget);
    expect(find.text('Category superseded'), findsNothing);
    expect(api.packageCalls, hasLength(3));
    expect(api.bargainCalls, hasLength(3));
    expect(api.regionCalls, hasLength(3));
    await tester.pumpWidget(const SizedBox.shrink());
    api.packageCalls[2].complete(packagesData('dispose'));
    api.bargainCalls[2].completeError(ApiException(503, 'Unavailable'));
    api.regionCalls[2].complete([couponData('Regional dispose')]);
    await tester.pump();
    expect(tester.takeException(), isNull);
  });
}
