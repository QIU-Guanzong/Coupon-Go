import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/main.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

class FakeApi extends ApiClient {
  FakeApi({this.role});
  final String? role;
  Completer<Map<String, dynamic>>? pendingShops;
  @override
  bool get isLoggedIn => role != null;
  @override
  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) async {
    if (path == '/me') return {'role': role};
    if (path == '/merchant/shops') return pendingShops?.future ?? {'shops': [{'can_redeem': true, 'is_owner': true}]};
    return {};
  }
  @override
  Future<Map<String, dynamic>> wallet({int? status, int page = 1}) async => {'items': [], 'total_pages': 1};
  @override
  Future<List<dynamic>> bargainCampaigns() async => [];
  @override
  Future<List<dynamic>> vendorCoupons() async => [];
}

Future<void> showShell(WidgetTester tester, FakeApi api, {bool vendor = false}) async {
  SharedPreferences.setMockInitialValues({});
  await tester.pumpWidget(LanguageScope(
    language: AppLanguage()..english = true,
    child: MaterialApp(home: HomeShell(
      api: api, authed: api.isLoggedIn, vendorMode: vendor,
      onAuthed: () {}, onLogout: () {}, onToggleMode: () {},
    )),
  ));
  await tester.pump();
  await tester.pump(const Duration(seconds: 1));
}

int currentIndex(WidgetTester tester) => tester.widget<BottomNavigationBar>(find.byType(BottomNavigationBar).first).currentIndex;

void main() {
  testWidgets('consumer back returns the selected tab to home only once', (tester) async {
    final api = FakeApi();
    await showShell(tester, api);
    await tester.tap(find.text('Q Wallet').last); await tester.pump();
    expect(currentIndex(tester), 4);
    await tester.tap(find.text('Collect').last); await tester.pump();
    expect(currentIndex(tester), 1);
    expect(await tester.binding.handlePopRoute(), true); await tester.pump(const Duration(milliseconds: 500));
    expect(currentIndex(tester), 0);
    expect(Navigator.of(tester.element(find.byType(HomeShell))).canPop(), false);
    await tester.pumpWidget(const SizedBox()); api.dispose();
  });
  testWidgets('an inner route and drawer close before returning the tab home', (tester) async {
    final api = FakeApi();
    await showShell(tester, api);
    await tester.tap(find.text('Q Wallet').last); await tester.pump();
    final nav = Navigator.of(tester.element(find.byType(HomeShell)));
    nav.push(MaterialPageRoute<void>(builder: (_) => const Scaffold(body: Text('Inner detail'))));
    await tester.pump(const Duration(milliseconds: 500));
    expect(await tester.binding.handlePopRoute(), true); await tester.pump(const Duration(milliseconds: 500));
    expect(find.text('Inner detail'), findsNothing); expect(currentIndex(tester), 4);
    tester.state<ScaffoldState>(find.byType(Scaffold).first).openDrawer();
    await tester.pump(const Duration(milliseconds: 500));
    expect(await tester.binding.handlePopRoute(), true); await tester.pump(const Duration(milliseconds: 500));
    expect(currentIndex(tester), 4);
    expect(await tester.binding.handlePopRoute(), true); await tester.pump(const Duration(milliseconds: 500));
    expect(currentIndex(tester), 0);
    await tester.pumpWidget(const SizedBox()); api.dispose();
  });
  testWidgets('merchant tab back returns to merchant home', (tester) async {
    final api = FakeApi(role: 'vendor');
    await showShell(tester, api, vendor: true);
    await tester.tap(find.text('Account').last); await tester.pump();
    expect(currentIndex(tester), 3);
    expect(await tester.binding.handlePopRoute(), true); await tester.pump(const Duration(milliseconds: 500));
    expect(currentIndex(tester), 0);
    await tester.pumpWidget(const SizedBox()); api.dispose();
  });
  testWidgets('role changes clear or recreate tab history for the visible mode', (tester) async {
    SharedPreferences.setMockInitialValues({});
    final api = FakeApi(role: 'vendor');
    var vendor = false;
    late StateSetter setParent;
    await tester.pumpWidget(LanguageScope(
      language: AppLanguage()..english = true,
      child: MaterialApp(home: StatefulBuilder(builder: (context, setState) {
        setParent = setState;
        return HomeShell(api: api, authed: true, vendorMode: vendor,
          onAuthed: () {}, onLogout: () {}, onToggleMode: () {});
      })),
    ));
    await tester.pump(); await tester.pump(const Duration(seconds: 1));
    await tester.tap(find.text('Q Wallet').last); await tester.pump();
    expect(currentIndex(tester), 4);
    setParent(() => vendor = true); await tester.pump(); await tester.pump();
    expect(currentIndex(tester), 0);
    expect(Navigator.of(tester.element(find.byType(HomeShell))).canPop(), false);
    await tester.tap(find.text('Account').last); await tester.pump();
    expect(currentIndex(tester), 3);
    setParent(() => vendor = false); await tester.pump(); await tester.pump();
    expect(currentIndex(tester), 4);
    expect(await tester.binding.handlePopRoute(), true); await tester.pump(const Duration(milliseconds: 500));
    expect(currentIndex(tester), 0);
    await tester.pumpWidget(const SizedBox()); api.dispose();
  });
  testWidgets('back cancels an in-flight merchant tab selection', (tester) async {
    final api = FakeApi(role: 'vendor');
    await showShell(tester, api, vendor: true);
    await tester.tap(find.text('Account').last); await tester.pump();
    api.pendingShops = Completer<Map<String, dynamic>>();
    await tester.tap(find.text('Scan').last); await tester.pump();
    expect(currentIndex(tester), 3);
    expect(await tester.binding.handlePopRoute(), true); await tester.pump(const Duration(milliseconds: 500));
    expect(currentIndex(tester), 0);
    api.pendingShops!.complete({'shops': [{'can_redeem': true, 'is_owner': true}]});
    await tester.pump(); await tester.pump();
    expect(currentIndex(tester), 0);
    await tester.pumpWidget(const SizedBox()); api.dispose();
  });

}
