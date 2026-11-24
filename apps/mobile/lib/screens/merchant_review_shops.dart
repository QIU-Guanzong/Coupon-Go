import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import 'reviews_admin.dart';

/// Only the shops already returned by the signed-in merchant roster are
/// eligible. Membership alone does not permit replying to reviews.
class MerchantReviewShopPicker extends StatelessWidget {
  const MerchantReviewShopPicker({
    super.key,
    required this.api,
    required this.shopRoster,
    required this.sessionRevision,
    required this.canAdministerAllShops,
  });

  final ApiClient api;
  final List<Map<String, dynamic>> shopRoster;
  final int sessionRevision;
  final bool canAdministerAllShops;

  static Map<int, String?> allowedShops(
    List<Map<String, dynamic>> roster, {
    required bool canAdministerAllShops,
  }) {
    final shops = <int, String?>{};
    for (final row in roster) {
      final id = row['id'];
      if (id is! int || id <= 0) continue;
      if (!canAdministerAllShops && row['is_owner'] != true) continue;
      final name = row['name'];
      shops.putIfAbsent(id, () => name is String && name.trim().isNotEmpty
          ? name.trim()
          : null);
    }
    return shops;
  }

  @override
  Widget build(BuildContext context) {
    return ListenableBuilder(
      listenable: api,
      builder: (context, _) {
        final current = api.sessionRevision == sessionRevision;
        final shops = current
            ? allowedShops(shopRoster,
                canAdministerAllShops: canAdministerAllShops)
            : const <int, String?>{};
        return Scaffold(
          appBar: AppBar(title: Text(context.tr('選擇門店', 'Choose a shop'))),
          body: shops.isEmpty
              ? Center(
                  child: Text(current
                      ? context.tr('沒有可回覆評價的門店。', 'No shops available for replies.')
                      : context.tr('帳戶已切換，請返回重試。', 'The account changed. Go back and try again.')),
                )
              : ListView(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                  children: [
                    for (final shop in shops.entries)
                      ListTile(
                        key: ValueKey('review-shop-${shop.key}'),
                        title: Text(shop.value ??
                            context.tr('門店 #${shop.key}', 'Shop #${shop.key}')),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () {
                          if (api.sessionRevision != sessionRevision) return;
                          Navigator.of(context).push(MaterialPageRoute<void>(
                            builder: (_) => MerchantReviewReplyScreen(
                              api: api,
                              shopId: shop.key,
                              shopName: shop.value,
                              expectedSessionRevision: sessionRevision,
                            ),
                          ));
                        },
                      ),
                  ],
                ),
        );
      },
    );
  }
}
