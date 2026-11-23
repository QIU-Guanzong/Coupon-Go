import 'package:coupon_go/feature_flags.dart';
import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/models.dart';
import 'package:coupon_go/screens/account.dart';
import 'package:coupon_go/screens/customer.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _DetailApi extends ApiClient {
  bool fail = true;
  int reads = 0;
  @override
  Future<Map<String, dynamic>> shopDetails(int id) async {
    reads++;
    if (fail) throw ApiException(503, 'Unavailable');
    return {
      'shop': {'id': id, 'shop_name': 'Tea House', 'phone': '+852 2345 6789'},
      'coupons': [],
      'branches': [
        {'branch_name': 'Central branch', 'address': '皇后大道中 7 號'},
        {'branch_name': 'Address not listed'},
      ],
    };
  }
}

Widget _app(Widget home) => LanguageScope(
      language: AppLanguage()..english = true,
      child: MaterialApp(home: home, routes: {
        '/guide': (_) => const Scaffold(body: Text('Guide screen')),
      }),
    );

void main() {
  testWidgets('shop error retries, dial and map launch only valid links',
      (tester) async {
    final api = _DetailApi();
    final uris = <Uri>[];
    await tester.pumpWidget(_app(ShopDetailScreen(
      api: api,
      shop: Shop.fromJson({'id': 7, 'shop_name': 'Tea House'}),
      onOpenCoupon: (_) {},
      launchExternal: (uri) async {
        uris.add(uri);
        return true;
      },
    )));
    await tester.pumpAndSettle();
    expect(find.text('Could not load store details'), findsOneWidget);
    api.fail = false;
    await tester.tap(find.text('Retry'));
    await tester.pumpAndSettle();
    expect(api.reads, 2);
    await tester.tap(find.text('+852 2345 6789'));
    await tester.scrollUntilVisible(find.text('Central branch'), 100);
    await tester.tap(find.text('Central branch'));
    await tester.scrollUntilVisible(find.text('Address not listed'), 100);
    await tester.tap(find.text('Address not listed'));
    await tester.pumpAndSettle();
    if (publicPreview) {
      expect(uris, isEmpty);
    } else {
      expect(uris, hasLength(2));
      expect(uris.first, Uri.parse('tel:+85223456789'));
      expect(uris.last.host, 'www.google.com');
      expect(uris.last.queryParameters['query'], '皇后大道中 7 號');
    }
  });

  testWidgets('guest can open guide and about but cannot log out or delete',
      (tester) async {
    await tester
        .pumpWidget(_app(SettingsScreen(api: ApiClient(), onLogout: () {})));
    await tester.pumpAndSettle();
    expect(find.text('Log out'), findsNothing);
    expect(find.text('Delete account'), findsNothing);
    expect(find.text('刪除帳戶'), findsNothing);
    expect(find.text('Terms of service'), findsNothing);
    expect(find.text('Privacy policy'), findsNothing);
    await tester.tap(find.text('Guide and FAQs'));
    await tester.pumpAndSettle();
    expect(find.text('Guide screen'), findsOneWidget);
    final state = tester.state<NavigatorState>(find.byType(Navigator));
    state.pop();
    await tester.pumpAndSettle();
    await tester.tap(find.text('About Coupon GO!'));
    await tester.pumpAndSettle();
    expect(find.text('Open source licenses'), findsOneWidget);
  });
}
