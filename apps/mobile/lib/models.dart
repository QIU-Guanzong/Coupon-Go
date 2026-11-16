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
        latitude = (j['latitude'] as num?)?.toDouble(),
        longitude = (j['longitude'] as num?)?.toDouble(),
        distanceKm = (j['distance_km'] as num?)?.toDouble(),
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
  final double? latitude; // line A: map position (null = no coordinates)
  final double? longitude;
  final double? distanceKm; // line A: Haversine distance from /nearby-shops
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
        reviewed = j['reviewed'] as bool? ?? false,
        source = j['source'] as String? ?? 'claim',
        orderId = j['order_id'] as int?,
        isPackage = j['is_package'] as bool? ?? false,
        refundPolicy = j['refund_policy'] as String? ?? 'anytime';

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
  final String source; // claim | purchase | bargain | exchange
  final int? orderId;
  final bool isPackage;
  final String refundPolicy;

  String get statusText =>
      status == 1 ? '未使用' : status == 2 ? '已使用' : '已失效';

  /// Refundable from the wallet: an unused, purchased, refundable voucher.
  bool get canRefund =>
      status == 1 && isPackage && orderId != null && refundPolicy == 'anytime';
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

/// Line B: a 3-dim review. pointsGiven reviews do NOT count toward the
/// shop rating (countsTowardRating=false); see ApiClient.submitReview.
class Review {
  Review.fromJson(Map<String, dynamic> j)
      : id = j['id'] as int? ?? 0,
        userName = j['user_name'] as String? ?? '',
        shopId = j['shop_id'] as int? ?? 0,
        taste = j['taste'] as int? ?? 0,
        ambience = j['ambience'] as int? ?? 0,
        service = j['service'] as int? ?? 0,
        overall = (j['overall'] as num?)?.toDouble() ?? 0,
        content = j['content'] as String? ?? '',
        images = (j['images'] as List?)?.map((e) => '$e').toList() ?? [],
        tags = (j['tags'] as List?)?.map((e) => '$e').toList() ?? [],
        pointsGiven = j['points_given'] as bool? ?? false,
        countsTowardRating = j['counts_toward_rating'] as bool? ?? false,
        status = j['status'] as String? ?? '',
        replyText = j['reply_text'] as String? ?? '';

  final int id;
  final String userName;
  final int shopId;
  final int taste;
  final int ambience;
  final int service;
  final double overall;
  final String content;
  final List<String> images;
  final List<String> tags;
  final bool pointsGiven;
  final bool countsTowardRating;
  final String status;
  final String replyText;
}

/// Line B: aggregated shop rating; overall is null while no review
/// counts toward the rating.
class ShopRating {
  ShopRating.fromJson(Map<String, dynamic> j)
      : shopId = j['shop_id'] as int? ?? 0,
        overall = (j['overall'] as num?)?.toDouble(),
        taste = (j['taste'] as num?)?.toDouble(),
        ambience = (j['ambience'] as num?)?.toDouble(),
        service = (j['service'] as num?)?.toDouble(),
        reviewCount = j['review_count'] as int? ?? 0;

  final int shopId;
  final double? overall;
  final double? taste;
  final double? ambience;
  final double? service;
  final int reviewCount;
}

/// Line B: a merchant onboarding application (admin v1).
class MerchantApplication {
  MerchantApplication.fromJson(Map<String, dynamic> j)
      : id = j['id'] as int? ?? 0,
        applicantEmail = j['applicant_email'] as String? ?? '',
        shopName = j['shop_name'] as String? ?? '',
        contactName = j['contact_name'] as String? ?? '',
        phone = j['phone'] as String? ?? '',
        address = j['address'] as String? ?? '',
        status = j['status'] as String? ?? '',
        rejectReason = j['reject_reason'] as String? ?? '';

  final int id;
  final String applicantEmail;
  final String shopName;
  final String contactName;
  final String phone;
  final String address;
  final String status;
  final String rejectReason;
/// Line D — one cashback earning (see /api/v3/cashback).
class CashbackTransaction {
  CashbackTransaction.fromJson(Map<String, dynamic> j)
      : id = j['id'] as int? ?? 0,
        merchantId = j['merchant_id'] as int? ?? 0,
        shopName = j['shop_name'] as String? ?? '',
        channel = j['channel'] as String? ?? 'dinein',
        amount = (j['amount'] as num?)?.toDouble() ?? 0,
        rate = (j['rate'] as num?)?.toDouble() ?? 0,
        status = j['status'] as String? ?? 'pending',
        statusText = j['status_text'] as String? ?? '',
        expectedConfirmAt =
            DateTime.tryParse(j['expected_confirm_at'] as String? ?? ''),
        expiresAt = DateTime.tryParse(j['expires_at'] as String? ?? ''),
        confirmedAt = DateTime.tryParse(j['confirmed_at'] as String? ?? ''),
        remainingAmount = (j['remaining_amount'] as num?)?.toDouble() ?? 0,
        voidReason = j['void_reason'] as String?,
        createdAt = DateTime.tryParse(j['created_at'] as String? ?? '');

  final int id;
  final int merchantId;
  final String shopName;
  final String channel; // dinein | online
  final double amount;
  final double rate;
  final String status; // tracked | pending | confirmed | withdrawable | void | withdrawn
  final String statusText;
  final DateTime? expectedConfirmAt;
  final DateTime? expiresAt;
  final DateTime? confirmedAt;
  final double remainingAmount;
  final String? voidReason;
  final DateTime? createdAt;

  String get channelText => channel == 'dinein' ? '線下餐飲' : '線上 / 通用';
}

/// Line D — one withdrawal request (see /api/v3/cashback/withdrawals).
class CashbackWithdrawal {
  CashbackWithdrawal.fromJson(Map<String, dynamic> j)
      : id = j['id'] as int? ?? 0,
        amount = (j['amount'] as num?)?.toDouble() ?? 0,
        method = j['method'] as String? ?? 'fps',
        methodText = j['method_text'] as String? ?? '',
        accountName = j['account_name'] as String? ?? '',
        status = j['status'] as String? ?? 'pending',
        createdAt = DateTime.tryParse(j['created_at'] as String? ?? '');

  final int id;
  final double amount;
  final String method; // fps | bank
  final String methodText;
  final String accountName;
  final String status; // pending | paid | failed
  final DateTime? createdAt;

  String get statusText =>
      status == 'paid' ? '已打款' : status == 'failed' ? '打款失敗' : '待打款';}
/// Bargain 2C (line E): campaign / session / help / refund.
/// Amounts are HKD; see apps/api/app/routers/bargain.py for the rules.
class BargainCampaign {
  BargainCampaign.fromJson(Map<String, dynamic> j)
      : id = j['id'] as int? ?? 0,
        packageId = j['package_id'] as int?,
        shopId = j['shop_id'] as int? ?? 0,
        shopName = j['shop_name'] as String? ?? '',
        name = j['name'] as String? ?? '',
        items = j['items'] as String? ?? '',
        personCount = j['person_count'] as int? ?? 2,
        originalPrice = (j['original_price'] as num?)?.toDouble() ?? 0,
        floorPrice = (j['floor_price'] as num?)?.toDouble() ?? 0,
        stockTotal = j['stock_total'] as int? ?? 0,
        stockLeft = j['stock_left'] as int? ?? 0,
        helperLimit = j['helper_limit'] as int? ?? 5,
        durationMinutes = j['duration_minutes'] as int? ?? 1440,
        lockStock = j['lock_stock'] as bool? ?? true,
        status = j['status'] as String? ?? 'active',
        expiresAt = DateTime.tryParse(j['expires_at'] as String? ?? '');

  final int id;
  final int? packageId;
  final int shopId;
  final String shopName;
  final String name;
  final String items;
  final int personCount;
  final double originalPrice;
  final double floorPrice;
  final int stockTotal;
  final int stockLeft;
  final int helperLimit;
  final int durationMinutes;
  final bool lockStock;
  final String status;
  final DateTime? expiresAt;

  bool get isSoldOut => stockLeft <= 0 || status == 'sold_out';
}

class BargainHelp {
  BargainHelp.fromJson(Map<String, dynamic> j)
      : helperUserId = j['helper_user_id'] as int? ?? 0,
        helperName = j['helper_name'] as String? ?? '好友',
        cutAmount = (j['cut_amount'] as num?)?.toDouble() ?? 0,
        isNewUser = j['is_new_user'] as bool? ?? false,
        createdAt = DateTime.tryParse(j['created_at'] as String? ?? '');

  final int helperUserId;
  final String helperName;
  final double cutAmount;
  final bool isNewUser;
  final DateTime? createdAt;
}

class BargainRefund {
  BargainRefund.fromJson(Map<String, dynamic> j)
      : amount = (j['amount'] as num?)?.toDouble() ?? 0,
        reason = j['reason'] as String? ?? '',
        status = j['status'] as String? ?? '',
        note = j['note'] as String? ?? '';

  final double amount;
  final String reason;
  final String status;
  final String note;

  String get reasonText {
    switch (reason) {
      case 'bargain_failed':
        return '未成團，全額退款';
      case 'expired':
        return '成團後未下單，全額退款';
      case 'price_cut':
        return '砍價差價退款';
      default:
        return '退款';
    }
  }
}

class BargainSession {
  BargainSession.fromJson(Map<String, dynamic> j)
      : id = j['session_id'] as int? ?? j['id'] as int? ?? 0,
        campaignId = j['campaign_id'] as int? ?? 0,
        campaign = j['campaign'] is Map<String, dynamic>
            ? BargainCampaign.fromJson(j['campaign'] as Map<String, dynamic>)
            : null,
        userId = j['user_id'] as int? ?? 0,
        currentPrice = (j['current_price'] as num?)?.toDouble() ?? 0,
        targetPrice = (j['target_price'] as num?)?.toDouble() ?? 0,
        floorPrice = (j['floor_price'] as num?)?.toDouble() ?? 0,
        originalPrice = (j['original_price'] as num?)?.toDouble() ?? 0,
        paidAmount = (j['paid_amount'] as num?)?.toDouble() ?? 0,
        status = j['status'] as String? ?? 'active',
        helperCount = j['helper_count'] as int? ?? 0,
        helperLimit = j['helper_limit'] as int? ?? 5,
        stockLocked = j['stock_locked'] as bool? ?? false,
        walletItemId = j['wallet_item_id'] as int?,
        expiresAt = DateTime.tryParse(j['expires_at'] as String? ?? ''),
        secondsLeft = j['seconds_left'] as int? ?? 0,
        canPurchase = j['can_purchase'] as bool? ?? false,
        shareLink = j['share_link'] as String? ?? '',
        helps = ((j['helps'] as List?) ?? [])
            .map((e) => BargainHelp.fromJson(e as Map<String, dynamic>))
            .toList(),
        refunds = ((j['refunds'] as List?) ?? [])
            .map((e) => BargainRefund.fromJson(e as Map<String, dynamic>))
            .toList();

  final int id;
  final int campaignId;
  final BargainCampaign? campaign;
  final int userId;
  final double currentPrice;
  final double targetPrice;
  final double floorPrice;
  final double originalPrice;
  final double paidAmount;
  final String status; // active | success | failed | expired
  final int helperCount;
  final int helperLimit;
  final bool stockLocked;
  final int? walletItemId;
  final DateTime? expiresAt;
  final int secondsLeft;
  final bool canPurchase;
  final String shareLink;
  final List<BargainHelp> helps;
  final List<BargainRefund> refunds;

  String get statusText {
    switch (status) {
      case 'success':
        return '已成團';
      case 'failed':
        return '未成團，已退款';
      case 'expired':
        return '已過期，已退款';
      default:
        return '砍價進行中';
    }
  }

  /// 0..1 progress from original price down to the floor price.
  double get cutProgress {
    final span = originalPrice - floorPrice;
    if (span <= 0) return 1;
    return ((originalPrice - currentPrice) / span).clamp(0.0, 1.0);
  }

  String get countdownText {
    final h = secondsLeft ~/ 3600;
    final m = (secondsLeft % 3600) ~/ 60;
    final s = secondsLeft % 60;
    return '剩餘 ${h.toString().padLeft(2, '0')}:'
        '${m.toString().padLeft(2, '0')}:${s.toString().padLeft(2, '0')}';
  }
}
/// Paid set-meal product (line C: 套餐 2A). Buying one generates a
/// package voucher (kind='package') into Q Wallet.
class Package {
  Package.fromJson(Map<String, dynamic> j)
      : id = j['package_id'] as int? ?? j['id'] as int? ?? 0,
        shopId = j['shop_id'] as int? ?? 0,
        shopName = j['shop_name'] as String? ?? '',
        name = j['name'] as String? ?? '',
        items = (j['items'] as List? ?? [])
            .map((e) => e is Map
                ? '${e['name'] ?? ''} x${e['qty'] ?? 1}'
                : '$e')
            .toList(),
        personCount = j['person_count'] as int? ?? 2,
        originalPrice = (j['original_price'] as num?)?.toDouble() ?? 0,
        packagePrice = (j['package_price'] as num?)?.toDouble() ?? 0,
        saveAmount = (j['save_amount'] as num?)?.toDouble() ?? 0,
        savePercent = (j['save_percent'] as num?)?.toDouble() ?? 0,
        validTo = DateTime.tryParse(j['valid_to'] as String? ?? ''),
        usageRules = j['usage_rules'] is Map
            ? Map<String, dynamic>.from(j['usage_rules'] as Map)
            : <String, dynamic>{},
        stockLeft = j['stock_left'] as int? ?? 0,
        soldCount = j['sold_count'] as int? ?? 0,
        refundPolicy = j['refund_policy'] as String? ?? 'anytime',
        refundPolicyText = j['refund_policy_text'] as String? ?? '',
        status = j['status'] as String? ?? '';

  final int id;
  final int shopId;
  final String shopName;
  final String name;
  final List<String> items;
  final int personCount;
  final double originalPrice;
  final double packagePrice;
  final double saveAmount;
  final double savePercent;
  final DateTime? validTo;
  final Map<String, dynamic> usageRules;
  final int stockLeft;
  final int soldCount;
  final String refundPolicy;
  final String refundPolicyText;
  final String status;

  bool get needBooking => usageRules['need_booking'] == true;
  List<String> get includes =>
      (usageRules['includes'] as List? ?? []).map((e) => '$e').toList();
  List<String> get excludes =>
      (usageRules['excludes'] as List? ?? []).map((e) => '$e').toList();
  int get daysLeft =>
      validTo == null ? 0 : validTo!.difference(DateTime.now()).inDays;
}

/// A paid package order (line C).
class PackageOrder {
  PackageOrder.fromJson(Map<String, dynamic> j)
      : id = j['order_id'] as int? ?? j['id'] as int? ?? 0,
        packageId = j['package_id'] as int?,
        packageName = j['package_name'] as String? ?? '',
        shopName = j['shop_name'] as String? ?? '',
        amount = (j['amount'] as num?)?.toDouble() ?? 0,
        paidAmount = (j['paid_amount'] as num?)?.toDouble() ?? 0,
        paymentMethod = j['payment_method'] as String? ?? '',
        paymentStatus = j['payment_status'] as String? ?? '',
        refundStatus = j['refund_status'] as String? ?? 'none',
        settlementStatus = j['settlement_status'] as String? ?? '',
        walletItemId = j['wallet_item_id'] as int?,
        createdAt = DateTime.tryParse(j['created_at'] as String? ?? ''),
        paymentCount = (j['payments'] as List? ?? []).length;

  final int id;
  final int? packageId;
  final String packageName;
  final String shopName;
  final double amount;
  final double paidAmount;
  final String paymentMethod;
  final String paymentStatus; // pending | paid | failed | timeout | cancelled
  final String refundStatus; // none | requested | refunded | rejected
  final String settlementStatus;
  final int? walletItemId;
  final DateTime? createdAt;
  final int paymentCount;

  String get paymentStatusText => switch (paymentStatus) {
        'paid' => '已支付',
        'failed' => '支付失敗',
        'timeout' => '支付逾時',
        'cancelled' => '已取消',
        _ => '待支付',
      };

  String get paymentMethodText => paymentMethod == 'card' ? '信用卡' : 'FPS 轉數快';

  bool get canPay => paymentStatus == 'pending';
  bool get canRetry =>
      paymentStatus == 'failed' || paymentStatus == 'timeout';
  bool get canCancel => paymentStatus == 'pending';
  bool get canRefund => paymentStatus == 'paid' && refundStatus == 'none';
}