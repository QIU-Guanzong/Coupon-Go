import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/merchant_staff.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class StaffApi extends ApiClient {
  int revision = 0;
  bool accepted = false;
  final calls = <Map<String, dynamic>>[];
  @override
  int get sessionRevision => revision;
  void changeSession() {
    revision++;
    notifyListeners();
  }

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    if (path == '/merchant/shop-invitations') {
      return {
        'invitations': accepted
            ? []
            : [
                {'id': 5, 'shop_id': 1, 'shop_name': '測試店', 'role': 'cashier'}
              ]
      };
    }
    if (path == '/admin/shops/1/members') {
      return {
        'members': [
          {
            'id': 5,
            'user_id': 9,
            'name': '阿明',
            'email': 'staff@example.com',
            'role': 'cashier',
            'status': 'active'
          }
        ]
      };
    }
    throw ApiException(404, '找不到記錄');
  }

  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    calls.add({'path': path, ...?body});
    if (path.endsWith('/confirm')) accepted = true;
    return {'message': '已完成'};
  }

  @override
  Future<Map<String, dynamic>> patch(String path,
      {Map<String, dynamic>? body}) async {
    calls.add({'path': path, ...?body});
    return {'message': '已更新'};
  }

  @override
  Future<Map<String, dynamic>> delete(String path,
      {Map<String, String>? query}) async {
    calls.add({'path': path});
    return {'message': '已移除'};
  }
}

void main() {
  testWidgets('店主使用電郵與角色發送邀請', (tester) async {
    final api = StaffApi();
    await tester.pumpWidget(
        MaterialApp(home: MerchantStaffScreen(api: api, shopId: 1)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('邀請店員'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField), 'staff@example.com');
    await tester.tap(find.text('發送邀請'));
    await tester.pumpAndSettle();
    expect(api.calls.single['email'], 'staff@example.com');
    expect(api.calls.single['role'], 'cashier');
  });

  testWidgets('邀請人可查看並確認加入', (tester) async {
    final api = StaffApi();
    await tester.pumpWidget(MaterialApp(home: ShopInvitationsScreen(api: api)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('查看邀請'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('確認加入'));
    await tester.pumpAndSettle();
    expect(api.calls.single['path'], '/admin/shops/1/members/5/confirm');
    expect(find.text('暫無待確認邀請'), findsOneWidget);
  });

  testWidgets('店主可更改角色並確認移除', (tester) async {
    final api = StaffApi();
    await tester.pumpWidget(
        MaterialApp(home: MerchantStaffScreen(api: api, shopId: 1)));
    await tester.pumpAndSettle();
    await tester.tap(find.byType(PopupMenuButton<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('更改角色'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('經理'));
    await tester.pumpAndSettle();
    expect(api.calls.single['role'], 'manager');
    await tester.tap(find.byType(PopupMenuButton<String>));
    await tester.pumpAndSettle();
    await tester.tap(find.text('移除店員'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('移除'));
    await tester.pumpAndSettle();
    expect(api.calls.last['path'], '/admin/shops/1/members/9');
  });

  testWidgets('確認前切換帳號不送出舊邀請', (tester) async {
    final api = StaffApi();
    await tester.pumpWidget(MaterialApp(home: ShopInvitationsScreen(api: api)));
    await tester.pumpAndSettle();
    await tester.tap(find.text('查看邀請'));
    await tester.pumpAndSettle();
    api.changeSession();
    await tester.tap(find.text('確認加入'));
    await tester.pumpAndSettle();
    expect(api.calls, isEmpty);
    expect(find.textContaining('登入狀態已變更'), findsOneWidget);
  });
}
