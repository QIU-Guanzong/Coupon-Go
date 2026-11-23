import 'dart:async';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/qbot.dart';
import 'package:coupon_go/screens/trial_banner.dart';

class FakeTrialApi extends ApiClient {
  final roles = <String>[];
  bool fail = false;
  bool failPersistence = false;
  final overviewTokens = <String?>[];
  @override
  Future<Map<String, dynamic>> adminOverview() async {
    overviewTokens.add(paymentAccessToken);
    return {};
  }
  @override
  Future<void> prepareTrialToken(String? token) async {
    if (failPersistence) throw Exception('storage unavailable');
    await super.prepareTrialToken(token);
  }
  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    if (fail) throw Exception('unavailable');
    roles.add(body!['role'] as String);
    return {'access_token': 'example-token'};
  }
}

Future<void> openTrialSelector(WidgetTester tester) async {
  final controller = tester.state<TrialBannerState>(find.byType(TrialBanner));
  unawaited(controller.open(tester.element(find.byType(Scaffold).first)));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('identity dialog can be cancelled without changing the session', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final api = FakeTrialApi();
    final roles = <String>[];
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: TrialBanner(
      api: api, onRoleChanging: () async {}, onRoleChangeFailed: () {},
      onRoleChanged: roles.add,
    ))));
    await openTrialSelector(tester);
    expect(find.textContaining('付款為模擬'), findsOneWidget);
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(api.roles, isEmpty);
    expect(roles, isEmpty);
  });

  testWidgets('failed route transition restores the previous identity', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final api = FakeTrialApi();
    await api.saveToken('old-admin');
    var restored = false;
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: TrialBanner(
      api: api,
      onRoleChanging: () async => throw StateError('transition failed'),
      onRoleChangeFailed: () => restored = true,
      onRoleChanged: (_) => fail('should not activate a new role'),
    ))));
    await openTrialSelector(tester);
    await tester.tap(find.text('示例商戶'));
    await tester.pumpAndSettle();
    expect(restored, isTrue);
    expect(api.paymentAccessToken, 'old-admin');
    expect(find.text('切換失敗，請重試'), findsOneWidget);
  });

  testWidgets('role switch handles errors without a top banner', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final semantics = tester.ensureSemantics();
    final api = FakeTrialApi();
    final selected = <String>[];
    await tester.pumpWidget(MaterialApp(
      builder: (_, child) => Column(verticalDirection: VerticalDirection.up, children: [
        Expanded(child: child!),
        TrialBanner(api: api, onRoleChanging: () async {}, onRoleChangeFailed: () {}, onRoleChanged: selected.add),
      ]),
      home: const Scaffold(),
    ));
    expect(find.text('示例數據・模擬付款｜資料會重置'), findsNothing);
    await openTrialSelector(tester);
    await tester.tap(find.text('示例顧客'));
    await tester.pumpAndSettle();
    expect(api.roles, ['customer']);
    expect(selected, ['customer']);
    expect(api.isLoggedIn, isTrue);
    api.fail = true;
    await openTrialSelector(tester);
    await tester.tap(find.text('示例商戶'));
    await tester.pumpAndSettle();
    expect(find.text('切換失敗，請重試'), findsOneWidget);
    expect(selected, ['customer']);
    semantics.dispose();
  });

  testWidgets('old route is removed before the new identity becomes active', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final api = FakeTrialApi();
    await api.saveToken('old-admin');
    final gate = Completer<void>();
    final events = <String>[];
    api.addListener(() => events.add('active:${api.paymentAccessToken}'));
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: TrialBanner(
      api: api,
      onRoleChanging: () { events.add('remove-old-route'); return gate.future; },
      onRoleChangeFailed: () => events.add('failed'),
      onRoleChanged: (role) => events.add('show:$role'),
    ))));
    await openTrialSelector(tester);
    await tester.tap(find.text('示例商戶'));
    await tester.pump();
    await tester.pump();
    expect(events, ['remove-old-route']);
    expect(api.paymentAccessToken, 'old-admin');
    gate.complete();
    await tester.pumpAndSettle();
    expect(events, ['remove-old-route', 'active:example-token', 'show:vendor']);
  });

  testWidgets('old overview never requests with the switched merchant identity', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final api = FakeTrialApi();
    await api.saveToken('old-admin');
    var hidden = false;
    await tester.pumpWidget(MaterialApp(home: StatefulBuilder(builder: (context, setHarnessState) {
      return Scaffold(body: Column(children: [
        if (!hidden) AnimatedBuilder(animation: api, builder: (context, _) {
          api.adminOverview();
          return const Text('old admin overview');
        }),
        TrialBanner(api: api,
          onRoleChanging: () async {
            setHarnessState(() => hidden = true);
            await WidgetsBinding.instance.endOfFrame;
          },
          onRoleChangeFailed: () => setHarnessState(() => hidden = false),
          onRoleChanged: (_) {},
        ),
      ]));
    })));
    expect(api.overviewTokens, ['old-admin']);
    await openTrialSelector(tester);
    await tester.tap(find.text('示例商戶'));
    await tester.pumpAndSettle();
    expect(find.text('old admin overview'), findsNothing);
    expect(api.paymentAccessToken, 'example-token');
    expect(api.overviewTokens, everyElement('old-admin'));
    expect(api.overviewTokens, isNotEmpty);
  });

  testWidgets('failed identity persistence retains the previous route and token', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final api = FakeTrialApi();
    await api.saveToken('old-admin');
    api.failPersistence = true;
    var removed = false;
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: TrialBanner(
      api: api, onRoleChanging: () async { removed = true; },
      onRoleChangeFailed: () {}, onRoleChanged: (_) {},
    ))));
    await openTrialSelector(tester);
    await tester.tap(find.text('示例商戶'));
    await tester.pumpAndSettle();
    expect(removed, isFalse);
    expect(api.paymentAccessToken, 'old-admin');
    expect(find.text('切換失敗，請重試'), findsOneWidget);
  });

  testWidgets('identity controls fit a narrow screen', (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.binding.setSurfaceSize(const Size(320, 640));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(MaterialApp(
      builder: (_, child) => Column(verticalDirection: VerticalDirection.up, children: [
        Expanded(child: child!),
        TrialBanner(api: FakeTrialApi(), onRoleChanging: () async {}, onRoleChangeFailed: () {}, onRoleChanged: (_) {}),
      ]),
      home: const Scaffold(),
    ));
    await openTrialSelector(tester);
    expect(find.text('示例商戶'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('Q仔 explains the read-only recommendation boundary', (tester) async {
    final language = AppLanguage();
    await tester.pumpWidget(LanguageScope(language: language,
      child: MaterialApp(home: QBotScreen(api: FakeTrialApi()))));
    expect(find.textContaining('Q仔使用只讀示例'), findsOneWidget);
    language.dispose();
  }, skip: !const bool.fromEnvironment('PUBLIC_TRIAL'));
}
