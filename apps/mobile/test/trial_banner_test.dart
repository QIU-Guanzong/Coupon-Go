import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/trial_banner.dart';

class FakeTrialApi extends ApiClient {
  final roles = <String>[];
  bool fail = false;
  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    if (fail) throw Exception('unavailable');
    roles.add(body!['role'] as String);
    return {'access_token': 'example-token'};
  }
}

void main() {
  testWidgets('trial label remains visible and role switch handles errors', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final api = FakeTrialApi();
    final selected = <String>[];
    await tester.pumpWidget(MaterialApp(
      builder: (_, child) => Column(children: [
        TrialBanner(api: api, onRoleChanged: selected.add),
        Expanded(child: child!),
      ]),
      home: const Scaffold(),
    ));
    expect(find.text('示例數據・模擬付款｜資料會重置'), findsOneWidget);
    await tester.tap(find.text('切換身份 ▾'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('示例顧客'));
    await tester.pumpAndSettle();
    expect(api.roles, ['customer']);
    expect(selected, ['customer']);
    expect(api.isLoggedIn, isTrue);
    api.fail = true;
    await tester.tap(find.text('切換身份 ▾'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('示例商戶'));
    await tester.pumpAndSettle();
    expect(find.text('切換失敗，請重試'), findsOneWidget);
    expect(selected, ['customer']);
  });

  testWidgets('identity controls fit a narrow screen', (tester) async {
    SharedPreferences.setMockInitialValues({});
    await tester.binding.setSurfaceSize(const Size(320, 640));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(MaterialApp(
      builder: (_, child) => Column(children: [
        TrialBanner(api: FakeTrialApi(), onRoleChanged: (_) {}),
        Expanded(child: child!),
      ]),
      home: const Scaffold(),
    ));
    await tester.tap(find.text('切換身份 ▾'));
    await tester.pumpAndSettle();
    expect(find.text('示例商戶'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
