import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/merchant_branches.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

const _trialMode = bool.fromEnvironment('PUBLIC_TRIAL');
const _entry = ValueKey('edit-shop-details');
const _save = ValueKey('save-shop-details');
const _name = ValueKey('edit-shop-name');
const _announcement = ValueKey('edit-shop-announcement');

class _ShopApi extends ApiClient {
  _ShopApi({this.isOwner = true});

  final bool isOwner;
  int revision = 0;
  int? writeError;
  String name = '原店舖';
  String announcement = '';
  final reads = <String>[];
  final writes = <(String, Map<String, dynamic>)>[];

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
    reads.add(path);
    switch (path) {
      case '/merchant/shops':
        return {
          'shops': [
            {'id': 7, 'name': name, 'is_owner': isOwner, 'can_manage_branches': true},
          ],
        };
      case '/merchant/shops/7/branches':
        return {'branches': <Object>[]};
      case '/merchant/shops/7':
        return {
          'shop': {
            'id': 7,
            'shop_name': name,
            'announcement': announcement,
            'address': '合成地址',
            'business_hours': {'mon_fri': '11:00–22:00'},
            'gallery': ['/uploads/merchant-images/fixture.png'],
          },
        };
      default:
        fail('Unexpected GET: $path');
    }
  }

  @override
  Future<Map<String, dynamic>> patch(
    String path, {
    Map<String, dynamic>? body,
  }) async {
    final values = body ?? (throw StateError('PATCH body expected'));
    writes.add((path, Map<String, dynamic>.from(values)));
    final error = writeError;
    if (error != null) throw ApiException(error, 'Synthetic');
    if (values['name'] case final String value) name = value;
    if (values['announcement'] case final String value) announcement = value;
    return {'shop': {'id': 7, 'shop_name': name, 'announcement': announcement}};
  }
}

Future<void> _show(WidgetTester tester, _ShopApi api) async {
  tester.view.devicePixelRatio = 1;
  tester.view.physicalSize = const Size(390, 844);
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
  await tester.pumpWidget(MaterialApp(home: MerchantBranchesScreen(api: api)));
  await tester.pumpAndSettle();
}

Future<void> _open(WidgetTester tester) async {
  await tester.tap(find.byKey(_entry));
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('owner sends only changed text to the exact selected shop', (tester) async {
    final api = _ShopApi();
    await _show(tester, api);
    await _open(tester);
    await tester.enterText(find.byKey(_name), '  新店舖  ');
    await tester.ensureVisible(find.byKey(_announcement));
    await tester.enterText(find.byKey(_announcement), '新公告');
    await tester.tap(find.byKey(_save));
    await tester.pumpAndSettle();

    expect(api.writes, [
      ('/merchant/shops/7', {'name': '新店舖', 'announcement': '新公告'}),
    ]);
    expect(api.reads.where((path) => path == '/merchant/shops').length, 2);
    expect(find.text('新店舖'), findsOneWidget);
  }, skip: _trialMode);

  testWidgets('member with branch management does not gain shop editing', (tester) async {
    final api = _ShopApi(isOwner: false);
    await _show(tester, api);
    expect(find.byKey(_entry), findsNothing);
    expect(api.reads, isNot(contains('/merchant/shops/7')));
    expect(api.writes, isEmpty);
  });

  testWidgets('rejecting validation retains the owner draft', (tester) async {
    final api = _ShopApi()..writeError = 422;
    await _show(tester, api);
    await _open(tester);
    await tester.enterText(find.byKey(_name), '未儲存名稱');
    await tester.tap(find.byKey(_save));
    await tester.pumpAndSettle();

    expect(api.writes, hasLength(1));
    final field = tester.widget<TextFormField>(find.byKey(_name));
    expect(field.controller?.text, '未儲存名稱');
    expect(find.byKey(_save), findsOneWidget);
  }, skip: _trialMode);

  testWidgets('uncertain save requires reload and never auto-retries', (tester) async {
    final api = _ShopApi()..writeError = 503;
    await _show(tester, api);
    await _open(tester);
    await tester.enterText(find.byKey(_name), '未確認名稱');
    await tester.tap(find.byKey(_save));
    await tester.pumpAndSettle();

    expect(api.writes, hasLength(1));
    expect(find.byKey(_save), findsNothing);
    final entry = tester.widget<OutlinedButton>(find.byKey(_entry));
    expect(entry.onPressed, isNull);
    expect(api.name, '原店舖');
  }, skip: _trialMode);

  testWidgets('session transition dismisses editor and cannot submit stale text', (tester) async {
    final api = _ShopApi();
    await _show(tester, api);
    await _open(tester);
    await tester.enterText(find.byKey(_name), '舊工作階段資料');
    api.changeSession();
    await tester.pumpAndSettle();

    expect(find.byKey(_save), findsNothing);
    expect(api.writes, isEmpty);
  }, skip: _trialMode);

  testWidgets('trial does not expose the shop editor', (tester) async {
    final api = _ShopApi();
    await _show(tester, api);
    expect(find.byKey(_entry), findsNothing);
    expect(api.writes, isEmpty);
  }, skip: !_trialMode);
}
