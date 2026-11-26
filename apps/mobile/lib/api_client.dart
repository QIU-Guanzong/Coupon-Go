import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'app_config.dart';
import 'feature_flags.dart';
import 'vendor_coupon_lifecycle.dart';

import 'package:flutter/foundation.dart' show ChangeNotifier;
import 'package:http/http.dart' as http;
import 'package:http_parser/http_parser.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// API client for /api/v3 (endpoint names mirror the original app).
/// Base URL comes from --dart-define=API_BASE_URL, defaulting to the local
/// dev server. Guest calls (home, banners) need no token; Bearer token is
/// attached automatically when present.
class ApiClient extends ChangeNotifier {
  ApiClient({String? baseUrl}) : baseUrl = baseUrl ?? configuredBase;

  static String get configuredBase {
    const url = String.fromEnvironment('API_BASE_URL',
        defaultValue: publicTrial ? '/coupongo-trial' : 'http://localhost:8000');
    return publicTrial ? resolveTrialBase(url) : resolveBaseUrl(url, Uri.base);
  }

  /// A web deployment may live below a domain prefix such as /coupongo/.
  static String resolveBaseUrl(String configured, Uri location) {
    return configured.isEmpty
        ? location.resolve('.').toString().replaceFirst(RegExp(r'/$'), '')
        : configured;
  }

  /// Fail closed instead of sending a trial token to a production API.
  static String resolveTrialBase(String configured) {
    if (configured != '/coupongo-trial') {
      throw StateError('The public app requires its same-origin trial gateway.');
    }
    return configured;
  }

  final String baseUrl;
  late final appConfig = AppConfigStore(() => get('/app-config'));
  String? _token;
  int _sessionRevision = 0;
  int get sessionRevision => _sessionRevision;

  // A public simulated trial shares the same origin as the real application.
  // It must never overwrite an existing account's persisted token.
  static const _tokenStorageKey = publicTrial
      ? 'coupon_trial_access_token'
      : 'access_token';

  Future<void> loadToken() async {
    final revision = _sessionRevision;
    final prefs = await SharedPreferences.getInstance();
    if (revision != _sessionRevision) return;
    final token = prefs.getString(_tokenStorageKey);
    if (_token != token) {
      _token = token;
      _sessionRevision++;
      notifyListeners();
    }
  }

  Future<void> saveToken(String token, {bool persist = true}) async {
    _token = token;
    _sessionRevision++;
    notifyListeners();
    final prefs = await SharedPreferences.getInstance();
    if (persist) {
      await prefs.setString(_tokenStorageKey, token);
    } else {
      await prefs.remove(_tokenStorageKey);
    }
  }

  Future<void> clearToken() async {
    _token = null;
    _sessionRevision++;
    notifyListeners();
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_tokenStorageKey);
  }

  int _trialRoleRequest = 0;
  Future<void> _trialRoleTail = Future<void>.value();

  ({int request, int revision}) beginTrialRoleRequest() =>
      (request: ++_trialRoleRequest, revision: _sessionRevision);

  Future<String> requestTrialRole(String role) async {
    if (role != 'customer' && role != 'vendor') throw const FormatException();
    final result = await post('/trial/role', body: {'role': role});
    final token = result['access_token'];
    if (token is! String || token.isEmpty ||
        result['role'] != role || result['mode'] != 'simulated') {
      throw const FormatException();
    }
    return token;
  }

  /// One commit lane for the direct entry and menu. A cancelled/superseded
  /// change never activates, and restores storage before the next change writes.
  Future<bool> commitTrialRole(
    String? token, {
    required ({int request, int revision}) attempt,
    required bool Function() isCurrent,
    Future<void> Function()? beforeActivate,
  }) {
    bool eligible() =>
        attempt.request == _trialRoleRequest &&
        attempt.revision == _sessionRevision &&
        isCurrent();

    final operation = _trialRoleTail.then((_) async {
      if (!eligible()) return false;
      try {
        await prepareTrialToken(token);
        if (eligible() && beforeActivate != null) {
          await beforeActivate();
        }
        if (eligible()) {
          activateTrialToken(token);
          return true;
        }
      } catch (_) {
        // SharedPreferences can change its cache before a platform write fails.
        // Keep returning the original failure even if the store remains down.
        try {
          await prepareTrialToken(_token);
        } catch (_) {}
        rethrow;
      }
      await prepareTrialToken(_token);
      return false;
    });

    _trialRoleTail = operation.then<void>((_) {}, onError: (Object _) {});
    return operation;
  }

  /// Stage trial identity before tearing down the old route. The active
  /// in-memory identity is changed only after persistence succeeds.
  Future<void> prepareTrialToken(String? token) async {
    final prefs = await SharedPreferences.getInstance();
    final saved = token == null
        ? await prefs.remove(_tokenStorageKey)
        : await prefs.setString(_tokenStorageKey, token);
    if (!saved) throw StateError('Unable to save trial identity');
  }

  void activateTrialToken(String? token) {
    _token = token;
    _sessionRevision++;
    notifyListeners();
  }

  bool get isLoggedIn => _token != null;

  /// Used only to resume the authenticated test-payment flow in this tab.
  String? get paymentAccessToken => _token;

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_token != null) 'Authorization': 'Bearer $_token',
      };

  Future<Map<String, dynamic>> get(String path,
      {Map<String, String>? query}) async {
    final uri =
        Uri.parse('$baseUrl/api/v3$path').replace(queryParameters: query);
    final res = await http.get(uri, headers: _headers);
    return _decode(res);
  }

  Future<Map<String, dynamic>> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    final uri =
        Uri.parse('$baseUrl/api/v3$path').replace(queryParameters: query);
    final res = await http.post(uri,
        headers: _headers, body: body == null ? null : jsonEncode(body));
    return _decode(res);
  }

  Future<Map<String, dynamic>> put(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    final uri =
        Uri.parse('$baseUrl/api/v3$path').replace(queryParameters: query);
    final res = await http.put(uri,
        headers: _headers, body: body == null ? null : jsonEncode(body));
    return _decode(res);
  }

  Future<Map<String, dynamic>> patch(String path,
      {Map<String, dynamic>? body}) async {
    final uri = Uri.parse('$baseUrl/api/v3$path');
    final res = await http.patch(uri,
        headers: _headers, body: body == null ? null : jsonEncode(body));
    return _decode(res);
  }

  Future<Map<String, dynamic>> delete(String path,
      {Map<String, String>? query}) async {
    final uri =
        Uri.parse('$baseUrl/api/v3$path').replace(queryParameters: query);
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
      final detail = data is Map ? data['detail'] : null;
      var message = detail is String ? detail : '請求失敗';
      if (res.statusCode == 422 && detail is List) {
        final invalidEmail = detail.any((entry) =>
            entry is Map &&
            entry['loc'] is List &&
            (entry['loc'] as List).contains('email'));
        message = invalidEmail ? '請輸入有效的電郵地址' : '請檢查輸入資料';
      }
      throw ApiException(res.statusCode, message,
          code: data is Map && data['code'] is String
              ? data['code'] as String
              : null);
    }
    return data as Map<String, dynamic>;
  }

  @override
  void dispose() {
    appConfig.dispose();
    super.dispose();
  }

  Future<Map<String, dynamic>> uploadMerchantDocument(
      String kind, String filename, Uint8List bytes) async {
    final ext = filename.split('.').last.toLowerCase();
    final type = ext == 'pdf'
        ? MediaType('application', 'pdf')
        : MediaType('image', ext == 'png' ? 'png' : 'jpeg');
    final req = http.MultipartRequest(
        'POST', Uri.parse('$baseUrl/api/v3/merchant-files'));
    if (_token != null) req.headers['Authorization'] = 'Bearer $_token';
    req.fields['kind'] = kind;
    req.files.add(http.MultipartFile.fromBytes('file', bytes,
        filename: filename, contentType: type));
    return _decode(await http.Response.fromStream(await req.send()));
  }

  Future<Map<String, dynamic>> myMerchantApplications() =>
      get('/merchant-applications/mine');
  Future<Map<String, dynamic>> currentMerchantContract() =>
      get('/merchant-contracts/current');
  Future<Map<String, dynamic>> signMerchantContract(int id, int versionId) =>
      post('/merchant-applications/$id/sign-contract',
          body: {'contract_version_id': versionId});
  Future<Map<String, dynamic>> merchantContractPdf(int id) =>
      get('/merchant-applications/$id/contract-pdf');

  Future<Map<String, dynamic>> startApplicationConnect(int id) =>
      post('/merchant-applications/$id/connect/onboarding', body: {});
  Future<Map<String, dynamic>> syncApplicationConnect(int id) =>
      post('/merchant-applications/$id/connect/sync', body: {});
  Future<Map<String, dynamic>> merchantConnectBalance(int shopId) =>
      get('/merchant/connect/balance', query: {'shop_id': '$shopId'});
  Future<Map<String, dynamic>> activeMerchantPayouts(int shopId) =>
      get('/merchant/connect/payouts/active', query: {'shop_id': '$shopId'});
  Future<Map<String, dynamic>> createMerchantPayout(
          int shopId, String amount, String requestId) =>
      post('/merchant/connect/payouts', body: {
        'shop_id': shopId,
        'amount': amount,
        'request_id': requestId,
      });

  Future<Map<String, dynamic>> merchantBalance() => get('/merchant/balance');
  Future<Map<String, dynamic>> merchantDashboard(int days) =>
      get('/merchant/dashboard', query: {'days': '$days'});
  Future<Map<String, dynamic>> merchantShopBalance(int shopId) =>
      get('/merchant/shops/$shopId/balance');
  Future<Map<String, dynamic>> merchantShopDashboard(int shopId, int days) =>
      get('/merchant/shops/$shopId/dashboard', query: {'days': '$days'});
  Future<Map<String, dynamic>> merchantBalanceEntries(int shopId, int page) =>
      get('/merchant/shops/$shopId/balance/entries', query: {'page': '$page'});

  // ---- auth ----
  Future<Map<String, dynamic>> login(String email, String password) async {
    final data =
        await post('/login', body: {'email': email, 'password': password});
    await saveToken(data['access_token'] as String);
    return data;
  }

  Future<void> register(
          String email, String password, String name, String role) =>
      post('/new-register', body: {
        'email': email,
        'password': password,
        'name': name,
        'role': role
      });

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
      (await post('/auto-suggestion-new',
          query: {'keyword': keyword}))['suggestions'] as List<dynamic>;

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
          {int page = 1,
          bool hasImages = false,
          String sort = 'latest'}) async =>
      get('/shops/$shopId/reviews', query: {
        'page': '$page',
        if (hasImages) 'has_images': 'true',
        // Gap 9: sort=helpful is a real server sort by helpful_count.
        if (sort == 'helpful') 'sort': 'helpful',
      });
  Future<Map<String, dynamic>> merchantReviews(
    int shopId, {
    required int page,
    required String sort,
    required String status,
    bool? unreplied,
    int? maxStars,
  }) =>
      get('/merchant/reviews', query: {
        'shop_id': '$shopId',
        'page': '$page',
        'sort': sort,
        'status': status,
        if (unreplied != null) 'unreplied': '$unreplied',
        if (maxStars != null) 'max_stars': '$maxStars',
      });

  Future<int?> merchantReviewPendingCount({int? shopId}) async {
    final reply = await get(
      '/merchant/reviews/pending-count',
      query: {if (shopId != null) 'shop_id': '$shopId'},
    );
    final total = reply['total'];
    return reply.containsKey('shop_id') &&
            reply['shop_id'] == shopId &&
            total is int &&
            total >= 0
        ? total
        : null;
  }

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
  Future<Map<String, dynamic>> adminShops() async => get('/admin/shops');
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
  Future<Map<String, dynamic>> withdrawCashback(
          double amount, String method, String accountName, String accountRef,
          {String? requestKey}) =>
      post('/cashback/withdraw', body: {
        'amount': amount,
        'method': method,
        'account_name': accountName,
        'account_ref': accountRef,
        if (requestKey != null) 'request_key': requestKey,
      });
  Future<Map<String, dynamic>> withdrawalPage({int? before}) =>
      get('/cashback/withdrawals',
          query: {if (before != null) 'before': '$before'});
  Future<List<dynamic>> cashbackMissingAppeals() async =>
      (await get('/cashback/missing'))['appeals'] as List<dynamic>;
  Future<Map<String, dynamic>> submitMissingCashback(
          int merchantId,
          String orderNo,
          String spentAt,
          double amount,
          String evidence,
          String note) =>
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
      (await post('/favorite-coupon-all',
          query: {'coupon_id': '$id'}))['is_coupon_favorite'] as bool;
  Future<bool> toggleFavoriteShop(int id) async => (await post('/favorite-shop',
      query: {'shop_id': '$id'}))['is_shop_favorite'] as bool;
  Future<Map<String, dynamic>> notifications() async =>
      get('/get-notification-list');
  Future<void> markNotificationRead(int id) => post('/notifications/$id/read');
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
  Future<Map<String, dynamic>> prepareScan(String code,
          {int? branchId}) async =>
      post('/new-scan-coupon/prepare', body: {
        'code': code,
        if (branchId != null) 'branch_id': branchId,
      });
  Future<Map<String, dynamic>> scanBatch(List<String> codes, List<String> tokens,
          List<int?> branchIds, String requestId) =>
      post('/new-scan-coupon/batch', body: {
        'codes': codes,
        'prepare_tokens': tokens,
        'idempotency_key': requestId,
        if (branchIds.any((id) => id != null)) 'branch_ids': branchIds,
      });
  Future<Map<String, dynamic>> revokeRedemption(int redemptionId) async =>
      post('/redemptions/$redemptionId/revoke',
          query: {'acknowledge_existing': 'true'});
  Future<Map<String, dynamic>> vendorRedemptionsExport() async =>
      get('/vendor/redemptions/export');
  // The list and raw CSV use the same server-side calendar date filter.
  Map<String, String> _redemptionDateQuery(DateTime? from, DateTime? to) {
    String day(DateTime date) =>
        '${date.year.toString().padLeft(4, '0')}-${date.month.toString().padLeft(2, '0')}-${date.day.toString().padLeft(2, '0')}';
    final start = from == null ? null : day(from);
    final end = to == null ? null : day(to);
    if (start != null && end != null && start.compareTo(end) > 0) {
      throw ArgumentError('Start date must not be after end date');
    }
    return {if (start != null) 'from': start, if (end != null) 'to': end};
  }

  Future<List<Map<String, dynamic>>> vendorRedemptions(
      {DateTime? from, DateTime? to}) async {
    final query = _redemptionDateQuery(from, to);
    final data = await get('/vendor/redemptions/export',
        query: query.isEmpty ? null : query);
    final records = data['records'];
    if (records is! List || records.any((item) => item is! Map)) {
      throw const FormatException('Invalid redemption records');
    }
    return records.map((e) => Map<String, dynamic>.from(e as Map)).toList();
  }

  Future<String> vendorRedemptionsCsv({DateTime? from, DateTime? to}) async {
    final uri = Uri.parse('$baseUrl/api/v3/vendor/redemptions/export')
        .replace(queryParameters: {
      'format': 'csv',
      ..._redemptionDateQuery(from, to),
    });
    final res = await http.get(uri, headers: _headers);
    if (res.statusCode >= 400) {
      throw ApiException(res.statusCode, '請求失敗');
    }
    return utf8.decode(res.bodyBytes);
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
      post('/bargain-sessions/$sessionId/help', body: {'device_id': deviceId});
  Future<Map<String, dynamic>> purchaseBargain(int sessionId) =>
      post('/bargain-sessions/$sessionId/purchase');
  Future<List<dynamic>> myBargainSessions() async =>
      (await get('/bargain-my-sessions'))['sessions'] as List<dynamic>;
  // ---- vendor ----
  Future<List<dynamic>> vendorCoupons({String? status}) async =>
      (await get('/get-vendor-coupon-list',
          query: status == null ? null : {'status': status}))['coupons']
          as List<dynamic>;
  Future<Map<String, dynamic>> vendorCouponPage({
    required int page,
    int pageSize = 20,
    String? status,
    String? query,
  }) => get('/get-vendor-coupon-list', query: {
    'page': '$page',
    'page_size': '$pageSize',
    if (status != null) 'status': status,
    if (query != null && query.isNotEmpty) 'q': query,
  });

  Future<Map<String, dynamic>> vendorCouponDetails(int id) =>
      get('/vendor/coupons/$id');

  Future<Map<String, dynamic>> editVendorCoupon(
          int id, Map<String, dynamic> changedFields) =>
      patch('/vendor/coupons/$id', body: changedFields);

  static bool isTrialCouponImagePath(String value) => RegExp(
          r'^/api/v3/trial/coupon-images/[a-f0-9]{32}\.webp$')
      .hasMatch(value);

  Future<String> uploadCouponImage(String filename, Uint8List bytes,
      {int? shopId}) async {
    final ext = filename.split('.').last.toLowerCase();
    final subtype = switch (ext) {
      'jpg' || 'jpeg' => 'jpeg',
      'png' => 'png',
      'webp' => 'webp',
      _ => throw const FormatException('Unsupported coupon image'),
    };
    if (bytes.isEmpty || bytes.length > 5 * 1024 * 1024) {
      throw const FormatException('Invalid coupon image size');
    }
    if (publicTrial && (shopId == null || shopId < 1 || _token == null)) {
      throw const FormatException('Choose a shop and merchant identity');
    }
    const endpoint = publicTrial
        ? '/api/v3/trial/coupon-images'
        : '/api/v3/uploads/merchant-images';
    final req = http.MultipartRequest('POST', Uri.parse('$baseUrl$endpoint'));
    if (_token != null) req.headers['Authorization'] = 'Bearer $_token';
    if (publicTrial) req.fields['shop_id'] = '$shopId';
    req.files.add(http.MultipartFile.fromBytes('files', bytes,
        filename: 'coupon.$ext', contentType: MediaType('image', subtype)));
    final response = _decode(await http.Response.fromStream(await req.send()));
    final images = response['images'];
    if (images is! List || images.length != 1 || images.single is! String ||
        !(publicTrial
            ? isTrialCouponImagePath(images.single as String)
            : RegExp(r'^/uploads/merchant-images/[a-zA-Z0-9_-]+\.(?:jpg|jpeg|png|webp)$')
                .hasMatch(images.single as String))) {
      throw const FormatException('Invalid coupon image receipt');
    }
    return images.single as String;
  }


  Future<String> updateVendorCouponStatus(
    int id,
    VendorCouponAction action,
  ) async {
    final result = await post('/vendor/coupons/$id/${action.endpoint}');
    final status = result['status'];
    if (result['coupon_id'] != id ||
        status is! String ||
        !action.confirmedStates.contains(status)) {
      throw const FormatException('Invalid coupon status response');
    }
    return status;
  }

  Future<Map<String, dynamic>> vendorPackageWorkspace() =>
      get('/vendor/package-workspace');
  Future<Map<String, dynamic>> createVendorPackage(Map<String, dynamic> body) =>
      post('/packages', body: body);
  Future<Map<String, dynamic>> editVendorPackage(
          int id, Map<String, dynamic> body) =>
      patch('/vendor/packages/$id', body: body);
  Future<Map<String, dynamic>> vendorPackageAction(int id, String action) =>
      post('/vendor/packages/$id/$action');

  // ---- packages / orders (line C: 套餐 2A) ----
  Future<List<dynamic>> packages({int? shopId}) async =>
      (await get('/packages', query: {
        if (shopId != null) 'shop_id': '$shopId',
      }))['packages'] as List<dynamic>;
  Future<Map<String, dynamic>> packageDetails(int id) async =>
      (await get('/package-details', query: {'package_id': '$id'}))['package']
          as Map<String, dynamic>;
  // Public trial projection is deliberately independent of account headers.
  Future<Map<String, dynamic>> publicTrialPackage(String slug) async {
    final uri = Uri.parse('$baseUrl/api/v3/trial/public-packages/')
        .resolve(Uri.encodeComponent(slug));
    return _decode(
        await http.get(uri, headers: {'Accept': 'application/json'}));
  }

  Future<Map<String, dynamic>> purchasePackage(int id, String method,
          {String clientRef = '', int quantity = 1}) async =>
      post('/packages/$id/purchase', body: {
        'payment_method': method,
        'client_ref': clientRef,
        if (quantity > 1) 'quantity': quantity,
      });
  Future<Map<String, dynamic>> purchasePaidCoupon(int id, String method,
          {required String clientRef}) =>
      post('/coupons/$id/purchase', body: {
        'payment_method': method,
        'client_ref': clientRef,
      });
  // NOTE (UI_SPEC §3.3): the client never posts a self-reported payment
  // outcome. Results come from the gateway; the Flutter client only
  // polls GET /orders/{id} (see PaymentResultScreen). The old wrapper
  // was removed so no call site can regress.
  Future<Map<String, dynamic>> retryPayment(int orderId, String method) =>
      post('/orders/$orderId/retry-payment', body: {'payment_method': method});
  Future<Map<String, dynamic>> cancelOrder(int orderId) =>
      post('/orders/$orderId/cancel');
  Future<Map<String, dynamic>> refundOrder(int orderId, {String reason = ''}) =>
      post('/orders/$orderId/refund', body: {'reason': reason});
  Future<Map<String, dynamic>> refundPackage(int id, {String reason = ''}) =>
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
          {String? idempotencyKey,
          String? prepareToken,
          int? branchId}) async =>
      post('/new-scan-coupon', body: {
        'code': code,
        if (branchId != null) 'branch_id': branchId,
        if (idempotencyKey != null) 'idempotency_key': idempotencyKey,
        if (prepareToken != null) 'prepare_token': prepareToken,
      });
  Future<List<dynamic>> subscriptionPlans() async =>
      (await get('/get-subscription-plan-list'))['plans'] as List<dynamic>;

  // ---- admin v2 (line F) ----
  Future<Map<String, dynamic>> adminOverview() async => get('/admin/overview');
  Future<Map<String, dynamic>> settlements(
          {int? shopId, String? status, int page = 1}) async =>
      get('/admin/settlements', query: {
        'page': '$page',
        if (shopId != null) 'shop_id': '$shopId',
        if (status != null) 'status': status,
      });
  Future<Map<String, dynamic>> generateSettlement(int shopId, String period,
          [double? commissionRate]) async =>
      post('/admin/settlements/generate', body: {
        'shop_id': shopId,
        'period': period,
        if (commissionRate != null) 'commission_rate': commissionRate,
      });
  Future<Map<String, dynamic>> approveSettlement(int id) async =>
      post('/admin/settlements/$id/approve');
  Future<Map<String, dynamic>> disputeSettlement(int id, String reason) async =>
      post('/admin/settlements/$id/dispute', body: {'reason': reason});
  Future<Map<String, dynamic>> resolveSettlement(
          int id, String decision, String note) async =>
      post('/admin/settlements/$id/resolve',
          body: {'decision': decision, 'note': note});
  Future<Map<String, dynamic>> paySettlement(
          int id, num amount, String reference) async =>
      post('/admin/settlements/$id/pay',
          body: {'amount': amount, 'reference': reference});
  Future<Map<String, dynamic>> reconciliationRun(
          int shopId, String period) async =>
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
  Future<Map<String, dynamic>> createCategory(
          Map<String, dynamic> body) async =>
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
  ApiException(this.status, this.message, {this.code});
  final int status;
  final String message;
  final String? code;
  @override
  String toString() => message;
}
