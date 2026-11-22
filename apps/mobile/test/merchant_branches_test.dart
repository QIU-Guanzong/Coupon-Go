import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/merchant_branches.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class BranchApi extends ApiClient {
  bool canManage = true;
  bool failSave = false;
  int revision = 0;
  final calls = <Map<String, dynamic>>[];
  final branches = <Map<String, dynamic>>[
    {'id': 11, 'name': '中環店', 'address': '德輔道', 'phone': '12345678'},
  ];
  @override
  int get sessionRevision => revision;
  void changeSession() {
    revision++;
    notifyListeners();
  }

  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, String>? query,
  }) async {
    if (path == '/merchant/shops') {
      return {
        'shops': [
          {
            'id': 1,
            'name': '測試商戶',
            'can_manage_branches': canManage,
            'can_manage_members': canManage,
          },
        ],
      };
    }
    if (path == '/merchant/shops/1/branches') return {'branches': branches};
    throw ApiException(404, '找不到');
  }

  @override
  Future<Map<String, dynamic>> post(
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
  }) async {
    calls.add({'path': path, 'body': body, 'query': query});
    if (failSave) throw ApiException(409, '無法儲存');
    if (body != null) branches.add({'id': 12, ...body});
    if (query != null) branches[0] = {'id': 11, ...query};
    return {'message': '完成'};
  }
}

void main() {
  testWidgets('建立分店前校驗名稱，成功後重新讀取', (tester) async {
    final api = BranchApi();
    await tester.pumpWidget(
      MaterialApp(home: MerchantBranchesScreen(api: api)),
    );
    await tester.pumpAndSettle();
    expect(find.text('中環店'), findsOneWidget);
    await tester.tap(find.text('新增分店'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('儲存'));
    await tester.pumpAndSettle();
    expect(find.text('請輸入分店名稱'), findsOneWidget);
    await tester.enterText(find.byType(TextFormField).first, '灣仔店');
    await tester.tap(find.text('儲存'));
    await tester.pumpAndSettle();
    expect(api.calls.single['body']['name'], '灣仔店');
    expect(find.text('灣仔店'), findsOneWidget);
    expect(find.text('分店已建立'), findsOneWidget);
  });

  testWidgets('修改資料發往真實接口，失敗時保留伺服器內容', (tester) async {
    final api = BranchApi()..failSave = true;
    await tester.pumpWidget(
      MaterialApp(home: MerchantBranchesScreen(api: api)),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('編輯'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField).first, '新名稱');
    await tester.tap(find.text('儲存'));
    await tester.pumpAndSettle();
    expect(api.calls.single['path'], '/update-branch-profile');
    expect(api.calls.single['query']['branch_id'], '11');
    expect(find.textContaining('無法儲存'), findsOneWidget);
    expect(find.text('中環店'), findsOneWidget);
  });

  testWidgets('店主從選中的店舖打開員工頁', (tester) async {
    final api = BranchApi();
    await tester.pumpWidget(MaterialApp(
        home: MerchantBranchesScreen(
            api: api, staffBuilder: (id) => Scaffold(body: Text('員工店舖 $id')))));
    await tester.pumpAndSettle();
    await tester.ensureVisible(find.text('員工與角色'));
    await tester.tap(find.text('員工與角色'));
    await tester.pumpAndSettle();
    expect(find.text('員工店舖 1'), findsOneWidget);
  });

  testWidgets('只有查看權限時不展示新增或編輯', (tester) async {
    final api = BranchApi()..canManage = false;
    await tester.pumpWidget(
      MaterialApp(
        home: MerchantBranchesScreen(
          api: api,
          staffBuilder: (_) => const Text('員工頁'),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('中環店'), findsOneWidget);
    expect(find.text('新增分店'), findsNothing);
    expect(find.text('編輯'), findsNothing);
    expect(find.text('員工與角色'), findsNothing);
  });

  testWidgets('編輯期間切換帳號不提交上一帳號的操作', (tester) async {
    final api = BranchApi();
    await tester.pumpWidget(
      MaterialApp(home: MerchantBranchesScreen(api: api)),
    );
    await tester.pumpAndSettle();
    await tester.tap(find.text('新增分店'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextFormField).first, '灣仔店');
    api.changeSession();
    await tester.pumpAndSettle();
    await tester.tap(find.text('儲存'));
    await tester.pumpAndSettle();
    expect(api.calls, isEmpty);
  });
}
