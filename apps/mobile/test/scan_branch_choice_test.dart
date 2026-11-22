import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class ScanApi extends ApiClient {
  ScanApi({required this.restricted}) : super(baseUrl: 'https://example.test');
  final bool restricted;
  final List<int?> preflightBranches = [];
  final List<int?> confirmedBranches = [];
  bool rejectBranch = false;

  @override
  Future<Map<String, dynamic>> prepareScan(String code, {int? branchId}) async {
    preflightBranches.add(branchId);
    if (restricted && branchId == null) {
      return {
        'can_redeem': false,
        'branch_selection_required': true,
        'allowed_branches': [
          {'id': 7, 'name': '中環店', 'address': '中環街 1 號'}
        ]
      };
    }
    if (branchId != null && (branchId != 7 || rejectBranch)) {
      return {'can_redeem': false, 'reason': '此分店不適用'};
    }
    return {
      'can_redeem': true,
      'prepare_token': 'test-token',
      'coupon_name': '測試券',
      'shop_name': '測試商戶'
    };
  }

  @override
  Future<Map<String, dynamic>> scanCoupon(String code,
      {String? idempotencyKey, String? prepareToken, int? branchId}) async {
    confirmedBranches.add(branchId);
    return {'coupon_name': '測試券'};
  }
}

Future<void> prepare(WidgetTester tester, ScanApi api) async {
  await tester.pumpWidget(MaterialApp(home: ScanScreen(api: api)));
  await tester.pump();
  await tester.enterText(find.byType(TextField), '123456');
  await tester.testTextInput.receiveAction(TextInputAction.done);
  await tester.pump();
  await tester.pump(const Duration(milliseconds: 300));
}

void main() {
  testWidgets('受限券須選擇分店，確認提交使用相同分店', (tester) async {
    final api = ScanApi(restricted: true);
    await prepare(tester, api);
    expect(find.text('選擇核銷分店'), findsOneWidget);
    expect(api.confirmedBranches, isEmpty);
    await tester.tap(find.text('中環店'));
    await tester.pump();
    expect(api.preflightBranches, [null, 7]);
    expect(find.text('中環店'), findsOneWidget);
    await tester.tap(find.text('確認核銷'));
    await tester.pump();
    expect(api.confirmedBranches, [7]);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('無限制券不需要選擇分店', (tester) async {
    final api = ScanApi(restricted: false);
    await prepare(tester, api);
    expect(find.text('選擇核銷分店'), findsNothing);
    await tester.tap(find.text('確認核銷'));
    await tester.pump();
    expect(api.preflightBranches, [null]);
    expect(api.confirmedBranches, [null]);
    await tester.pumpWidget(const SizedBox());
  });
  testWidgets('分店已失效時顯示原因且不核銷', (tester) async {
    final api = ScanApi(restricted: true)..rejectBranch = true;
    await prepare(tester, api);
    await tester.tap(find.text('中環店'));
    await tester.pump();
    expect(find.text('此分店不適用'), findsOneWidget);
    expect(find.text('確認核銷'), findsNothing);
    expect(api.confirmedBranches, isEmpty);
    await tester.pumpWidget(const SizedBox());
  });
}
