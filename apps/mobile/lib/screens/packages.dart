import 'package:flutter/material.dart';

import '../api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';

/// Line C (套餐 2A): package list, package purchase, my orders + refund.
///
/// NOTE: this build has no Flutter SDK on the dev machine, so this file
/// has NOT been through `flutter analyze` or a real-device build — the
/// wiring mirrors the existing screens (api_client / showCgToast) and
/// needs a real `flutter analyze` + device run before release.
class PackageListScreen extends StatefulWidget {
  const PackageListScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<PackageListScreen> createState() => _PackageListScreenState();
}

class _PackageListScreenState extends State<PackageListScreen> {
  List<Package> _items = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final list = await widget.api.packages();
      if (!mounted) return;
      setState(() {
        _items = list.map((e) => Package.fromJson(e)).toList();
        _loading = false;
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _loading = false);
        showCgToast(context, e.message);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('套餐')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _items.isEmpty
              ? const Center(child: Text('暫時沒有套餐。'))
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
                          leading: const Icon(Icons.restaurant_menu,
                              color: CgColors.primary),
                          title: Text(p.name),
                          subtitle: Text(
                              '${p.shopName} · ${p.personCount} 人用 · 已售 ${p.soldCount}'),
                          trailing: Column(
                            mainAxisAlignment: MainAxisAlignment.center,
                            crossAxisAlignment: CrossAxisAlignment.end,
                            children: [
                              Text('\$${p.packagePrice.toStringAsFixed(0)}',
                                  style: const TextStyle(
                                      color: CgColors.primary,
                                      fontSize: 18,
                                      fontWeight: FontWeight.bold)),
                              Text(
                                  '\$${p.originalPrice.toStringAsFixed(0)} · 省 ${p.savePercent.toStringAsFixed(0)}%',
                                  style: const TextStyle(
                                      decoration: TextDecoration.lineThrough,
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

/// Package purchase page: items, price anchor, usage rules, refund policy
/// (shown prominently before payment), FPS / card choice, then pay.
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
  String _method = 'fps';
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await widget.api.packageDetails(widget.packageId);
      if (!mounted) return;
      setState(() => _pkg = Package.fromJson(data));
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  Future<void> _buy() async {
    final pkg = _pkg;
    if (pkg == null) return;
    setState(() => _busy = true);
    try {
      final res = await widget.api.purchasePackage(pkg.id, _method,
          clientRef: 'app-${DateTime.now().millisecondsSinceEpoch}');
      final order = PackageOrder.fromJson(res['order'] as Map<String, dynamic>);
      if (!mounted) return;
      setState(() => _busy = false);
      final ok = await showDialog<bool>(
        context: context,
        builder: (ctx) => AlertDialog(
          title: const Text('完成支付（測試模式）'),
          content: Text(
              '訂單 #${order.id} · \$${order.amount.toStringAsFixed(0)}\n'
              '支付方式：${order.paymentMethodText}\n'
              'Stripe 測試模式：未接真實商戶號，不會扣款。'),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(ctx).pop(false),
              child: const Text('模擬支付失敗'),
            ),
            ElevatedButton(
              onPressed: () => Navigator.of(ctx).pop(true),
              child: const Text('模擬支付成功'),
            ),
          ],
        ),
      );
      if (ok == null) return;
      if (!mounted) return;
      try {
        final paid = await widget.api.confirmPayment(
            order.id, ok ? 'succeeded' : 'failed');
        if (!mounted) return;
        showCgToast(context,
            (paid['message'] as String?) ?? (ok ? '支付成功' : '支付失敗'));
        if (ok) {
          Navigator.of(context).pushReplacement(MaterialPageRoute(
              builder: (_) => OrdersScreen(api: widget.api)));
        } else {
          setState(() {});
        }
      } on ApiException catch (e) {
        if (mounted) showCgToast(context, e.message);
      }
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        showCgToast(context, e.message);
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final p = _pkg;
    if (p == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('套餐詳情')),
        body: const Center(child: CircularProgressIndicator()),
      );
    }
    return Scaffold(
      appBar: AppBar(title: const Text('套餐詳情')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(p.name,
              style:
                  const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
          const SizedBox(height: 6),
          Text('${p.shopName} · ${p.personCount} 人用',
              style: const TextStyle(color: CgColors.textSecondary)),
          const SizedBox(height: 12),
          Row(
            children: [
              Text('\$${p.packagePrice.toStringAsFixed(0)}',
                  style: const TextStyle(
                      color: CgColors.primary,
                      fontSize: 26,
                      fontWeight: FontWeight.bold)),
              const SizedBox(width: 10),
              Text('\$${p.originalPrice.toStringAsFixed(0)}',
                  style: const TextStyle(
                      decoration: TextDecoration.lineThrough,
                      color: CgColors.textSecondary)),
              const SizedBox(width: 8),
              Chip(label: Text('省 ${p.savePercent.toStringAsFixed(0)}%')),
              const Spacer(),
              Chip(
                  label: Text(p.daysLeft > 0
                      ? '剩 ${p.daysLeft} 日'
                      : '即將到期')),
            ],
          ),
          const SizedBox(height: 12),
          Text('剩餘 ${p.stockLeft} 份 · 已售 ${p.soldCount}'),
          if (p.validTo != null)
            Text('有效期至 ${p.validTo!.toLocal().toString().split(' ').first}'),
          const SizedBox(height: 16),
          const Text('套餐內容', style: TextStyle(fontWeight: FontWeight.bold)),
          ...p.items.map((i) => Padding(
                padding: const EdgeInsets.only(top: 4),
                child: Text('· $i'),
              )),
          const SizedBox(height: 16),
          const Text('使用須知', style: TextStyle(fontWeight: FontWeight.bold)),
          if (p.needBooking) const Text('· 需提前預約'),
          if (p.includes.isNotEmpty) Text('· 包含：${p.includes.join('、')}'),
          if (p.excludes.isNotEmpty) Text('· 不包含：${p.excludes.join('、')}'),
          const Text('· 到店出示 QR 碼核銷，整張一次核銷，不可拆分'),
          const SizedBox(height: 16),
          Container(
            padding: const EdgeInsets.all(12),
            decoration: BoxDecoration(
              color: const Color(0xFFFFF3CD),
              borderRadius: BorderRadius.circular(8),
            ),
            child: Text('退款規則：${p.refundPolicyText}',
                style: const TextStyle(fontWeight: FontWeight.bold)),
          ),
          const SizedBox(height: 16),
          const Text('支付方式', style: TextStyle(fontWeight: FontWeight.bold)),
          RadioListTile<String>(
            title: const Text('FPS 轉數快'),
            value: 'fps',
            groupValue: _method,
            onChanged: (v) => setState(() => _method = v ?? 'fps'),
          ),
          RadioListTile<String>(
            title: const Text('信用卡'),
            value: 'card',
            groupValue: _method,
            onChanged: (v) => setState(() => _method = v ?? 'card'),
          ),
          const Text('Stripe 測試模式：未接真實商戶號，不會扣款。',
              style: TextStyle(color: CgColors.textSecondary, fontSize: 12)),
        ],
      ),
      bottomNavigationBar: Padding(
        padding: const EdgeInsets.all(16),
        child: ElevatedButton(
          onPressed: _busy ? null : _buy,
          child: Text(_busy
              ? '處理中…'
              : '立即購買　\$${p.packagePrice.toStringAsFixed(0)}'),
        ),
      ),
    );
  }
}

/// My orders: status, pay / retry / cancel / refund entries.
class OrdersScreen extends StatefulWidget {
  const OrdersScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<OrdersScreen> createState() => _OrdersScreenState();
}

class _OrdersScreenState extends State<OrdersScreen> {
  List<PackageOrder> _orders = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final list = await widget.api.orders();
      if (!mounted) return;
      setState(() {
        _orders = list.map((e) => PackageOrder.fromJson(e)).toList();
        _loading = false;
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _loading = false);
        showCgToast(context, e.message);
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

  Future<void> _refund(PackageOrder o) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('申請退款'),
        content: Text(
            '訂單 #${o.id} · \$${o.amount.toStringAsFixed(0)}\n'
            '退款後套餐券立即作廢，不可再核銷。款項原路返回，預計 1–3 個工作日到賬。'),
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('我的訂單')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _orders.isEmpty
              ? const Center(child: Text('暫時沒有訂單。'))
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView.builder(
                    padding: const EdgeInsets.all(12),
                    itemCount: _orders.length,
                    itemBuilder: (_, i) {
                      final o = _orders[i];
                      return Card(
                        margin: const EdgeInsets.only(bottom: 12),
                        child: Padding(
                          padding: const EdgeInsets.all(12),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Row(
                                children: [
                                  Expanded(
                                    child: Text(o.packageName,
                                        style: const TextStyle(
                                            fontWeight: FontWeight.bold)),
                                  ),
                                  Chip(label: Text(o.paymentStatusText)),
                                ],
                              ),
                              Text(
                                  '${o.shopName} · ${o.paymentMethodText} · \$${o.amount.toStringAsFixed(0)}'),
                              if (o.refundStatus != 'none')
                                Text(
                                    '退款狀態：${o.refundStatus == 'refunded' ? '已退款' : o.refundStatus}',
                                    style: const TextStyle(
                                        color: CgColors.primary)),
                              const SizedBox(height: 8),
                              Wrap(
                                spacing: 8,
                                children: [
                                  if (o.canPay)
                                    ElevatedButton(
                                      onPressed: () => _act(() =>
                                          widget.api.confirmPayment(
                                              o.id, 'succeeded')),
                                      child: const Text('模擬支付成功'),
                                    ),
                                  if (o.canPay)
                                    OutlinedButton(
                                      onPressed: () =>
                                          _act(() => widget.api.cancelOrder(o.id)),
                                      child: const Text('取消訂單'),
                                    ),
                                  if (o.canRetry)
                                    ElevatedButton(
                                      onPressed: () => _act(() => widget
                                          .api
                                          .retryPayment(o.id, 'fps')),
                                      child: const Text('重新支付（FPS）'),
                                    ),
                                  if (o.canRefund)
                                    OutlinedButton(
                                      onPressed: () => _refund(o),
                                      child: const Text('申請退款'),
                                    ),
                                ],
                              ),
                            ],
                          ),
                        ),
                      );
                    },
                  ),
                ),
    );
  }
}
