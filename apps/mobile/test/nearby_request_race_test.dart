import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/nearby.dart';
import 'package:coupon_go/widgets/widgets.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

class _NearbyApi extends ApiClient {
  final requests = <Completer<Map<String, dynamic>>>[];

  @override
  Future<Map<String, dynamic>> nearbyShops({
    required double lat,
    required double lng,
    double radiusKm = 10,
    String sort = 'distance',
  }) {
    final request = Completer<Map<String, dynamic>>();
    requests.add(request);
    return request.future;
  }
}

Map<String, dynamic> _shops(String name) => {
      'shops': [
        {'shop_id': 1, 'shop_name': name, 'distance_km': 0.5},
      ],
    };

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const locationChannel = MethodChannel('flutter.baseflow.com/geolocator');

  setUp(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(locationChannel, (call) async {
      if (call.method == 'checkPermission') return 2; // already granted
      throw PlatformException(code: 'unexpected_location_call');
    });
  });

  tearDown(() {
    TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger
        .setMockMethodCallHandler(locationChannel, null);
  });

  Future<_NearbyApi> open(WidgetTester tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = _NearbyApi();
    await tester.pumpWidget(MaterialApp(home: NearbyScreen(api: api)));
    await tester.pump();
    expect(api.requests, hasLength(1));
    return api;
  }

  testWidgets('late area result cannot replace the latest area', (tester) async {
    final api = await open(tester);
    await tester.tap(find.text('旺角'));
    await tester.pump();
    expect(api.requests, hasLength(2));
    api.requests[1].complete(_shops('旺角商戶'));
    await tester.pump();
    expect(find.text('旺角商戶'), findsOneWidget);

    api.requests[0].complete(_shops('中環商戶'));
    await tester.pump();
    expect(find.text('旺角商戶'), findsOneWidget);
    expect(find.text('中環商戶'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('old request completion does not end newer loading', (tester) async {
    final api = await open(tester);
    await tester.tap(find.text('1 公里'));
    await tester.pump();
    api.requests[0].complete(_shops('舊結果'));
    await tester.pump();
    expect(find.byType(SkeletonBox), findsNWidgets(3));
    expect(find.text('舊結果'), findsNothing);

    api.requests[1].complete(_shops('附近商戶'));
    await tester.pump();
    expect(find.byType(SkeletonBox), findsNothing);
    expect(find.text('附近商戶'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('late error cannot hide latest sorted results', (tester) async {
    final api = await open(tester);
    await tester.tap(find.text('評分最高'));
    await tester.pump();
    api.requests[1].complete(_shops('最新結果'));
    await tester.pump();

    api.requests[0].completeError(ApiException(503, 'unavailable'));
    await tester.pump();
    expect(find.text('最新結果'), findsOneWidget);
    expect(find.text('網絡不穩定，請重試'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  testWidgets('current failure still offers a working retry', (tester) async {
    final api = await open(tester);
    api.requests[0].completeError(ApiException(503, 'unavailable'));
    await tester.pump();
    expect(find.text('網絡不穩定，請重試'), findsOneWidget);
    await tester.tap(find.text('重試'));
    await tester.pump();
    expect(api.requests, hasLength(2));
    api.requests[1].complete(_shops('重試結果'));
    await tester.pump();
    expect(find.text('重試結果'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('completing after leaving does not update disposed screen',
      (tester) async {
    final api = await open(tester);
    await tester.pumpWidget(const MaterialApp(home: SizedBox()));
    api.requests[0].complete(_shops('過期結果'));
    await tester.pump();
    expect(tester.takeException(), isNull);
  });
}
