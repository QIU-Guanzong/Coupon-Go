import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import '../lib/api_client.dart';
import '../lib/language.dart';
import '../lib/screens/qbot.dart';

class _QbotApi extends ApiClient {
  final sent = <Map<String, dynamic>>[];

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
  }) async {
    expect(path, '/qbot/chat');
    sent.add(Map<String, dynamic>.of(body!));
    return {'answer': 'Test reply'};
  }
}

void main() {
  Future<_QbotApi> open(WidgetTester tester, {bool english = false}) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(390, 844);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    final language = AppLanguage()..english = english;
    final api = _QbotApi();
    addTearDown(language.dispose);
    addTearDown(api.dispose);
    await tester.pumpWidget(LanguageScope(
      language: language,
      child: MaterialApp(home: QBotScreen(api: api)),
    ));
    return api;
  }

  testWidgets('quick questions require consent before their first send',
      (tester) async {
    final api = await open(tester);
    expect(find.text('附近有咩優惠'), findsOneWidget);
    expect(find.text('我有幾多積分'), findsOneWidget);
    expect(find.text('點樣用券'), findsOneWidget);
    await tester.tap(find.text('附近有咩優惠'));
    await tester.pumpAndSettle();
    expect(api.sent, isEmpty);
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(api.sent, isEmpty);
    await tester.tap(find.text('我有幾多積分'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('同意並送出'));
    await tester.pumpAndSettle();
    expect(api.sent.single['message'], '我有幾多積分');
    expect(api.sent.single['language'], 'zh-Hant');
    expect(find.text('Test reply'), findsOneWidget);
    expect(find.byType(ActionChip), findsNothing);
  });

  testWidgets('English typed send uses the same first-send consent',
      (tester) async {
    final api = await open(tester, english: true);
    expect(find.text('How do I use a coupon?'), findsOneWidget);
    await tester.enterText(find.byType(TextField), 'Any offers?');
    await tester.tap(find.byTooltip('Send'));
    await tester.pumpAndSettle();
    expect(api.sent, isEmpty);
    await tester.tap(find.text('Agree and send'));
    await tester.pumpAndSettle();
    expect(api.sent.single['message'], 'Any offers?');
    expect(api.sent.single['language'], 'en');
  });
}
