import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/merchant_onboarding.dart';
import 'package:coupon_go/widgets/merchant_layout.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class OnboardingApi extends ApiClient {
  OnboardingApi({
    this.existing = false,
    this.failLoad = false,
    this.source = 'simulated',
  });
  String source;
  bool existing;
  bool failLoad;
  bool signed = false;
  Map<String, dynamic>? submission;
  int? signedVersion;
  bool connectSimulated = true;
  int syncCalls = 0;

  @override
  Future<Map<String, dynamic>> startApplicationConnect(int id) async => {
        'onboarding_url': connectSimulated
            ? 'https://accounts.stripe.test/mock'
            : 'https://connect.stripe.com/test',
        'simulated': connectSimulated,
      };

  @override
  Future<Map<String, dynamic>> syncApplicationConnect(int id) async {
    syncCalls++;
    return {
      'onboarding_status': 'active',
      'charges_enabled': true,
      'payouts_enabled': true,
      'transfers_enabled': true,
      'simulated': connectSimulated,
    };
  }

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
                'stripe_status_source': source,
                'contract': signed ? {'version': 'v2'} : null,
              },
            ]
          : [],
    };
  }

  @override
  Future<Map<String, dynamic>> submitMerchantApplication(
    Map<String, dynamic> body,
  ) async {
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
    int id,
    int versionId,
  ) async {
    signedVersion = versionId;
    signed = true;
    return {'message': 'signed'};
  }
}

void main() {
  group('production application flow', () {
    testWidgets('申請使用香港欄位並顯示服務端待審進度', (tester) async {
      final api = OnboardingApi();
      await tester.pumpWidget(
        MaterialApp(home: MerchantOnboardingScreen(api: api)),
      );
      await tester.pumpAndSettle();
      await tester.enterText(find.byType(TextFormField).first, '測試店舖');
      await tester.enterText(find.byType(TextFormField).last, '12345678');
      await tester.ensureVisible(find.text('提交申請'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('提交申請'));
      await tester.pumpAndSettle();
      expect(api.submission?['shop_name'], '測試店舖');
      expect(api.submission?['br_number'], '12345678');
      expect(find.text('測試店舖'), findsOneWidget);
      expect(find.widgetWithText(MerchantStatusPill, '待審核'), findsOneWidget);
      expect(find.text('收款帳戶：未開通正式收款'), findsOneWidget);
    });

    testWidgets('測試合約明確標示並須同意後簽署指定版本', (tester) async {
      final api = OnboardingApi(existing: true);
      await tester.pumpWidget(
        MaterialApp(home: MerchantOnboardingScreen(api: api)),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('查看當前合約'));
      await tester.pumpAndSettle();
      expect(find.text('合約內容尚待確認，未生效。'), findsOneWidget);
      await tester.dragUntilVisible(find.text('簽署此版本'),
          find.byType(Scrollable).first, const Offset(0, -300));
      await tester.pumpAndSettle();
      final before = tester.widget<FilledButton>(
        find.widgetWithText(FilledButton, '簽署此版本'),
      );
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
      await tester.pumpWidget(
        MaterialApp(home: MerchantOnboardingScreen(api: api)),
      );
      await tester.pumpAndSettle();
      expect(find.text('進度暫時不可用'), findsOneWidget);
      expect(find.text('提交申請'), findsNothing);
      expect(find.text('刷新申請進度'), findsOneWidget);
    });
    testWidgets('測試及未知收款來源不顯示為未開始或正式開戶', (tester) async {
      final api = OnboardingApi(existing: true, source: 'stripe_test');
      await tester.pumpWidget(
        MaterialApp(home: MerchantOnboardingScreen(api: api)),
      );
      await tester.pumpAndSettle();
      expect(find.text('收款帳戶：未開通正式收款'), findsOneWidget);
      api.source = 'future_state';
      await tester.tap(find.text('刷新進度'));
      await tester.pumpAndSettle();
      expect(find.text('收款帳戶：狀態待確認'), findsOneWidget);
    });
    testWidgets('模擬開戶不打開虛構網頁，同步狀態清楚標示', (tester) async {
      final api = OnboardingApi(existing: true, source: 'not_started');
      var opened = false;
      await tester.pumpWidget(
        MaterialApp(
          home: MerchantOnboardingScreen(
            api: api,
            openConnectUrl: (_) async {
              opened = true;
              return true;
            },
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('開啟收款設定'));
      await tester.pumpAndSettle();
      expect(opened, isFalse);
      expect(find.text('收款帳戶：未開通正式收款'), findsOneWidget);
      expect(find.text('未有可開啟的正式收款頁面。'), findsOneWidget);
      await tester.tap(find.text('刷新收款狀態'));
      await tester.pumpAndSettle();
      expect(find.text('已完成收款設定，正式收款尚未開通。'), findsOneWidget);
    });

    testWidgets('Stripe 測試頁返回後同步實際能力', (tester) async {
      final api = OnboardingApi(existing: true)..connectSimulated = false;
      Uri? opened;
      await tester.pumpWidget(
        MaterialApp(
          home: MerchantOnboardingScreen(
            api: api,
            openConnectUrl: (uri) async {
              opened = uri;
              return true;
            },
          ),
        ),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.text('開啟收款設定'));
      await tester.pumpAndSettle();
      expect(opened?.host, 'connect.stripe.com');
      tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
      await tester.pumpAndSettle();
      expect(api.syncCalls, 1);
      expect(find.text('已完成收款設定，正式收款尚未開通。'), findsOneWidget);
    });
  }, skip: const bool.fromEnvironment('PUBLIC_TRIAL'));
}
