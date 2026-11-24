import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/guest_auth.dart';
import 'package:coupon_go/screens/trial_banner.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';
// Test the plugin's real persistence boundary without adding an app dependency.
// ignore: depend_on_referenced_packages
import 'package:shared_preferences_platform_interface/shared_preferences_platform_interface.dart';

const _trial = bool.fromEnvironment('PUBLIC_TRIAL');
const _key = 'flutter.coupon_trial_access_token';

class _DelayedStore extends InMemorySharedPreferencesStore {
  _DelayedStore() : super.empty();
  final vendorStarted = Completer<void>();
  final vendorWrite = Completer<void>();

  @override
  Future<bool> setValue(String type, String key, Object value) async {
    if (key == _key && value == 'vendor-token') {
      vendorStarted.complete();
      await vendorWrite.future;
    }
    return super.setValue(type, key, value);
  }
}

class _Api extends ApiClient {
  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    if (path != '/trial/role') throw StateError('Unexpected path: $path');
    final role = body!['role'];
    return {'role': role, 'mode': 'simulated', 'access_token': '$role-token'};
  }
}

void main() {
  for (final switchInMenu in [false, true]) {
    testWidgets(
      switchInMenu
          ? 'menu switch wins while a dismissed direct role is being stored'
          : 'leaving during direct storage preserves the active and persisted role',
      (tester) async {
        SharedPreferences.setMockInitialValues({});
        final store = _DelayedStore();
        SharedPreferencesStorePlatform.instance = store;
        final api = _Api();
        addTearDown(api.dispose);
        await api.saveToken('old-token');
        final activeEvents = <String?>[];
        api.addListener(() => activeEvents.add(api.paymentAccessToken));
        final menuRoles = <String>[];
        final navigator = GlobalKey<NavigatorState>();
        final menu = GlobalKey<TrialBannerState>();
        var directCallbacks = 0;
        await tester.pumpWidget(MaterialApp(
          navigatorKey: navigator,
          builder: (_, child) => Column(children: [
            Expanded(child: child!),
            TrialBanner(
              key: menu,
              api: api,
              onRoleChanging: () async {},
              onRoleChangeFailed: () => fail('menu failed'),
              onRoleChanged: menuRoles.add,
            ),
          ]),
          home: const Scaffold(body: Text('Home')),
        ));
        unawaited(navigator.currentState!.push(MaterialPageRoute<void>(
          builder: (_) => AuthScreen(
              api: api, vendor: true, onAuthed: () => directCallbacks++),
        )));
        await tester.pumpAndSettle();
        await tester.tap(find.byType(FilledButton));
        await tester.pumpAndSettle();
        expect(store.vendorStarted.isCompleted, isTrue);
        navigator.currentState!.pop();
        await tester.pumpAndSettle();
        if (switchInMenu) {
          unawaited(
              menu.currentState!.open(tester.element(find.byType(Scaffold))));
          await tester.pumpAndSettle();
          await tester.tap(find.text('示例顧客'));
          await tester.pumpAndSettle();
        }
        store.vendorWrite.complete();
        await tester.pumpAndSettle();
        final expected = switchInMenu ? 'customer-token' : 'old-token';
        expect(directCallbacks, 0);
        expect(api.paymentAccessToken, expected);
        expect((await store.getAll())[_key], expected);
        expect(activeEvents, switchInMenu ? ['customer-token'] : isEmpty);
        expect(menuRoles, switchInMenu ? ['customer'] : isEmpty);
      },
      skip: !_trial,
    );
  }
}
