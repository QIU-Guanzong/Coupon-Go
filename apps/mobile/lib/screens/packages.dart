import 'dart:async';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api_client.dart';
import '../language.dart';
import '../models.dart';
import '../stripe_test_bridge.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import '../widgets/wallet_updates.dart';
import 'account.dart';
import 'reviews_admin.dart';

/// Line C (套餐 2A): package list, package detail (§3.1), checkout (§3.2),
/// payment polling (§3.3, PaymentResultScreen — already done, untouched),
/// my orders + refund (§3.4).
///
/// 支付结果（UI_SPEC §3.3）：客户端不再向服务端传支付结果。支付页完成
/// 后，[PaymentResultScreen] 每 2 秒轮询 GET /orders/{id}，最多 60 秒，
/// 直到 payment_status 为 paid / failed / timeout。金额一律以服务端
/// 返回为准，客户端不自己算钱。
///
/// 未登录可浏览套餐详情（只读）；点「立即抢购」时先登录，登录后回原界面。
String _displayRefundRule(BuildContext context, Package package) {
  if (!const bool.fromEnvironment('PUBLIC_TRIAL') || package.isNonRefundable) {
    return package.refundRuleText;
  }
  if (package.refundPolicy == 'anytime') {
    return context.tr('未使用可申請退款；過期不會自動退款。', 'Unused vouchers can be refunded on request. Expiry does not trigger an automatic refund.');
  }
  if (package.refundPolicy == 'expiry_only') {
    return context.tr('只可在過期後申請退款；過期不會自動退款。', 'Request a refund only after expiry. Expiry does not trigger an automatic refund.');
  }
  return context.tr('過期不會自動退款，詳情以商品規則為準。', 'Expiry does not trigger an automatic refund. See the offer terms.');
}

Future<bool> requirePackageLogin(BuildContext context, ApiClient api) async {
  if (api.isLoggedIn) return true;
  final result = await Navigator.of(context).push<bool>(
    MaterialPageRoute(
      builder: (_) => AuthScreen(
        api: api,
        onAuthed: () => Navigator.of(context).pop(true),
      ),
    ),
  );
  return result == true && api.isLoggedIn;
}

class PackageListScreen extends StatefulWidget {
  const PackageListScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<PackageListScreen> createState() => _PackageListScreenState();
}

class _PackageListScreenState extends State<PackageListScreen> {
  List<Package> _items = [];
  bool _loading = true;
  bool _error = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (mounted) setState(() => _loading = true);
    try {
      final list = await widget.api.packages();
      if (!mounted) return;
      setState(() {
        _items = list.map((e) => Package.fromJson(e)).toList();
        _loading = false;
        _error = false;
      });
    } on ApiException catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = true;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('套餐')),
      body: _loading
          ? const _LoadingList()
          : _error
              ? _ErrorView(onRetry: _load)
              : _items.isEmpty
                  ? _EmptyView(
                      message: '暫時沒有套餐。',
                      actionLabel: '重新整理',
                      onAction: _load,
                    )
                  : RefreshIndicator(
                      onRefresh: _load,
                      child: ListView.builder(
                        padding: const EdgeInsets.all(12),
                        itemCount: _items.length,
                        itemBuilder: (_, i) {
                          final p = _items[i];
                          return Card(
                            margin: const EdgeInsets.only(bottom: 12),
                            child: ListTile(
                              leading: p.images.isEmpty ? const Icon(Icons.restaurant_menu, color: CgColors.primary)
                                  : SizedBox(width: 64, height: 56, child: ClipRRect(
                                      borderRadius: BorderRadius.circular(6), child: CatalogPhoto(url: p.images.first))),
                              title: Text(p.name),
                              subtitle: Text(
                                  '${p.shopName} · ${p.personCount} 人用 · 已售 ${p.soldCount}'),
                              trailing: Column(
                                mainAxisAlignment: MainAxisAlignment.center,
                                crossAxisAlignment: CrossAxisAlignment.end,
                                children: [
                                  Text(formatHkd(p.packagePrice),
                                      style: const TextStyle(
                                          color: CgColors.primary,
                                          fontSize: 18,
                                          fontWeight: FontWeight.bold,
                                          fontFeatures: [
                                            FontFeature.tabularFigures()
                                          ])),
                                  Text(
                                      '${formatHkd(p.originalPrice)} · 省 ${p.savePercent.toStringAsFixed(0)}%',
                                      style: const TextStyle(
                                          decoration:
                                              TextDecoration.lineThrough,
                                          color: CgColors.textSecondary,
                                          fontSize: 12)),
                                ],
                              ),
                              onTap: () => Navigator.of(context).push(
                                  MaterialPageRoute(
                                      builder: (_) => PackageDetailScreen(
                                          api: widget.api, packageId: p.id))),
                            ),
                          );
                        },
                      ),
                    ),
    );
  }
}

/// Package detail (UI_SPEC §3.1): image carousel, price area + refund
/// rule line, contents, usage rules, stores, reviews, fixed bottom bar.
class PackageDetailScreen extends StatefulWidget {
  const PackageDetailScreen(
      {super.key, required this.api, required this.packageId});

  final ApiClient api;
  final int packageId;

  @override
  State<PackageDetailScreen> createState() => _PackageDetailScreenState();
}

class _PackageDetailScreenState extends State<PackageDetailScreen> {
  Package? _pkg;
  bool _loading = true;
  bool _error = false;
  int _page = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (mounted) setState(() => _loading = true);
    try {
      final data = await widget.api.packageDetails(widget.packageId);
      if (!mounted) return;
      final pkg = Package.fromJson(data);
      if (pkg.id != widget.packageId || pkg.id <= 0) {
        throw const FormatException('invalid package');
      }
      setState(() {
        _pkg = pkg;
        _loading = false;
        _error = false;
      });
    } catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = true;
        });
      }
    }
  }

  Future<void> _buy() async {
    final pkg = _pkg;
    if (pkg == null || pkg.stockLeft <= 0 || pkg.isOffShelf) return;
    final ok = await requirePackageLogin(context, widget.api);
    if (!ok || !mounted) return;
    await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) =>
            PackageCheckoutScreen(api: widget.api, package: pkg)));
    if (mounted) _load();
  }

  Future<void> _openMap(Map<String, dynamic> branch) async {
    final lat = branch['latitude'] ?? branch['lat'];
    final lng = branch['longitude'] ?? branch['lng'];
    final name = (branch['branch_name'] ?? branch['name'] ?? '') as String;
    final address = (branch['address'] ?? '') as String;
    final query = (lat != null && lng != null)
        ? '$lat,$lng'
        : Uri.encodeComponent('$name $address'.trim());
    final uri =
        Uri.parse('https://www.google.com/maps/search/?api=1&query=$query');
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    } else if (mounted) {
      showCgToast(context, '未能開啟地圖');
    }
  }

  void _openReviews() {
    final p = _pkg;
    if (p == null) return;
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => ShopReviewsScreen(
              api: widget.api,
              shop: Shop.fromJson(
                  {'shop_id': p.shopId, 'shop_name': p.shopName}),
            )));
  }

  @override
  Widget build(BuildContext context) {
    final p = _pkg;
    if (_loading) {
      return Scaffold(
        appBar: AppBar(title: const Text('套餐詳情')),
        body: const _LoadingList(),
      );
    }
    if (_error || p == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('套餐詳情')),
        body: _ErrorView(onRetry: _load),
      );
    }
    // 已下架：整页只显示下架提示 + 返回按钮。
    if (p.isOffShelf) {
      return Scaffold(
        appBar: AppBar(title: const Text('套餐詳情')),
        body: Center(
          child: Padding(
            padding: const EdgeInsets.all(24),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                const Icon(Icons.remove_circle_outline,
                    size: 64, color: CgColors.textSecondary),
                const SizedBox(height: 16),
                const Text('該套餐已下架',
                    style:
                        TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
                const SizedBox(height: 8),
                Text(p.name, textAlign: TextAlign.center,
                    style: const TextStyle(color: CgColors.textSecondary)),
                const SizedBox(height: 24),
                ElevatedButton(
                  style: ElevatedButton.styleFrom(
                      minimumSize: const Size(160, 48)),
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text('返回'),
                ),
              ],
            ),
          ),
        ),
      );
    }
    final images = p.carouselImages;
    final soldOut = p.stockLeft <= 0;
    return Scaffold(
      appBar: AppBar(title: const Text('套餐詳情')),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(16, 16, 16, 24),
        children: [
          // 顶部轮播图（≤ 6 张）
          ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: SizedBox(
              height: 220,
              child: images.isEmpty
                  ? const CatalogPhoto(url: '')
                  : Stack(
                      children: [
                        PageView.builder(
                          itemCount: images.length,
                          onPageChanged: (i) => setState(() => _page = i),
                          itemBuilder: (_, i) => CatalogPhoto(url: images[i]),
                        ),
                        if (images.length > 1)
                          Positioned(
                            bottom: 8,
                            left: 0,
                            right: 0,
                            child: Row(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: List.generate(
                                images.length,
                                (i) => Container(
                                  width: 8,
                                  height: 8,
                                  margin: const EdgeInsets.symmetric(
                                      horizontal: 3),
                                  decoration: BoxDecoration(
                                    shape: BoxShape.circle,
                                    color: i == _page
                                        ? Colors.white
                                        : Colors.white.withValues(alpha: 0.5),
                                  ),
                                ),
                              ),
                            ),
                          ),
                      ],
                    ),
            ),
          ),
          const SizedBox(height: 16),
          Text(p.name,
              style:
                  const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
          const SizedBox(height: 6),
          Text('${p.shopName} · ${p.personCount} 人用',
              style: const TextStyle(color: CgColors.textSecondary)),
          const SizedBox(height: 12),
          // 价格区
          Wrap(
            spacing: 8, runSpacing: 4, crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(formatHkd(p.packagePrice),
                  style: const TextStyle(
                      color: CgColors.primary,
                      fontSize: 28,
                      fontWeight: FontWeight.bold,
                      fontFeatures: [FontFeature.tabularFigures()])),
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text(formatHkd(p.originalPrice),
                    style: const TextStyle(
                        decoration: TextDecoration.lineThrough,
                        color: CgColors.textSecondary)),
              ),
              if (p.saveAmount > 0)
                Padding(
                  padding: const EdgeInsets.only(bottom: 4),
                  child: Chip(
                      label: Text('省 ${formatHkd(p.saveAmount)}'),
                      visualDensity: VisualDensity.compact),
                ),
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Text('已售 ${p.soldCount}',
                    style: const TextStyle(color: CgColors.textSecondary)),
              ),
            ],
          ),
          const SizedBox(height: 8),
          // 退款规则：固定放价格区下方一行字，来自接口字段，不可退不允许隐藏。
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: p.isNonRefundable
                  ? const Color(0xFFFDECEA)
                  : const Color(0xFFFFF3CD),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text('退款規則：${_displayRefundRule(context, p)}',
                style: const TextStyle(fontWeight: FontWeight.bold)),
          ),
          const SizedBox(height: 16),
          // 套餐内容
          const Text('套餐內容', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          if (p.itemLines.isNotEmpty)
            ...p.itemLines.map((line) {
              final name = (line['name'] ?? '') as String;
              final qty = line['qty'] ?? line['quantity'] ?? 1;
              final unit = (line['unit_price'] as num?)?.toDouble() ??
                  (line['price'] as num?)?.toDouble();
              return Padding(
                padding: const EdgeInsets.only(top: 6),
                child: Row(
                  children: [
                    Expanded(child: Text(name)),
                    Text('x$qty',
                        style: const TextStyle(color: CgColors.textSecondary)),
                    if (unit != null) ...[
                      const SizedBox(width: 12),
                      SizedBox(
                        width: 96,
                        child: Text(formatHkd(unit),
                            textAlign: TextAlign.right,
                            style: const TextStyle(
                                fontFeatures: [FontFeature.tabularFigures()])),
                      ),
                    ],
                  ],
                ),
              );
            })
          else
            ...p.items.map((i) => Padding(
                  padding: const EdgeInsets.only(top: 4),
                  child: Text('· $i'),
                )),
          const Divider(height: 24),
          Row(
            children: [
              const Text('合計', style: TextStyle(fontWeight: FontWeight.bold)),
              const Spacer(),
              Text(formatHkd(p.packagePrice),
                  style: const TextStyle(
                      fontWeight: FontWeight.bold,
                      fontFeatures: [FontFeature.tabularFigures()])),
            ],
          ),
          const SizedBox(height: 16),
          // 使用规则
          const Text('使用規則', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          _RuleRow(
              label: '有效期',
              value: p.validTo == null
                  ? ''
                  : p.validFrom == null
                      ? '至 ${formatDateYmd(p.validTo)}'
                      : '${formatDateYmd(p.validFrom)} 至 ${formatDateYmd(p.validTo)}'),
          if (p.includes.isNotEmpty) _RuleRow(label: '已包括', value: p.includes.join('、')),
          if (p.excludes.isNotEmpty) _RuleRow(label: '另收費', value: p.excludes.join('、')),
          if (p.timeSlots.isNotEmpty)
            _RuleRow(label: '可用時段', value: p.timeSlots.join('、')),
          _RuleRow(label: '是否需預約', value: p.needBooking ? '需提前預約' : '無需預約'),
          if (p.perUserLimit != null)
            _RuleRow(label: '每人限購', value: '每人限購 ${p.perUserLimit} 份'),
          if (p.stackable != null)
            _RuleRow(
                label: '是否可疊加',
                value: p.stackable! ? '可與其他優惠疊加' : '不可與其他優惠疊加'),
          const Text('· 到店出示 QR 碼核銷，整張一次核銷，不可拆分',
              style: TextStyle(color: CgColors.textSecondary)),
          const SizedBox(height: 16),
          // 适用门店
          if (p.branches.isNotEmpty) ...[
            ExpansionTile(
              tilePadding: EdgeInsets.zero,
              title: Text('適用門店（${p.branches.length} 家）',
                  style: const TextStyle(fontWeight: FontWeight.bold)),
              children: p.branches.map((b) {
                final name =
                    (b['branch_name'] ?? b['name'] ?? p.shopName) as String;
                final address = (b['address'] ?? '') as String;
                return ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading:
                      const Icon(Icons.store, color: CgColors.primary),
                  title: Text(name),
                  subtitle: address.isEmpty ? null : Text(address),
                  trailing: SizedBox(
                    height: 44,
                    child: TextButton.icon(
                      onPressed: () => _openMap(b),
                      icon: const Icon(Icons.map, size: 18),
                      label: const Text('地圖'),
                    ),
                  ),
                );
              }).toList(),
            ),
            const SizedBox(height: 8),
          ],
          // 评价
          ListTile(
            contentPadding: EdgeInsets.zero,
            leading: const Icon(Icons.star, color: CgColors.amber),
            title: const Text('評價',
                style: TextStyle(fontWeight: FontWeight.bold)),
            subtitle: p.rating == null
                ? Text(context.tr('暫無評分', 'No ratings yet'))
                : Text(
                    '總分 ${p.rating!.overall?.toStringAsFixed(1) ?? '-'} · '
                    '口味 ${p.rating!.taste?.toStringAsFixed(1) ?? '-'} · '
                    '環境 ${p.rating!.ambience?.toStringAsFixed(1) ?? '-'} · '
                    '服務 ${p.rating!.service?.toStringAsFixed(1) ?? '-'} '
                    '（${p.rating!.reviewCount} 條）'),
            trailing: const Icon(Icons.chevron_right),
            onTap: _openReviews,
          ),
          if (p.reviews.isNotEmpty)
            ...p.reviews.take(3).map((r) => Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          Text(r.userName.isEmpty ? '用戶' : r.userName,
                              style: const TextStyle(
                                  fontWeight: FontWeight.w600)),
                          const SizedBox(width: 8),
                          _Stars(value: r.overall),
                        ],
                      ),
                      if (r.content.isNotEmpty)
                        Padding(
                          padding: const EdgeInsets.only(top: 2),
                          child: Text(r.content, maxLines: 3,
                              overflow: TextOverflow.ellipsis),
                        ),
                    ],
                  ),
                )),
        ],
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
          child: Row(
            children: [
              Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(formatHkd(p.packagePrice),
                      style: const TextStyle(
                          color: CgColors.primary,
                          fontSize: 22,
                          fontWeight: FontWeight.bold,
                          fontFeatures: [FontFeature.tabularFigures()])),
                  Text(formatHkd(p.originalPrice),
                      style: const TextStyle(
                          decoration: TextDecoration.lineThrough,
                          color: CgColors.textSecondary,
                          fontSize: 12)),
                ],
              ),
              const SizedBox(width: 16),
              Expanded(
                child: SizedBox(
                  height: 48,
                  child: ElevatedButton(
                    onPressed: soldOut ? null : _buy,
                    child: Text(soldOut ? context.tr('已售罄', 'Sold out') : context.tr('立即搶購', 'Buy now')),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

class _RuleRow extends StatelessWidget {
  const _RuleRow({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    if (value.isEmpty) return const SizedBox();
    return Padding(
      padding: const EdgeInsets.only(top: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
              width: 88,
              child: Text(label,
                  style: const TextStyle(color: CgColors.textSecondary))),
          Expanded(child: Text(value)),
        ],
      ),
    );
  }
}

class _Stars extends StatelessWidget {
  const _Stars({required this.value});

  final double value;

  @override
  Widget build(BuildContext context) {
    final filled = value.round().clamp(0, 5);
    return Semantics(
      label: '評分 ${value.toStringAsFixed(1)} 分',
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: List.generate(
          5,
          (i) => Icon(
            i < filled ? Icons.star : Icons.star_border,
            size: 16,
            color: CgColors.amber,
          ),
        ),
      ),
    );
  }
}

/// 下单页（UI_SPEC §3.2）：套餐摘要 + 数量步进器、支付方式二选一、
/// 金额明细、购买须知勾选、确认支付（防重复点击）。
class PackageCheckoutScreen extends StatefulWidget {
  const PackageCheckoutScreen(
      {super.key, required this.api, required this.package});

  final ApiClient api;
  final Package package;

  @override
  State<PackageCheckoutScreen> createState() => _PackageCheckoutScreenState();
}

class _PackageCheckoutScreenState extends State<PackageCheckoutScreen> {
  int _qty = 1;
  final Map<String, String> _checkoutRefs = {};
  String _method = 'card';
  bool _agreed = false;
  bool _busy = false;
  bool _optionsLoading = true;
  bool _cardAvailable = false;
  bool _fpsAvailable = false;
  String _paymentMode = 'unavailable';

  @override
  void initState() {
    super.initState();
    _loadPaymentOptions();
  }

  Future<void> _loadPaymentOptions() async {
    if (mounted) setState(() => _optionsLoading = true);
    try {
      final options = await widget.api.get('/payment-options');
      final prefs = await SharedPreferences.getInstance();
      if (!mounted) return;
      final mode = options['mode'] is String ? options['mode'] as String : 'unavailable';
      final valid = !const bool.fromEnvironment('PUBLIC_TRIAL') || mode == 'demo';
      final fps = valid && options['fps'] == true;
      final card = valid && options['card'] == true;
      final last = prefs.getString('last_payment_method');
      setState(() {
        _fpsAvailable = fps;
        _cardAvailable = card;
        _paymentMode = valid ? mode : 'unavailable';
        _method = last == 'fps' && fps ? 'fps' : 'card';
        _optionsLoading = false;
      });
    } catch (_) {
      if (mounted) setState(() => _optionsLoading = false);
    }
  }

  int get _maxQty => widget.package.maxQuantity;

  /// 展示用合计（套餐价 × 数量）；实际支付金额以服务端订单为准。
  double get _displayTotal => widget.package.packagePrice * _qty;

  Future<void> _confirmPay() async {
    if (_busy || !_agreed || (_method == 'card' ? !_cardAvailable : !_fpsAvailable)) return;
    final pkg = widget.package;
    setState(() => _busy = true);
    try {
      final res = await widget.api.purchasePackage(pkg.id, _method,
          quantity: _qty,
          clientRef: _checkoutRefs.putIfAbsent('$_qty/$_method',
              () => 'app-${DateTime.now().microsecondsSinceEpoch}'));
      final prefs = await SharedPreferences.getInstance();
      await prefs.setString('last_payment_method', _method);
      final order = PackageOrder.fromJson(res['order'] as Map<String, dynamic>);
      if (!mounted) return;
      setState(() => _busy = false);
      // 支付页完成后进入结果轮询，不再向服务端传支付结果。
      final done = await showPaymentSheet(context, widget.api, order);
      if (done != true || !mounted) return;
      Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => PaymentResultScreen(
              api: widget.api, initialOrder: order)));
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        showCgToast(context, e.message);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = widget.package;
    final soldOut = _maxQty <= 0;
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('確認訂單', 'Review order'))),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          // 套餐摘要
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Row(
                children: [
                  ClipRRect(
                    borderRadius: BorderRadius.circular(8),
                    child: SizedBox(
                      width: 72,
                      height: 72,
                      child: CatalogPhoto(url: p.image),
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(p.name,
                            style: const TextStyle(
                                fontWeight: FontWeight.bold, fontSize: 16)),
                        const SizedBox(height: 4),
                        Text('${p.shopName} · ${p.personCount} 人用',
                            style: const TextStyle(
                                color: CgColors.textSecondary)),
                        const SizedBox(height: 4),
                        Text(formatHkd(p.packagePrice),
                            style: const TextStyle(
                                color: CgColors.primary,
                                fontWeight: FontWeight.bold,
                                fontFeatures: [FontFeature.tabularFigures()])),
                      ],
                    ),
                  ),
                  // 数量步进器
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      IconButton(
                        icon: const Icon(Icons.remove_circle_outline),
                        tooltip: '減少數量',
                        onPressed: _qty > 1
                            ? () => setState(() => _qty--)
                            : null,
                      ),
                      Text('$_qty',
                          style: const TextStyle(
                              fontSize: 18,
                              fontWeight: FontWeight.bold,
                              fontFeatures: [FontFeature.tabularFigures()])),
                      IconButton(
                        icon: const Icon(Icons.add_circle_outline),
                        tooltip: '增加數量',
                        onPressed: _qty < _maxQty
                            ? () => setState(() => _qty++)
                            : null,
                      ),
                    ],
                  ),
                ],
              ),
            ),
          ),
          if (p.limitRemaining != null || p.perUserLimit != null)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                  '每單最多購買 $_maxQty 份（限購餘量與庫存取較小值）',
                  style: const TextStyle(color: CgColors.textSecondary)),
            ),
          if (soldOut)
            const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text('該套餐已售罄。',
                  style: TextStyle(color: CgColors.primary)),
            ),
          const SizedBox(height: 16),
          // 支付方式
          const Text('支付方式', style: TextStyle(fontWeight: FontWeight.bold)),
          RadioGroup<String>(
            groupValue: _method,
            onChanged: (v) => setState(() => _method = v ?? 'card'),
            child: Column(
              children: [
                RadioListTile<String>(
                  title: Text(_fpsAvailable ? 'FPS 轉數快' : 'FPS 轉數快（暫未提供）'),
                  value: 'fps',
                  enabled: _fpsAvailable,
                ),
                RadioListTile<String>(
                  title: const Text('信用卡'),
                  value: 'card',
                  enabled: _cardAvailable,
                ),
              ],
            ),
          ),
          if (_paymentMode == 'demo' || _paymentMode == 'stripe_test')
            Text(context.tr('確認訂單不會扣款。', 'Confirming the order will not charge you.')),
          if (!_optionsLoading && !_cardAvailable && !_fpsAvailable)
            TextButton(onPressed: _loadPaymentOptions, child: Text(context.tr('未能載入付款方式，請重試。', 'Could not load payment options. Try again.'))),
          const SizedBox(height: 8),
          // 金额明细
          const Text('金額明細', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          Row(
            children: [
              Text('套餐價 ${formatHkd(p.packagePrice)} × $_qty'),
              const Spacer(),
              Text(formatHkd(_displayTotal),
                  style: const TextStyle(
                      fontFeatures: [FontFeature.tabularFigures()])),
            ],
          ),
          const Divider(height: 24),
          Row(
            children: [
              const Text('合計', style: TextStyle(fontWeight: FontWeight.bold)),
              const Spacer(),
              Text(formatHkd(_displayTotal),
                  style: const TextStyle(
                      color: CgColors.primary,
                      fontSize: 20,
                      fontWeight: FontWeight.bold,
                      fontFeatures: [FontFeature.tabularFigures()])),
            ],
          ),
          const Padding(
            padding: EdgeInsets.only(top: 4),
            child: Text('實際支付金額以服務端訂單為準。',
                style:
                    TextStyle(color: CgColors.textSecondary, fontSize: 12)),
          ),
          const SizedBox(height: 16),
          // 不可退：购买页再次醒目提示
          if (p.isNonRefundable)
            Container(
              padding: const EdgeInsets.all(12),
              decoration: BoxDecoration(
                color: const Color(0xFFFDECEA),
                borderRadius: BorderRadius.circular(8),
              ),
              child: Text(context.tr('該套餐售出後不可退款，請確認後再購買。',
                  'This package is non-refundable. Check your order before buying.'),
                  style: const TextStyle(fontWeight: FontWeight.bold)),
            ),
          if (p.isNonRefundable) const SizedBox(height: 16),
          // 购买须知勾选
          Row(
            children: [
              Expanded(
                child: CheckboxListTile(
                  value: _agreed,
                  onChanged: (v) => setState(() => _agreed = v ?? false),
                  controlAffinity: ListTileControlAffinity.leading,
                  contentPadding: EdgeInsets.zero,
                  title: Text(context.tr('我已閱讀並同意購買須知', 'I agree to the purchase terms')),
                ),
              ),
              TextButton(
                onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => PackageTermsScreen(
                        package: p, demoPayment: _paymentMode == 'demo'))),
                child: Text(context.tr('查看購買須知', 'View purchase terms')),
              ),
            ],
          ),
        ],
      ),
      bottomNavigationBar: SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: ElevatedButton(
            style: ElevatedButton.styleFrom(
                minimumSize: const Size(double.infinity, 48)),
            onPressed: (_agreed && !_busy && !soldOut && !_optionsLoading &&
                    (_method == 'card' ? _cardAvailable : _fpsAvailable))
                ? _confirmPay : null,
            child: Text(_busy ? context.tr('處理中…', 'Processing…') :
                context.tr('確認支付 ${formatHkd(_displayTotal)}', 'Confirm payment ${formatHkd(_displayTotal)}')),
          ),
        ),
      ),
    );
  }
}

/// 购买须知页：内含退款规则（不可退套餐醒目提示）与使用规则。
class PackageTermsScreen extends StatelessWidget {
  const PackageTermsScreen({super.key, required this.package, this.demoPayment = false});

  final Package package;
  final bool demoPayment;

  @override
  Widget build(BuildContext context) {
    final p = package;
    return Scaffold(
      appBar: AppBar(title: const Text('購買須知')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: p.isNonRefundable
                  ? const Color(0xFFFDECEA)
                  : const Color(0xFFFFF3CD),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text('退款規則：${_displayRefundRule(context, p)}',
                style: const TextStyle(fontWeight: FontWeight.bold)),
          ),
          if (!p.isNonRefundable)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(demoPayment || const bool.fromEnvironment('PUBLIC_TRIAL')
                  ? context.tr('退款只更新訂單記錄，不會退回款項。', 'A refund only updates the order record; no money is returned.')
                  : context.tr('退款退回原付款方式，入賬時間視付款機構而定。', 'Refunds go to the original payment method. Timing depends on the payment provider.')),
            ),
          const SizedBox(height: 16),
          const Text('使用規則', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          if (p.validTo != null)
            Text('有效期：${p.validFrom == null ? '' : '${formatDateYmd(p.validFrom)} 至 '}${formatDateYmd(p.validTo)}'),
          if (p.timeSlots.isNotEmpty) Text('可用時段：${p.timeSlots.join('、')}'),
          Text(p.needBooking ? '需提前預約。' : '無需預約。'),
          if (p.perUserLimit != null) Text('每人限購 ${p.perUserLimit} 份。'),
          if (p.stackable != null)
            Text(p.stackable! ? '可與其他優惠疊加。' : '不可與其他優惠疊加。'),
          const Text('到店出示 QR 碼核銷，整張一次核銷，不可拆分。'),
          const SizedBox(height: 16),
          const Text('套餐內容', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          ...p.items.map((i) => Text('· $i')),
        ],
      ),
    );
  }
}



/// My orders (UI_SPEC §3.4): tabs 全部 / 待支付 / 已支付 / 已退款；
/// tap a card for the order detail (number, method, times, refund).
class OrdersScreen extends StatefulWidget {
  const OrdersScreen({
    super.key,
    required this.api,
    this.guest = false,
    this.initialTab = 0,
  }) : assert(initialTab >= 0 && initialTab < 4);

  final ApiClient api;
  final bool guest;
  final int initialTab;

  @override
  State<OrdersScreen> createState() => _OrdersScreenState();
}

class _OrdersScreenState extends State<OrdersScreen> with WalletUpdates<OrdersScreen> {
  List<PackageOrder> _orders = [];
  bool _loading = true;
  bool _error = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  bool get walletPageActive => !widget.guest;

  @override
  Future<void> syncWallet() => refreshOnReceiptChange(widget.api, () async {
        await _load(quiet: true);
        return mounted && !_error;
      });

  Future<void> _load({bool quiet = false}) async {
    if (widget.guest) {
      if (mounted) setState(() { _orders = []; _loading = false; _error = false; });
      return;
    }
    if (mounted && !quiet) setState(() => _loading = true);
    try {
      final list = await widget.api.orders();
      if (!mounted) return;
      setState(() {
        _orders = list.map((e) => PackageOrder.fromJson(e)).toList();
        _loading = false;
        _error = false;
      });
    } on ApiException catch (_) {
      if (quiet) rethrow;
      if (mounted) {
        setState(() {
          _loading = false;
          _error = true;
        });
      }
    }
  }

  Future<void> _act(Future<Map<String, dynamic>> Function() call) async {
    try {
      final res = await call();
      if (!mounted) return;
      showCgToast(context, (res['message'] as String?) ?? '已處理');
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  /// 待支付订单：打开支付页，完成後进入结果轮询。
  Future<void> _pay(PackageOrder o) async {
    final done = await showPaymentSheet(context, widget.api, o);
    if (done != true || !mounted) return;
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) =>
            PaymentResultScreen(api: widget.api, initialOrder: o)));
  }

  /// 失败/逾時订单：重新发起支付，完成後进入结果轮询。
  Future<void> _retry(PackageOrder o) async {
    try {
      final res = await widget.api.retryPayment(
          o.id, o.paymentMethod.isEmpty ? 'fps' : o.paymentMethod);
      final order = PackageOrder.fromJson(res['order'] as Map<String, dynamic>);
      if (!mounted) return;
      final done = await showPaymentSheet(context, widget.api, order);
      if (done != true) {
        _load();
        return;
      }
      if (!mounted) return;
      Navigator.of(context).push(MaterialPageRoute(
          builder: (_) =>
              PaymentResultScreen(api: widget.api, initialOrder: order)));
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  Future<void> _refund(PackageOrder o) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('申請退款'),
        content: Text(
            '訂單 ${o.orderNoText} · ${formatHkd(o.paidAmount)}\n將退款整筆訂單及其所有券（共 ${o.quantity} 份），只有全部符合退款條件時才可申請。確定嗎？'),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('取消')),
          ElevatedButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: const Text('確認退款')),
        ],
      ),
    );
    if (ok == true) {
      _act(() => widget.api.refundOrder(o.id));
    }
  }

  List<PackageOrder> _filtered(int tab) {
    switch (tab) {
      case 1:
        return _orders.where((o) => o.paymentStatus == 'pending').toList();
      case 2:
        return _orders.where((o) => o.paymentStatus == 'paid' &&
            o.refundStatus != 'refunded' && o.refundStatus != 'requested').toList();
      case 3:
        return _orders.where((o) => o.hasRefundActivity).toList();
      default:
        return _orders;
    }
  }

  String _emptyMessage(int tab) {
    if (widget.guest) {
      return context.tr('登入後可查看你的訂單。', 'Sign in to view your orders.');
    }
    switch (tab) {
      case 1:
        return const bool.fromEnvironment('PUBLIC_TRIAL')
            ? context.tr('暫時沒有待確認訂單。', 'No orders awaiting confirmation.')
            : context.tr('暫時沒有待支付訂單。', 'No orders awaiting payment.');
      case 2:
        return const bool.fromEnvironment('PUBLIC_TRIAL')
            ? context.tr('暫時沒有已確認訂單。', 'No confirmed orders.')
            : context.tr('暫時沒有已支付訂單。', 'No paid orders.');
      case 3:
        return context.tr('暫時沒有退款記錄。', 'No refund activity.');
      default:
        return context.tr('暫時沒有訂單。', 'No orders yet.');
    }
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 4,
      initialIndex: widget.initialTab,
      child: Scaffold(
        appBar: AppBar(
          title: Text(context.tr('我的訂單', 'My orders')),
          bottom: TabBar(
            tabs: [
              Tab(text: context.tr('全部', 'All')),
              Tab(text: const bool.fromEnvironment('PUBLIC_TRIAL')
                  ? context.tr('待確認', 'Awaiting confirmation')
                  : context.tr('待支付', 'Awaiting payment')),
              Tab(text: const bool.fromEnvironment('PUBLIC_TRIAL')
                  ? context.tr('已確認', 'Confirmed')
                  : context.tr('已支付', 'Paid')),
              Tab(text: context.tr('退款', 'Refunds')),
            ],
          ),
        ),
        body: _loading
            ? const _LoadingList()
            : _error
                ? _ErrorView(onRetry: _load)
                : TabBarView(
                    children: List.generate(4, (tab) {
                      final orders = _filtered(tab);
                      if (orders.isEmpty) {
                        return _EmptyView(
                          message: _emptyMessage(tab),
                          actionLabel: '去看看套餐',
                          onAction: () => Navigator.of(context).push(
                              MaterialPageRoute(
                                  builder: (_) => PackageListScreen(
                                      api: widget.api))),
                        );
                      }
                      return RefreshIndicator(
                        onRefresh: _load,
                        child: ListView.builder(
                          padding: const EdgeInsets.all(12),
                          itemCount: orders.length,
                          itemBuilder: (_, i) {
                            final o = orders[i];
                            return Card(
                              margin: const EdgeInsets.only(bottom: 12),
                              child: InkWell(
                                onTap: () => Navigator.of(context)
                                    .push(MaterialPageRoute(
                                        builder: (_) => OrderDetailScreen(
                                            api: widget.api, orderId: o.id)))
                                    .then((_) => _load()),
                                child: Padding(
                                  padding: const EdgeInsets.all(12),
                                  child: Column(
                                    crossAxisAlignment:
                                        CrossAxisAlignment.start,
                                    children: [
                                      Row(
                                        children: [
                                          Expanded(
                                            child: Text(o.packageName,
                                                style: const TextStyle(
                                                    fontWeight:
                                                        FontWeight.bold)),
                                          ),
                                          Chip(label: Text(_statusLabel(context, o))),
                                        ],
                                      ),
                                      Text(o.demoPayment
                                          ? '${o.shopName} · ${formatHkd(o.amount)}'
                                          : '${o.shopName} · ${o.paymentMethodText} · ${formatHkd(o.amount)}'),
                                      if (o.refundStatus != 'none')
                                        Text(
                                            '${context.tr('退款狀態：${o.refundStatusText}', 'Refund: ${o.refundStatusEnglishText}')}'
                                            '${o.refundAmount > 0 ? ' · ${formatHkd(o.refundAmount)}' : ''}',
                                            style: const TextStyle(
                                                color: CgColors.primary)),
                                      const SizedBox(height: 8),
                                      Wrap(
                                        spacing: 8,
                                        children: [
                                          if (o.canPay)
                                            ElevatedButton(
                                              style: ElevatedButton.styleFrom(
                                                  minimumSize:
                                                      const Size(88, 44)),
                                              onPressed: () => _pay(o),
                                              child: Text(o.demoPayment
                                                  ? context.tr('確認訂單', 'Confirm order')
                                                  : context.tr('前往付款', 'Continue to checkout')),
                                            ),
                                          if (o.canPay)
                                            OutlinedButton(
                                              style: OutlinedButton.styleFrom(
                                                  minimumSize:
                                                      const Size(88, 44)),
                                              onPressed: () => _act(() =>
                                                  widget.api
                                                      .cancelOrder(o.id)),
                                              child: const Text('取消訂單'),
                                            ),
                                          if (o.canRetry)
                                            ElevatedButton(
                                              style: ElevatedButton.styleFrom(
                                                  minimumSize:
                                                      const Size(88, 44)),
                                              onPressed: () => _retry(o),
                                              child: const Text('重新支付'),
                                            ),
                                          if (o.canRefund)
                                            OutlinedButton(
                                              style: OutlinedButton.styleFrom(
                                                  minimumSize:
                                                      const Size(88, 44)),
                                              onPressed: () => _refund(o),
                                              child: const Text('申請退款'),
                                            ),
                                        ],
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            );
                          },
                        ),
                      );
                    }),
                  ),
      ),
    );
  }
}

/// 逾時订单服务端已关闭，列表上直接显示「訂單已關閉」。
String _statusLabel(BuildContext context, PackageOrder o) =>
    o.refundStatus != 'none'
        ? context.tr(o.refundStatusText, o.refundStatusEnglishText)
        : o.walletStatus == 3
            ? context.tr('券已失效', 'Voucher expired')
            : o.paymentStatus == 'timeout'
                ? context.tr('訂單已關閉', 'Order closed')
                : context.tr(o.paymentStatusText, o.paymentStatusEnglishText);

/// Order detail (UI_SPEC §3.4): order number, payment method/time,
/// refund status + amount, expected arrival; refund button only when
/// the voucher is unused and refundable (hidden, not disabled, otherwise).
class OrderDetailScreen extends StatefulWidget {
  const OrderDetailScreen({super.key, required this.api, required this.orderId});

  final ApiClient api;
  final int orderId;

  @override
  State<OrderDetailScreen> createState() => _OrderDetailScreenState();
}

class _OrderDetailScreenState extends State<OrderDetailScreen> with WalletUpdates<OrderDetailScreen> {
  PackageOrder? _order;
  bool _loading = true;
  bool _error = false;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  Future<void> syncWallet() => refreshOnReceiptChange(widget.api, () async {
        await _load(quiet: true);
        return mounted && !_error;
      });

  Future<void> _load({bool quiet = false}) async {
    if (mounted && !quiet) setState(() => _loading = true);
    try {
      final data = await widget.api.orderDetails(widget.orderId);
      if (!mounted) return;
      setState(() {
        _order = PackageOrder.fromJson(data);
        _loading = false;
        _error = false;
      });
    } on ApiException catch (_) {
      if (quiet) rethrow;
      if (mounted) {
        setState(() {
          _loading = false;
          _error = true;
        });
      }
    }
  }

  Future<void> _refund() async {
    final o = _order;
    if (o == null || _busy) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('申請退款'),
        content: Text(
            '訂單 ${o.orderNoText} · ${formatHkd(o.paidAmount)}\n將退款整筆訂單及其所有券（共 ${o.quantity} 份），只有全部符合退款條件時才可申請。確定嗎？'),
        actions: [
          TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('取消')),
          ElevatedButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: const Text('確認退款')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      final result = await widget.api.refundOrder(o.id);
      if (!mounted) return;
      showCgToast(context, result['message'] as String? ?? '退款申請已提交');
      _load();
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        showCgToast(context, e.message);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final o = _order;
    if (_loading) {
      return Scaffold(
        appBar: AppBar(title: const Text('訂單詳情')),
        body: const _LoadingList(),
      );
    }
    if (_error || o == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('訂單詳情')),
        body: _ErrorView(onRetry: _load),
      );
    }
    final showRefundEta = o.refundStatus == 'refunded';
    return Scaffold(
      appBar: AppBar(title: const Text('訂單詳情')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(o.packageName,
              style:
                  const TextStyle(fontSize: 20, fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          Text(o.shopName,
              style: const TextStyle(color: CgColors.textSecondary)),
          const SizedBox(height: 16),
          _DetailRow(label: '訂單號', value: o.orderNoText),
          if (!o.demoPayment)
            _DetailRow(label: context.tr('付款方式', 'Payment method'), value: o.paymentMethodText),
          _DetailRow(
              label: o.demoPayment ? context.tr('確認時間', 'Confirmed at') : context.tr('付款時間', 'Payment time'),
              value: o.paidAt != null
                  ? formatDateTime(o.paidAt)
                  : (o.paymentStatus == 'paid'
                      ? formatDateTime(o.createdAt)
                      : '—')),
          _DetailRow(label: '下單時間', value: formatDateTime(o.createdAt)),
          if (o.quantity > 1) _DetailRow(label: '數量', value: '${o.quantity} 份'),
          _DetailRow(
              label: '金額',
              value: formatHkd(o.amount),
              valueBold: true),
          _DetailRow(label: o.demoPayment ? context.tr('確認狀態', 'Confirmation status') : context.tr('支付狀態', 'Payment status'),
              value: context.tr(o.paymentStatusText, o.paymentStatusEnglishText)),
          _DetailRow(label: context.tr('退款狀態', 'Refund status'),
              value: context.tr(o.refundStatusText, o.refundStatusEnglishText)),
          if (o.voucherStatusText.isNotEmpty)
            _DetailRow(label: context.tr('套餐券', 'Package voucher'),
                value: context.tr(o.voucherStatusText, o.voucherStatusEnglishText)),
          if (o.latestRefundReason.isNotEmpty)
            _DetailRow(label: context.tr('退款原因', 'Refund reason'),
                value: o.latestRefundReason),
          if (o.refundAmount > 0)
            _DetailRow(label: '退款金額', value: formatHkd(o.refundAmount)),
          if (showRefundEta)
            Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(o.demoPayment
                  ? context.tr('退款只更新訂單記錄，不會退回款項。', 'A refund only updates the order record; no money is returned.')
                  : context.tr('退款已發起，入賬時間視付款機構而定。', 'The refund was initiated. Timing depends on the payment provider.'),
                  style: const TextStyle(color: CgColors.textSecondary)),
            ),
          if (o.canRefund) ...[
            const SizedBox(height: 24),
            SizedBox(
              width: double.infinity,
              height: 48,
              child: OutlinedButton(
                onPressed: _busy ? null : _refund,
                child: Text(_busy ? context.tr('處理中…', 'Processing…') :
                    context.tr('申請退款', 'Request a refund')),
              ),
            ),
          ],
        ],
      ),
    );
  }
}

class _DetailRow extends StatelessWidget {
  const _DetailRow(
      {required this.label, required this.value, this.valueBold = false});

  final String label;
  final String value;
  final bool valueBold;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
              width: 88,
              child: Text(label,
                  style: const TextStyle(color: CgColors.textSecondary))),
          Expanded(
            child: Text(value,
                style: TextStyle(
                    fontWeight:
                        valueBold ? FontWeight.bold : FontWeight.normal,
                    fontFeatures: const [FontFeature.tabularFigures()])),
          ),
        ],
      ),
    );
  }
}

/// 支付页（跳转/对话框）完成后的确认入口：用户在支付页完成支付后
/// 点「我已完成支付」，调用方随即进入 [PaymentResultScreen] 轮询结果。
/// 返回 true 表示进入轮询，false/null 表示用户取消，订单留在待支付状态。
Future<bool?> showPaymentSheet(BuildContext context, ApiClient api, PackageOrder order) async {
  if (const bool.fromEnvironment('PUBLIC_TRIAL') && !order.demoPayment) {
    showCgToast(context, context.tr('未能確認訂單，請稍後重試。', 'Could not confirm the order. Try again later.'));
    return false;
  }
  if (order.paymentStatus == 'paid') return true;
  final confirmed = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (ctx) => AlertDialog(
      title: Text(ctx.tr('確認訂單', 'Confirm order')),
      content: Text(ctx.tr(
        '訂單 #${order.id} · ${formatHkd(order.amount)}\n確認此訂單不會扣款。',
        'Order #${order.id} · ${formatHkd(order.amount)}\nConfirming this order will not charge you.',
      )),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(ctx).pop(false),
          child: const Text('取消'),
        ),
        ElevatedButton(
          onPressed: () => Navigator.of(ctx).pop(true),
          child: Text(order.demoPayment ? ctx.tr('確認訂單', 'Confirm order') : ctx.tr('前往付款頁', 'Continue to checkout')),
        ),
      ],
    ),
  );
  if (confirmed != true) return false;
  if (!order.demoPayment) {
    try {
      final details = await api.get('/orders/${order.id}/stripe-test-payment');
      final token = api.paymentAccessToken;
      if (token == null || !context.mounted) return false;
      final opened = await openStripeTestPayment({
        ...details,
        'order_id': order.id,
        'api_base': api.baseUrl,
        'access_token': token,
        'return_url': Uri.base.toString(),
      });
      if (!opened && context.mounted) {
        showCgToast(context, context.tr('請使用網頁版完成付款。', 'Use the web version to continue to checkout.'));
      }
    } on ApiException catch (e) {
      if (context.mounted) showCgToast(context, e.message);
    } catch (_) {
      if (context.mounted) showCgToast(context, '未能開啟付款頁，請稍後重試');
    }
    // Web navigation replaces this screen; never treat opening the page as payment.
    return false;
  }
  try {
    await api.post('/orders/${order.id}/demo-payment');
    return true;
  } on ApiException catch (e) {
    if (context.mounted) showCgToast(context, e.message);
    return false;
  }
}

/// 支付结果轮询页（UI_SPEC §3.3）。
///
/// 支付页完成后每 2 秒轮询 GET /orders/{id}，最多 60 秒，直到
/// payment_status 为 paid / failed / timeout。60 秒后仍为 pending，
/// 停在「仍在確認」页，由用户选择继续确认或去订单列表查看。
/// pending 且已超 15 分钟（以服务端返回的 created_at 为准）视为已关闭。
class PaymentResultScreen extends StatefulWidget {
  const PaymentResultScreen(
      {super.key, required this.api, required this.initialOrder});

  final ApiClient api;

  /// 下单/重试接口返回的订单快照；金额等字段以轮询到的服务端值为准。
  final PackageOrder initialOrder;

  @override
  State<PaymentResultScreen> createState() => _PaymentResultScreenState();
}

enum _PollPhase { polling, paid, failed, timeout, closed, stillPending, error }

class _PaymentResultScreenState extends State<PaymentResultScreen> {
  static const _pollInterval = Duration(seconds: 2);

  /// 单轮轮询上限 60 秒（30 次 × 2 秒）。
  static const _maxAttempts = 30;

  /// 服务端订单关闭时限：pending 超 15 分钟视为已关闭。
  static const _closeAfter = Duration(minutes: 15);

  Timer? _timer;
  _PollPhase _phase = _PollPhase.polling;
  late PackageOrder _order;
  String? _failureMessage;
  int _attempts = 0;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _order = widget.initialOrder;
    _startPolling();
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  void _startPolling() {
    _timer?.cancel();
    _attempts = 0;
    setState(() => _phase = _PollPhase.polling);
    _pollOnce();
    _timer = Timer.periodic(_pollInterval, (_) {
      if (_attempts >= _maxAttempts) {
        _timer?.cancel();
        if (mounted && _phase == _PollPhase.polling) {
          setState(() => _phase = _PollPhase.stillPending);
        }
        return;
      }
      _pollOnce();
    });
  }

  Future<void> _pollOnce() async {
    if (!mounted || _phase != _PollPhase.polling) return;
    _attempts++;
    try {
      final raw = await widget.api.orderDetails(_order.id);
      if (!mounted || _phase != _PollPhase.polling) return;
      final order = PackageOrder.fromJson(raw);
      final created = order.createdAt;
      final expiredPending = order.paymentStatus == 'pending' &&
          created != null &&
          DateTime.now().difference(created) >= _closeAfter;
      setState(() => _order = order);
      if (expiredPending) {
        _finish(_PollPhase.timeout);
        return;
      }
      switch (order.paymentStatus) {
        case 'paid':
          _finish(_PollPhase.paid);
        case 'failed':
          _failureMessage = _lastFailureMessage(raw);
          _finish(_PollPhase.failed);
        case 'timeout':
          _finish(_PollPhase.timeout);
        case 'cancelled':
          _finish(_PollPhase.closed);
      }
    } on ApiException catch (_) {
      if (!mounted) return;
      _timer?.cancel();
      setState(() => _phase = _PollPhase.error);
    }
  }

  void _finish(_PollPhase phase) {
    _timer?.cancel();
    if (mounted) setState(() => _phase = phase);
  }

  static String? _lastFailureMessage(Map<String, dynamic> raw) {
    final payments = raw['payments'] as List? ?? [];
    if (payments.isEmpty) return null;
    final last = payments.last as Map<String, dynamic>;
    final msg = (last['failure_message'] as String?)?.trim();
    return (msg == null || msg.isEmpty) ? null : msg;
  }

  Future<void> _onBackPressed() async {
    if (_phase != _PollPhase.polling) {
      if (mounted) Navigator.of(context).pop();
      return;
    }
    final leave = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('確定離開？'),
        content: const Text('支付可能仍在處理，確定離開？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('繼續等待'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('確定離開'),
          ),
        ],
      ),
    );
    if (leave == true && mounted) {
      // 离开后订单在「我的订单」里继续显示状态。
      Navigator.of(context).pushReplacement(MaterialPageRoute(
          builder: (_) => OrdersScreen(api: widget.api)));
    }
  }

  Future<void> _retry() async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final res = await widget.api.retryPayment(
          _order.id, _order.paymentMethod.isEmpty ? 'fps' : _order.paymentMethod);
      final order = PackageOrder.fromJson(res['order'] as Map<String, dynamic>);
      if (!mounted) return;
      setState(() => _busy = false);
      final done = await showPaymentSheet(context, widget.api, order);
      if (done != true || !mounted) return;
      Navigator.of(context).pushReplacement(MaterialPageRoute(
          builder: (_) => PaymentResultScreen(
              api: widget.api, initialOrder: order)));
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        showCgToast(context, e.message);
      }
    }
  }

  Future<void> _cancel() async {
    if (_busy) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('取消訂單'),
        content: Text('確定取消訂單 #${_order.id} 嗎？'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('返回'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: const Text('確定取消'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      await widget.api.cancelOrder(_order.id);
      if (!mounted) return;
      showCgToast(context, '訂單已取消');
      Navigator.of(context).pushReplacement(MaterialPageRoute(
          builder: (_) => OrdersScreen(api: widget.api)));
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        showCgToast(context, e.message);
      }
    }
  }

  void _openOrders() {
    Navigator.of(context).pushReplacement(MaterialPageRoute(
        builder: (_) => OrdersScreen(api: widget.api)));
  }

  void _openWallet() {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => Scaffold(
          appBar: AppBar(title: const Text('我的券')),
          body: WalletScreen(
            api: widget.api,
            onShowQr: (item) => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => QrShowScreen(api: widget.api, item: item)))))));
  }

  void _goHome() {
    Navigator.of(context).popUntil((route) => route.isFirst);
  }

  @override
  Widget build(BuildContext context) {
    final polling = _phase == _PollPhase.polling;
    return PopScope(
      canPop: !polling,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && polling) _onBackPressed();
      },
      child: Scaffold(
        appBar: AppBar(
          title: const Text('支付結果'),
          leading: IconButton(
            icon: const Icon(Icons.arrow_back),
            tooltip: '返回',
            onPressed: _onBackPressed,
          ),
        ),
        body: switch (_phase) {
          _PollPhase.polling => _buildPolling(),
          _PollPhase.paid => _buildPaid(),
          _PollPhase.failed => _buildFailed(),
          _PollPhase.timeout => _buildFailed(),
          _PollPhase.closed => _buildClosed(),
          _PollPhase.stillPending => _buildStillPending(),
          _PollPhase.error => _ErrorView(
              message: '網絡不穩定，請重試',
              onRetry: _startPolling,
            ),
        },
      ),
    );
  }

  Widget _orderSummary() {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(_order.packageName,
            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
        const SizedBox(height: 4),
        Text('${_order.shopName} · 訂單 #${_order.id}'),
        const SizedBox(height: 4),
        Text(formatHkd(_order.amount),
            style: const TextStyle(
                fontSize: 22,
                fontWeight: FontWeight.bold,
                color: CgColors.primary,
                fontFeatures: [FontFeature.tabularFigures()])),
      ],
    );
  }

  Widget _buildPolling() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const SkeletonBox(height: 20, width: 180),
        const SizedBox(height: 12),
        const SkeletonBox(height: 16, width: 240),
        const SizedBox(height: 12),
        const SkeletonBox(height: 28, width: 140),
        const SizedBox(height: 24),
        const Row(
          children: [
            SizedBox(
              width: 24,
              height: 24,
              child: CircularProgressIndicator(strokeWidth: 2.5),
            ),
            SizedBox(width: 12),
            Expanded(
              child: Text('正在確認支付結果',
                  style: TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            ),
          ],
        ),
        const SizedBox(height: 8),
        const Text('請勿關閉頁面，結果確認後會自動顯示。',
            style: TextStyle(color: CgColors.textSecondary)),
        const SizedBox(height: 24),
        _orderSummary(),
      ],
    );
  }

  Widget _buildPaid() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const SizedBox(height: 32),
        const Icon(Icons.confirmation_number,
            size: 72, color: CgColors.primary),
        const SizedBox(height: 16),
        Text(context.tr('優惠券已存入錢包', 'Voucher added to wallet'),
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
        if (_order.demoPayment || const bool.fromEnvironment('PUBLIC_TRIAL'))
          Text(context.tr('訂單已確認，不會扣款。', 'Order confirmed. You will not be charged.'),
              textAlign: TextAlign.center),
        const SizedBox(height: 24),
        _orderSummary(),
        const SizedBox(height: 32),
        ElevatedButton(
          style: ElevatedButton.styleFrom(
              minimumSize: const Size(double.infinity, 48)),
          onPressed: _openWallet,
          child: const Text('查看券'),
        ),
        const SizedBox(height: 12),
        OutlinedButton(
          style: OutlinedButton.styleFrom(
              minimumSize: const Size(double.infinity, 48)),
          onPressed: _goHome,
          child: const Text('繼續逛'),
        ),
      ],
    );
  }

  Widget _buildFailed() {
    final isTimeout = _phase == _PollPhase.timeout;
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const SizedBox(height: 32),
        Icon(isTimeout ? Icons.timer_off : Icons.error_outline,
            size: 72, color: CgColors.primary),
        const SizedBox(height: 16),
        Text(isTimeout ? '訂單已關閉' : '支付失敗',
            textAlign: TextAlign.center,
            style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        Text(
            isTimeout
                ? '支付逾時，訂單已關閉。你可以重新支付或取消訂單。'
                : (_failureMessage ?? '支付失敗，請重試或選擇其他支付方式。'),
            textAlign: TextAlign.center,
            style: const TextStyle(color: CgColors.textSecondary)),
        const SizedBox(height: 24),
        _orderSummary(),
        const SizedBox(height: 32),
        ElevatedButton(
          style: ElevatedButton.styleFrom(
              minimumSize: const Size(double.infinity, 48)),
          onPressed: _busy ? null : _retry,
          child: Text(_busy ? '處理中…' : '重新支付'),
        ),
        const SizedBox(height: 12),
        OutlinedButton(
          style: OutlinedButton.styleFrom(
              minimumSize: const Size(double.infinity, 48)),
          onPressed: _busy ? null : _cancel,
          child: const Text('取消訂單'),
        ),
        const SizedBox(height: 12),
        TextButton(
          style: TextButton.styleFrom(
              minimumSize: const Size(double.infinity, 48)),
          onPressed: _openOrders,
          child: const Text('查看我的訂單'),
        ),
      ],
    );
  }

  Widget _buildClosed() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const SizedBox(height: 32),
        const Icon(Icons.receipt_long, size: 72, color: CgColors.primary),
        const SizedBox(height: 16),
        const Text('訂單已關閉',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        const Text('此訂單已取消。',
            textAlign: TextAlign.center,
            style: TextStyle(color: CgColors.textSecondary)),
        const SizedBox(height: 24),
        _orderSummary(),
        const SizedBox(height: 32),
        ElevatedButton(
          style: ElevatedButton.styleFrom(
              minimumSize: const Size(double.infinity, 48)),
          onPressed: _openOrders,
          child: const Text('查看我的訂單'),
        ),
        const SizedBox(height: 12),
        OutlinedButton(
          style: OutlinedButton.styleFrom(
              minimumSize: const Size(double.infinity, 48)),
          onPressed: _goHome,
          child: const Text('繼續逛'),
        ),
      ],
    );
  }

  Widget _buildStillPending() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        const SizedBox(height: 32),
        const Icon(Icons.hourglass_top, size: 72, color: CgColors.primary),
        const SizedBox(height: 16),
        const Text('仍在確認支付結果',
            textAlign: TextAlign.center,
            style: TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
        const SizedBox(height: 8),
        const Text('支付結果仍在確認中，你可以繼續等待，或稍後在「我的訂單」查看最新狀態。',
            textAlign: TextAlign.center,
            style: TextStyle(color: CgColors.textSecondary)),
        const SizedBox(height: 24),
        _orderSummary(),
        const SizedBox(height: 32),
        ElevatedButton(
          style: ElevatedButton.styleFrom(
              minimumSize: const Size(double.infinity, 48)),
          onPressed: _startPolling,
          child: const Text('繼續確認'),
        ),
        const SizedBox(height: 12),
        OutlinedButton(
          style: OutlinedButton.styleFrom(
              minimumSize: const Size(double.infinity, 48)),
          onPressed: _openOrders,
          child: const Text('查看我的訂單'),
        ),
      ],
    );
  }
}

/// 加载态：骨架屏，不用转圈。
class _LoadingList extends StatelessWidget {
  const _LoadingList();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(12),
      children: const [
        SkeletonBox(height: 72),
        SizedBox(height: 12),
        SkeletonBox(height: 72),
        SizedBox(height: 12),
        SkeletonBox(height: 72),
      ],
    );
  }
}

/// 空态：一句话 + 一个动作按钮。
class _EmptyView extends StatelessWidget {
  const _EmptyView(
      {required this.message, required this.actionLabel, required this.onAction});

  final String message;
  final String actionLabel;
  final VoidCallback onAction;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 16),
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                  minimumSize: const Size(160, 48)),
              onPressed: onAction,
              child: Text(actionLabel),
            ),
          ],
        ),
      ),
    );
  }
}

/// 错误态：一句话 + 重试。
class _ErrorView extends StatelessWidget {
  const _ErrorView({this.message = '網絡不穩定，請重試', required this.onRetry});

  final String message;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.wifi_off, size: 48, color: CgColors.textSecondary),
            const SizedBox(height: 12),
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 16),
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                  minimumSize: const Size(160, 48)),
              onPressed: onRetry,
              child: const Text('重試'),
            ),
          ],
        ),
      ),
    );
  }
}
