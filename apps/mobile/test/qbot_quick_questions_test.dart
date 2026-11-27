import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

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
  final compare = find.byKey(const ValueKey('qbot-compare-packages'));
  final people = find.byKey(const ValueKey('qbot-compare-people'));
  final budget = find.byKey(const ValueKey('qbot-compare-budget'));
  final submit = find.byKey(const ValueKey('qbot-compare-submit'));
  final composer = find.widgetWithText(TextField, '問 Q仔…');
  Future<_QbotApi> open(WidgetTester tester,
      {bool english = false, AppLanguage? appLanguage}) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(390, 844);
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    final language = appLanguage ?? (AppLanguage()..english = english);
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
    expect(find.textContaining('自由問答會把你的問題及最近最多 6 則對話傳送給 DeepSeek。'), findsOneWidget);
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
    expect(find.byType(ActionChip), findsOneWidget);
    expect(compare, findsOneWidget);
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

  testWidgets('two diners and HKD200 check catalog with no history and preserve draft',
      (tester) async {
    final api = await open(tester);
    await tester.enterText(composer, '晚餐草稿');
    await tester.pump();
    await tester.tap(compare);
    await tester.pumpAndSettle();
    expect(tester.widget<TextFormField>(people).controller!.text, '2');
    expect(find.text(r'整份預算（HK$，可留空）'), findsWidgets);
    await tester.enterText(budget, '200');
    await tester.tap(submit);
    await tester.pumpAndSettle();
    expect(api.sent, isEmpty);
    expect(find.textContaining('不會把對話傳送給 DeepSeek'), findsOneWidget);
    await tester.tap(find.text('同意並送出'));
    await tester.pumpAndSettle();
    expect(api.sent.single, {
      'message': r'比較2人套餐，總預算HK$200',
      'language': 'zh-Hant',
      'history': [],
    });
    expect(tester.widget<TextField>(composer).controller!.text, '晚餐草稿');

    // Acceptance for the catalog lookup must not opt in to sending free text.
    await tester.tap(find.byTooltip('送出'));
    await tester.pumpAndSettle();
    expect(api.sent, hasLength(1));
    expect(find.textContaining('自由問答會把你的問題'), findsOneWidget);
    await tester.tap(find.text('同意並送出'));
    await tester.pumpAndSettle();
    expect(api.sent.last['message'], '晚餐草稿');
  });

  testWidgets('editable party size, blank budget and no match in English',
      (tester) async {
    final api = await open(tester, english: true);
    api.answer = 'No available package matches. Try another party size or total budget.';
    expect(find.text('Compare packages'), findsOneWidget);
    await tester.tap(compare);
    await tester.pumpAndSettle();
    expect(find.text(r'Total budget (HK$, optional)'), findsWidgets);
    await tester.enterText(people, '5');
    await tester.tap(submit);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Agree and send'));
    await tester.pumpAndSettle();
    expect(api.sent.single, {
      'message': 'Compare packages for 5',
      'language': 'en',
      'history': [],
    });
    expect(find.text(api.answer), findsOneWidget);
    expect(find.byType(Card), findsNothing);
    expect(compare, findsOneWidget);
  });

  testWidgets('invalid numbers and cancelling either dialog never send or clear draft',
      (tester) async {
    final api = await open(tester);
    await tester.enterText(composer, '保留草稿');
    await tester.pump();
    await tester.tap(compare);
    await tester.pumpAndSettle();
    await tester.enterText(people, '0');
    await tester.enterText(budget, '20.123');
    await tester.tap(submit);
    await tester.pumpAndSettle();
    expect(find.text('請輸入 1 至 99 人。'), findsOneWidget);
    expect(find.text('請輸入港元金額，最多兩位小數。'), findsOneWidget);
    expect(api.sent, isEmpty);
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(tester.widget<TextField>(composer).controller!.text, '保留草稿');
    await tester.tap(compare);
    await tester.pumpAndSettle();
    await tester.tap(submit);
    await tester.pumpAndSettle();
    await tester.tap(find.text('取消'));
    await tester.pumpAndSettle();
    expect(api.sent, isEmpty);
    expect(tester.widget<TextField>(composer).controller!.text, '保留草稿');
  });

  testWidgets('language changes while dialog is open keep fields and submit localized fixed format',
      (tester) async {
    SharedPreferences.setMockInitialValues({});
    final language = AppLanguage();
    final api = await open(tester, appLanguage: language);
    await tester.tap(compare);
    await tester.pumpAndSettle();
    await tester.enterText(people, '3');
    await tester.enterText(budget, '200.50');
    await language.commit(true);
    await tester.pumpAndSettle();
    expect(find.text(r'Total budget (HK$, optional)'), findsWidgets);
    expect(tester.widget<TextFormField>(people).controller!.text, '3');
    await tester.tap(submit);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Agree and send'));
    await tester.pumpAndSettle();
    expect(api.sent.single['message'],
        r'Compare packages for 3, total budget HK$200.50');
    expect(api.sent.single['language'], 'en');
  });

  testWidgets('retry for lookup stays on the catalog path and keeps draft',
      (tester) async {
    final api = await open(tester, english: true);
    api.nextError = 429;
    await tester.enterText(find.byType(TextField), 'Dinner draft');
    await tester.pump();
    await tester.tap(compare);
    await tester.pumpAndSettle();
    await tester.tap(submit);
    await tester.pumpAndSettle();
    await tester.tap(find.text('Agree and send'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(api.sent, hasLength(2));
    expect(api.sent.first, api.sent.last);
    expect(api.sent.last['history'], isEmpty);
    expect(tester.widget<TextField>(find.byType(TextField)).controller!.text,
        'Dinner draft');
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
