import 'package:flutter/material.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';

/// Vendor home: gold theme, usage summary, issue-coupon entry.
class VendorHomeScreen extends StatefulWidget {
  const VendorHomeScreen({super.key, required this.api});

  final ApiClient api;

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
            OutlinedButton(
              onPressed: () =>
                  Navigator.of(context).pushNamed('/admin'),
              child: const Text('管理後台（結算 / 對賬 / 發票 / 內容 / 角色）'),
            ),
            const SizedBox(height: 16),
            const Text('我的優惠券', style: TextStyle(fontWeight: FontWeight.bold)),
            ..._coupons.map((c) => Card(
                  margin: const EdgeInsets.only(top: 8),
                  child: ListTile(
                    leading: const Icon(Icons.local_offer, color: CgColors.amber),
                    title: Text(c.name),
                    subtitle: Text('${c.status} · 已下載 ${c.downloads} · 已用 ${c.total - c.remaining}'),
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
  int _shopId = 1;
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
            value: _type,
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
            child: const Text('預覽並提交',
                style: TextStyle(color: CgColors.textPrimary)),
          ),
        ],
      ),
    );
  }
}

/// Scan: camera via mobile_scanner + manual code entry fallback.
class ScanScreen extends StatefulWidget {
  const ScanScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<ScanScreen> createState() => _ScanScreenState();
}

class _ScanScreenState extends State<ScanScreen> {
  final _code = TextEditingController();
  bool _handled = false;
  String? _result;
  // Line A two-step redemption: prepare (read-only check) -> confirm consume.
  Map<String, dynamic>? _prepared;
  String? _pendingCode;
  int? _lastRedemptionId;
  bool _revoking = false;

  Future<void> _prepare(String code) async {
    if (_handled) return;
    _handled = true;
    try {
      final res = await widget.api.prepareScan(code);
      if (!mounted) return;
      setState(() {
        _prepared = res;
        _pendingCode = code;
        _result = res['can_redeem'] == true
            ? null
            : (res['reason'] as String? ?? '無法核銷');
        _lastRedemptionId = null;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _result = e.message);
    }
    Future.delayed(const Duration(seconds: 2), () {
      if (mounted) setState(() => _handled = false);
    });
  }

  Future<void> _confirmConsume() async {
    final code = _pendingCode;
    final token = _prepared?['prepare_token'] as String?;
    if (code == null || token == null) return;
    setState(() => _handled = true);
    try {
      final res = await widget.api.scanCoupon(code,
          prepareToken: token,
          idempotencyKey: 'scan-${DateTime.now().millisecondsSinceEpoch}');
      if (!mounted) return;
      setState(() {
        _result = '使用成功！${res['coupon_name'] ?? ''}';
        _lastRedemptionId = res['redemption_id'] as int?;
        _prepared = null;
        _pendingCode = null;
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _result = e.message;
          _prepared = null;
          _pendingCode = null;
        });
      }
    } finally {
      if (mounted) setState(() => _handled = false);
    }
  }

  Future<void> _revoke() async {
    final rid = _lastRedemptionId;
    if (rid == null) return;
    setState(() => _revoking = true);
    try {
      await widget.api.revokeRedemption(rid);
      if (!mounted) return;
      setState(() {
        _result = '核銷已撤销，優惠券已退回未使用';
        _lastRedemptionId = null;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _result = e.message);
    } finally {
      if (mounted) setState(() => _revoking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        backgroundColor: CgColors.vendorGold,
        foregroundColor: CgColors.textPrimary,
        title: const Text('掃碼核銷'),
      ),
      body: Column(
        children: [
          Expanded(
            child: Stack(
              children: [
                MobileScanner(
                  onDetect: (capture) {
                    final value = capture.barcodes.firstOrNull?.rawValue;
                    if (value != null) {
                      final code = Uri.tryParse(value)?.queryParameters['code'] ?? value;
                      _prepare(code);
                    }
                  },
                ),
                Center(
                  child: Image.asset(
                    'assets/images/ic_scan_frame.png',
                    width: 260,
                    errorBuilder: (_, _, _) => Container(
                      width: 260,
                      height: 260,
                      decoration: BoxDecoration(
                        border: Border.all(color: CgColors.primary, width: 3),
                        borderRadius: BorderRadius.circular(12),
                      ),
                    ),
                  ),
                ),
              ],
            ),
          ),
          const Padding(
            padding: EdgeInsets.all(8),
            child: Text('請將 QR 碼放入方框中並進行掃描\n先預核銷確認券面與門店，再按確認核銷'),
          ),
          if (_prepared != null && _prepared!['can_redeem'] == true)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(_prepared!['coupon_name'] as String? ?? '',
                          style: const TextStyle(
                              fontWeight: FontWeight.bold, fontSize: 16)),
                      Text('門店：${_prepared!['shop_name'] ?? ''}'),
                      const SizedBox(height: 12),
                      Row(
                        children: [
                          Expanded(
                            child: ElevatedButton(
                              onPressed: _confirmConsume,
                              child: const Text('確認核銷'),
                            ),
                          ),
                          const SizedBox(width: 8),
                          TextButton(
                            onPressed: () => setState(() {
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
          if (_result != null)
            Padding(
              padding: const EdgeInsets.all(8),
              child: Text(_result!,
                  style: const TextStyle(
                      fontWeight: FontWeight.bold, fontSize: 16)),
            ),
          if (_lastRedemptionId != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: OutlinedButton(
                onPressed: _revoking ? null : _revoke,
                child: Text(_revoking ? '撤销中…' : '撤销核銷（10 分鐘內）'),
              ),
            ),
          Padding(
            padding: const EdgeInsets.all(16),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _code,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(hintText: '或手動輸入兌換碼'),
                  ),
                ),
                const SizedBox(width: 8),
                ElevatedButton(
                  onPressed: () {
                    if (_code.text.isNotEmpty) _prepare(_code.text);
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
                  errorBuilder: (_, _, _) => const SizedBox()),
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
