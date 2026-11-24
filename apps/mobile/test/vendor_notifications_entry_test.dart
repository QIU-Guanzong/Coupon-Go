import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/main.dart';
import 'package:coupon_go/screens/account.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

const entryKey = Key('vendor-notifications');

class NotificationsFixtureApi extends ApiClient {
  NotificationsFixtureApi(this.role, {this.pendingRole});

  String? role;
  Future<Map<String, dynamic>>? pendingRole;
  int notificationReads = 0;

  @override
  bool get isLoggedIn => role != null;

  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, String>? query,
  }) async {
    if (path == '/me') {
      final pending = pendingRole;
      if (pending != null) return pending;
      return {'role': role};
    }
    if (path == '/merchant/shops') return {'shops': []};
    if (path == '/get-vendor-coupon-list') return {'coupons': []};
    if (path == '/get-notification-list') {
      notificationReads++;
      return {
        'notifications': [
          {'title': 'Merchant application accepted', 'is_read': true},
          {'title': 'Settlement confirmed', 'is_read': true},
          {'title': 'New review', 'is_read': true},
        ],
      };
    }
    return {};
  }

  @override
  Future<Map<String, dynamic>> wallet({int? status, int page = 1}) async =>
      {'items': [], 'total_pages': 1};

  @override
  Future<List<dynamic>> bargainCampaigns() async => [];

  void updateRole(String? newRole) {
    role = newRole;
    notifyListeners();
  }
}

Future<void> showShell(
  WidgetTester tester,
  NotificationsFixtureApi api, {
  bool vendor = true,
  bool english = true,
}) async {
  SharedPreferences.setMockInitialValues({});
  final language = AppLanguage()..english = english;
  addTearDown(language.dispose);
  addTearDown(api.dispose);
  await tester.pumpWidget(LanguageScope(
    language: language,
    child: MaterialApp(
      home: HomeShell(
        api: api,
        authed: api.isLoggedIn,
        vendorMode: vendor,
        onAuthed: () {},
        onLogout: () {},
        onToggleMode: () {},
      ),
      routes: {
        '/notifications': (_) =>
            NotificationsScreen(api: api, guest: !api.isLoggedIn),
      },
    ),
  ));
  await tester.pump();
  await tester.pump(const Duration(seconds: 1));
}

Future<void> openDrawer(WidgetTester tester) async {
  tester.state<ScaffoldState>(find.byType(Scaffold).first).openDrawer();
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('merchant opens existing notifications, one entry and no badge',
      (tester) async {
    final api = NotificationsFixtureApi('vendor');
    await showShell(tester, api);
    await openDrawer(tester);

    final entry = find.byKey(entryKey);
      expect(entry, findsOneWidget);
      final tile = tester.widget<ListTile>(entry);
      expect((tile.title as Text).data, 'Notifications');
      expect(tile.trailing, isNull);
      expect(api.notificationReads, 0);

      await tester.tap(entry);
      await tester.pumpAndSettle();
      expect(find.byType(NotificationsScreen), findsOneWidget);
      expect(find.text('Merchant application accepted'), findsOneWidget);
      expect(find.text('Settlement confirmed'), findsOneWidget);
      expect(find.text('New review'), findsOneWidget);
      expect(api.notificationReads, 1);

      await tester.pageBack();
      await tester.pumpAndSettle();
      expect(find.byType(HomeShell), findsOneWidget);
      expect(find.byType(NotificationsScreen), findsNothing);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('merchant-admin entry uses Traditional Chinese when selected',
      (tester) async {
    final api = NotificationsFixtureApi('merchant-admin');
    await showShell(tester, api, english: false);
    await openDrawer(tester);
    final entry = find.byKey(entryKey);
      expect(entry, findsOneWidget);
      expect((tester.widget<ListTile>(entry).title as Text).data, '通知');
    expect(api.notificationReads, 0);
    await tester.pumpWidget(const SizedBox());
  });

  for (final role in <String?>[null, 'customer', 'admin', 'support']) {
    testWidgets('enterprise drawer hides merchant entry for ${role ?? 'guest'}',
        (tester) async {
      final api = NotificationsFixtureApi(role);
      await showShell(tester, api);
      await openDrawer(tester);
      expect(find.byKey(entryKey), findsNothing);
      expect(api.notificationReads, 0);
      await tester.pumpWidget(const SizedBox());
    });
  }

  testWidgets('entry waits for verification and fails closed on /me failure',
      (tester) async {
    final me = Completer<Map<String, dynamic>>();
    final api = NotificationsFixtureApi('vendor', pendingRole: me.future);
    await showShell(tester, api);
    await openDrawer(tester);
    expect(find.byKey(entryKey), findsNothing);
    me.completeError(StateError('synthetic role failure'));
    await tester.pumpAndSettle();
    expect(find.byKey(entryKey), findsNothing);
    expect(api.notificationReads, 0);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('late merchant role cannot add an entry for a replacement account',
      (tester) async {
    final oldRole = Completer<Map<String, dynamic>>();
    final api = NotificationsFixtureApi('vendor', pendingRole: oldRole.future);
    await showShell(tester, api);
    await openDrawer(tester);
    expect(find.byKey(entryKey), findsNothing);

    api.pendingRole = null;
    api.updateRole('customer');
    await tester.pumpAndSettle();
    oldRole.complete({'role': 'vendor'});
    await tester.pumpAndSettle();

    expect(find.byKey(entryKey), findsNothing);
    expect(api.notificationReads, 0);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('consumer home bell keeps the original notifications route',
      (tester) async {
    final api = NotificationsFixtureApi('customer');
    await showShell(tester, api, vendor: false);
    expect(find.byKey(entryKey), findsNothing);
    await tester.tap(find.byIcon(Icons.notifications_outlined).first);
    await tester.pumpAndSettle();
    expect(find.byType(NotificationsScreen), findsOneWidget);
    expect(api.notificationReads, 1);
    await tester.pumpWidget(const SizedBox());
  });
}
