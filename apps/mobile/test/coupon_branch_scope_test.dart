import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/coupon_branch_scope.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

class ScopeApi extends ApiClient {
  ScopeApi() : super(baseUrl: 'https://example.test');
  Map<String, dynamic>? previewBody;
  Map<String, dynamic>? createBody;
  Map<String, dynamic>? assigned;
  bool rejectAssignment = false;
  @override
  Future<Map<String, dynamic>> vendorPackageWorkspace() async => {
        'shops': [
          {'id': 2, 'name': '其他商店'},
          {'id': 9, 'name': '目標商店'}
        ]
      };
  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    if (path == '/merchant/shops') {
      return {'shops': [
      {'id': 2, 'name': '其他商店', 'can_manage_coupons': true},
      {'id': 9, 'name': '目標商店', 'can_manage_coupons': true},
      {'id': 10, 'name': '無權商店', 'can_manage_coupons': false},
    ]};
    }
    if (path == '/coupon-details-new') {
      return {
        'coupon': {
          'shop_id': 9,
          'branch_ids': [90]
        }
      };
    }
    return {
      'branches': [
        {'id': path.contains('/9/') ? 90 : 20, 'name': '測試分店', 'address': '測試街'}
      ]
    };
  }

  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    assigned = body;
    if (rejectAssignment) throw ApiException(409, '此優惠券已有訂單，不能修改適用分店');
    return {};
  }

  @override
  Future<Map<String, dynamic>> couponPreview(Map<String, dynamic> body) async {
    previewBody = Map.from(body);
    return {'coupon_name': '新券', 'amount_text': '85折', 'total': 100};
  }

  @override
  Future<Map<String, dynamic>> couponCreate(Map<String, dynamic> body) async {
    createBody = Map.from(body);
    return {'message': '已提交'};
  }
}

void main() {
  setUp(() => SharedPreferences.setMockInitialValues({}));
  testWidgets('發券選真實商店與指定分店，預覽與提交一致', (tester) async {
    final api = ScopeApi();
    await tester.pumpWidget(MaterialApp(home: IssueCouponScreen(api: api)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('其他商店'));
    await tester.pumpAndSettle();
    expect(find.text('無權商店'), findsNothing);
    await tester.tap(find.text('目標商店').last);
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).first, '新券');
    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('指定分店'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('指定分店'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('測試分店'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('測試分店'));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('檢查並提交'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('檢查並提交'));
    await tester.pumpAndSettle();
    expect(api.previewBody!['shop_id'], 9);
    expect(api.previewBody!['branch_ids'], [90]);
    expect(find.textContaining('適用：指定 1 間分店'), findsOneWidget);
    await tester.tap(find.text('提交審核'));
    await tester.pumpAndSettle();
    expect(api.createBody!['branch_ids'], [90]);
  });
  testWidgets('已有訂單不可改分店時顯示伺服器原因', (tester) async {
    final api = ScopeApi()..rejectAssignment = true;
    await tester.pumpWidget(MaterialApp(
        home: CouponBranchEditScreen(
            api: api, couponId: 5, shopId: 9, couponName: '已售券')));
    await tester.pumpAndSettle();
    expect(find.text('已有訂單的優惠券不能更改適用分店。'), findsOneWidget);
    await tester.tap(find.text('所有分店'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('保存'));
    await tester.pumpAndSettle();
    expect(api.assigned!['branch_ids'], isEmpty);
    expect(find.text('此優惠券已有訂單，不能修改適用分店'), findsOneWidget);
  });
  testWidgets('切換帳號清除原商戶的編輯資料', (tester) async {
    final api = ScopeApi();
    await api.saveToken('first', persist: false);
    await tester.pumpWidget(MaterialApp(home: CouponBranchEditScreen(
      api: api, couponId: 5, shopId: 9, couponName: '舊帳戶券')));
    await tester.pumpAndSettle();
    await api.saveToken('second', persist: false);
    await tester.pumpAndSettle();
    expect(find.textContaining('登入狀態已變更'), findsOneWidget);
    expect(find.text('保存'), findsNothing);
    expect(api.assigned, isNull);
  });

}
