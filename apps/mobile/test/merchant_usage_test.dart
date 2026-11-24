import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/merchant_usage.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

class UsageFixtureApi extends ApiClient {
  final requests = <int>[];
  final pending = <({int days, Completer<Map<String, dynamic>> response})>[];
  int revision = 0;

  @override
  int get sessionRevision => revision;

  void switchAccount() {
    revision++;
    notifyListeners();
  }

  @override
  Future<Map<String, dynamic>> merchantDashboard(int days) {
    requests.add(days);
    final response = Completer<Map<String, dynamic>>();
    pending.add((days: days, response: response));
    return response.future;
  }

  void answer(int request, Map<String, dynamic> data) =>
      pending[request].response.complete(data);

  void fail(int request, ApiException error) =>
      pending[request].response.completeError(error);

  // Anything outside the explicit dashboard contract is a test failure.
  @override
  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) =>
      throw StateError('Unexpected GET: $path');
}

Map<String, dynamic> dashboard(
  int days, {
  String name = 'Central',
  bool empty = false,
  bool missingClaimCount = false,
}) {
  final to = DateTime.utc(2026, 11, 24);
  final from = to.subtract(Duration(days: empty ? 0 : days - 1));
  final claims = empty ? 0 : days * 2;
  return {
    'period': {
      'from': from.toIso8601String().substring(0, 10),
      'to': to.toIso8601String().substring(0, 10),
      'days': days,
    },
    'totals': {'claims': claims, 'redemptions': empty ? 0 : days},
    'shops': empty
        ? []
        : [
            {
              'shop_id': 42,
              'shop_name': name,
              'totals': {
                if (!missingClaimCount) 'claims': claims,
                'redemptions': days,
                'gross_amount': 99999,
              },
            },
          ],
  };
}

Future<void> showUsage(
  WidgetTester tester,
  UsageFixtureApi api, {
  bool english = true,
  WidgetBuilder? legacy,
}) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(390, 844);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final language = AppLanguage()..english = english;
  addTearDown(language.dispose);
  addTearDown(api.dispose);
  await tester.pumpWidget(
    LanguageScope(
      language: language,
      child: MaterialApp(
        home: MerchantUsageScreen(api: api, legacyUsageBuilder: legacy),
      ),
    ),
  );
}

Future<void> selectDays(WidgetTester tester, int days) async {
  await tester.tap(find.byKey(ValueKey('usage-days-$days')));
  await tester.pump();
}

Future<void> selectExport(WidgetTester tester) async {
  final export = find.text('Export shop CSV');
  await tester.ensureVisible(export);
  await tester.tap(export);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets(
    'exact 7, 30 and 90-day queries update shop counts and UTC period',
    (tester) async {
      final api = UsageFixtureApi();
      await showUsage(tester, api, english: false);
      expect(api.requests, [30]);
      api.answer(0, dashboard(30));
      await tester.pumpAndSettle();
      expect(find.text('領券次數: 60'), findsNWidgets(2));
      expect(find.text('2026-10-26 – 2026-11-24 (UTC)'), findsOneWidget);
      await selectDays(tester, 7);
      expect(find.text('領券次數: 60'), findsNothing);
      api.answer(1, dashboard(7));
      await tester.pumpAndSettle();
      expect(find.text('可對賬核銷: 7'), findsNWidgets(2));
      expect(find.text('2026-11-18 – 2026-11-24 (UTC)'), findsOneWidget);
      await selectDays(tester, 90);
      api.answer(2, dashboard(90));
      await tester.pumpAndSettle();
      expect(api.requests, [30, 7, 90]);
      expect(find.text('可對賬核銷: 90'), findsNWidgets(2));
      expect(find.textContaining('99999'), findsNothing);
    },
  );

  testWidgets('late older window never reappears under the current period', (
    tester,
  ) async {
    final api = UsageFixtureApi();
    await showUsage(tester, api);
    await selectDays(tester, 7);
    await selectDays(tester, 90);
    api.answer(2, dashboard(90, name: 'New period'));
    await tester.pumpAndSettle();
    api.answer(0, dashboard(30, name: 'Old period'));
    api.answer(1, dashboard(7, name: 'Older request'));
    await tester.pumpAndSettle();
    expect(find.text('New period'), findsOneWidget);
    expect(find.text('Old period'), findsNothing);
    expect(find.text('Older request'), findsNothing);
  });

  testWidgets(
    'empty and invalid responses never invent data or enable export',
    (tester) async {
      final api = UsageFixtureApi();
      await showUsage(tester, api);
      api.answer(0, dashboard(30, empty: true));
      await tester.pumpAndSettle();
      expect(find.text('No shops to show.'), findsOneWidget);
      expect(find.text('Export shop CSV'), findsNothing);
      await selectDays(tester, 7);
      api.answer(1, dashboard(30));
      await tester.pumpAndSettle();
      expect(find.text('Could not load data for this period.'), findsOneWidget);
      await tester.tap(find.text('Try again'));
      await tester.pump();
      api.answer(2, dashboard(7, missingClaimCount: true));
      await tester.pumpAndSettle();
      expect(find.text('Could not load data for this period.'), findsOneWidget);
      expect(find.text('Export shop CSV'), findsNothing);
      expect(api.requests, [30, 7, 7]);
    },
  );

  testWidgets('permission denial cannot use all-time numbers as date data', (
    tester,
  ) async {
    final api = UsageFixtureApi();
    var openedLegacy = 0;
    await showUsage(
      tester,
      api,
      legacy: (_) {
        openedLegacy++;
        return const Scaffold(body: Text('Legacy counts 250'));
      },
    );
    api.fail(0, ApiException(403, 'internal detail'));
    await tester.pumpAndSettle();
    expect(
      find.text('You don’t have access to this shop data.'),
      findsOneWidget,
    );
    expect(find.text('Export shop CSV'), findsNothing);
    expect(find.text('Legacy counts 250'), findsNothing);
    expect(openedLegacy, 0);
    await tester.tap(find.text('All-time coupon activity'));
    await tester.pumpAndSettle();
    expect(find.text('Legacy counts 250'), findsOneWidget);
    expect(openedLegacy, 1);
  });

  testWidgets(
    'CSV contains displayed shop/date counts, escaped shop names only',
    (tester) async {
      String? clipboardValue;
      final messenger =
          TestDefaultBinaryMessengerBinding.instance.defaultBinaryMessenger;
      messenger.setMockMethodCallHandler(SystemChannels.platform, (call) async {
        if (call.method == 'Clipboard.setData') {
          clipboardValue = (call.arguments as Map)['text'] as String;
        }
        return null;
      });
      addTearDown(
        () => messenger.setMockMethodCallHandler(SystemChannels.platform, null),
      );
      final api = UsageFixtureApi();
      await showUsage(tester, api);
      api.answer(0, dashboard(30, name: '  =Some("shop, name")'));
      await tester.pumpAndSettle();
      await selectExport(tester);
      expect(clipboardValue, isNull);
      await tester.tap(find.text('Copy CSV'));
      await tester.pumpAndSettle();
      expect(
        clipboardValue,
        '"Shop ID","Shop name","Start date (UTC)","End date (UTC)",'
        '"Coupon claims","Reconciled redemptions"\r\n'
        '"42","\'  =Some(""shop, name"")","2026-10-26","2026-11-24","60","30"\r\n',
      );
      expect(clipboardValue, isNot(contains('99999')));
    },
  );

  testWidgets(
    'account change clears old results and open CSV before new fetch',
    (tester) async {
      final api = UsageFixtureApi();
      await showUsage(tester, api);
      api.answer(0, dashboard(30, name: 'Old account'));
      await tester.pumpAndSettle();
      await selectExport(tester);
      expect(find.byType(SelectableText), findsOneWidget);
      api.switchAccount();
      await tester.pump();
      expect(find.byType(SelectableText), findsNothing);
      expect(find.text('Copy CSV'), findsNothing);
      expect(find.text('The account changed.'), findsOneWidget);
      await tester.tap(find.text('Close'));
      api.answer(1, dashboard(30, name: 'New account'));
      await tester.pumpAndSettle();
      expect(api.requests, [30, 30]);
      expect(find.text('Old account'), findsNothing);
      expect(find.text('New account'), findsOneWidget);
    },
  );
}
