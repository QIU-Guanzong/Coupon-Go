import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../api_client.dart';
import '../models.dart';
import '../language.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import 'reviews_admin.dart';
import 'vendor_packages.dart';

/// Vendor home: gold theme, usage summary, issue-coupon entry.
class VendorHomeScreen extends StatefulWidget {
  const VendorHomeScreen({super.key, required this.api, this.showAdmin = false});

  final ApiClient api;
  final bool showAdmin;

  @override
  State<VendorHomeScreen> createState() => _VendorHomeScreenState();
}

class _VendorHomeScreenState extends State<VendorHomeScreen> {
  Map<String, dynamic>? _usage;
  List<Coupon> _coupons = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final usage = await widget.api.usageData();
      final list = await widget.api.vendorCoupons();
      if (!mounted) return;
      setState(() {
        _usage = usage;
        _coupons = list.map((e) => Coupon.fromJson(e)).toList();
      });
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: CgColors.vendorGold,
        foregroundColor: CgColors.textPrimary,
        title: const Text('企業版'),
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Row(
              children: [
                Expanded(
                    child: _stat('總下載次數', '${_usage?['total_downloads'] ?? 0}')),
                const SizedBox(width: 12),
                Expanded(child: _stat('總使用量', '${_usage?['total_usage'] ?? 0}')),
              ],
            ),
            const SizedBox(height: 16),
            ElevatedButton(
              style: ElevatedButton.styleFrom(backgroundColor: CgColors.vendorGold),
              onPressed: () => Navigator.of(context)
                  .pushNamed('/vendor/issue')
                  .then((_) => _load()),
              child: const Text('發券',
                  style: TextStyle(color: CgColors.textPrimary)),
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => VendorPackagesScreen(api: widget.api))),
              icon: const Icon(Icons.restaurant_menu),
              label: Text(context.tr('套餐管理', 'Packages')),
            ),
            const SizedBox(height: 8),
            OutlinedButton(
              onPressed: () => Navigator.of(context).pushNamed('/vendor/records'),
              child: const Text('核銷記錄'),
            ),
            const SizedBox(height: 8),
            OutlinedButton(
              onPressed: () {
                if (_coupons.isEmpty) {
                  showCgToast(context, '暫無商戶資料，無法查看評價');
                  return;
                }
                final shop = _coupons.first;
                Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => MerchantReviewReplyScreen(
                        api: widget.api,
                        shopId: shop.shopId,
                        shopName: shop.shopName)));
              },
              child: const Text('評價回覆（差評）'),
            ),
            const SizedBox(height: 8),
            if (widget.showAdmin)
              OutlinedButton(
                onPressed: () =>
                    Navigator.of(context).pushNamed('/admin'),
                child: const Text('管理後台（結算 / 對賬 / 發票 / 內容 / 角色）'),
              ),
            const SizedBox(height: 16),
            OutlinedButton(onPressed: () => Navigator.of(context).pushNamed('/vendor/settlements'),
                child: const Text('商家結算單')),
            const Text('我的優惠券', style: TextStyle(fontWeight: FontWeight.bold)),
            ..._coupons.map((c) => Card(
                  margin: const EdgeInsets.only(top: 8),
                  child: ListTile(
                    leading: const Icon(Icons.local_offer, color: CgColors.amber),
                    title: Text(c.name),
                    subtitle: Text('剩餘 ${c.remaining} · 已核銷 ${c.usedCount ?? '—'}'),
                  ),
                )),
            if (_coupons.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 24),
                child: Text('暫時沒有優惠券，點上面發第一張吧！'),
              ),
          ],
        ),
      ),
    );
  }

  Widget _stat(String label, String value) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          children: [
            Text(value,
                style: const TextStyle(fontSize: 28, fontWeight: FontWeight.bold)),
            Text(label, style: const TextStyle(color: CgColors.textSecondary)),
          ],
        ),
      ),
    );
  }
}

class VendorAccountScreen extends StatefulWidget {
  const VendorAccountScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<VendorAccountScreen> createState() => _VendorAccountScreenState();
}

class _VendorAccountScreenState extends State<VendorAccountScreen> {
  late Future<Map<String, dynamic>> _profile = widget.api.get('/me');

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: CgColors.vendorGold,
        foregroundColor: CgColors.textPrimary,
        title: Text(context.tr('商戶帳戶', 'Merchant account')),
      ),
      body: ListView(
        children: [
          FutureBuilder<Map<String, dynamic>>(
            future: _profile,
            builder: (context, snapshot) {
              if (snapshot.connectionState != ConnectionState.done) {
                return const Padding(
                  padding: EdgeInsets.all(16),
                  child: SkeletonBox(height: 64),
                );
              }
              if (snapshot.hasError) {
                return ListTile(
                  title: Text(context.tr('未能載入帳戶', 'Could not load the account')),
                  trailing: TextButton(
                    onPressed: () {
                      final result = widget.api.get('/me');
                      setState(() { _profile = result; });
                    },
                    child: Text(context.tr('重試', 'Retry')),
                  ),
                );
              }
              final name = snapshot.data?['name'] as String? ?? '';
              final email = snapshot.data?['email'] as String? ?? '';
              if (name.isEmpty && email.isEmpty) {
                return ListTile(title: Text(context.tr('未有帳戶資料', 'No account details')));
              }
              return ListTile(
                leading: const Icon(Icons.storefront),
                title: Text(name.isEmpty ? email : name),
                subtitle: name.isNotEmpty && email.isNotEmpty ? Text(email) : null,
              );
            },
          ),
          ListTile(
            leading: const Icon(Icons.credit_card),
            title: Text(context.tr('訂閱方案', 'Subscription plans')),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => Navigator.of(context).pushNamed('/vendor/plans'),
          ),
        ],
      ),
    );
  }
}

/// Issue coupon: two-step — fill form -> preview -> submit for review.
class IssueCouponScreen extends StatefulWidget {
  const IssueCouponScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<IssueCouponScreen> createState() => _IssueCouponScreenState();
}

class _IssueCouponScreenState extends State<IssueCouponScreen> {
  final _name = TextEditingController();
  String _type = 'Discount';
  final _percentage = TextEditingController(text: '85');
  final _cash = TextEditingController();
  final _total = TextEditingController(text: '100');
  final _minSpend = TextEditingController(text: '0');
  final _terms = TextEditingController();
  final int _shopId = 1;
  DateTime _validTo = DateTime.now().add(const Duration(days: 30));

  Map<String, dynamic> _body() => {
        'shop_id': _shopId,
        'coupon_type': _type,
        'coupon_name': _name.text,
        'percentage': _type == 'Discount' ? double.tryParse(_percentage.text) : null,
        'cash_amount': _type == 'Cash' ? double.tryParse(_cash.text) : null,
        'min_spend': double.tryParse(_minSpend.text) ?? 0,
        'total': int.tryParse(_total.text) ?? 100,
        'additional_terms': _terms.text,
        'valid_from': DateTime.now().toIso8601String(),
        'valid_to': _validTo.toIso8601String(),
      };

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: CgColors.vendorGold,
        foregroundColor: CgColors.textPrimary,
        title: const Text('發券'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
              controller: _name,
              decoration: const InputDecoration(labelText: '優惠券名稱')),
          const SizedBox(height: 12),
          DropdownButtonFormField<String>(
            initialValue: _type,
            decoration: const InputDecoration(labelText: '券類型'),
            items: const [
              DropdownMenuItem(value: 'Cash', child: Text('現金優惠券')),
              DropdownMenuItem(value: 'Discount', child: Text('折扣優惠券')),
              DropdownMenuItem(value: 'Gift', child: Text('贈品優惠券')),
            ],
            onChanged: (v) => setState(() => _type = v ?? 'Discount'),
          ),
          const SizedBox(height: 12),
          if (_type == 'Discount')
            TextField(
                controller: _percentage,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: '折扣（例: 85）')),
          if (_type == 'Cash')
            TextField(
                controller: _cash,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: '現金金額')),
          const SizedBox(height: 12),
          TextField(
              controller: _total,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: '數量')),
          const SizedBox(height: 12),
          TextField(
              controller: _minSpend,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: '需消費金額')),
          const SizedBox(height: 12),
          TextField(
              controller: _terms,
              decoration: const InputDecoration(labelText: '條款和條件')),
          const SizedBox(height: 12),
          ListTile(
            title: const Text('到期日期'),
            subtitle: Text(_validTo.toLocal().toString().split(' ').first),
            trailing: const Icon(Icons.calendar_today),
            onTap: () async {
              final picked = await showDatePicker(
                context: context,
                initialDate: _validTo,
                firstDate: DateTime.now(),
                lastDate: DateTime.now().add(const Duration(days: 365)),
              );
              if (picked != null) setState(() => _validTo = picked);
            },
          ),
          const SizedBox(height: 16),
          ElevatedButton(
            style: ElevatedButton.styleFrom(backgroundColor: CgColors.vendorGold),
            onPressed: () async {
              try {
                final preview = await widget.api.couponPreview(_body());
                if (!context.mounted) return;
                final ok = await showDialog<bool>(
                  context: context,
                  builder: (c) => AlertDialog(
                    title: const Text('Coupon Preview'),
                    content: Text(
                        '${preview['coupon_name']}\n${preview['amount_text']}\n數量：${preview['total']}'),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(c, false),
                          child: const Text('取消')),
                      TextButton(
                          onPressed: () => Navigator.pop(c, true),
                          child: const Text('提交審核')),
                    ],
                  ),
                );
                if (ok == true) {
                  final res = await widget.api.couponCreate(_body());
                  if (context.mounted) {
                    showCgToast(context, res['message'] as String? ?? '已提交審核');
                    Navigator.of(context).pop();
                  }
                }
              } on ApiException catch (e) {
                if (context.mounted) showCgToast(context, e.message);
              }
            },
            child: Text(context.tr('檢查並提交', 'Review and submit'),
                style: const TextStyle(color: CgColors.textPrimary)),
          ),
        ],
      ),
    );
  }
}

Future<bool?> _confirmRevoke(BuildContext context, String coupon) =>
    showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('撤銷核銷'),
        content: Text('確定撤銷「$coupon」的這筆核銷？顧客的積分及相關返現會一併更新。'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: const Text('取消')),
          TextButton(
              onPressed: () => Navigator.pop(c, true),
              child: const Text('確認撤銷')),
        ],
      ),
    );

/// Scan with the camera or enter a six-digit code.
class ScanScreen extends StatefulWidget {
  const ScanScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<ScanScreen> createState() => _ScanScreenState();
}

class _ScanScreenState extends State<ScanScreen> {
  final _code = TextEditingController();
  bool _handled = false;
  // Line A two-step redemption: prepare (read-only check) -> confirm consume.
  Map<String, dynamic>? _prepared;
  String? _pendingCode;
  String? _attemptKey;
  int? _lastRedemptionId;
  bool _revoking = false;
  // UI_SPEC §8: full-screen success / failure result pages.
  String? _successCouponName;
  String? _revokedMessage;
  DateTime? _redeemedAt;
  int _revokeSeconds = 600;
  String? _errorReason;
  Timer? _revokeTimer;

  @override
  void dispose() {
    _code.dispose();
    _revokeTimer?.cancel();
    super.dispose();
  }

  String _dateText(String? raw) {
    if (raw == null || raw.isEmpty) return '—';
    final d = DateTime.tryParse(raw);
    if (d == null) return raw;
    return '${d.year}年${d.month}月${d.day}日';
  }

  void _resetScan() {
    _revokeTimer?.cancel();
    setState(() {
      _prepared = null;
      _pendingCode = null;
      _successCouponName = null;
      _revokedMessage = null;
      _redeemedAt = null;
      _errorReason = null;
      _lastRedemptionId = null;
      _handled = false;
      _code.clear();
    });
  }

  Future<void> _prepare(String code) async {
    if (_handled || _prepared != null || _errorReason != null || _successCouponName != null) return;
    code = code.trim();
    if (!RegExp(r'^[0-9]{6}$').hasMatch(code)) {
      setState(() => _errorReason = '請輸入券上的六位兌換碼');
      return;
    }
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() => _handled = true);
    try {
      final res = await widget.api.prepareScan(code);
      if (!mounted) return;
      setState(() {
        if (res['can_redeem'] == true) {
          _prepared = res;
          _pendingCode = code;
          final random = Random.secure();
          _attemptKey = List.generate(16, (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0')).join();
          _errorReason = null;
        } else {
          // Invalid / already used / other-shop code: full-screen red page.
          _errorReason = res['reason'] as String? ?? '無法核銷';
          _prepared = null;
          _pendingCode = null;
        }
        _lastRedemptionId = null;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _errorReason = e.message);
    } catch (_) {
      if (mounted) setState(() => _errorReason = '未能連線，請重試');
    } finally {
      if (mounted) setState(() => _handled = false);
    }
  }

  Future<void> _confirmConsume() async {
    final code = _pendingCode;
    final token = _prepared?['prepare_token'] as String?;
    if (_handled || code == null || token == null || _attemptKey == null) return;
    setState(() => _handled = true);
    try {
      final res = await widget.api.scanCoupon(code,
          prepareToken: token,
          idempotencyKey: _attemptKey!);
      if (!mounted) return;
      setState(() {
        _successCouponName =
            res['coupon_name'] as String? ?? _prepared?['coupon_name'] as String? ?? '';
        _lastRedemptionId = res['redemption_id'] as int?;
        _revokeSeconds = ((res['revoke_window_minutes'] as int?) ?? 10) * 60;
        _redeemedAt = DateTime.tryParse(res['scanned_at'] as String? ?? '') ?? DateTime.now();
        _prepared = null;
        _pendingCode = null;
        _errorReason = null;
      });
      // Tick the revoke countdown; at 0 the button disappears (not greyed).
      _revokeTimer?.cancel();
      _revokeTimer = Timer.periodic(const Duration(seconds: 1), (t) {
        if (!mounted || _redeemedAt == null) {
          t.cancel();
          return;
        }
        if (_revokeLeft <= 0) {
          t.cancel();
          setState(() => _lastRedemptionId = null);
          return;
        }
        setState(() {});
      });
    } on ApiException catch (e) {
      if (mounted && e.status >= 500) {
        showCgToast(context, '未能確認結果，請再次確認核銷');
      } else if (mounted) {
        setState(() {
          _errorReason = e.message;
          _prepared = null;
          _pendingCode = null;
        });
      }
    } catch (_) {
      if (mounted) showCgToast(context, '未能確認結果，請再次確認核銷');
    } finally {
      if (mounted) setState(() => _handled = false);
    }
  }

  Future<void> _revoke() async {
    final rid = _lastRedemptionId;
    if (rid == null) return;
    final ok = await _confirmRevoke(context, _successCouponName ?? '優惠券');
    if (ok != true || !mounted || _revoking) return;
    setState(() => _revoking = true);
    try {
      final receipt = await widget.api.revokeRedemption(rid);
      if (!mounted) return;
      _revokeTimer?.cancel();
      setState(() {
        _lastRedemptionId = null;
        _revokedMessage = receipt['message'] as String?;
      });
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    } catch (_) {
      if (mounted) showCgToast(context, '未能確認撤銷結果，請重試');
    } finally {
      if (mounted) setState(() => _revoking = false);
    }
  }

  /// Seconds left in the 10-minute revoke window, from _redeemedAt.
  int get _revokeLeft {
    if (_redeemedAt == null) return 0;
    final elapsed = DateTime.now().difference(_redeemedAt!).inSeconds;
    return (_revokeSeconds - elapsed).clamp(0, _revokeSeconds);
  }

  Widget _detailRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          SizedBox(
              width: 110,
              child: Text(label,
                  style: const TextStyle(color: CgColors.textSecondary))),
          Expanded(child: Text(value)),
        ],
      ),
    );
  }

  Widget _successPage() {
    final left = _revokeLeft;
    final showRevoke = _lastRedemptionId != null && left > 0;
    return Container(
      color: Colors.white,
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(_revokedMessage == null ? Icons.check_circle : Icons.undo, size: 72,
              color: _revokedMessage == null ? CgColors.success : CgColors.textSecondary),
          const SizedBox(height: 16),
          Text(_revokedMessage ?? '已核銷', textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          Text(_successCouponName ?? '',
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 18)),
          const SizedBox(height: 24),
          if (showRevoke)
            SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                onPressed: _revoking ? null : _revoke,
                child: Text(_revoking
                    ? '撤銷中…'
                    : '撤銷核銷（剩餘 ${formatCountdown(Duration(seconds: left))}）'),
              ),
            ),
          const SizedBox(height: 8),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: _resetScan,
              child: const Text('繼續掃碼'),
            ),
          ),
        ],
      ),
    );
  }

  Widget _errorPage() {
    return Container(
      color: Colors.red.shade700,
      padding: const EdgeInsets.all(24),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Icon(Icons.error_outline, size: 72, color: Colors.white),
          const SizedBox(height: 16),
          const Text('無法核銷',
              style: TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.bold,
                  color: Colors.white)),
          const SizedBox(height: 8),
          Text(_errorReason ?? '無法核銷',
              textAlign: TextAlign.center,
              style: const TextStyle(fontSize: 16, color: Colors.white)),
          const SizedBox(height: 24),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              style: ElevatedButton.styleFrom(backgroundColor: Colors.white),
              onPressed: _resetScan,
              child: const Text('繼續掃碼',
                  style: TextStyle(color: Colors.red)),
            ),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: CgColors.vendorGold,
        foregroundColor: CgColors.textPrimary,
        title: const Text('掃碼核銷'),
      ),
      body: _errorReason != null
          ? _errorPage()
          : _successCouponName != null
              ? _successPage()
              : ListView(
                  children: [
                    if (_prepared == null) SizedBox(
                      height: MediaQuery.sizeOf(context).height * .48,
                      child: Stack(
                        children: [
                          MobileScanner(
                            errorBuilder: (_, __, ___) => const ColoredBox(
                              color: Colors.black,
                              child: Center(child: Text('相機未能使用，請手動輸入兌換碼',
                                style: TextStyle(color: Colors.white))),
                            ),
                            onDetect: (capture) {
                              final value =
                                  capture.barcodes.firstOrNull?.rawValue;
                              if (value != null) {
                                final code = Uri.tryParse(value)
                                        ?.queryParameters['code'] ??
                                    value;
                                _prepare(code);
                              }
                            },
                          ),
                          Center(
                            child: Image.asset(
                              'assets/images/ic_scan_frame.png',
                              width: 260,
                              errorBuilder: (_, __, ___) => Container(
                                width: 260,
                                height: 260,
                                decoration: BoxDecoration(
                                  border: Border.all(
                                      color: CgColors.primary, width: 3),
                                  borderRadius: BorderRadius.circular(12),
                                ),
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                    if (_prepared == null) const Padding(
                      padding: EdgeInsets.all(8),
                      child: Text('請將 QR 碼放入方框中並進行掃描\n先預核銷確認券面與門店，再按確認核銷'),
                    ),
                    if (_prepared != null && _prepared!['can_redeem'] == true)
                      Padding(
                        padding:
                            const EdgeInsets.symmetric(horizontal: 16),
                        child: Card(
                          child: Padding(
                            padding: const EdgeInsets.all(16),
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                    _prepared!['coupon_name'] as String? ??
                                        '',
                                    style: const TextStyle(
                                        fontWeight: FontWeight.bold,
                                        fontSize: 16)),
                                const SizedBox(height: 8),
                                _detailRow('門店',
                                    '${_prepared!['shop_name'] ?? ''}'),
                                // Customer phone last-4 / validity come
                                // from prepare_scan when the backend
                                // sends them; '—' until then.
                                _detailRow(
                                    '顧客手機後四位',
                                    _prepared!['customer_phone_last4']
                                            as String? ??
                                        '—'),
                                _detailRow('有效期',
                                    _dateText(_prepared!['valid_to'] as String?)),
                                if (_prepared!['package_quantity'] != null)
                                  _detailRow('整券核銷', "${_prepared!['package_quantity']} 份"),
                                _detailRow('是否已使用', '未使用'),
                                const SizedBox(height: 12),
                                Row(
                                  children: [
                                    Expanded(
                                      child: ElevatedButton(
                                        onPressed: _handled ? null : _confirmConsume,
                                        child: Text(_handled ? '核銷中…' : '確認核銷'),
                                      ),
                                    ),
                                    const SizedBox(width: 8),
                                    TextButton(
                                      onPressed: _handled ? null : () => setState(() {
                                        _prepared = null;
                                        _pendingCode = null;
                                      }),
                                      child: const Text('取消'),
                                    ),
                                  ],
                                ),
                              ],
                            ),
                          ),
                        ),
                      ),
                    if (_prepared == null) Padding(
                      padding: const EdgeInsets.all(16),
                      child: Row(
                        children: [
                          Expanded(
                            child: TextField(
                              controller: _code,
                              inputFormatters: [FilteringTextInputFormatter.digitsOnly, LengthLimitingTextInputFormatter(6)],
                              onSubmitted: _prepare,
                              keyboardType: TextInputType.number,
                              decoration: const InputDecoration(
                                  hintText: '或手動輸入兌換碼'),
                            ),
                          ),
                          const SizedBox(width: 8),
                          ElevatedButton(
                            style: ElevatedButton.styleFrom(minimumSize: const Size(88, 56)),
                            onPressed: _handled ? null : () {
                              if (_code.text.isNotEmpty) {
                                _prepare(_code.text);
                              }
                            },
                            child: const Text('預核銷'),
                          ),
                        ],
                      ),
                    ),
                  ],
                ),
    );
  }
}

/// Vendor redemption records (UI_SPEC §8): date filter + CSV export.
class RedemptionRecordsScreen extends StatefulWidget {
  const RedemptionRecordsScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<RedemptionRecordsScreen> createState() =>
      _RedemptionRecordsScreenState();
}

class _RedemptionRecordsScreenState extends State<RedemptionRecordsScreen> {
  List<Map<String, dynamic>> _records = [];
  bool _loading = false;
  String? _error;
  DateTime? _from;
  DateTime? _to;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final records =
          await widget.api.vendorRedemptions(from: _from, to: _to);
      if (!mounted) return;
      setState(() {
        _records = records;
        _loading = false;
      });
    } on ApiException catch (_) {
      if (mounted) {
        setState(() {
          _error = '網絡不穩定，請重試';
          _loading = false;
        });
      }
    }
  }

  int? _revokingId;

  Future<void> _revokeRecord(Map<String, dynamic> record) async {
    if (_revokingId != null) return;
    final id = record['redemption_id'] as int;
    final ok = await _confirmRevoke(context, record['coupon_name'] as String);
    if (!mounted || ok != true) return;
    setState(() => _revokingId = id);
    try {
      await widget.api.revokeRedemption(id);
      if (mounted) await _load();
    } catch (e) {
      if (mounted) showCgToast(context, e is ApiException ? e.message : '未能確認撤銷結果，請重試');
    } finally {
      if (mounted) setState(() => _revokingId = null);
    }
  }

  String _dateLabel(DateTime? d) =>
      d == null ? '全部' : '${d.year}-${d.month}-${d.day}';

  Future<void> _pickDate(bool isFrom) async {
    final picked = await showDatePicker(
      context: context,
      initialDate: (isFrom ? _from : _to) ?? DateTime.now(),
      firstDate: DateTime.now().subtract(const Duration(days: 365)),
      lastDate: DateTime.now(),
    );
    if (picked == null) return;
    setState(() {
      if (isFrom) {
        _from = picked;
      } else {
        _to = picked;
      }
    });
    _load();
  }

  Future<void> _exportCsv() async {
    try {
      final csv = await widget.api.vendorRedemptionsCsv();
      if (!mounted) return;
      await showDialog<void>(
        context: context,
        builder: (c) => AlertDialog(
          title: Text('核銷記錄 CSV（共 ${_records.length} 條）'),
          content: SingleChildScrollView(
            child: SelectableText(
                csv.length > 2000 ? '${csv.substring(0, 2000)}…' : csv),
          ),
          actions: [
            TextButton(
              onPressed: () {
                Clipboard.setData(ClipboardData(text: csv));
                Navigator.pop(c);
                showCgToast(context, 'CSV 已複製，可粘貼保存');
              },
              child: const Text('複製全部'),
            ),
            TextButton(
                onPressed: () => Navigator.pop(c),
                child: const Text('關閉')),
          ],
        ),
      );
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: CgColors.vendorGold,
        foregroundColor: CgColors.textPrimary,
        title: const Text('核銷記錄'),
        actions: [
          Semantics(
            label: '匯出 CSV',
            button: true,
            child: IconButton(
              icon: const Icon(Icons.download),
              tooltip: '匯出 CSV',
              onPressed: _records.isEmpty ? null : _exportCsv,
            ),
          ),
        ],
      ),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () => _pickDate(true),
                    icon: const Icon(Icons.calendar_today, size: 18),
                    label: Text('開始：${_dateLabel(_from)}'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () => _pickDate(false),
                    icon: const Icon(Icons.calendar_today, size: 18),
                    label: Text('結束：${_dateLabel(_to)}'),
                  ),
                ),
                if (_from != null || _to != null)
                  TextButton(
                    onPressed: () {
                      setState(() {
                        _from = null;
                        _to = null;
                      });
                      _load();
                    },
                    child: const Text('清除'),
                  ),
              ],
            ),
          ),
          Expanded(
            child: _loading
                ? ListView(
                    padding: const EdgeInsets.all(16),
                    children: const [
                      SkeletonBox(height: 72),
                      SizedBox(height: 8),
                      SkeletonBox(height: 72),
                    ],
                  )
                : _error != null
                    ? Center(
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            const Text('網絡不穩定，請重試'),
                            const SizedBox(height: 12),
                            ElevatedButton(
                                onPressed: _load, child: const Text('重試')),
                          ],
                        ),
                      )
                    : _records.isEmpty
                        ? Center(
                            child: Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                const Text('所選日期內暫無核銷記錄。'),
                                const SizedBox(height: 12),
                                ElevatedButton(
                                    onPressed: _load,
                                    child: const Text('重新載入')),
                              ],
                            ),
                          )
                        : RefreshIndicator(
                            onRefresh: _load,
                            child: ListView.builder(
                              itemCount: _records.length,
                              itemBuilder: (_, i) {
                                final r = _records[i];
                                final revoked = r['revoked'] == true;
                                final deadline = DateTime.tryParse(r['revoke_until'] as String? ?? '');
                                final canRevoke = !revoked && deadline != null && deadline.isAfter(DateTime.now());
                                return Card(
                                  margin: const EdgeInsets.fromLTRB(
                                      12, 8, 12, 0),
                                  child: ListTile(
                                    title: Text(
                                        '${r['coupon_name'] ?? ''}'),
                                    subtitle: Text(
                                        '${r['shop_name'] ?? ''} · ${r['scanned_at'] ?? ''}'),
                                    trailing: canRevoke ? TextButton(
                                      onPressed: _revokingId == null ? () => _revokeRecord(r) : null,
                                      child: const Text('撤銷核銷'),
                                    ) : Text(
                                        revoked ? '已撤銷' : '已核銷',
                                        style: TextStyle(
                                            color: revoked
                                                ? CgColors.textSecondary
                                                : CgColors.success)),
                                  ),
                                );
                              },
                            ),
                          ),
          ),
        ],
      ),
    );
  }
}

/// Usage data: totals + per-coupon downloads / redemptions.
class UsageScreen extends StatelessWidget {
  const UsageScreen({super.key, required this.api});

  final ApiClient api;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: CgColors.vendorGold,
        foregroundColor: CgColors.textPrimary,
        title: const Text('使用數據'),
      ),
      body: FutureBuilder<Map<String, dynamic>>(
        future: api.usageData(),
        builder: (context, snap) {
          if (!snap.hasData) {
            return const Padding(
              padding: EdgeInsets.all(16),
              child: Column(children: [SkeletonBox(height: 120)]),
            );
          }
          final data = snap.data!;
          final coupons = (data['coupons'] as List?) ?? [];
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Row(
                children: [
                  Expanded(
                      child: Card(
                          child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(children: [
                      Text('${data['total_downloads']}',
                          style: const TextStyle(
                              fontSize: 28, fontWeight: FontWeight.bold)),
                      const Text('總下載次數'),
                    ]),
                  ))),
                  const SizedBox(width: 12),
                  Expanded(
                      child: Card(
                          child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Column(children: [
                      Text('${data['total_usage']}',
                          style: const TextStyle(
                              fontSize: 28, fontWeight: FontWeight.bold)),
                      const Text('總使用量'),
                    ]),
                  ))),
                ],
              ),
              const SizedBox(height: 16),
              ...coupons.map((c) => Card(
                    margin: const EdgeInsets.only(top: 8),
                    child: ListTile(
                      title: Text(c['coupon_name'] as String),
                      subtitle: Text(
                          '下載 ${c['downloads']} · 已用 ${c['used']} · 剩 ${c['remaining']}'),
                    ),
                  )),
            ],
          );
        },
      ),
    );
  }
}

/// Subscription plans (two-level list from the API).
class SubscriptionScreen extends StatelessWidget {
  const SubscriptionScreen({super.key, required this.api});

  final ApiClient api;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: CgColors.vendorGold,
        foregroundColor: CgColors.textPrimary,
        title: const Text('訂閱計劃'),
      ),
      body: FutureBuilder<List<dynamic>>(
        future: api.subscriptionPlans(),
        builder: (context, snap) {
          if (!snap.hasData) {
            return const Padding(
              padding: EdgeInsets.all(16),
              child: Column(children: [SkeletonBox(height: 120)]),
            );
          }
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              Image.asset('assets/images/ic_program_card.png',
                  height: 120,
                  fit: BoxFit.cover,
                  errorBuilder: (_, __, ___) => const SizedBox()),
              const SizedBox(height: 12),
              ...snap.data!.map((p) => Card(
                    margin: const EdgeInsets.only(top: 8),
                    child: ListTile(
                      title: Text(p['name'] as String),
                      subtitle: Text(p['description'] as String? ?? ''),
                      trailing: Text(
                          '\$${(p['monthly_fee'] as num).toStringAsFixed(0)}/月',
                          style: const TextStyle(fontWeight: FontWeight.bold)),
                    ),
                  )),
              const Padding(
                padding: EdgeInsets.only(top: 12),
                child: Text('月費計劃可隨時取消，每月從帳戶扣除，無隱藏費用。',
                    style: TextStyle(color: CgColors.textSecondary)),
              ),
            ],
          );
        },
      ),
    );
  }
}
