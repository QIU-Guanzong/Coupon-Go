import 'dart:async';

import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/main.dart';
import 'package:coupon_go/screens/customer.dart';
import 'package:coupon_go/theme.dart';
import 'package:coupon_go/widgets/favorite_control.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class _FavoriteApi extends ApiClient {
  bool couponFavorite = false;
  bool shopFavorite = true;
  Completer<bool>? mutation;
  int writes = 0;
  int favoriteReads = 0;

  @override
  bool get isLoggedIn => true;

  @override
  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    if (path == '/favorite-coupon-list') {
      favoriteReads++;
      return {
        'coupons': [
          if (couponFavorite)
            {
              'id': 7,
              'coupon_name': 'Tea offer',
              'shop_name': 'Tea House',
              'is_coupon_favorite': true,
            },
        ],
      };
    }
    if (path == '/get-collection-shops') {
      return {
        'shops': [
          if (shopFavorite)
            {'id': 4, 'shop_name': 'Tea House', 'is_shop_favorite': true},
        ],
      };
    }
    if (path == '/shop-details') {
      return {'coupons': <dynamic>[], 'branches': <dynamic>[]};
    }
    throw StateError(path);
  }

  @override
  Future<bool> toggleFavoriteCoupon(int id) async {
    expect(id, 7);
    writes++;
    return couponFavorite = await mutation!.future;
  }

  @override
  Future<bool> toggleFavoriteShop(int id) async {
    expect(id, 4);
    writes++;
    return shopFavorite = !shopFavorite;
  }
}

void main() {
  testWidgets(
      'shared heart updates optimistically; duplicate and failed writes are safe',
      (tester) async {
    final api = _FavoriteApi()..mutation = Completer<bool>();
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(
        appBar: AppBar(actions: [
          FavoriteControl(api: api, id: 7),
          FavoriteControl(api: api, id: 7),
        ]),
      ),
    ));
    await tester.pumpAndSettle();
    final add = find.byIcon(Icons.favorite_border);
    expect(add, findsNWidgets(2));
    await tester.tap(add.first);
    await tester.pump();
    final saved = find.byIcon(Icons.favorite);
    expect(saved, findsNWidgets(2));
    await tester.tap(saved.last);
    expect(api.writes, 1);
    api.mutation!.completeError(ApiException(503, 'Unavailable'));
    await tester.pumpAndSettle();
    expect(add, findsNWidgets(2));
    expect(find.text('未能更新收藏，請重試'), findsOneWidget);

    api.mutation = Completer<bool>();
    await tester.tap(add.first);
    await tester.pump();
    api.mutation!.complete(true);
    await tester.pumpAndSettle();
    expect(saved, findsNWidgets(2));
    expect(api.writes, 2);
  });

  testWidgets('saved store opens detail and removing it refreshes the list',
      (tester) async {
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final api = _FavoriteApi();
    await tester.pumpWidget(LanguageScope(
      language: AppLanguage()..english = true,
      child: MaterialApp(
        theme: CgTheme.light(),
        home: Builder(builder: (context) {
          return FavoritesScreen(
            api: api,
            onOpenCoupon: (_) async {},
            onOpenShop: (shop) => Navigator.of(context).push<void>(
              MaterialPageRoute(
                builder: (_) => ShopDetailScreen(
                    api: api, shop: shop, onOpenCoupon: (_) {}),
              ),
            ),
          );
        }),
      ),
    ));
    await tester.pumpAndSettle();
    expect(find.text('Favorites'), findsOneWidget);
    await tester.tap(find.text('Tea House'));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('Remove from favorites'));
    await tester.pumpAndSettle();
    expect(find.byTooltip('Add to favorites'), findsOneWidget);
    final initialReads = api.favoriteReads;
    await tester.pageBack();
    await tester.pumpAndSettle();
    expect(api.favoriteReads, initialReads + 1);
    expect(find.text('No saved stores yet.'), findsOneWidget);
  });
}
