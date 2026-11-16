/// Data models matching the FastAPI /api/v3 responses.
class Coupon {
  Coupon.fromJson(Map<String, dynamic> j)
      : id = j['coupon_id'] as int? ?? j['id'] as int? ?? 0,
        shopId = j['shop_id'] as int? ?? 0,
        shopName = j['shop_name'] as String? ?? '',
        vendorName = j['vendor_name'] as String? ?? '',
        name = j['coupon_name'] as String? ?? '',
        type = j['coupon_type'] as String? ?? 'Discount',
        cashAmount = (j['cash_amount'] as num?)?.toDouble(),
        percentage = (j['percentage'] as num?)?.toDouble(),
        gift = j['gift'] as String? ?? '',
        discountPrice = (j['discount_price'] as num?)?.toDouble(),
        regularPrice = (j['regular_price'] as num?)?.toDouble(),
        minSpend = (j['min_spend'] as num?)?.toDouble() ?? 0,
        image = j['image'] as String? ?? '',
        validTo = DateTime.tryParse(j['valid_to'] as String? ?? ''),
        remaining = j['remaining'] as int? ?? 0,
        total = j['total'] as int? ?? 0,
        downloads = j['downloads'] as int? ?? 0,
        status = j['status'] as String? ?? '',
        terms = j['additional_terms'] as String? ?? '',
        isFavorite = j['is_coupon_favorite'] as bool? ?? false;

  final int id;
  final int shopId;
  final String shopName;
  final String vendorName;
  final String name;
  final String type;
  final double? cashAmount;
  final double? percentage;
  final String gift;
  final double? discountPrice;
  final double? regularPrice;
  final double minSpend;
  final String image;
  final DateTime? validTo;
  final int remaining;
  final int total;
  final int downloads;
  final String status;
  final String terms;
  final bool isFavorite;

  String get amountText {
    if (type == 'Cash' && cashAmount != null) return '減 \$${cashAmount!.toStringAsFixed(0)}';
    if (type == 'Discount' && percentage != null) {
      return '${percentage!.toStringAsFixed(percentage! % 1 == 0 ? 0 : 1)} 折';
    }
    if (type == 'Gift') return '送$gift';
    if (type == 'Price' && discountPrice != null) {
      return '\$${discountPrice!.toStringAsFixed(0)}';
    }
    return name;
  }

  int get daysLeft =>
      validTo == null ? 0 : validTo!.difference(DateTime.now()).inDays;
}

class Shop {
  Shop.fromJson(Map<String, dynamic> j)
      : id = j['shop_id'] as int? ?? j['id'] as int? ?? 0,
        name = j['shop_name'] as String? ?? '',
        categoryName = j['category_name'] as String? ?? '',
        areaName = j['area_name'] as String? ?? '',
        address = j['address'] as String? ?? '',
        phone = j['phone'] as String? ?? '',
        description = j['description'] as String? ?? '',
        image = j['image'] as String? ?? '',
        branchCount = j['branch_count'] as int? ?? 0,
        isFavorite = j['is_shop_favorite'] as bool? ?? false;

  final int id;
  final String name;
  final String categoryName;
  final String areaName;
  final String address;
  final String phone;
  final String description;
  final String image;
  final int branchCount;
  final bool isFavorite;
}

class WalletItem {
  WalletItem.fromJson(Map<String, dynamic> j)
      : id = j['id'] as int? ?? 0,
        couponId = j['coupon_id'] as int? ?? 0,
        couponName = j['coupon_name'] as String? ?? '',
        shopName = j['shop_name'] as String? ?? '',
        type = j['coupon_type'] as String? ?? '',
        status = j['status'] as int? ?? 1,
        code = j['code'] as String? ?? '',
        qrExpiresAt = DateTime.tryParse(j['qr_expires_at'] as String? ?? ''),
        validTo = DateTime.tryParse(j['valid_to'] as String? ?? ''),
        reviewed = j['reviewed'] as bool? ?? false;

  final int id;
  final int couponId;
  final String couponName;
  final String shopName;
  final String type;
  final int status; // 1 未使用 / 2 已使用 / 3 已失效
  final String code;
  final DateTime? qrExpiresAt;
  final DateTime? validTo;
  final bool reviewed;

  String get statusText =>
      status == 1 ? '未使用' : status == 2 ? '已使用' : '已失效';
}

class AppNotification {
  AppNotification.fromJson(Map<String, dynamic> j)
      : id = j['id'] as int? ?? 0,
        title = j['title'] as String? ?? '',
        body = j['body'] as String? ?? '',
        type = j['notification_type'] as String? ?? 'general',
        targetCouponId = j['target_coupon_id'] as int?,
        targetShopId = j['target_shop_id'] as int?,
        isRead = j['is_read'] as bool? ?? false;

  final int id;
  final String title;
  final String body;
  final String type;
  final int? targetCouponId;
  final int? targetShopId;
  final bool isRead;
}
