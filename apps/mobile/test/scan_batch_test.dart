import 'dart:async';
import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/screens/scan_batch.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

class BatchApi extends ApiClient {
  BatchApi() : super(baseUrl: 'https://example.test');
  final keys = <String>[];
  List<int?>? branches;
  @override
  Future<Map<String, dynamic>> prepareScan(String code, {int? branchId}) async {
    if (code == '111111' && branchId == null) {
      return {
        'can_redeem': false,
        'branch_selection_required': true,
        'allowed_branches': [
          {'id': 7, 'name': '中環店', 'address': ''}
        ]
      };
    }
    return {
      'can_redeem': true,
      'coupon_name': code,
      'prepare_token': 'token-$code'
    };
  }

  @override
  Future<Map<String, dynamic>> scanBatch(List<String> codes,
      List<String> tokens, List<int?> branchIds, String requestId) async {
    keys.add(requestId);
    branches = branchIds;
    if (keys.length == 1) throw TimeoutException('timeout');
    return {'total': codes.length};
  }
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));
  testWidgets('批量逐券預檢，含受限分店，超時沿用原批次編號', (tester) async {
    final api = BatchApi();
    await tester.pumpWidget(MaterialApp(home: ScanBatchScreen(api: api)));
    await tester.enterText(find.byType(TextField), '111111');
    await tester.tap(find.text('預檢加入'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('中環店'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), '222222');
    await tester.tap(find.text('預檢加入'));
    await tester.pumpAndSettle();
    expect(find.text('已預檢 2 / 20'), findsOneWidget);
    await tester.tap(find.text('確認批量核銷'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('確認核銷'));
    await tester.pumpAndSettle();
    expect(api.branches, [7, null]);
    expect(find.text('使用原請求重試'), findsOneWidget);
    await tester.tap(find.text('使用原請求重試'));
    await tester.pumpAndSettle();
    expect(api.keys, hasLength(2));
    expect(api.keys.first, api.keys.last);
    expect(find.text('已完成 2 張券核銷'), findsOneWidget);
  });
  testWidgets('確認批量時切換帳號不提交先前的券', (tester) async {
    final api = BatchApi();
    await api.saveToken('first', persist: false);
    await tester.pumpWidget(MaterialApp(home: ScanBatchScreen(api: api)));
    await tester.enterText(find.byType(TextField), '222222');
    await tester.tap(find.text('預檢加入'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('確認批量核銷'));
    await tester.pumpAndSettle();
    await api.saveToken('second', persist: false);
    await tester.pump();
    await tester.tap(find.text('確認核銷'));
    await tester.pumpAndSettle();
    expect(api.keys, isEmpty);
    expect(find.textContaining('登入狀態已變更'), findsOneWidget);
  });

  testWidgets(
      '320px larger English text can prepare and confirm with the keyboard',
      (tester) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(320, 640);
    tester.platformDispatcher.textScaleFactorTestValue = 1.3;
    addTearDown(tester.view.resetDevicePixelRatio);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.platformDispatcher.clearTextScaleFactorTestValue);
    final api = BatchApi();
    final language = AppLanguage()..english = true;
    await tester.pumpWidget(LanguageScope(
      language: language,
      child: MaterialApp(home: ScanBatchScreen(api: api)),
    ));
    await tester.enterText(find.byType(TextField), '222222');
    await tester.testTextInput.receiveAction(TextInputAction.done);
    await tester.pumpAndSettle();
    expect(find.text('Checked 1 / 20'), findsOneWidget);
    await tester.tap(find.text('Confirm batch redemption'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Confirm redemption'));
    await tester.pumpAndSettle();
    expect(find.text('Retry original request'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
