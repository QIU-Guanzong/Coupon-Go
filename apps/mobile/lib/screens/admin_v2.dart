import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import 'reviews_admin.dart';
import 'withdrawal_review.dart';

/// Admin v2 home (line F). UI_SPEC §9 first phase: onboarding review,
/// orders and settlements are the three first-phase blocks; the other
/// entries are the pre-existing v2 screens.
///
/// Role hiding: entries are filtered by [role]. The role must come
/// from the server (no /me endpoint exists yet, so callers default to
/// 'admin' and see everything); hiding a menu is never authorization —
/// the API enforces roles server-side on every call.
class AdminHomeScreen extends StatelessWidget {
  const AdminHomeScreen({super.key, required this.api, this.role = 'admin'});

  final ApiClient api;
  final String role;

  @override
  Widget build(BuildContext context) {
    final entries = [
      ('入駐審核', Icons.person_add_alt, ['admin', 'auditor', 'support'],
          () => MaterialPageRoute(
              builder: (_) => OnboardingReviewScreen(api: api))),
      ('訂單', Icons.receipt_long, ['admin', 'finance', 'support'],
          () => MaterialPageRoute(
              builder: (_) => OrdersAdminScreen(api: api))),
      ('提現審核', Icons.account_balance_wallet_outlined, ['admin', 'finance'],
          () => MaterialPageRoute(builder: (_) => WithdrawalReviewScreen(api: api))),
      ('結算單', Icons.receipt_long, ['admin', 'finance'],
          () => MaterialPageRoute(
              builder: (_) => SettlementsScreen(api: api))),
      ('對賬', Icons.compare_arrows, ['admin', 'finance'],
          () => MaterialPageRoute(
              builder: (_) => ReconciliationScreen(api: api))),
      ('發票', Icons.request_quote, ['admin', 'finance'],
          () => MaterialPageRoute(
              builder: (_) => InvoicesScreen(api: api))),
      ('內容管理', Icons.web, ['admin'],
          () => MaterialPageRoute(
              builder: (_) => ContentScreen(api: api))),
      ('角色與權限', Icons.admin_panel_settings, ['admin'],
          () => MaterialPageRoute(builder: (_) => RolesScreen(api: api))),
      ('評價申訴', Icons.rate_review, ['admin', 'support'],
          () => MaterialPageRoute(
              builder: (_) => ReviewDisputesScreen(api: api))),
      // Merge note: line B's v1 dashboard (入駐審核 / 看板) lives at
      // '/admin/v1' — '/admin' itself is this v2 home (one case label only).
      ('入駐審核 / 看板 (v1)', Icons.admin_panel_settings, ['admin'],
          () => MaterialPageRoute(builder: (_) => AdminScreen(api: api))),
    ];
    final visible =
        entries.where((e) => e.$3.contains(role)).toList();
    return Scaffold(
      appBar: AppBar(
        backgroundColor: CgColors.vendorGold,
        foregroundColor: CgColors.textPrimary,
        title: const Text('管理後台'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          FutureBuilder<Map<String, dynamic>>(
            future: api.adminOverview(),
            builder: (context, snap) {
              if (snap.hasError) {
                final err = snap.error;
                final msg = err is ApiException ? err.message : '載入失敗';
                return Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Text('總覽載入失敗：$msg（需要財務 / 審核 / 客服或管理員權限）'),
                  ),
                );
              }
              if (!snap.hasData) {
                return const SkeletonBox(height: 120);
              }
              final data = snap.data!;
              final byStatus =
                  (data['settlements_by_status'] as Map<String, dynamic>?) ?? {};
              return Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('總覽', style: TextStyle(fontWeight: FontWeight.bold)),
                      Text('待審核發票：${data['invoices_pending'] ?? 0}'),
                      Text('未處理差異：${data['discrepancies_open'] ?? 0}'),
                      Text('申訴中評價：${data['reviews_disputed'] ?? 0}'),
                      Text('結算單：${byStatus.entries.map((e) => '${e.key} ${e.value}').join(' · ')}'),
                    ],
                  ),
                ),
              );
            },
          ),
          const SizedBox(height: 16),
          ...visible.map((e) => Card(
                margin: const EdgeInsets.only(top: 8),
                child: ListTile(
                  leading: Icon(e.$2, color: CgColors.amber),
                  title: Text(e.$1),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.of(context).push(e.$4()),
                ),
              )),
          if (visible.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 24),
              child: Text('目前角色沒有可用的管理功能。'),
            ),
        ],
      ),
    );
  }
}

/// UI_SPEC §9 first phase — onboarding review: pending list, filter,
/// qualification-photo preview, approve / reject (reason required),
/// right detail drawer. No charts.
class OnboardingReviewScreen extends StatefulWidget {
  const OnboardingReviewScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<OnboardingReviewScreen> createState() =>
      _OnboardingReviewScreenState();
}

class _OnboardingReviewScreenState extends State<OnboardingReviewScreen> {
  final _scaffoldKey = GlobalKey<ScaffoldState>();
  List<MerchantApplication> _items = [];
  bool _loading = false;
  String? _error;
  String _filter = 'pending';
  MerchantApplication? _selected;
  Map<String, dynamic>? _detail;

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
      final data = await widget.api.adminApplications(
          status: _filter == 'all' ? null : _filter);
      if (!mounted) return;
      setState(() {
        _items = ((data['applications'] as List?) ?? [])
            .map((e) => MerchantApplication.fromJson(e))
            .toList();
        _loading = false;
      });
    } on ApiException catch (_) {
      if (mounted) {
        setState(() {
          _error = '网络不稳定，请重试';
          _loading = false;
        });
      }
    }
  }

  Future<void> _openDetail(MerchantApplication app) async {
    setState(() {
      _selected = app;
      _detail = null;
    });
    _scaffoldKey.currentState?.openEndDrawer();
    try {
      final d = await widget.api.adminApplication(app.id);
      if (mounted) setState(() => _detail = d);
    } catch (_) {
      // Drawer still shows the list row's data.
    }
  }

  Future<void> _review(MerchantApplication app, bool approve) async {
    String reason = '';
    if (!approve) {
      reason = await showDialog<String>(
            context: context,
            builder: (c) {
              final ctrl = TextEditingController();
              return AlertDialog(
                title: const Text('駁回原因'),
                content: TextField(
                    controller: ctrl,
                    decoration:
                        const InputDecoration(hintText: '請填寫駁回原因（必填）')),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(c, ''),
                      child: const Text('取消')),
                  TextButton(
                      onPressed: () => Navigator.pop(c, ctrl.text.trim()),
                      child: const Text('確認駁回')),
                ],
              );
            },
          ) ??
          '';
      if (reason.isEmpty) return;
    }
    try {
      await widget.api.reviewMerchantApplication(app.id, approve,
          rejectReason: reason);
      if (mounted) {
        showCgToast(context, approve ? '入駐申請已通過' : '入駐申請已駁回');
        Navigator.of(context).maybePop();
      }
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  String _statusText(String s) => switch (s) {
        'pending' => '待審核',
        'approved' => '已通過',
        'rejected' => '已駁回',
        _ => s,
      };

  Widget _row(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
              width: 88,
              child: Text(label,
                  style: const TextStyle(color: CgColors.textSecondary))),
          Expanded(child: Text(value.isEmpty ? '—' : value)),
        ],
      ),
    );
  }

  Widget _drawer() {
    final app = _selected;
    if (app == null) return const Drawer(child: SizedBox());
    final photos = _detail?['shop_photos'] as List? ?? app.photos;
    return Drawer(
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text(app.shopName,
                style:
                    const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            Text('狀態：${_statusText(app.status)}'),
            const Divider(),
            _row('申請人', app.applicantEmail),
            _row('聯絡人', app.contactName),
            _row('電話', app.phone),
            _row('地址', app.address),
            _row('營業執照', app.businessLicense),
            _row('行業許可證', app.industryLicense),
            if (app.rejectReason.isNotEmpty) _row('駁回原因', app.rejectReason),
            const SizedBox(height: 12),
            const Text('資質圖片',
                style: TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 8),
            if (photos.isEmpty)
              const Text('未上傳資質圖片。',
                  style: TextStyle(color: CgColors.textSecondary))
            else
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: photos.map<Widget>((p) {
                  final url = '$p';
                  return GestureDetector(
                    onTap: () => showDialog<void>(
                      context: context,
                      builder: (c) => Dialog(
                        child: url.startsWith('http')
                            ? Image.network(url,
                                errorBuilder: (_, __, ___) =>
                                    const Padding(
                                        padding: EdgeInsets.all(24),
                                        child: Text('圖片無法載入')))
                            : Padding(
                                padding: const EdgeInsets.all(24),
                                child: Text(url)),
                      ),
                    ),
                    child: Container(
                      width: 96,
                      height: 96,
                      decoration: BoxDecoration(
                        border:
                            Border.all(color: CgColors.cardBorder),
                        borderRadius: BorderRadius.circular(8),
                      ),
                      clipBehavior: Clip.antiAlias,
                      child: url.startsWith('http')
                          ? Image.network(url,
                              fit: BoxFit.cover,
                              errorBuilder: (_, __, ___) => const Icon(
                                  Icons.broken_image,
                                  color: CgColors.textSecondary))
                          : const Icon(Icons.image,
                              color: CgColors.textSecondary),
                    ),
                  );
                }).toList(),
              ),
            if (app.status == 'pending') ...[
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: () => _review(app, true),
                  child: const Text('通過'),
                ),
              ),
              const SizedBox(height: 8),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton(
                  onPressed: () => _review(app, false),
                  child: const Text('駁回',
                      style: TextStyle(color: Colors.red)),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      key: _scaffoldKey,
      appBar: AppBar(title: const Text('入駐審核')),
      endDrawer: _drawer(),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: Wrap(
              spacing: 8,
              children: [
                for (final f in [
                  ('all', '全部'),
                  ('pending', '待審核'),
                  ('approved', '已通過'),
                  ('rejected', '已駁回'),
                ])
                  ChoiceChip(
                    label: Text(f.$2),
                    selected: _filter == f.$1,
                    onSelected: (_) {
                      setState(() => _filter = f.$1);
                      _load();
                    },
                  ),
              ],
            ),
          ),
          Expanded(
            child: _loading
                ? ListView(
                    padding: const EdgeInsets.all(16),
                    children: const [
                      SkeletonBox(height: 84),
                      SizedBox(height: 8),
                      SkeletonBox(height: 84),
                    ],
                  )
                : _error != null
                    ? Center(
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            const Text('网络不稳定，请重试'),
                            const SizedBox(height: 12),
                            ElevatedButton(
                                onPressed: _load, child: const Text('重試')),
                          ],
                        ),
                      )
                    : _items.isEmpty
                        ? Center(
                            child: Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                const Text('暫無入駐申請。'),
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
                              itemCount: _items.length,
                              itemBuilder: (_, i) {
                                final a = _items[i];
                                return Card(
                                  margin: const EdgeInsets.fromLTRB(
                                      12, 8, 12, 0),
                                  child: ListTile(
                                    title: Text(a.shopName),
                                    subtitle: Text(
                                        '${a.applicantEmail} · ${a.contactName} ${a.phone}\n狀態：${_statusText(a.status)}'),
                                    isThreeLine: true,
                                    trailing: const Icon(
                                        Icons.chevron_right),
                                    onTap: () => _openDetail(a),
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

/// UI_SPEC §9 first phase — package orders: status filter, list,
/// detail drawer with the payment / refund timeline, admin refund
/// with double confirmation + a required reason. No charts.
///
class OrdersAdminScreen extends StatefulWidget {
  const OrdersAdminScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<OrdersAdminScreen> createState() => _OrdersAdminScreenState();
}

class _OrdersAdminScreenState extends State<OrdersAdminScreen> {
  final _scaffoldKey = GlobalKey<ScaffoldState>();
  List<PackageOrder> _orders = [];
  bool _loading = false;
  String? _error;
  String _filter = 'all';
  PackageOrder? _selected;
  List<Map<String, dynamic>> _detailPayments = [];

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
      final list = await widget.api.adminOrders();
      if (!mounted) return;
      setState(() {
        _orders = list
            .map((e) => PackageOrder.fromJson(e as Map<String, dynamic>))
            .toList();
        _loading = false;
      });
    } on ApiException catch (_) {
      if (mounted) {
        setState(() {
          _error = '网络不稳定，请重试';
          _loading = false;
        });
      }
    }
  }

  List<PackageOrder> get _filtered {
    switch (_filter) {
      case 'pending':
        return _orders.where((o) => o.paymentStatus == 'pending').toList();
      case 'paid':
        return _orders
            .where((o) =>
                o.paymentStatus == 'paid' && o.refundStatus == 'none')
            .toList();
      case 'refunded':
        return _orders.where((o) => o.refundStatus == 'refunded').toList();
      default:
        return _orders;
    }
  }

  Future<void> _openDetail(PackageOrder order) async {
    setState(() {
      _selected = order;
      _detailPayments = order.payments;
    });
    _scaffoldKey.currentState?.openEndDrawer();
    try {
      final d = await widget.api.adminOrderDetail(order.id);
      if (!mounted) return;
      final full = PackageOrder.fromJson(d);
      setState(() {
        _selected = full;
        _detailPayments = full.payments;
      });
    } catch (_) {
      // Drawer still shows the list row's data.
    }
  }

  Future<void> _adminRefund(PackageOrder order) async {
    final ctrl = TextEditingController();
    final reason = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('管理員退款'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('訂單 #${order.id} · ${formatHkd(order.paidAmount)}'),
            const SizedBox(height: 8),
            const Text('退款後套餐券作廢，確定退款嗎？'),
            const SizedBox(height: 8),
            TextField(
              controller: ctrl,
              decoration:
                  const InputDecoration(hintText: '退款原因（必填）'),
            ),
          ],
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(c),
              child: const Text('取消')),
          TextButton(
              onPressed: () => Navigator.pop(c, ctrl.text.trim()),
              child: const Text('確認退款')),
        ],
      ),
    );
    if (reason == null || reason.isEmpty) return;
    try {
      final result = await widget.api.post('/admin/orders/${order.id}/refund',
          body: {'reason': reason});
      if (!mounted) return;
      setState(() {
        _selected = PackageOrder.fromJson(result['order'] as Map<String, dynamic>);
        _detailPayments = _selected!.payments;
      });
      showCgToast(context, result['message'] as String? ?? '退款已提交');
      await _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  String _refundText(PackageOrder o) => switch (o.refundStatus) {
        'requested' => '退款處理中',
        'refunded' => '已退款',
        'rejected' => '退款被拒',
        _ => '未退款',
      };

  Widget _timelineRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
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

  String _timeText(DateTime? d) =>
      d == null ? '—' : d.toLocal().toString().split('.').first;

  Widget _drawer() {
    final o = _selected;
    if (o == null) return const Drawer(child: SizedBox());
    return Drawer(
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text('訂單 #${o.id}',
                style:
                    const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            Text('${o.packageName} · ${o.shopName}'),
            const Divider(),
            _timelineRow('訂單金額', formatHkd(o.amount)),
            _timelineRow('實付金額', formatHkd(o.paidAmount)),
            _timelineRow('支付方式', o.paymentMethodText),
            _timelineRow('支付狀態', o.paymentStatusText),
            _timelineRow('退款狀態', _refundText(o)),
            _timelineRow('下單時間', _timeText(o.createdAt)),
            _timelineRow('支付時間', _timeText(o.paidAt)),
            if (o.refundStatus == 'refunded' && !o.demoPayment)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text('預計 1–3 個工作日原路退回',
                    style: TextStyle(color: CgColors.textSecondary)),
              ),
            const SizedBox(height: 12),
            const Text('支付 / 退款時間線',
                style: TextStyle(fontWeight: FontWeight.bold)),
            const SizedBox(height: 4),
            if (_detailPayments.isEmpty)
              const Text('暫無支付記錄。',
                  style: TextStyle(color: CgColors.textSecondary))
            else
              ..._detailPayments.map((p) => _timelineRow(
                  '${p['status'] ?? ''}',
                  '${formatHkd((p['amount'] as num?)?.toDouble() ?? 0)} · ${_timeText(DateTime.tryParse('${p['created_at'] ?? ''}'))}')),
            if (o.adminRefundable) ...[
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: () => _adminRefund(o),
                  child: const Text('管理員退款'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final orders = _filtered;
    return Scaffold(
      key: _scaffoldKey,
      appBar: AppBar(title: const Text('訂單')),
      endDrawer: _drawer(),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: Wrap(
              spacing: 8,
              children: [
                for (final f in [
                  ('all', '全部'),
                  ('pending', '待支付'),
                  ('paid', '已支付'),
                  ('refunded', '已退款'),
                ])
                  ChoiceChip(
                    label: Text(f.$2),
                    selected: _filter == f.$1,
                    onSelected: (_) => setState(() => _filter = f.$1),
                  ),
              ],
            ),
          ),
          Expanded(
            child: _loading
                ? ListView(
                    padding: const EdgeInsets.all(16),
                    children: const [
                      SkeletonBox(height: 84),
                      SizedBox(height: 8),
                      SkeletonBox(height: 84),
                    ],
                  )
                : _error != null
                    ? Center(
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            const Text('网络不稳定，请重试'),
                            const SizedBox(height: 12),
                            ElevatedButton(
                                onPressed: _load, child: const Text('重試')),
                          ],
                        ),
                      )
                    : orders.isEmpty
                        ? Center(
                            child: Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                const Text('暫無符合條件的訂單。'),
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
                              itemCount: orders.length,
                              itemBuilder: (_, i) {
                                final o = orders[i];
                                return Card(
                                  margin: const EdgeInsets.fromLTRB(
                                      12, 8, 12, 0),
                                  child: ListTile(
                                    title: Text(
                                        '#${o.id} · ${o.packageName}'),
                                    subtitle: Text(
                                        '${o.shopName} · ${formatHkd(o.paidAmount)} · ${o.paymentStatusText} · ${_refundText(o)}'),
                                    trailing: const Icon(
                                        Icons.chevron_right),
                                    onTap: () => _openDetail(o),
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

/// Settlements: generate for a shop + YYYY-MM period, then approve /
/// dispute / resolve / pay. Amounts come from redeemed coupons' face
/// value (see backend); commission_rate is entered per generation.
class SettlementsScreen extends StatefulWidget {
  const SettlementsScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<SettlementsScreen> createState() => _SettlementsScreenState();
}

class _SettlementsScreenState extends State<SettlementsScreen> {
  final _scaffoldKey = GlobalKey<ScaffoldState>();
  List<dynamic> _items = [];
  bool _loading = true;
  String _statusFilter = 'all';
  Map<String, dynamic>? _selected;
  final _shopCtrl = TextEditingController();
  final _periodCtrl = TextEditingController(
      text: '${DateTime.now().year}-${DateTime.now().month.toString().padLeft(2, '0')}');
  final _rateCtrl = TextEditingController(text: '0.10');

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _shopCtrl.dispose();
    _periodCtrl.dispose();
    _rateCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await widget.api.settlements(
          status: _statusFilter == 'all' ? null : _statusFilter);
      if (!mounted) return;
      setState(() {
        _items = data['settlements'] as List<dynamic>;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  String _settlementStatusText(String s) => switch (s) {
        'pending' => '待確認',
        'approved' => '已確認',
        'disputed' => '有爭議',
        'paid' => '已付款',
        _ => s,
      };

  String _moneyText(dynamic v) =>
      formatHkd((v as num?)?.toDouble() ?? 0);

  Future<void> _exportCsv() async {
    final lines = [
      'id,shop_name,period,gross_amount,commission,net_amount,status'
    ];
    for (final s in _items) {
      lines.add([
        '${s['id']}',
        '${s['shop_name']}',
        '${s['period']}',
        '${s['gross_amount']}',
        '${s['commission']}',
        '${s['net_amount']}',
        '${s['status']}',
      ].join(','));
    }
    final csv = lines.join('\n');
    await showDialog<void>(
      context: context,
      builder: (c) => AlertDialog(
        title: Text('結算單 CSV（共 ${_items.length} 條）'),
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
  }

  Widget _detailDrawer() {
    final s = _selected;
    if (s == null) return const Drawer(child: SizedBox());
    return Drawer(
      child: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text('#${s['id']} · ${s['shop_name']}',
                style:
                    const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
            Text('${s['period']} · ${_settlementStatusText('${s['status']}')}'),
            const Divider(),
            _drawerRow('核銷筆數', '${s['redemption_count'] ?? 0}'),
            _drawerRow('毛額', _moneyText(s['gross_amount'])),
            _drawerRow('佣金', _moneyText(s['commission'])),
            _drawerRow('淨額', _moneyText(s['net_amount'])),
            if ((s['dispute_reason'] as String? ?? '').isNotEmpty)
              _drawerRow('爭議原因', '${s['dispute_reason']}'),
            _drawerRow('建立時間', '${s['created_at'] ?? '—'}'),
            _drawerRow('確認時間', '${s['decided_at'] ?? '—'}'),
            _drawerRow('付款時間', '${s['paid_at'] ?? '—'}'),
          ],
        ),
      ),
    );
  }

  Widget _drawerRow(String label, String value) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 3),
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

  Future<void> _act(Future<Map<String, dynamic>> Function() call) async {
    try {
      await call();
      if (mounted) showCgToast(context, '已更新');
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      key: _scaffoldKey,
      appBar: AppBar(
        title: const Text('結算單'),
        actions: [
          Semantics(
            label: '匯出 CSV',
            button: true,
            child: IconButton(
              icon: const Icon(Icons.download),
              tooltip: '匯出 CSV',
              onPressed: _items.isEmpty ? null : _exportCsv,
            ),
          ),
        ],
      ),
      endDrawer: _detailDrawer(),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Wrap(
              spacing: 8,
              children: [
                for (final f in [
                  ('all', '全部'),
                  ('pending', '待確認'),
                  ('approved', '已確認'),
                  ('disputed', '有爭議'),
                  ('paid', '已付款'),
                ])
                  ChoiceChip(
                    label: Text(f.$2),
                    selected: _statusFilter == f.$1,
                    onSelected: (_) {
                      setState(() => _statusFilter = f.$1);
                      _load();
                    },
                  ),
              ],
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _shopCtrl,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: '商户 ID'),
            ),
            TextField(
              controller: _periodCtrl,
              decoration: const InputDecoration(labelText: '結算週期（YYYY-MM）'),
            ),
            TextField(
              controller: _rateCtrl,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: '佣金比例（0–1）'),
            ),
            const SizedBox(height: 8),
            ElevatedButton(
              onPressed: () async {
                final shopId = int.tryParse(_shopCtrl.text.trim());
                final rate = double.tryParse(_rateCtrl.text.trim());
                if (shopId == null || rate == null) {
                  showCgToast(context, '請填寫正確的商户 ID 與佣金比例');
                  return;
                }
                await _act(() => widget.api.generateSettlement(
                    shopId, _periodCtrl.text.trim(), rate));
              },
              child: const Text('生成結算單'),
            ),
            const SizedBox(height: 16),
            if (_loading)
              const Column(
                children: [
                  SkeletonBox(height: 72),
                  SizedBox(height: 8),
                  SkeletonBox(height: 72),
                ],
              )
            else
              ..._items.map((s) => Card(
                    margin: const EdgeInsets.only(top: 8),
                    child: ListTile(
                      title: Text(
                          '#${s['id']} · ${s['shop_name']} · ${s['period']}'),
                      subtitle: Text(
                          '毛額 ${_moneyText(s['gross_amount'])} · 佣金 ${_moneyText(s['commission'])} · 淨額 ${_moneyText(s['net_amount'])} · ${_settlementStatusText('${s['status']}')}'),
                      onTap: () {
                        setState(() => _selected =
                            Map<String, dynamic>.from(s as Map));
                        _scaffoldKey.currentState?.openEndDrawer();
                      },
                      trailing: PopupMenuButton<String>(
                        onSelected: (v) {
                          final id = s['id'] as int;
                          switch (v) {
                            case 'approve':
                              _act(() => widget.api.approveSettlement(id));
                            case 'pay':
                              _act(() => widget.api.paySettlement(id));
                            case 'dispute':
                              _act(() => widget.api.disputeSettlement(
                                  id, '金額有疑問，請核對'));
                          }
                        },
                        itemBuilder: (_) => const [
                          PopupMenuItem(value: 'approve', child: Text('確認')),
                          PopupMenuItem(value: 'dispute', child: Text('提出爭議')),
                          PopupMenuItem(value: 'pay', child: Text('付款')),
                        ],
                      ),
                    ),
                  )),
            if (!_loading && _items.isEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 24),
                child: Center(
                  child: Column(
                    children: [
                      const Text('暫時沒有結算單'),
                      const SizedBox(height: 12),
                      ElevatedButton(
                          onPressed: _load, child: const Text('重新載入')),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

/// Reconciliation: run a shop + period comparison, then resolve each
/// open discrepancy with a note.
class ReconciliationScreen extends StatefulWidget {
  const ReconciliationScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<ReconciliationScreen> createState() => _ReconciliationScreenState();
}

class _ReconciliationScreenState extends State<ReconciliationScreen> {
  List<dynamic> _reports = [];
  bool _loading = true;
  final _shopCtrl = TextEditingController();
  final _periodCtrl = TextEditingController(
      text: '${DateTime.now().year}-${DateTime.now().month.toString().padLeft(2, '0')}');

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _shopCtrl.dispose();
    _periodCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await widget.api.reconciliationReports();
      if (!mounted) return;
      setState(() {
        _reports = data['reports'] as List<dynamic>;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('對賬')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            TextField(
              controller: _shopCtrl,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: '商户 ID'),
            ),
            TextField(
              controller: _periodCtrl,
              decoration: const InputDecoration(labelText: '週期（YYYY-MM）'),
            ),
            const SizedBox(height: 8),
            ElevatedButton(
              onPressed: () async {
                final shopId = int.tryParse(_shopCtrl.text.trim());
                if (shopId == null) {
                  showCgToast(context, '請填寫正確的商户 ID');
                  return;
                }
                try {
                  final res = await widget.api.reconciliationRun(
                      shopId, _periodCtrl.text.trim());
                  if (!context.mounted) return;
                  showCgToast(context, res['message'] as String? ?? '對賬完成');
                  _load();
                } on ApiException catch (e) {
                  if (context.mounted) showCgToast(context, e.message);
                }
              },
              child: const Text('執行對賬'),
            ),
            const SizedBox(height: 16),
            if (_loading)
              const Center(child: CircularProgressIndicator())
            else
              ..._reports.map((r) => Card(
                    margin: const EdgeInsets.only(top: 8),
                    child: ListTile(
                      title: Text(
                          '#${r['id']} · ${r['shop_name']} · ${r['period']}'),
                      subtitle: Text(
                          '核銷 ${r['redemption_count']} · 結算行 ${r['settlement_line_count']} · 差異 ${r['discrepancy_count']} · ${r['status']}'),
                    ),
                  )),
            if (!_loading && _reports.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 24),
                child: Text('暫時沒有對賬報告'),
              ),
          ],
        ),
      ),
    );
  }
}

/// Invoices: list, request (against a settlement or standalone),
/// approve / reject / issue. Finance / admin only for decisions.
class InvoicesScreen extends StatefulWidget {
  const InvoicesScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<InvoicesScreen> createState() => _InvoicesScreenState();
}

class _InvoicesScreenState extends State<InvoicesScreen> {
  List<dynamic> _items = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await widget.api.invoices();
      if (!mounted) return;
      setState(() {
        _items = data['invoices'] as List<dynamic>;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  Future<void> _act(Future<Map<String, dynamic>> Function() call) async {
    try {
      await call();
      if (mounted) showCgToast(context, '已更新');
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('發票')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_loading)
              const Center(child: CircularProgressIndicator())
            else
              ..._items.map((inv) => Card(
                    margin: const EdgeInsets.only(top: 8),
                    child: ListTile(
                      title: Text('#${inv['id']} · ${inv['title']}'),
                      subtitle: Text(
                          '${inv['shop_name']} · 金額 ${inv['amount']} · ${inv['status']}'),
                      trailing: PopupMenuButton<String>(
                        onSelected: (v) {
                          final id = inv['id'] as int;
                          switch (v) {
                            case 'approve':
                              _act(() => widget.api.approveInvoice(id));
                            case 'reject':
                              _act(() => widget.api
                                  .rejectInvoice(id, '資料不全，請補充'));
                            case 'issue':
                              _act(() => widget.api.issueInvoice(id));
                          }
                        },
                        itemBuilder: (_) => const [
                          PopupMenuItem(value: 'approve', child: Text('批准')),
                          PopupMenuItem(value: 'reject', child: Text('拒絕')),
                          PopupMenuItem(value: 'issue', child: Text('開具')),
                        ],
                      ),
                    ),
                  )),
            if (!_loading && _items.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 24),
                child: Text('暫時沒有發票申請'),
              ),
          ],
        ),
      ),
    );
  }
}

/// CMS content: banners / announcements / categories in three tabs.
class ContentScreen extends StatefulWidget {
  const ContentScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<ContentScreen> createState() => _ContentScreenState();
}

class _ContentScreenState extends State<ContentScreen> {
  List<dynamic> _banners = [];
  List<dynamic> _announcements = [];
  List<dynamic> _categories = [];
  bool _loading = true;
  final _bannerTitleCtrl = TextEditingController();
  final _annTitleCtrl = TextEditingController();
  final _catNameCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _bannerTitleCtrl.dispose();
    _annTitleCtrl.dispose();
    _catNameCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final banners = await widget.api.adminBanners();
      final anns = await widget.api.adminAnnouncements();
      final cats = await widget.api.adminCategories();
      if (!mounted) return;
      setState(() {
        _banners = banners;
        _announcements = anns;
        _categories = cats;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  Future<void> _act(Future<dynamic> Function() call) async {
    try {
      await call();
      if (mounted) showCgToast(context, '已更新');
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 3,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('內容管理'),
          bottom: const TabBar(tabs: [
            Tab(text: '橫幅'),
            Tab(text: '公告'),
            Tab(text: '分類'),
          ]),
        ),
        body: TabBarView(
          children: [
            _tab(
              _loading,
              TextField(
                controller: _bannerTitleCtrl,
                decoration: const InputDecoration(labelText: '橫幅標題'),
              ),
              ElevatedButton(
                onPressed: () {
                  final title = _bannerTitleCtrl.text.trim();
                  if (title.isEmpty) {
                    showCgToast(context, '請填寫標題');
                    return;
                  }
                  _act(() => widget.api
                      .createBanner({'title': title, 'status': 'draft'}));
                },
                child: const Text('新增橫幅（草稿）'),
              ),
              _banners.map((b) => ListTile(
                    title: Text(b['title'] as String),
                    subtitle: Text('${b['status']} · 排序 ${b['sort_order']}'),
                    trailing: IconButton(
                      icon: const Icon(Icons.archive),
                      tooltip: '下架',
                      onPressed: () =>
                          _act(() => widget.api.deleteBanner(b['id'] as int)),
                    ),
                  )),
            ),
            _tab(
              _loading,
              TextField(
                controller: _annTitleCtrl,
                decoration: const InputDecoration(labelText: '公告標題'),
              ),
              ElevatedButton(
                onPressed: () {
                  final title = _annTitleCtrl.text.trim();
                  if (title.isEmpty) {
                    showCgToast(context, '請填寫標題');
                    return;
                  }
                  _act(() => widget.api.createAnnouncement(
                      {'title': title, 'status': 'draft'}));
                },
                child: const Text('新增公告（草稿）'),
              ),
              _announcements.map((a) => ListTile(
                    title: Text(a['title'] as String),
                    subtitle: Text('${a['status']}'),
                    trailing: IconButton(
                      icon: const Icon(Icons.archive),
                      tooltip: '下架',
                      onPressed: () => _act(() =>
                          widget.api.deleteAnnouncement(a['id'] as int)),
                    ),
                  )),
            ),
            _tab(
              _loading,
              TextField(
                controller: _catNameCtrl,
                decoration: const InputDecoration(labelText: '分類名稱'),
              ),
              ElevatedButton(
                onPressed: () {
                  final name = _catNameCtrl.text.trim();
                  if (name.isEmpty) {
                    showCgToast(context, '請填寫名稱');
                    return;
                  }
                  _act(() =>
                      widget.api.createCategory({'name': name}));
                },
                child: const Text('新增分類'),
              ),
              _categories.map((c) => ListTile(
                    title: Text(c['name'] as String),
                    subtitle: Text(
                        (c['is_active'] as bool) ? '啟用中' : '已停用'),
                    trailing: IconButton(
                      icon: Icon((c['is_active'] as bool)
                          ? Icons.block
                          : Icons.check_circle),
                      tooltip: (c['is_active'] as bool) ? '停用' : '啟用',
                      onPressed: () => _act(() => (c['is_active'] as bool)
                          ? widget.api.deactivateCategory(c['id'] as int)
                          : widget.api.activateCategory(c['id'] as int)),
                    ),
                  )),
            ),
          ],
        ),
      ),
    );
  }

  Widget _tab(bool loading, Widget input, Widget button,
      Iterable<Widget> rows) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        input,
        const SizedBox(height: 8),
        button,
        const SizedBox(height: 16),
        if (loading) const Center(child: CircularProgressIndicator()),
        ...rows,
      ],
    );
  }
}

/// Roles: view the role -> permission map and change a user's role
/// (admin only; the backend blocks self-change and unknown roles).
class RolesScreen extends StatefulWidget {
  const RolesScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<RolesScreen> createState() => _RolesScreenState();
}

class _RolesScreenState extends State<RolesScreen> {
  Map<String, dynamic>? _roles;
  bool _loading = true;
  final _userCtrl = TextEditingController();
  String _role = 'finance';

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _userCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await widget.api.adminRoles();
      if (!mounted) return;
      setState(() {
        _roles = data;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final perms =
        (_roles?['permissions'] as Map<String, dynamic>?) ?? {};
    return Scaffold(
      appBar: AppBar(title: const Text('角色與權限')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
            controller: _userCtrl,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: '用戶 ID'),
          ),
          DropdownButton<String>(
            value: _role,
            isExpanded: true,
            items: const [
              'customer',
              'vendor',
              'merchant-admin',
              'finance',
              'auditor',
              'support',
              'admin',
            ]
                .map((r) => DropdownMenuItem(value: r, child: Text(r)))
                .toList(),
            onChanged: (v) {
              if (v != null) setState(() => _role = v);
            },
          ),
          ElevatedButton(
            onPressed: () async {
              final uid = int.tryParse(_userCtrl.text.trim());
              if (uid == null) {
                showCgToast(context, '請填寫正確的用戶 ID');
                return;
              }
              try {
                await widget.api.changeUserRole(uid, _role);
                if (context.mounted) showCgToast(context, '角色已更新');
              } on ApiException catch (e) {
                if (context.mounted) showCgToast(context, e.message);
              }
            },
            child: const Text('更新角色'),
          ),
          const SizedBox(height: 16),
          if (_loading)
            const Center(child: CircularProgressIndicator())
          else
            ...perms.entries.map((e) => Card(
                  margin: const EdgeInsets.only(top: 8),
                  child: ListTile(
                    title: Text(e.key),
                    subtitle: Text(
                        ((e.value as List<dynamic>)).join(', ')),
                  ),
                )),
        ],
      ),
    );
  }
}

/// Review disputes: disputed reviews queue; uphold hides the review,
/// dismiss restores it into the rating aggregate.
class ReviewDisputesScreen extends StatefulWidget {
  const ReviewDisputesScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<ReviewDisputesScreen> createState() => _ReviewDisputesScreenState();
}

class _ReviewDisputesScreenState extends State<ReviewDisputesScreen> {
  List<dynamic> _items = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await widget.api.reviewDisputes();
      if (!mounted) return;
      setState(() {
        _items = data['reviews'] as List<dynamic>;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  Future<void> _resolve(int id, String decision) async {
    try {
      final res = await widget.api.resolveReviewDispute(
          id, decision, decision == 'uphold' ? '申訴成立' : '申訴不成立');
      if (mounted) showCgToast(context, res['message'] as String? ?? '已處理');
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('評價申訴')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_loading)
              const Center(child: CircularProgressIndicator())
            else
              ..._items.map((r) => Card(
                    margin: const EdgeInsets.only(top: 8),
                    child: ListTile(
                      title: Text(
                          '#${r['id']} · ${r['content'] ?? ''}'),
                      subtitle: Text(
                          '申訴原因：${r['dispute_reason'] ?? ''} · ${r['taste']}/${r['ambience']}/${r['service']} 分'),
                      trailing: PopupMenuButton<String>(
                        onSelected: (v) =>
                            _resolve(r['id'] as int, v),
                        itemBuilder: (_) => const [
                          PopupMenuItem(
                              value: 'uphold', child: Text('申訴成立（隱藏）')),
                          PopupMenuItem(
                              value: 'dismiss', child: Text('申訴不成立（恢復）')),
                        ],
                      ),
                    ),
                  )),
            if (!_loading && _items.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 24),
                child: Text('暫時沒有申訴中的評價'),
              ),
          ],
        ),
      ),
    );
  }
}
