import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/main.dart' show HomeShell;
import 'package:coupon_go/qbot_offers.dart';
import 'package:coupon_go/screens/bargain.dart';
import 'package:coupon_go/screens/customer.dart';
import 'package:coupon_go/screens/packages.dart';
import 'package:coupon_go/screens/qbot.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _BotApi extends ApiClient {
  Object? offers;
  final reads = <String>[];
  final posts = <String>[];

  @override
  Future<Map<String, dynamic>> home({double? lat, double? lng}) async => {};

  @override
  Future<List<dynamic>> packages({int? shopId}) async => [];
  Completer<Map<String, dynamic>>? waitingCoupon;

  @override
  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    posts.add(path);
    expect(path, '/qbot/chat');
    return {
      'answer': 'Here are three offers.',
      if (offers != null) 'offers': offers,
    };
  }

  @override
  Future<Map<String, dynamic>> couponDetails(int id) async {
    reads.add('coupon:$id');
    return waitingCoupon?.future ??
        {
          'coupon': {'id': id, 'name': 'Tea coupon', 'shop_name': 'Central Tea'}
        };
  }

  @override
  Future<Map<String, dynamic>> packageDetails(int id) async {
    reads.add('package:$id');
    return {'package_id': id, 'name': 'Lunch package', 'shop_name': 'Bistro'};
  }

  @override
  Future<List<dynamic>> bargainCampaigns() async {
    reads.add('campaigns');
    return [
      {
        'id': 68,
        'name': 'Same title',
        'status': 'active',
        'stock_left': 2,
        'expires_at': '2099-01-01T00:00:00Z'
      },
      {
        'id': 69,
        'name': 'Same title',
        'status': 'active',
        'stock_left': 2,
        'expires_at': '2099-01-01T00:00:00Z'
      },
      {
        'id': 70,
        'name': 'Expired event',
        'status': 'active',
        'stock_left': 2,
        'expires_at': '2000-01-01T00:00:00Z'
      },
    ];
  }
}

Map<String, Object> _offer(String kind, int id, String name) => {
      'type': kind,
      'id': id,
      'name': name,
      'shop': 'Shop for $id',
    };
Finder _tile(String kind, int id) =>
    find.byKey(ValueKey('qbot-offer-$kind-$id'));

Future<void> _ask(WidgetTester tester, _BotApi api) async {
  await tester.pumpWidget(LanguageScope(
      language: AppLanguage()..english = true,
      child: MaterialApp(home: QBotScreen(api: api))));
  await tester.enterText(find.byType(TextField), 'What can I browse?');
  await tester.tap(find.byTooltip('Send'));
  await tester.pumpAndSettle();
  await tester.tap(find.text('Agree and send'));
  await tester.pumpAndSettle();
}

void main() {
  test('only bounded typed entries are accepted, deduplicated by type and id',
      () {
    final offers = QBotOffer.parse([
      _offer('coupon', 1, 'A'),
      _offer('coupon', 1, 'duplicate'),
      _offer('package', 1, 'same category ID'),
      _offer('unknown', 2, 'bad'),
      {..._offer('bargain', 2, 'bad'), 'id': '2'},
      {'type': 'coupon', 'id': 3, 'name': 'no shop'},
      {},
      4,
      _offer('bargain', 5, 'beyond upper bound'),
    ]);
    expect(offers.map((row) => row.kind),
        [QBotOfferKind.coupon, QBotOfferKind.package]);
    expect(QBotOffer.parse(null), isEmpty);
    expect(QBotOffer.parse([]), isEmpty);
  });

  testWidgets(
      'three cards navigate using exact types and IDs without buying or starting',
      (tester) async {
    final api = _BotApi()
      ..offers = [
        _offer('coupon', 11, 'Tea coupon'),
        _offer('package', 42, 'Lunch package'),
        _offer('bargain', 69, 'Same title'),
      ];
    await _ask(tester, api);
    Future<void> open(String kind, int id) async {
      await tester.ensureVisible(_tile(kind, id));
      await tester.tap(_tile(kind, id));
      await tester.pumpAndSettle();
    }

    Future<void> close() async {
      tester.state<NavigatorState>(find.byType(Navigator)).pop();
      await tester.pumpAndSettle();
    }

    await open('coupon', 11);
    expect(
        tester
            .widget<CouponDetailScreen>(find.byType(CouponDetailScreen))
            .coupon
            .id,
        11);
    await close();
    await open('package', 42);
    expect(
        tester
            .widget<PackageDetailScreen>(find.byType(PackageDetailScreen))
            .packageId,
        42);
    await close();
    await open('bargain', 69);
    expect(
        tester
            .widget<BargainCampaignScreen>(find.byType(BargainCampaignScreen))
            .campaign
            .id,
        69);
    expect(api.reads, ['coupon:11', 'package:42', 'campaigns']);
    expect(api.posts, ['/qbot/chat']);
  });

  testWidgets('browse-only offer detail actions do not open login or send transactions',
      (tester) async {
    final api = _BotApi()..offers = [
      _offer('coupon', 11, 'Tea coupon'),
      _offer('package', 42, 'Lunch package'),
      _offer('bargain', 69, 'Same title'),
    ];
    await _ask(tester, api);
    for (final item in [
      ('coupon', 11, 'Browse only'),
      ('package', 42, 'Browse only'),
      ('bargain', 69, 'Browse only'),
    ]) {
      await tester.ensureVisible(_tile(item.$1, item.$2));
      await tester.tap(_tile(item.$1, item.$2));
      await tester.pumpAndSettle();
      await tester.ensureVisible(find.text(item.$3, skipOffstage: false));
      await tester.pumpAndSettle();
      final button = find.widgetWithText(ElevatedButton, item.$3);
      expect(button, findsOneWidget);
      expect(tester.widget<ElevatedButton>(button).onPressed, isNull);
      expect(api.isLoggedIn, isFalse);
      expect(api.posts, ['/qbot/chat']);
      tester.state<NavigatorState>(find.byType(Navigator)).pop();
      await tester.pumpAndSettle();
    }
  }, skip: !const bool.fromEnvironment('PUBLIC_DEMO'));

  testWidgets('guest home opens Q仔 and reads an offer without a login wall',
      (tester) async {
    SharedPreferences.setMockInitialValues({'home_qbot_hint_seen_v1': true});
    final api = _BotApi()..offers = [_offer('coupon', 11, 'Tea coupon')];
    expect(api.isLoggedIn, isFalse);
    await tester.pumpWidget(LanguageScope(
      language: AppLanguage()..english = true,
      child: MaterialApp(
        home: HomeShell(
            api: api,
            authed: false,
            vendorMode: false,
            onAuthed: () => fail('must not log in'),
            onLogout: () {},
            onToggleMode: () {}),
        routes: {
          '/qbot': (_) => QBotScreen(api: api),
          '/login': (_) => throw StateError('must not open login'),
        },
      ),
    ));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 400));
    if (const bool.fromEnvironment('PUBLIC_DEMO')) {
      expect(find.text('The shops and offers below are sample data for browsing only.'), findsOneWidget);
    }
    await tester.tap(find.text('Q仔'));
    await tester.pumpAndSettle();
    expect(find.byType(QBotScreen), findsOneWidget);
    if (const bool.fromEnvironment('PUBLIC_DEMO')) {
      expect(find.text('The shops and offers below are sample data for browsing only.'), findsOneWidget);
    }
    await tester.enterText(find.byType(TextField), 'What can I browse?');
    await tester.tap(find.byTooltip('Send'));
    await tester.pumpAndSettle();
    expect(api.posts, isEmpty);
    await tester.tap(find.text('Agree and send'));
    await tester.pumpAndSettle();
    expect(find.text('Here are three offers.'), findsOneWidget);
    await tester.tap(_tile('coupon', 11));
    await tester.pumpAndSettle();
    expect(find.byType(CouponDetailScreen), findsOneWidget);
    expect(api.isLoggedIn, isFalse);
    expect(api.posts, ['/qbot/chat']);
  });

  testWidgets('missing and malformed offers preserve plain reply',
      (tester) async {
    for (final offers in [
      null,
      [],
      [
        {'type': 'coupon', 'name': 'no id'}
      ]
    ]) {
      final api = _BotApi()..offers = offers;
      await _ask(tester, api);
      expect(find.text('Here are three offers.'), findsOneWidget);
      expect(find.byType(Card), findsNothing);
      await tester.pumpWidget(const SizedBox());
    }
  });

  testWidgets('expired bargain does not open or start anything',
      (tester) async {
    final api = _BotApi()..offers = [_offer('bargain', 70, 'Expired event')];
    await _ask(tester, api);
    await tester.tap(_tile('bargain', 70));
    await tester.pumpAndSettle();
    expect(find.byType(BargainCampaignScreen), findsNothing);
    expect(api.posts, ['/qbot/chat']);
  });

  testWidgets('clearing a chat cancels pending card navigation',
      (tester) async {
    final api = _BotApi()
      ..waitingCoupon = Completer<Map<String, dynamic>>()
      ..offers = [_offer('coupon', 11, 'Tea coupon')];
    await _ask(tester, api);
    await tester.tap(_tile('coupon', 11));
    await tester.pump();
    await tester.tap(find.byTooltip('Clear chat'));
    api.waitingCoupon!.complete({
      'coupon': {'id': 11}
    });
    await tester.pumpAndSettle();
    expect(find.byType(CouponDetailScreen), findsNothing);
    expect(find.text('Tea coupon'), findsNothing);
  });
}
