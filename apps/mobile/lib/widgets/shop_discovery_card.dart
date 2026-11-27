import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../language.dart';
import '../models.dart';
import '../theme.dart';
import 'widgets.dart';

Uri? shopMapsUri(Shop shop) {
  final lat = shop.latitude, lng = shop.longitude;
  if (lat != null && lng != null && lat.abs() <= 90 && lng.abs() <= 180) {
    return Uri.https('www.google.com', '/maps/dir/', {
      'api': '1',
      'destination': '$lat,$lng',
    });
  }
  final address = shop.address.trim();
  if (address.isEmpty) return null;
  return Uri.https('www.google.com', '/maps/search/', {
    'api': '1',
    'query': address,
  });
}

Future<void> openShopMap(BuildContext context, Uri uri) async {
  try {
    if (await launchUrl(uri, mode: LaunchMode.externalApplication)) return;
  } catch (_) {
    // The user can still open the shop details and copy its address.
  }
  if (context.mounted) {
    showCgToast(context, context.tr('未能開啟地圖', 'Could not open the map'));
  }
}

class ShopDiscoveryCard extends StatelessWidget {
  const ShopDiscoveryCard({
    super.key,
    required this.shop,
    required this.apiBaseUrl,
    required this.onOpen,
    this.selected = false,
  });

  final Shop shop;
  final String apiBaseUrl;
  final VoidCallback onOpen;
  final bool selected;

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    final map = shopMapsUri(shop);
    final labels = [
      if (shop.categoryName.isNotEmpty) shop.categoryName,
      if (shop.distanceKm != null) formatDistance(shop.distanceKm),
      if (shop.rating case final rating?) '★ ${rating.toStringAsFixed(1)}',
    ];
    return Card(
      margin: EdgeInsets.zero,
      elevation: 0,
      color: selected ? CgColors.statusAttentionBg : Colors.white,
      child: ListTile(
        contentPadding: const EdgeInsetsDirectional.fromSTEB(10, 6, 6, 6),
        onTap: onOpen,
        leading: SizedBox.square(
          dimension: 52,
          child: ClipRRect(
            borderRadius: BorderRadius.circular(8),
            child: CatalogPhoto(
                url: shop.image, height: 52, apiBaseUrl: apiBaseUrl),
          ),
        ),
        title: Text(shop.name,
            style: const TextStyle(fontWeight: FontWeight.w700)),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (labels.isNotEmpty) Text(labels.join(' · ')),
            if (shop.address.isNotEmpty)
              Text(shop.address, maxLines: 2, overflow: TextOverflow.ellipsis),
            if (shop.hasCoupon || shop.hasPackage)
              Text(
                  [
                    if (shop.hasCoupon) t('有優惠券', 'Coupons'),
                    if (shop.hasPackage) t('有套餐', 'Packages'),
                  ].join(' · '),
                  style: const TextStyle(color: CgColors.primary)),
          ],
        ),
        trailing: map == null
            ? const Icon(Icons.chevron_right)
            : IconButton(
                tooltip: shop.latitude != null && shop.longitude != null
                    ? t('導航至${shop.name}', 'Directions to ${shop.name}')
                    : t('在地圖查看${shop.name}', 'Show ${shop.name} on map'),
                icon: const Icon(Icons.directions_outlined),
                onPressed: () => openShopMap(context, map),
              ),
      ),
    );
  }
}
