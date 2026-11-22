import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/merchant_onboarding.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class OnboardingApi extends ApiClient {
  OnboardingApi({this.existing = false, this.failLoad = false});
  bool existing;
  bool failLoad;
  bool signed = false;
  Map<String, dynamic>? submission;
  int? signedVersion;

  @override
  Future<Map<String, dynamic>> myMerchantApplications() async {
    if (failLoad) throw ApiException(503, '進度暫時不可用');
    return {
      'applications': existing
          ? [
              {
                'id': 8,
                'shop_name': '測試店舖',
                'status': 'pending',
                'stripe_status_source': 'simulated',
                'contract': signed ? {'version': 'v2'} : null,
              }
            ]
          : []
    };
  }

  @override
  Future<Map<String, dynamic>> submitMerchantApplication(
      Map<String, dynamic> body) async {
    submission = body;
    existing = true;
    return {'message': 'submitted'};
  }

  @override
  Future<Map<String, dynamic>> currentMerchantContract() async => {
        'id': 42,
        'title': '測試合約',
        'version': 'v2',
        'body_text': '【待法務確認】測試內容',
        'test_only': true,
      };

  @override
  Future<Map<String, dynamic>> signMerchantContract(
      int id, int versionId) async {
    signedVersion = versionId;
    signed = true;
    return {'message': 'signed'};
  }
}

void main() {
  testWidgets('申請使用香港欄位並顯示服務端待審進度', (tester) async {
    final api = OnboardingApi();
    await tester
        .pumpWidget(MaterialApp(home: MerchantOnboardingScreen(api: api)));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField).first, '測試店舖');
    await tester.enterText(find.byType(TextFormField).last, '12345678');
    await tester.ensureVisible(find.text('提交申請'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('提交申請'));
    await tester.pumpAndSettle();
    expect(api.submission?['shop_name'], '測試店舖');
    expect(api.submission?['br_number'], '12345678');
    expect(find.text('測試店舖 · 待審核'), findsOneWidget);
    expect(find.text('收款帳戶：測試模擬狀態，並非正式開戶'), findsOneWidget);
  });

  testWidgets('測試合約明確標示並須同意後簽署指定版本', (tester) async {
    final api = OnboardingApi(existing: true);
    await tester
        .pumpWidget(MaterialApp(home: MerchantOnboardingScreen(api: api)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('查看當前合約'));
    await tester.pumpAndSettle();
    expect(find.text('測試合約，內容尚待確認，並非正式合約。'), findsOneWidget);
    final before =
        tester.widget<FilledButton>(find.widgetWithText(FilledButton, '簽署此版本'));
    expect(before.onPressed, isNull);
    await tester.ensureVisible(find.byType(CheckboxListTile));
    await tester.pumpAndSettle();
    await tester.tap(find.byType(CheckboxListTile));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('簽署此版本'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('簽署此版本'));
    await tester.pumpAndSettle();
    expect(api.signedVersion, 42);
    expect(find.text('合約：已簽署版本 v2'), findsOneWidget);
    expect(find.text('下載已簽合約 PDF'), findsOneWidget);
  });

  testWidgets('讀取進度失敗時不提供重複申請', (tester) async {
    final api = OnboardingApi(failLoad: true);
    await tester
        .pumpWidget(MaterialApp(home: MerchantOnboardingScreen(api: api)));
    await tester.pumpAndSettle();
    expect(find.text('進度暫時不可用'), findsOneWidget);
    expect(find.text('提交申請'), findsNothing);
    expect(find.text('刷新申請進度'), findsOneWidget);
  });
}
