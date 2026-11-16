import 'dart:convert';

import 'package:http/http.dart' as http;
import 'package:shared_preferences/shared_preferences.dart';

/// API client for /api/v3 (endpoint names mirror the original app).
/// Base URL comes from --dart-define=API_BASE_URL, defaulting to the local
/// dev server. Guest calls (home, banners) need no token; Bearer token is
/// attached automatically when present.
class ApiClient {
  ApiClient({String? baseUrl})
      : baseUrl = baseUrl ??
            const String.fromEnvironment(
              'API_BASE_URL',
              defaultValue: 'http://localhost:8000',
            );

  final String baseUrl;
  String? _token;

  Future<void> loadToken() async {
    final prefs = await SharedPreferences.getInstance();
    _token = prefs.getString('access_token');
  }

  Future<void> saveToken(String token) async {
    _token = token;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setString('access_token', token);
  }

  Future<void> clearToken() async {
    _token = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove('access_token');
  }

  bool get isLoggedIn => _token != null;

  Map<String, String> get _headers => {
        'Content-Type': 'application/json',
        if (_token != null) 'Authorization': 'Bearer $_token',
      };

  Future<dynamic> get(String path, {Map<String, String>? query}) async {
    final uri = Uri.parse('$baseUrl/api/v3$path').replace(queryParameters: query);
    final res = await http.get(uri, headers: _headers);
    return _decode(res);
  }

  Future<dynamic> post(String path,
      {Map<String, dynamic>? body, Map<String, String>? query}) async {
    final uri = Uri.parse('$baseUrl/api/v3$path').replace(queryParameters: query);
    final res = await http.post(uri, headers: _headers,
        body: body == null ? null : jsonEncode(body));
    return _decode(res);
  }

  dynamic _decode(http.Response res) {
    final data = res.body.isEmpty ? {} : jsonDecode(res.body);
    if (res.statusCode >= 400) {
      throw ApiException(res.statusCode,
          (data is Map && data['detail'] != null) ? '${data['detail']}' : '請求失敗');
    }
    return data;
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
  Future<Map<String, dynamic>> home() async => get('/home');
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
  Future<void> claimCoupon(int couponId) =>
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
  Future<Map<String, dynamic>> exchangePoints(int couponId) =>
      post('/points/exchange', query: {'coupon_id': '$couponId'});

  // ---- favourites / notifications / records ----
  Future<void> toggleFavoriteCoupon(int id) =>
      post('/favorite-coupon-all', query: {'coupon_id': '$id'});
  Future<void> toggleFavoriteShop(int id) =>
      post('/favorite-shop', query: {'shop_id': '$id'});
  Future<Map<String, dynamic>> notifications() async =>
      get('/get-notification-list');
  Future<void> markNotificationRead(int id) =>
      post('/notifications/$id/read');
  Future<void> markAllNotificationsRead() => post('/notifications/read-all');
  Future<List<dynamic>> records() async =>
      (await get('/get-user-record-list'))['records'] as List<dynamic>;

  // ---- vendor ----
  Future<List<dynamic>> vendorCoupons() async =>
      (await get('/get-vendor-coupon-list'))['coupons'] as List<dynamic>;
  Future<Map<String, dynamic>> couponPreview(Map<String, dynamic> body) =>
      post('/add-coupon-preview', body: body);
  Future<Map<String, dynamic>> couponCreate(Map<String, dynamic> body) =>
      post('/coupon-previewed', body: body);
  Future<Map<String, dynamic>> usageData() async =>
      get('/get-vendor-usage-data-new');
  Future<Map<String, dynamic>> scanCoupon(String code) =>
      post('/new-scan-coupon', body: {'code': code});
  Future<List<dynamic>> subscriptionPlans() async =>
      (await get('/get-subscription-plan-list'))['plans'] as List<dynamic>;
}

class ApiException implements Exception {
  ApiException(this.status, this.message);
  final int status;
  final String message;
  @override
  String toString() => message;
}
