import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/widgets/merchant_layout.dart';
import 'package:coupon_go/screens/merchant_staff.dart';
import 'package:coupon_go/screens/guest_auth.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const _trialMode = bool.fromEnvironment('PUBLIC_TRIAL');

class StaffApi extends ApiClient {
  String memberName = '阿明';
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
            'name': memberName,
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

class StaffLoginApi extends StaffApi {
  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    if (path == '/login') {
      return {'role': 'merchant-admin', 'access_token': 'test-token'};
    }
    return super.post(path, body: body, query: query);
  }

  @override
  Future<void> saveToken(String token, {bool persist = true}) async {}
}

void main() {
  testWidgets('narrow English staff list uses one email and a status pill',
      (tester) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(390, 844);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final language = AppLanguage()..english = true;
    final api = StaffApi()..memberName = '';
    await tester.pumpWidget(LanguageScope(
      language: language,
      child: MaterialApp(
        builder: (context, child) => MediaQuery(
          data: MediaQuery.of(context).copyWith(
            textScaler: const TextScaler.linear(1.3),
          ),
          child: child!,
        ),
        home: MerchantStaffScreen(api: api, shopId: 1),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('staff@example.com'), findsOneWidget);
    expect(find.text('Cashier'), findsOneWidget);
    expect(find.widgetWithText(MerchantStatusPill, 'Active'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('已接受邀請的店員可從企業登入', (tester) async {
    final api = StaffLoginApi();
    var authed = false;
    await tester.pumpWidget(MaterialApp(
        home:
            AuthScreen(api: api, vendor: true, onAuthed: () => authed = true)));
    await tester.enterText(find.byType(TextField).at(0), 'staff@example.com');
    await tester.enterText(find.byType(TextField).at(1), 'password');
    await tester.tap(find.text('登錄').last);
    await tester.pumpAndSettle();
    expect(authed, isTrue);
  }, skip: _trialMode); // These modes do not offer account login.
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
  }, skip: _trialMode); // Trial does not send invitations.

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
  }, skip: _trialMode); // Trial cannot modify staff roles or membership.

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
