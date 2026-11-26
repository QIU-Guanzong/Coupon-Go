import 'dart:async';
import 'dart:convert';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:http/testing.dart';

const _endpoint = '/api/v3/vendor/redemptions/export';

String _ymd(DateTime date) =>
    '${date.year}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}';
String _mdy(DateTime date) => '${date.month}/${date.day}/${date.year}';

http.Response _json(String label) => http.Response(
    jsonEncode({
      'records': [
        {'coupon_name': label, 'shop_name': 'Fixture shop', 'revoked': true}
      ]
    }),
    200,
    headers: {'content-type': 'application/json; charset=utf-8'});

http.Response _csv(String label) =>
    http.Response.bytes(utf8.encode('coupon_name\r\n$label\r\n'), 200,
        headers: {'content-type': 'text/csv'});

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

Future<void> _open(WidgetTester tester, ApiClient api, bool en) async {
  await tester.runAsync(_loadFonts);
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(390, 844);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  final language = AppLanguage()..english = en;
  addTearDown(language.dispose);
  await tester.pumpWidget(LanguageScope(
    language: language,
    child: MaterialApp(
      theme: CgTheme.light(),
      routes: {
        '/vendor/records': (_) => RedemptionRecordsScreen(api: api),
      },
      home: Builder(
        builder: (context) => Scaffold(
          body: TextButton(
            onPressed: () => Navigator.of(context).pushNamed('/vendor/records'),
            child: const Text('Open records'),
          ),
        ),
      ),
    ),
  ));
  await tester.tap(find.text('Open records'));
  await tester.pumpAndSettle();
}

Future<void> _pick(WidgetTester tester, bool from, DateTime date) async {
  await tester.tap(find.byKey(ValueKey(
    from ? 'vendor-date-start' : 'vendor-date-end',
  )));
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 400));
  final locale =
      MaterialLocalizations.of(tester.element(find.byType(DatePickerDialog)));
  await tester.tap(find.byTooltip(locale.inputDateModeButtonLabel));
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 400));
  await tester.enterText(
    find.descendant(
        of: find.byType(DatePickerDialog), matching: find.byType(TextField)),
    _mdy(date),
  );
  await tester.tap(find.text('OK'));
  await tester.pump();
}

void main() {
  for (final en in [true, false]) {
    testWidgets('records and CSV share date window at 390px, english=$en',
        (tester) async {
      final api = ApiClient(baseUrl: 'https://example.test');
      addTearDown(api.dispose);
      final csvLabel = en ? 'Export CSV' : '匯出 CSV';
      final start = DateTime.now().subtract(const Duration(days: 6));
      final end = DateTime.now().subtract(const Duration(days: 2));
      final queries = <Map<String, String>>[];
      final paths = <String>[];
      final methods = <String>[];
      final client = MockClient((request) async {
        paths.add(request.url.path);
        methods.add(request.method);
        final q = request.url.queryParameters;
        queries.add(q);
        final label = q['from'] == _ymd(start) && q['to'] == _ymd(end)
            ? '門市午餐'
            : 'Other window';
        return q['format'] == 'csv' ? _csv(label) : _json(label);
      });
      await http.runWithClient(() async {
        await _open(tester, api, en);
        await _pick(tester, true, start);
        await tester.pumpAndSettle();
        await _pick(tester, false, end);
        await tester.pumpAndSettle();
        expect(find.text('門市午餐'), findsOneWidget);
        await tester.tap(find.byTooltip(csvLabel));
        await tester.pumpAndSettle();
        expect(find.textContaining('門市午餐', findRichText: true), findsWidgets);
        expect(
            find.text(en
                ? '${_ymd(start)} to ${_ymd(end)}'
                : '${_ymd(start)} 至 ${_ymd(end)}'),
            findsOneWidget);
        await tester.tap(find.text(en ? 'Close' : '關閉'));
        await tester.pumpAndSettle();
        await tester.tap(find.byKey(const ValueKey('vendor-date-clear')));
        await tester.pumpAndSettle();
        expect(find.text('Other window'), findsOneWidget);
      }, () => client);
      expect(queries, [
        {},
        {'from': _ymd(start)},
        {'from': _ymd(start), 'to': _ymd(end)},
        {'from': _ymd(start), 'to': _ymd(end), 'format': 'csv'},
        {},
      ]);
      expect(paths, everyElement(_endpoint));
      expect(methods, everyElement('GET'));
      expect(tester.takeException(), isNull);
    });
  }

  testWidgets(
      'new date beats late list and CSV; session change drops pending CSV',
      (tester) async {
    final api = ApiClient(baseUrl: 'https://example.test');
    addTearDown(api.dispose);
    final start = DateTime.now().subtract(const Duration(days: 6));
    final end = DateTime.now().subtract(const Duration(days: 2));
    final lateList = Completer<http.Response>();
    final staleExport = Completer<http.Response>();
    final expiredExport = Completer<http.Response>();
    final queries = <Map<String, String>>[];
    var emptyRequests = 0;
    final client = MockClient((request) async {
      final q = request.url.queryParameters;
      queries.add(q);
      if (q['format'] == 'csv') {
        return q.containsKey('from')
            ? expiredExport.future
            : staleExport.future;
      }
      if (q.isEmpty) {
        emptyRequests++;
        return _json('Current unfiltered list');
      }
      if (!q.containsKey('to')) return lateList.future;
      return _json('Current narrowed list');
    });
    final button = find.byWidgetPredicate(
        (widget) => widget is IconButton && widget.tooltip == 'Export CSV');
    await http.runWithClient(() async {
      await _open(tester, api, true);
      await tester.tap(button);
      await tester.pump();
      expect(tester.widget<IconButton>(button).onPressed, isNull);
      await _pick(tester, true, start);
      // Date still selectable while first date request is being fetched.
      await tester.pump(const Duration(milliseconds: 300));
      expect(tester.widget<IconButton>(button).onPressed, isNull);
      await _pick(tester, false, end);
      await tester.pumpAndSettle();
      expect(find.text('Current narrowed list'), findsOneWidget);
      lateList.complete(_json('Old first date'));
      staleExport.complete(_csv('Old unfiltered export'));
      await tester.pumpAndSettle();
      expect(find.text('Old first date'), findsNothing);
      expect(find.byType(AlertDialog), findsNothing);
      await tester.tap(button);
      await tester.pump();
      api.activateTrialToken('synthetic-new-session');
      expiredExport.complete(_csv('Old account export'));
      await tester.pumpAndSettle();
      expect(find.text('Reopen redemption history.'), findsOneWidget);
      expect(find.text('Current narrowed list'), findsNothing);
      expect(find.byType(AlertDialog), findsNothing);
    }, () => client);
    expect(emptyRequests, 1);
    expect(queries.where((x) => x['format'] == 'csv').toList(), [
      {'format': 'csv'},
      {'format': 'csv', 'from': _ymd(start), 'to': _ymd(end)},
    ]);
  });

  testWidgets('open one-sided preview closes on account switch',
      (tester) async {
    final api = ApiClient(baseUrl: 'https://example.test');
    addTearDown(api.dispose);
    final end = DateTime.now().subtract(const Duration(days: 2));
    final rangeRequests = <Map<String, String>>[];
    final client = MockClient((request) async {
      final q = request.url.queryParameters;
      rangeRequests.add(q);
      return q['format'] == 'csv'
          ? _csv('selected CSV rows')
          : _json('selected records');
    });
    await http.runWithClient(() async {
      await _open(tester, api, true);
      await _pick(tester, false, end);
      await tester.pumpAndSettle();
      await tester.tap(find.byTooltip('Export CSV'));
      await tester.pumpAndSettle();
      expect(find.text('Through ${_ymd(end)}'), findsOneWidget);
      expect(find.byType(AlertDialog), findsOneWidget);
      api.activateTrialToken('synthetic-new-session');
      await tester.pumpAndSettle();
      expect(find.byType(AlertDialog), findsNothing);
      expect(find.text('Reopen redemption history.'), findsOneWidget);
      expect(find.text('selected records'), findsNothing);
    }, () => client);
    expect(rangeRequests, [
      {},
      {'to': _ymd(end)},
      {'to': _ymd(end), 'format': 'csv'},
    ]);
  });

  test('inverted dates are rejected without issuing JSON or CSV GET', () async {
    final api = ApiClient(baseUrl: 'https://example.test');
    addTearDown(api.dispose);
    var calls = 0;
    final client = MockClient((_) async {
      calls++;
      return _json('Unexpected');
    });
    await http.runWithClient(() async {
      await expectLater(
          api.vendorRedemptions(
              from: DateTime(2026, 11, 2), to: DateTime(2026, 11, 1)),
          throwsArgumentError);
      await expectLater(
          api.vendorRedemptionsCsv(
              from: DateTime(2026, 11, 2), to: DateTime(2026, 11, 1)),
          throwsArgumentError);
    }, () => client);
    expect(calls, 0);
  });
}
