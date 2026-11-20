import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'app_config.dart';

import 'package:flutter/foundation.dart' show ChangeNotifier;
import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

/// API client for /api/v3 (endpoint names mirror the original app).
/// Base URL comes from --dart-define=API_BASE_URL, defaulting to the local
/// dev server. Guest calls (home, banners) need no token; Bearer token is
/// attached automatically when present.
class ApiClient extends ChangeNotifier {
  ApiClient({String? baseUrl})
      : baseUrl = baseUrl ?? configuredBase;

  static String get configuredBase {
    const url = String.fromEnvironment('API_BASE_URL', defaultValue: 'http://localhost:8000');
    return resolveBaseUrl(url, Uri.base);
  }

  /// A web deployment may live below a domain prefix such as /coupongo/.
  static String resolveBaseUrl(String configured, Uri location) {
    return configured.isEmpty
        ? location.resolve('.').toString().replaceFirst(RegExp(r'/$'), '')
        : configured;
  }

  final String baseUrl;
  late final appConfig = AppConfigStore(() => get('/app-config'));
  String? _token;

  Future<void> loadToken() async {
    final prefs = await SharedPreferences.getInstance();
    _token = prefs.getString('access_token');
  }

  Future<void> saveToken(String token, {bool persist = true}) async {
    _token = token;
    final prefs = await SharedPreferences.getInstance();
    if (persist) {
      await prefs.setString('access_token', token);
    } else {
      await prefs.remove('access_token');
    }
    notifyListeners();
  }

  Future<void> clearToken() async {
    _token = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('access_token');
    notifyListeners();
  }

  bool get isLoggedIn => _token != null;

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_token != null) 'Authorization': 'Bearer $_token',
      };

  Future<Map<String, dynamic>> get(String path, {Map<String, String>? query}) async {
    final uri = Uri.parse('$baseUrl/api/v3$path').replace(queryParameters: query);
    final res = await http.get(uri, headers: _headers);
    return _decode(res);
  }

  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    final uri = Uri.parse('$baseUrl/api/v3$path').replace(queryParameters: query);
    final res = await http.post(uri, headers: _headers,
        body: body == null ? null : jsonEncode(body));
    return _decode(res);
  }

  Future<Map<String, dynamic>> put(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    final uri = Uri.parse('$baseUrl/api/v3$path').replace(queryParameters: query);
    final res = await http.put(uri, headers: _headers,
        body: body == null ? null : jsonEncode(body));
    return _decode(res);
  }

  Future<Map<String, dynamic>> delete(String path,
      {Map<String, String>? query}) async {
    final uri = Uri.parse('$baseUrl/api/v3$path').replace(queryParameters: query);
    final res = await http.delete(uri, headers: _headers);
    return _decode(res);
  }

  /// Multipart upload for review images (line F). Returns the list of
  /// `/uploads/review-images/...` paths to pass as `images[]` to
  /// POST /reviews. Local placeholder storage on the dev server, not
  /// object storage.
  Future<List<dynamic>> uploadReviewImages(List<Uint8List> images) async {
    final uri = Uri.parse('$baseUrl/api/v3/uploads/review-images');
    final req = http.MultipartRequest('POST', uri);
    if (_token != null) req.headers['Authorization'] = 'Bearer $_token';
    for (var i = 0; i < images.length; i++) {
      req.files.add(http.MultipartFile.fromBytes('files', images[i],
          filename: 'review_$i.png'));
    }
    final streamed = await req.send();
    final res = await http.Response.fromStream(streamed);
    final data = _decode(res);
    return (data['images'] as List<dynamic>);
  }

  Map<String, dynamic> _decode(http.Response res) {
    final data = res.body.isEmpty ? {} : jsonDecode(res.body);
    if (res.statusCode >= 400) {
      throw ApiException(res.statusCode,
          (data is Map && data['detail'] != null) ? '${data['detail']}' : '請求失敗');
    }
    return data as Map<String, dynamic>;
  }

  @override
  void dispose() {
    appConfig.dispose();
    super.dispose();
  }

  // ---- auth ----
  Future<Map<String, dynamic>> login(String email, String password) async {
    final data = await post('/login', body: {'email': email, 'password': password});
    await saveToken(data['access_token'] as String);
    return data;
  }

  Future<void> register(String email, String password, String name, String role) =>
      post('/new-register',
          body: {'email': email, 'password': password, 'name': name, 'role': role});

  Future<Map<String, dynamic>> verifyOtp(
          String email, String code, String purpose) async =>
      post('/new-verify-otp',
          body: {'email': email, 'code': code, 'purpose': purpose});

  // ---- discovery ----
  // Gap 10: /home accepts an optional location; when lat/lng are
  // sent, every home coupon carries distance_km (Haversine) and the
  // coupon card shows it via Coupon.distanceText. Without a location
  // the server returns distance_km null and the row stays hidden.
  Future<Map<String, dynamic>> home({double? lat, double? lng}) async =>
      get('/home', query: {
        if (lat != null) 'lat': '$lat',
        if (lng != null) 'lng': '$lng',
      });
  Future<List<dynamic>> limitedTime() async =>
      (await get('/get-limited-time-coupons'))['coupons'] as List<dynamic>;
  Future<List<dynamic>> mostGrab() async =>
      (await get('/get-most-people-grab'))['coupons'] as List<dynamic>;
  Future<List<dynamic>> recommended() async =>
      (await get('/recomandation-coupons'))['coupons'] as List<dynamic>;
  Future<Map<String, dynamic>> couponDetails(int id) =>
      get('/coupon-details-new', query: {'coupon_id': '$id'});
  Future<Map<String, dynamic>> shopDetails(int id) =>
      get('/shop-details', query: {'shop_id': '$id'});
  Future<List<dynamic>> classification({int? categoryId, String? area}) async =>
      (await get('/get-classification-coupons', query: {
        if (categoryId != null) 'category_id': '$categoryId',
        if (area != null) 'area': area,
      }))['coupons'] as List<dynamic>;
  Future<List<dynamic>> suggestions(String keyword) async =>
      (await post('/auto-suggestion-new', query: {'keyword': keyword}))['suggestions']
          as List<dynamic>;

  // ---- wallet / points ----
  Future<Map<String, dynamic>> claimCoupon(int couponId) =>
      post('/new-favorite-coupon', query: {'coupon_id': '$couponId'});
  Future<Map<String, dynamic>> wallet({int? status, int page = 1}) async =>
      get('/get-favorite-coupons-list', query: {
        'page': '$page',
        if (status != null) 'status': '$status',
      });
  Future<Map<String, dynamic>> points() async => get('/points');
  Future<Map<String, dynamic>> generateQr(int walletItemId) async =>
      post('/store-scaned-qr-values',
          query: {'wallet_item_id': '$walletItemId'});
  Future<void> review(int walletItemId) =>
      post('/review', query: {'wallet_item_id': '$walletItemId'});

  // ---- Reviews: three-dimension rating and optional review points ----
  Future<Map<String, dynamic>> submitReview({
    int? walletItemId,
    int? redemptionId,
    required int taste,
    required int ambience,
    required int service,
    String content = '',
    List<String> images = const [],
    List<String> tags = const [],
    bool claimPoints = true,
  }) async =>
      post('/reviews', body: {
        if (walletItemId != null) 'wallet_item_id': walletItemId,
        if (redemptionId != null) 'redemption_id': redemptionId,
        'taste': taste,
        'ambience': ambience,
        'service': service,
        'content': content,
        'images': images,
        'tags': tags,
        'claim_points': claimPoints,
      });
  Future<Map<String, dynamic>> shopReviews(int shopId,
          {int page = 1, bool hasImages = false, String sort = 'latest'}) async =>
      get('/shops/$shopId/reviews', query: {
        'page': '$page',
        if (hasImages) 'has_images': 'true',
        // Gap 9: sort=helpful is a real server sort by helpful_count.
        if (sort == 'helpful') 'sort': 'helpful',
      });
  Future<Map<String, dynamic>> shopRating(int shopId) async =>
      get('/shops/$shopId/rating');
  // Gap 9: vote / cancel a helpful vote on a review. Returns
  // {review_id, helpful_count, helped_by_me}; one vote per user.
  Future<Map<String, dynamic>> toggleHelpfulReview(
          int reviewId, bool helpful) async =>
      post('/reviews/$reviewId/helpful', body: {'helpful': helpful});
  Future<Map<String, dynamic>> replyReview(int reviewId, String reply) =>
      post('/reviews/$reviewId/reply', body: {'reply': reply});
  Future<Map<String, dynamic>> disputeReview(int reviewId, String reason) =>
      post('/reviews/$reviewId/dispute', body: {'reason': reason});

  // ---- admin v1 (line B): onboarding review + read-only dashboard ----
  Future<Map<String, dynamic>> submitMerchantApplication(
          Map<String, dynamic> body) =>
      post('/admin/merchant-applications', body: body);
  Future<Map<String, dynamic>> adminDashboard() async =>
      get('/admin/dashboard');
  Future<Map<String, dynamic>> adminApplications({String? status}) async =>
      get('/admin/merchant-applications', query: {
        if (status != null) 'status': status,
      });
  Future<Map<String, dynamic>> reviewMerchantApplication(int id, bool approve,
          {String rejectReason = ''}) =>
      post('/admin/merchant-applications/$id/review',
          body: {'approve': approve, 'reject_reason': rejectReason});
  Future<Map<String, dynamic>> adminShops() async =>
      get('/admin/shops');
  Future<Map<String, dynamic>> adminCoupons({String? status}) async =>
      get('/admin/coupons', query: {
        if (status != null) 'status': status,
      });
  Future<Map<String, dynamic>> adminRedemptions() async =>
      get('/admin/redemptions');
  // UI_SPEC §9 — admin package orders. NOTE: GET /admin/orders does
  // not exist on the backend yet (only the user-scoped GET /orders);
  // the screen shows the standard error state until it lands.
  Future<List<dynamic>> adminOrders({String? status}) async =>
      (await get('/admin/orders', query: {
        if (status != null) 'status': status,
      }))['orders'] as List<dynamic>;
  Future<Map<String, dynamic>> adminOrderDetail(int id) async =>
      (await get('/admin/orders/$id'))['order'] as Map<String, dynamic>;
  // UI_SPEC §9 — single onboarding application incl. qualification
  // photos, for the detail drawer.
  Future<Map<String, dynamic>> adminApplication(int id) async =>
      (await get('/admin/merchant-applications/$id'))['application']
          as Map<String, dynamic>;
  Future<Map<String, dynamic>> adminUsers() async => get('/admin/users');
  Future<Map<String, dynamic>> adminAuditLogs() async =>
      get('/admin/audit-logs');
  Future<Map<String, dynamic>> exchangePoints(int couponId) =>
      post('/points/exchange', query: {'coupon_id': '$couponId'});

  // ---- cashback (Line D) ----
  Future<Map<String, dynamic>> cashback() async => get('/cashback');
  Future<List<dynamic>> cashbackTransactions({String? status}) async =>
      (await get('/cashback/transactions', query: {
        if (status != null) 'status': status,
      }))['transactions'] as List<dynamic>;
  Future<Map<String, dynamic>> withdrawCashback(double amount, String method,
          String accountName, String accountRef, {String? requestKey}) =>
      post('/cashback/withdraw', body: {
        'amount': amount,
        'method': method,
        'account_name': accountName,
        'account_ref': accountRef,
        if (requestKey != null) 'request_key': requestKey,
      });
  Future<Map<String, dynamic>> withdrawalPage({int? before}) =>
      get('/cashback/withdrawals', query: {if (before != null) 'before': '$before'});
  Future<List<dynamic>> cashbackMissingAppeals() async =>
      (await get('/cashback/missing'))['appeals'] as List<dynamic>;
  Future<Map<String, dynamic>> submitMissingCashback(
          int merchantId, String orderNo, String spentAt, double amount,
          String evidence, String note) =>
      post('/cashback/missing', body: {
        'merchant_id': merchantId,
        'order_no': orderNo,
        'spent_at': spentAt,
        'amount': amount,
        'evidence': evidence,
        'note': note,
      });

  // ---- favourites / notifications / records ----
  Future<bool> toggleFavoriteCoupon(int id) async =>
      (await post('/favorite-coupon-all', query: {'coupon_id': '$id'}))
          ['is_coupon_favorite'] as bool;
  Future<bool> toggleFavoriteShop(int id) async =>
      (await post('/favorite-shop', query: {'shop_id': '$id'}))
          ['is_shop_favorite'] as bool;
  Future<Map<String, dynamic>> notifications() async =>
      get('/get-notification-list');
  Future<void> markNotificationRead(int id) =>
      post('/notifications/$id/read');
  Future<void> markAllNotificationsRead() => post('/notifications/read-all');
  Future<List<dynamic>> records() async =>
      (await get('/get-user-record-list'))['records'] as List<dynamic>;

  // ---- line A: nearby map ----
  // Gap 8: sort is served by /nearby-shops (distance | rating | newest);
  // the list response now carries rating / has_coupons / has_packages /
  // created_at, so no per-shop rating calls are needed to sort.
  Future<Map<String, dynamic>> nearbyShops(
      {required double lat,
      required double lng,
      double radiusKm = 10,
      String sort = 'distance'}) async =>
      get('/nearby-shops', query: {
        'lat': '$lat',
        'lng': '$lng',
        'radius_km': '$radiusKm',
        'sort': sort,
      });
  Future<Map<String, dynamic>> shopMap(int shopId) async =>
      get('/shops/$shopId/map');

  // ---- line A: redemption hardening ----
  Future<Map<String, dynamic>> prepareScan(String code) async =>
      post('/new-scan-coupon/prepare', body: {'code': code});
  Future<Map<String, dynamic>> revokeRedemption(int redemptionId) async =>
      post('/redemptions/$redemptionId/revoke', query: {'acknowledge_existing': 'true'});
  Future<Map<String, dynamic>> vendorRedemptionsExport() async =>
      get('/vendor/redemptions/export');
  // UI_SPEC §8 — vendor redemption records with a client-side date
  // filter (the export endpoint takes no date params yet; scanned_at
  // is filtered here) and a raw CSV download.
  Future<List<Map<String, dynamic>>> vendorRedemptions(
      {DateTime? from, DateTime? to}) async {
    final data = await vendorRedemptionsExport();
    final records = ((data['records'] as List?) ?? [])
        .map((e) => Map<String, dynamic>.from(e as Map))
        .toList();
    return records.where((r) {
      final at = DateTime.tryParse('${r['scanned_at'] ?? ''}');
      if (at == null) return true;
      if (from != null && at.isBefore(from)) return false;
      if (to != null && at.isAfter(to.add(const Duration(days: 1)))) {
        return false;
      }
      return true;
    }).toList();
  }

  Future<String> vendorRedemptionsCsv() async {
    final uri = Uri.parse('$baseUrl/api/v3/vendor/redemptions/export')
        .replace(queryParameters: {'format': 'csv'});
    final res = await http.get(uri, headers: _headers);
    if (res.statusCode >= 400) {
      throw ApiException(res.statusCode, '請求失敗');
    }
    return res.body;
  }
  // ---- bargain 2C (line E) ----
  /// Stable per-device id for the bargain anti-fraud device check.
  /// Stored locally; never a credential, just a dedupe key.
  Future<String> deviceId() async {
    final prefs = await SharedPreferences.getInstance();
    var id = prefs.getString('device_id');
    if (id == null) {
      id = 'dev-${DateTime.now().millisecondsSinceEpoch}-'
          '${Random().nextInt(1 << 32)}';
      await prefs.setString('device_id', id);
    }
    return id;
  }

  Future<List<dynamic>> bargainCampaigns() async =>
      (await get('/bargain-campaigns'))['campaigns'] as List<dynamic>;
  Future<Map<String, dynamic>> bargainSession(int id) async =>
      (await get('/bargain-sessions/$id'))['session'] as Map<String, dynamic>;
  Future<Map<String, dynamic>> startBargain(int campaignId) async =>
      post('/bargain-campaigns/$campaignId/start');
  Future<Map<String, dynamic>> helpBargain(int sessionId, String deviceId) =>
      post('/bargain-sessions/$sessionId/help',
          body: {'device_id': deviceId});
  Future<Map<String, dynamic>> purchaseBargain(int sessionId) =>
      post('/bargain-sessions/$sessionId/purchase');
  Future<List<dynamic>> myBargainSessions() async =>
      (await get('/bargain-my-sessions'))['sessions'] as List<dynamic>;
  // ---- vendor ----
  Future<List<dynamic>> vendorCoupons() async =>
      (await get('/get-vendor-coupon-list'))['coupons'] as List<dynamic>;

  // ---- packages / orders (line C: 套餐 2A) ----
  Future<List<dynamic>> packages({int? shopId}) async =>
      (await get('/packages', query: {
        if (shopId != null) 'shop_id': '$shopId',
      }))['packages'] as List<dynamic>;
  Future<Map<String, dynamic>> packageDetails(int id) async =>
      (await get('/package-details', query: {'package_id': '$id'}))['package']
          as Map<String, dynamic>;
  Future<Map<String, dynamic>> purchasePackage(int id, String method,
          {String clientRef = '', int quantity = 1}) async =>
      post('/packages/$id/purchase', body: {
        'payment_method': method,
        'client_ref': clientRef,
        if (quantity > 1) 'quantity': quantity,
      });
  // NOTE (UI_SPEC §3.3): the client never posts a self-reported payment
  // outcome. Results come from the gateway; the Flutter client only
  // polls GET /orders/{id} (see PaymentResultScreen). The old wrapper
  // was removed so no call site can regress.
  Future<Map<String, dynamic>> retryPayment(int orderId, String method) =>
      post('/orders/$orderId/retry-payment',
          body: {'payment_method': method});
  Future<Map<String, dynamic>> cancelOrder(int orderId) =>
      post('/orders/$orderId/cancel');
  Future<Map<String, dynamic>> refundOrder(int orderId,
          {String reason = ''}) =>
      post('/orders/$orderId/refund', body: {'reason': reason});
  Future<Map<String, dynamic>> refundPackage(int id,
          {String reason = ''}) =>
      post('/packages/$id/refund', body: {'reason': reason});
  Future<List<dynamic>> orders({String? status}) async =>
      (await get('/orders', query: {
        if (status != null) 'status': status,
      }))['orders'] as List<dynamic>;
  Future<Map<String, dynamic>> orderDetails(int id) async =>
      (await get('/orders/$id'))['order'] as Map<String, dynamic>;

  Future<Map<String, dynamic>> couponPreview(Map<String, dynamic> body) =>
      post('/add-coupon-preview', body: body);
  Future<Map<String, dynamic>> couponCreate(Map<String, dynamic> body) =>
      post('/coupon-previewed', body: body);
  Future<Map<String, dynamic>> usageData() async =>
      get('/get-vendor-usage-data-new');
  Future<Map<String, dynamic>> scanCoupon(String code,
      {String? idempotencyKey, String? prepareToken}) async =>
      post('/new-scan-coupon', body: {
        'code': code,
        if (idempotencyKey != null) 'idempotency_key': idempotencyKey,
        if (prepareToken != null) 'prepare_token': prepareToken,
      });
  Future<List<dynamic>> subscriptionPlans() async =>
      (await get('/get-subscription-plan-list'))['plans'] as List<dynamic>;

  // ---- admin v2 (line F) ----
  Future<Map<String, dynamic>> adminOverview() async => get('/admin/overview');
  Future<Map<String, dynamic>> settlements({int? shopId, String? status, int page = 1}) async =>
      get('/admin/settlements', query: {
        'page': '$page',
        if (shopId != null) 'shop_id': '$shopId',
        if (status != null) 'status': status,
      });
  Future<Map<String, dynamic>> generateSettlement(
          int shopId, String period, [double? commissionRate]) async =>
      post('/admin/settlements/generate', body: {
        'shop_id': shopId, 'period': period, if (commissionRate != null) 'commission_rate': commissionRate,
      });
  Future<Map<String, dynamic>> approveSettlement(int id) async =>
      post('/admin/settlements/$id/approve');
  Future<Map<String, dynamic>> disputeSettlement(int id, String reason) async =>
      post('/admin/settlements/$id/dispute', body: {'reason': reason});
  Future<Map<String, dynamic>> resolveSettlement(
          int id, String decision, String note) async =>
      post('/admin/settlements/$id/resolve',
          body: {'decision': decision, 'note': note});
  Future<Map<String, dynamic>> paySettlement(int id, num amount, String reference) async =>
      post('/admin/settlements/$id/pay', body: {'amount': amount, 'reference': reference});
  Future<Map<String, dynamic>> reconciliationRun(int shopId, String period) async =>
      post('/admin/reconciliation/run',
          body: {'shop_id': shopId, 'period': period});
  Future<Map<String, dynamic>> reconciliationReports(
      {int? shopId, String? period}) async =>
      get('/admin/reconciliation/reports', query: {
        if (shopId != null) 'shop_id': '$shopId',
        if (period != null) 'period': period,
      });
  Future<Map<String, dynamic>> resolveDiscrepancy(
          int id, String resolution) async =>
      post('/admin/reconciliation/discrepancies/$id/resolve',
          body: {'resolution': resolution});
  Future<Map<String, dynamic>> invoices({int? shopId, String? status}) async =>
      get('/admin/invoices', query: {
        if (shopId != null) 'shop_id': '$shopId',
        if (status != null) 'status': status,
      });
  Future<Map<String, dynamic>> createInvoice(Map<String, dynamic> body) async =>
      post('/admin/invoices', body: body);
  Future<Map<String, dynamic>> approveInvoice(int id) async =>
      post('/admin/invoices/$id/approve');
  Future<Map<String, dynamic>> rejectInvoice(int id, String reason) async =>
      post('/admin/invoices/$id/reject', body: {'reason': reason});
  Future<Map<String, dynamic>> issueInvoice(int id) async =>
      post('/admin/invoices/$id/issue');
  Future<List<dynamic>> adminBanners() async =>
      (await get('/admin/banners'))['banners'] as List<dynamic>;
  Future<Map<String, dynamic>> createBanner(Map<String, dynamic> body) async =>
      post('/admin/banners', body: body);
  Future<Map<String, dynamic>> updateBanner(
          int id, Map<String, dynamic> body) async =>
      put('/admin/banners/$id', body: body);
  Future<Map<String, dynamic>> deleteBanner(int id) async =>
      delete('/admin/banners/$id');
  Future<List<dynamic>> adminAnnouncements() async =>
      (await get('/admin/announcements'))['announcements'] as List<dynamic>;
  Future<Map<String, dynamic>> createAnnouncement(
          Map<String, dynamic> body) async =>
      post('/admin/announcements', body: body);
  Future<Map<String, dynamic>> updateAnnouncement(
          int id, Map<String, dynamic> body) async =>
      put('/admin/announcements/$id', body: body);
  Future<Map<String, dynamic>> deleteAnnouncement(int id) async =>
      delete('/admin/announcements/$id');
  Future<List<dynamic>> adminCategories() async =>
      (await get('/admin/categories'))['categories'] as List<dynamic>;
  Future<Map<String, dynamic>> createCategory(Map<String, dynamic> body) async =>
      post('/admin/categories', body: body);
  Future<Map<String, dynamic>> deactivateCategory(int id) async =>
      post('/admin/categories/$id/deactivate');
  Future<Map<String, dynamic>> activateCategory(int id) async =>
      post('/admin/categories/$id/activate');
  Future<Map<String, dynamic>> adminRoles() async => get('/admin/roles');
  Future<Map<String, dynamic>> changeUserRole(int userId, String role) async =>
      post('/admin/users/$userId/role', body: {'role': role});
  Future<List<dynamic>> shopMembers(int shopId) async =>
      (await get('/admin/shops/$shopId/members'))['members'] as List<dynamic>;
  Future<Map<String, dynamic>> addShopMember(int shopId, int userId) async =>
      post('/admin/shops/$shopId/members', body: {'user_id': userId});
  Future<Map<String, dynamic>> removeShopMember(int shopId, int userId) async =>
      delete('/admin/shops/$shopId/members/$userId');
  Future<Map<String, dynamic>> reviewDisputes() async =>
      get('/admin/reviews/disputes');
  Future<Map<String, dynamic>> resolveReviewDispute(
          int reviewId, String decision, String note) async =>
      post('/reviews/$reviewId/resolve',
          body: {'decision': decision, 'note': note});
  Future<Map<String, dynamic>> merchantSettlements(int shopId) async =>
      get('/merchant/shops/$shopId/settlements');
  Future<Map<String, dynamic>> merchantInvoices(int shopId) async =>
      get('/merchant/shops/$shopId/invoices');
  Future<List<dynamic>> publicBanners() async =>
      (await get('/banners'))['banners'] as List<dynamic>;
  Future<List<dynamic>> publicAnnouncements() async =>
      (await get('/announcements'))['announcements'] as List<dynamic>;
}

class ApiException implements Exception {
  ApiException(this.status, this.message);
  final int status;
  final String message;
  @override
  String toString() => message;
}
