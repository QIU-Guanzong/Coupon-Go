import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/qbot.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _DelayedBot extends ApiClient {
  final response = Completer<Map<String, dynamic>>();
  final sent = <String>[];

  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) {
    sent.add(body!['message'] as String);
    return response.future;
  }
}

void main() {
  testWidgets('send requires nonblank text and keeps a draft while responding',
      (tester) async {
    final api = _DelayedBot();
    await tester.pumpWidget(LanguageScope(
        language: AppLanguage()..english = true,
        child: MaterialApp(home: QBotScreen(api: api))));
    final input = find.byType(TextField);
    IconButton send() => tester.widget<IconButton>(find.byKey(const ValueKey('qbot-send')));
    expect(send().onPressed, isNull);
    await tester.enterText(input, '   ');
    await tester.pump();
    expect(send().onPressed, isNull);
    await tester.enterText(input, 'Which shops?');
    await tester.pump();
    expect(send().onPressed, isNotNull);
    await tester.tap(find.byTooltip('Send'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Agree and send'));
    await tester.pump();
    expect(api.sent, ['Which shops?']);
    await tester.enterText(input, 'Next question');
    await tester.pump();
    expect(send().onPressed, isNull);
    api.response.complete({'answer': 'Here are some shops.'});
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(input).controller!.text, 'Next question');
    expect(send().onPressed, isNotNull);
  });

  testWidgets('a suggested question does not erase a different draft',
      (tester) async {
    final api = _DelayedBot();
    await tester.pumpWidget(LanguageScope(
        language: AppLanguage()..english = true,
        child: MaterialApp(home: QBotScreen(api: api))));
    final input = find.byType(TextField);
    await tester.enterText(input, 'My own question');
    await tester.tap(find.text('What offers are available?'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Agree and send'));
    await tester.pump();
    expect(api.sent, ['What offers are available?']);
    expect(tester.widget<TextField>(input).controller!.text, 'My own question');
    api.response.complete({'answer': 'Here are some offers.'});
    await tester.pumpAndSettle();
  });
}
