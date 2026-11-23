import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/qbot.dart';

class _QbotApi extends ApiClient {
  final sent = <Map<String, dynamic>>[];
  int? nextError;
  bool loggedIn = false;
  String answer = 'Test reply';

  @override
  bool get isLoggedIn => loggedIn;

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
  }) async {
    expect(path, '/qbot/chat');
    sent.add(Map<String, dynamic>.of(body!));
    final status = nextError;
    nextError = null;
    if (status != null) throw ApiException(status, 'Test error');
    return {'answer': answer};
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
    expect(find.text('有咩優惠'), findsOneWidget);
    expect(find.text('點樣領取優惠券'), findsOneWidget);
    expect(find.text('我有幾多積分'), findsNothing);
    expect(find.text('點樣用券'), findsOneWidget);
    await tester.tap(find.text('有咩優惠'));
    await tester.pumpAndSettle();
    expect(api.sent, isEmpty);
    expect(find.text('你的問題及最近最多 6 則對話會傳送給 DeepSeek 以產生回覆。請勿輸入密碼、驗證碼、兌換碼或其他個人資料。'), findsOneWidget);
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(api.sent, isEmpty);
    await tester.tap(find.text('點樣領取優惠券'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('同意並送出'));
    await tester.pumpAndSettle();
    expect(api.sent.single['message'], '點樣領取優惠券');
    expect(api.sent.single['language'], 'zh-Hant');
    expect(find.text('Test reply'), findsOneWidget);
    expect(find.byType(ActionChip), findsNothing);
  });

  testWidgets('English typed send uses the same first-send consent',
      (tester) async {
    final api = await open(tester, english: true);
    expect(find.text('How do I use a coupon?'), findsOneWidget);
    await tester.enterText(find.byType(TextField), 'Any offers?');
    await tester.pump();
    await tester.tap(find.byTooltip('Send'));
    await tester.pumpAndSettle();
    expect(api.sent, isEmpty);
    await tester.tap(find.text('Agree and send'));
    await tester.pumpAndSettle();
    expect(api.sent.single['message'], 'Any offers?');
    expect(api.sent.single['language'], 'en');
  });

  testWidgets('rate limit offers retry without sending duplicate history', (tester) async {
    final api = await open(tester);
    api.nextError = 429;
    await tester.enterText(find.byType(TextField), '找咖啡券');
    await tester.pump();
    await tester.tap(find.byTooltip('送出'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('同意並送出'));
    await tester.pumpAndSettle();
    expect(find.text('提問太頻密，請稍後再試。'), findsOneWidget);
    await tester.tap(find.text('重試'));
    await tester.pumpAndSettle();
    expect(api.sent.length, 2);
    expect(api.sent.last['history'], isEmpty);
    expect(find.text('找咖啡券'), findsOneWidget);
    expect(find.text('Test reply'), findsOneWidget);
  });

  testWidgets('invalid input restores the draft for editing', (tester) async {
    final api = await open(tester);
    api.nextError = 422;
    await tester.enterText(find.byType(TextField), '找咖啡券');
    await tester.pump();
    await tester.tap(find.byTooltip('送出'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('同意並送出'));
    await tester.pumpAndSettle();
    expect(find.text('問題無法處理，請修改後再送出。'), findsOneWidget);
    expect(find.text('重試'), findsNothing);
    expect(tester.widget<TextField>(find.byType(TextField)).controller!.text, '找咖啡券');
    await tester.enterText(find.byType(TextField), '咖啡優惠');
    await tester.pump();
    await tester.tap(find.byTooltip('送出'));
    await tester.pumpAndSettle();
    expect(api.sent.last['history'], isEmpty);
    expect(find.text('Test reply'), findsOneWidget);
  });

  test('formats common bullets and bold without losing plain or unfinished text', () {
    const raw = '一般文字\n- **商戶**：咖啡店\n* 另一項\n1. 有序項目\n未完 **標記';
    final result = formatQBotAnswer(raw);
    expect(result.toPlainText(), '一般文字\n• 商戶：咖啡店\n• 另一項\n1. 有序項目\n未完 **標記');
    expect(result.children!.whereType<TextSpan>()
        .singleWhere((span) => span.text == '商戶').style!.fontWeight, FontWeight.bold);
    expect(formatQBotAnswer('普通文字 * 與 **未完').toPlainText(), '普通文字 * 與 **未完');
  });

  testWidgets('formatted answer is selectable while history retains original Markdown', (tester) async {
    final semantics = tester.ensureSemantics();
    final api = await open(tester);
    api.answer = '- **商戶**：咖啡店';
    await tester.enterText(find.byType(TextField), '推薦');
    await tester.pump();
    await tester.tap(find.byTooltip('送出'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('同意並送出'));
    await tester.pumpAndSettle();
    final answerFinder = find.byWidgetPredicate((widget) =>
        widget is SelectableText && widget.textSpan?.toPlainText() == '• 商戶：咖啡店');
    final answer = tester.widget<SelectableText>(answerFinder);
    expect(answer.textSpan!.toPlainText(), '• 商戶：咖啡店');
    final semanticData = tester.getSemantics(answerFinder).getSemanticsData();
    expect('${semanticData.label}${semanticData.value}', contains('商戶'));
    await tester.enterText(find.byType(TextField), '更多');
    await tester.pump();
    await tester.tap(find.byTooltip('送出'));
    await tester.pumpAndSettle();
    expect(api.sent.last['history'], [
      {'role': 'user', 'text': '推薦'},
      {'role': 'assistant', 'text': '- **商戶**：咖啡店'},
    ]);
    semantics.dispose();
  });
}
