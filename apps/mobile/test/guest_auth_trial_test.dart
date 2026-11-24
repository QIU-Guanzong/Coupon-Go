import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/guest_auth.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

const _trial = bool.fromEnvironment('PUBLIC_TRIAL');

class _FakeApi extends ApiClient {
  final calls = <(String, Map<String, dynamic>?)>[];
  Completer<Map<String, dynamic>> response = Completer();

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
  }) {
    calls.add((path, body));
    return response.future;
  }
}

Future<void> _open(
  WidgetTester tester,
  _FakeApi api,
  VoidCallback onAuthed, {
  bool merchant = true,
  bool english = true,
}) async {
  final language = AppLanguage()..english = english;
  addTearDown(language.dispose);
  addTearDown(api.dispose);
  await tester.pumpWidget(
    LanguageScope(
      language: language,
      child: MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) {
              return TextButton(
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute<void>(
                    builder: (_) => AuthScreen(
                      api: api,
                      vendor: merchant,
                      onAuthed: onAuthed,
                    ),
                  ),
                ),
                child: const Text('Open'),
              );
            },
          ),
        ),
      ),
    ),
  );
  await tester.tap(find.text('Open'));
  await tester.pumpAndSettle();
}

Map<String, dynamic> _roleReply(String role) => {
  'access_token': 'trial-token',
  'role': role,
  'mode': 'simulated',
};

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));

  testWidgets('English merchant needs no credentials and switches only once', (
    tester,
  ) async {
    final api = _FakeApi();
    var entered = 0;
    await _open(tester, api, () => entered++);
    expect(find.text('Choose an identity'), findsOneWidget);
    expect(find.text('Continue as a merchant'), findsOneWidget);
    expect(find.text('Payments do not charge you.'), findsOneWidget);
    expect(find.textContaining('personal details'), findsNothing);
    expect(find.byType(TextField), findsNothing);
    await tester.tap(find.text('Continue'));
    await tester.pump();
    final button = tester.widget<FilledButton>(find.byType(FilledButton));
    expect(button.onPressed, isNull);
    expect(api.calls.single.$1, '/trial/role');
    expect(api.calls.single.$2, {'role': 'vendor'});
    api.response.complete(_roleReply('vendor'));
    await tester.pumpAndSettle();
    expect(api.paymentAccessToken, 'trial-token');
    expect(entered, 1);
  }, skip: !_trial);

  testWidgets('dismissed trial entry cannot replace the active role token', (
    tester,
  ) async {
    final api = _FakeApi();
    await api.saveToken('previous-role');
    await _open(tester, api, () => fail('dismissed role was entered'));
    await tester.tap(find.byType(FilledButton));
    await tester.pump();
    final route = ModalRoute.of(tester.element(find.byType(AuthScreen)))!;
    route.navigator!.pop();
    // Complete before exit animation/disposal, when mounted may still be true.
    api.response.complete(_roleReply('vendor'));
    await tester.pumpAndSettle();
    expect(api.paymentAccessToken, 'previous-role');
    final persisted = _FakeApi();
    addTearDown(persisted.dispose);
    await persisted.loadToken();
    expect(persisted.paymentAccessToken, 'previous-role');
  }, skip: !_trial);

  testWidgets(
    'Chinese customer route explains an expired session and rejects a mismatched role',
    (tester) async {
      final api = _FakeApi();
      await _open(
        tester,
        api,
        () => fail('unexpected authentication'),
        merchant: false,
        english: false,
      );
      expect(find.text('以顧客身份繼續'), findsOneWidget);
      expect(find.text('付款不會扣款'), findsOneWidget);
      expect(find.textContaining('個人資料'), findsNothing);
      await tester.tap(find.text('繼續'));
      await tester.pump();
      expect(api.calls.single.$2, {'role': 'customer'});
      api.response.completeError(ApiException(401, 'not the displayed copy'));
      await tester.pumpAndSettle();
      expect(find.text('請重新載入此頁，再選擇身份。'), findsOneWidget);
      expect(api.paymentAccessToken, isNull);
      api.response = Completer();
      await tester.tap(find.text('繼續'));
      await tester.pump();
      api.response.complete(_roleReply('vendor'));
      await tester.pumpAndSettle();
      expect(find.text('未能切換身份，請重試。'), findsOneWidget);
      expect(api.paymentAccessToken, isNull);
    },
    skip: !_trial,
  );

  testWidgets('normal merchant login still rejects blank credentials', (
    tester,
  ) async {
    final api = _FakeApi();
    await _open(tester, api, () => fail('blank login was accepted'));
    expect(find.byType(TextField), findsNWidgets(2));
    await tester.tap(find.text('Login'));
    await tester.pumpAndSettle();
    expect(find.text('Please complete the required fields'), findsOneWidget);
    expect(api.calls, isEmpty);
    expect(api.paymentAccessToken, isNull);
  }, skip: _trial);
}
