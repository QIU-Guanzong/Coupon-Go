import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../models.dart';
import '../language.dart';
import '../theme.dart';
import '../widgets/merchant_layout.dart';
import '../widgets/widgets.dart';
import 'reviews_admin.dart';
import 'withdrawal_review.dart';
import 'redemption_recovery.dart';

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
      (context.tr('入駐審核', 'Merchant review'), Icons.person_add_alt, ['admin', 'auditor', 'support'],
          () => MaterialPageRoute(
              builder: (_) => OnboardingReviewScreen(api: api))),
      (context.tr('訂單', 'Orders'), Icons.receipt_long, ['admin', 'finance', 'support'],
          () => MaterialPageRoute(
              builder: (_) => OrdersAdminScreen(api: api))),
      (context.tr('提現審核', 'Payout review'), Icons.account_balance_wallet_outlined, ['admin', 'finance'],
          () => MaterialPageRoute(builder: (_) => WithdrawalReviewScreen(api: api))),
      (context.tr('結算單', 'Settlements'), Icons.receipt_long, ['admin', 'finance'],
          () => MaterialPageRoute(
              builder: (_) => SettlementsScreen(api: api))),
      (context.tr('對賬', 'Reconciliation'), Icons.compare_arrows, ['admin', 'finance'],
          () => MaterialPageRoute(
              builder: (_) => ReconciliationScreen(api: api))),
      (context.tr('發票', 'Invoices'), Icons.request_quote, ['admin', 'finance'],
          () => MaterialPageRoute(
              builder: (_) => InvoicesScreen(api: api))),
      (context.tr('內容管理', 'Content'), Icons.web, ['admin'],
          () => MaterialPageRoute(
              builder: (_) => ContentScreen(api: api))),
      (context.tr('角色與權限', 'Roles and permissions'), Icons.admin_panel_settings, ['admin'],
          () => MaterialPageRoute(builder: (_) => RolesScreen(api: api))),
      (context.tr('客服撤銷核銷', 'Reverse redemption'), Icons.assignment_return_outlined, ['admin', 'support'],
          () => MaterialPageRoute(builder: (_) => ForceRevokeRedemptionScreen(api: api))),
      (context.tr('評價申訴', 'Review disputes'), Icons.rate_review, ['admin', 'support'],
          () => MaterialPageRoute(
              builder: (_) => ReviewDisputesScreen(api: api))),
      // Merge note: line B's v1 dashboard (入駐審核 / 看板) lives at
      // '/admin/v1' — '/admin' itself is this v2 home (one case label only).
      (context.tr('入駐審核 / 看板 (v1)', 'Merchant review / Dashboard (v1)'), Icons.admin_panel_settings, ['admin'],
          () => MaterialPageRoute(builder: (_) => AdminScreen(api: api))),
    ];
    final visible =
        entries.where((e) => e.$3.contains(role)).toList();
    return Scaffold(
      appBar: AppBar(
        title: Text(context.tr('管理後台', 'Admin console')),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          FutureBuilder<Map<String, dynamic>>(
            future: api.adminOverview(),
            builder: (context, snap) {
              if (snap.hasError) {
                final err = snap.error;
                final msg = err is ApiException ? err.message : context.tr('載入失敗', 'Could not load');
                return Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Text(context.tr('總覽載入失敗：$msg（需要財務 / 審核 / 客服或管理員權限）', 'Could not load the overview: $msg (finance, audit, support or admin access required)')),
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
                      Text(context.tr('總覽', 'Overview'), style: Theme.of(context).textTheme.titleMedium),
                      Text(context.tr('待審核發票：${data['invoices_pending'] ?? 0}', 'Invoices to review: ${data['invoices_pending'] ?? 0}')),
                      Text(context.tr('未處理差異：${data['discrepancies_open'] ?? 0}', 'Open discrepancies: ${data['discrepancies_open'] ?? 0}')),
                      Text(context.tr('申訴中評價：${data['reviews_disputed'] ?? 0}', 'Reviews under appeal: ${data['reviews_disputed'] ?? 0}')),
                      Text('${context.tr('結算單：', 'Settlements: ')}${byStatus.entries.map((e) => '${e.key} ${e.value}').join(' · ')}'),
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
            Padding(
              padding: const EdgeInsets.only(top: CgTheme.space24),
              child: Text(context.tr('目前角色沒有可用的管理功能。', 'No admin tools are available for this role.')),
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
          _error = '網絡不穩定，請重試';
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
                            const Text('網絡不穩定，請重試'),
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
          _error = '網絡不穩定，請重試';
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
        'rejected' => '退款未成功',
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
            if (o.voucherStatusText.isNotEmpty)
              _timelineRow('套餐券', o.voucherStatusText),
            if (o.latestRefundReason.isNotEmpty)
              _timelineRow('退款原因', o.latestRefundReason),
            _timelineRow('下單時間', _timeText(o.createdAt)),
            _timelineRow('支付時間', _timeText(o.paidAt)),
            if (o.refundStatus == 'refunded')
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(o.demoPayment
                    ? context.tr('訂單未扣款，沒有款項退回。', 'This order was not charged. No funds were returned.')
                    : context.tr('退款已發起，實際到賬時間視支付機構而定。', 'Refund initiated. Arrival time depends on the payment provider.'),
                    style: const TextStyle(color: CgColors.textSecondary)),
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
                            const Text('網絡不穩定，請重試'),
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
  const SettlementsScreen({super.key, required this.api, this.merchant = false});

  final ApiClient api;
  final bool merchant;

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
  int _page = 1;
  int _total = 0;
  bool _acting = false;
  bool _canWrite = false;
  String? _merchantError;
  int _request = 0, _detailRequest = 0;
  late int _merchantSession;

  @override
  void initState() {
    super.initState();
    _merchantSession = widget.api.sessionRevision;
    if (widget.merchant) widget.api.addListener(_onMerchantSession);
    _load();
  }

  @override
  void dispose() {
    if (widget.merchant) widget.api.removeListener(_onMerchantSession);
    _shopCtrl.dispose();
    _periodCtrl.dispose();

    super.dispose();
  }

  void _onMerchantSession() {
    if (_merchantSession == widget.api.sessionRevision) return;
    _merchantSession = widget.api.sessionRevision;
    _detailRequest++;
    _scaffoldKey.currentState?.closeEndDrawer();
    setState(() {
      _selected = null;
      _total = 0;
      _page = 1;
    });
    _load();
  }

  Future<void> _load() async {
    final request = ++_request;
    setState(() {
      _loading = true;
      if (widget.merchant) {
        _merchantError = null;
        _items = [];
      }
    });
    try {
      final data = widget.merchant
          ? await widget.api.get(
              '/merchant/settlements',
              query: {'page': '$_page'},
            )
          : await widget.api.settlements(
              page: _page,
              status: _statusFilter == 'all' ? null : _statusFilter,
            );
      if (!mounted || (widget.merchant && request != _request)) return;
      setState(() {
        _items = data['settlements'] as List<dynamic>;
        _canWrite = !widget.merchant && data['can_write'] == true;
        _total = data['total'] as int? ?? _items.length;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted || (widget.merchant && request != _request)) return;
      setState(() {
        _loading = false;
        if (widget.merchant) _merchantError = e.message;
      });
      if (!widget.merchant) showCgToast(context, e.message);
    } catch (_) {
      if (!widget.merchant) rethrow;
      if (!mounted || request != _request) return;
      setState(() {
        _loading = false;
        _merchantError = context.tr('未能讀取結算單。', 'Could not load statements.');
      });
    }
  }

  String _merchantStatus(Object? status) => switch (status) {
    'pending' => context.tr('待確認', 'Pending'),
    'approved' => context.tr('已確認', 'Approved'),
    'disputed' => context.tr('有爭議', 'Disputed'),
    'paid' => context.tr('已付款', 'Paid'),
    _ => context.tr('狀態待確認', 'Status unconfirmed'),
  };

  String _statementCopy(String chinese, String english) =>
      widget.merchant ? context.tr(chinese, english) : chinese;

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
    final session = _merchantSession;
    String cell(Object? field) {
      if (!widget.merchant) return '$field';
      final text = field?.toString() ?? '';
      final protected =
          field is String && RegExp(r'^[=+＝＋－@＠-]').hasMatch(text.trimLeft())
          ? "'$text"
          : text;
      return '"${protected.replaceAll('"', '""')}"';
    }

    final lines = [
      'id,shop_name,period,gross_amount,commission,net_amount,transferred_net,outstanding_net,status',
    ];
    for (final s in _items) {
      lines.add(
        [
          cell(s['id']),
          cell(s['shop_name']),
          cell(s['period']),
          cell(s['gross_amount']),
          cell(s['commission']),
          cell(s['net_amount']),
          cell(s['transferred_net']),
          cell(s['outstanding_net']),
          cell(s['status']),
        ].join(','),
      );
    }
    final csv = lines.join('\n');
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => ListenableBuilder(
        listenable: widget.api,
        builder: (context, _) {
          final current =
              !widget.merchant || session == widget.api.sessionRevision;
          return AlertDialog(
            title: Text(
              widget.merchant
                  ? context.tr('本頁結算單 CSV', 'Statements on this page')
                  : '本頁結算單 CSV（ ${_items.length} 條）',
            ),
            content: !current
                ? Text(context.tr('帳戶已切換。', 'The account changed.'))
                : SingleChildScrollView(
                    child: SelectableText(
                      csv.length > 2000 ? '${csv.substring(0, 2000)}…' : csv,
                    ),
                  ),
            actions: [
              if (current)
                TextButton(
                  onPressed: () {
                    if (widget.merchant &&
                        session != widget.api.sessionRevision) {
                      return;
                    }
                    Clipboard.setData(ClipboardData(text: csv));
                    Navigator.pop(dialogContext);
                    showCgToast(
                      this.context,
                      widget.merchant
                          ? context.tr('已複製 CSV', 'CSV copied')
                          : 'CSV 已複製，可粘貼保存',
                    );
                  },
                  child: Text(
                    widget.merchant ? context.tr('複製 CSV', 'Copy CSV') : '複製全部',
                  ),
                ),
              TextButton(
                onPressed: () => Navigator.pop(dialogContext),
                child: Text(widget.merchant ? context.tr('關閉', 'Close') : '關閉'),
              ),
            ],
          );
        },
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
            Text('${s['period']} · ${widget.merchant
                ? _merchantStatus(s['status'])
                : _settlementStatusText('${s['status']}')}'),
            TextButton(onPressed: () => _detail(s['id'] as int, open: false),
                child: Text(_statementCopy('更新結算明細', 'Refresh statement'))),
            const Divider(),
            _drawerRow(_statementCopy('核銷筆數', 'Redemptions'), '${s['redemption_count'] ?? 0}'),
            _drawerRow(_statementCopy('毛額', 'Gross amount'), _moneyText(s['gross_amount'])),
            _drawerRow(_statementCopy('佣金', 'Commission'), _moneyText(s['commission'])),
            _drawerRow(_statementCopy('商家應收', 'Merchant net'), _moneyText(s['net_amount'])),
            _drawerRow(_statementCopy('已付淨額', 'Paid net'), _moneyText(s['transferred_net'])),
            _drawerRow((s['outstanding_net'] as num) < 0
                  ? _statementCopy('待收回', 'Recovery due')
                  : _statementCopy('待付', 'Outstanding'), _moneyText((s['outstanding_net'] as num).abs())),
            if (_canWrite && s['status'] == 'paid' && (s['outstanding_net'] as num) != 0)
              OutlinedButton(onPressed: _acting ? null : () => _recordTransfer(s),
                  child: Text((s['outstanding_net'] as num) < 0 ? '登記已收回' : '登記已補付')),
            const Divider(),
            Text(_statementCopy('發票', 'Invoices'), style: const TextStyle(fontWeight: FontWeight.bold)),
            _drawerRow(_statementCopy('已開票', 'Issued'), _moneyText(s['invoice_issued_amount'])),
            _drawerRow(_statementCopy('已沖紅', 'Credited'), _moneyText(s['invoice_credited_amount'])),
            _drawerRow(_statementCopy('開票淨額', 'Net invoiced'), _moneyText(s['invoice_net_issued_amount'])),
            _drawerRow(_statementCopy('申請中', 'Reserved'), _moneyText(s['invoice_reserved_amount'])),
            _drawerRow(_statementCopy('可開票', 'Available to invoice'), _moneyText(s['invoice_available_amount'])),
            if ((s['invoice_excess_amount'] as num? ?? 0) > 0)
              _drawerRow(_statementCopy('待處理超額', 'Excess to review'), _moneyText(s['invoice_excess_amount'])),
            for (final inv in s['invoices'] as List? ?? []) ...[
              Text("發票 #${inv['id']} · ${inv['title']} · ${_moneyText(inv['amount'])} · ${switch (inv['status']) { 'issued' => '已開具', 'pending' => '待審核', 'approved' => '已批准', 'rejected' => '已拒絕', _ => inv['status'] }}"),
              if (inv['status'] == 'issued') ...[
                Text("已沖紅 ${_moneyText(inv['credited_amount'])} · 開票淨額 ${_moneyText(inv['net_issued_amount'])}"),
                for (final credit in inv['credits'] as List? ?? [])
                  Text("沖紅 ${_moneyText(credit['amount'])} · ${credit['reference']}"),
              ],
            ],
            if (widget.merchant && (s['invoice_available_amount'] as num? ?? 0) > 0)
              OutlinedButton(
                onPressed: _acting ? null : () => _requestInvoice(s),
                child: Text(_statementCopy('申請發票', 'Request invoice')),
              ),
            const Divider(),
            for (final line in s['items'] as List? ?? []) ...[
              Text("核銷 #${line['redemption_id']} · ${_moneyText(line['amount'])} · 佣金 ${_moneyText(line['commission'])}"),
              if (line['reversal'] != null)
                Text("沖回 #${line['redemption_id']} · ${_moneyText(line['reversal']['amount'])} · 佣金 ${_moneyText(line['reversal']['commission'])}"),
            ],
            for (final t in s['transfers'] as List? ?? [])
              Text("${t['kind'] == 'recovery' ? '收回' : t['kind'] == 'supplement' ? '補付' : '付款'} · ${_moneyText(t['amount'])} · ${t['reference']}"),
            const Divider(),
            if ((s['dispute_reason'] as String? ?? '').isNotEmpty)
              _drawerRow(_statementCopy('爭議原因', 'Dispute reason'), '${s['dispute_reason']}'),
            _drawerRow(_statementCopy('建立時間', 'Created'), _ledgerTime(s['created_at'] as String?)),
            _drawerRow(_statementCopy('最後決定', 'Last decision'), _ledgerTime(s['decided_at'] as String?)),
            _drawerRow(_statementCopy('付款時間', 'Payment date'), _ledgerTime(s['paid_at'] as String?)),
          ],
        ),
      ),
    );
  }

  Future<void> _requestInvoice(Map<String, dynamic> s) async {
    final session = _merchantSession;
    var title = '';
    var amount = '${s['invoice_available_amount']}';
    var taxId = '';
    final result = await showDialog<Map<String, dynamic>>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(_statementCopy('申請發票', 'Request invoice')),
        content: SingleChildScrollView(child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('可開票 ${_moneyText(s['invoice_available_amount'])}'),
            TextField(maxLength: 160, onChanged: (value) => title = value,
                decoration: InputDecoration(labelText: _statementCopy('發票抬頭', 'Invoice title'))),
            TextFormField(initialValue: amount, onChanged: (value) => amount = value,
                keyboardType: const TextInputType.numberWithOptions(decimal: true),
                decoration: InputDecoration(labelText: _statementCopy('申請金額', 'Amount'))),
            TextField(maxLength: 60, onChanged: (value) => taxId = value,
                decoration: InputDecoration(labelText: _statementCopy('稅號（選填）', 'Tax ID (optional)'))),
          ],
        )),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx), child: Text(_statementCopy('取消', 'Cancel'))),
          TextButton(onPressed: () => Navigator.pop(ctx, {
            'title': title.trim(), 'amount': double.tryParse(amount.trim()),
            'tax_id': taxId.trim(), 'shop_id': s['shop_id'], 'settlement_id': s['id'],
          }), child: Text(_statementCopy('提交申請', 'Submit request'))),
        ],
      ),
    );
    if (!mounted || result == null || session != widget.api.sessionRevision) return;
    final value = result['amount'] as double?;
    if ((result['title'] as String).isEmpty || value == null || value <= 0) {
      showCgToast(context, '請填寫發票抬頭和有效金額');
      return;
    }
    await _act(() => widget.api.createInvoice(result));
    if (mounted) await _detail(s['id'] as int, open: false);
  }

  String _ledgerTime(String? raw) {
    if (raw == null) return '—';
    final parsed = DateTime.tryParse(raw);
    if (parsed == null) return '—';
    // Older API responses omit the offset on their UTC timestamps.
    return formatDateTime(parsed.isUtc ? parsed : DateTime.parse('${raw}Z'));
  }

  String _balanceLabel(dynamic s) =>
      (s['outstanding_net'] as num) < 0 ? '待收回' : '待付';

  Future<void> _detail(int id, {bool open = true}) async {
    final request = ++_detailRequest;
    try {
      final data = await widget.api.get('/admin/settlements/$id');
      if (!mounted || (widget.merchant && request != _detailRequest)) return;
      setState(() => _selected = data['settlement'] as Map<String, dynamic>);
      if (open) _scaffoldKey.currentState?.openEndDrawer();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  Future<void> _recordTransfer(Map<String, dynamic> s, {bool firstPayment = false}) async {
    final amount = s['outstanding_net'] as num;
    var reference = '';
    final value = await showDialog<String>(context: context, builder: (ctx) => AlertDialog(
      title: Text(firstPayment ? (amount == 0 ? '結清零額結算單' : '登記已付款') : amount < 0 ? '登記已收回' : '登記已補付'),
      content: Column(mainAxisSize: MainAxisSize.min, children: [
        Text('實際金額 ${_moneyText(amount.abs())}'),
        if (amount != 0) TextField(maxLength: 120, onChanged: (v) => reference = v.trim(),
          decoration: const InputDecoration(labelText: '轉帳憑證編號')),
      ]),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
        TextButton(onPressed: () => Navigator.pop(ctx, amount == 0 ? 'settlement-${s['id']}' : reference), child: const Text('確認登記')),
      ]));
    if (!mounted || value == null) return;
    if (value.isEmpty) { showCgToast(context, '請填寫轉帳憑證編號'); return; }
    await _act(() => firstPayment ? widget.api.paySettlement(s['id'] as int, amount, value)
        : widget.api.post('/admin/settlements/${s['id']}/transfers',
        body: {'amount': amount, 'reference': value}));
    if (mounted && !firstPayment) await _detail(s['id'] as int, open: false);
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
    if (_acting) return;
    setState(() => _acting = true);
    try {
      await call();
      if (mounted) showCgToast(context, '已更新');
      if (mounted) await _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    } finally {
      if (mounted) setState(() => _acting = false);
    }
  }

  Widget _merchantStatements() {
    return Scaffold(
      key: _scaffoldKey,
      backgroundColor: const Color(0xFFFAF7F1),
      appBar: AppBar(
        title: Text(context.tr('結算單', 'Statements')),
        actions: [
          IconButton(
            onPressed: _loading ? null : _load,
            tooltip: context.tr('更新結算單', 'Refresh statements'),
            icon: const Icon(Icons.refresh),
          ),
          IconButton(
            onPressed: _items.isEmpty ? null : _exportCsv,
            tooltip: context.tr('匯出本頁 CSV', 'Export this page as CSV'),
            icon: const Icon(Icons.download_outlined),
          ),
        ],
      ),
      endDrawer: _detailDrawer(),
      body: RefreshIndicator(
        onRefresh: _load,
        child: SingleChildScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(16),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 1200),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  DecoratedBox(
                    decoration: BoxDecoration(
                      color: const Color(0xFF26221B),
                      borderRadius: BorderRadius.circular(16),
                    ),
                    child: Padding(
                      padding: const EdgeInsets.all(20),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Icon(
                            Icons.receipt_long_outlined,
                            color: CgColors.vendorGold,
                          ),
                          const SizedBox(height: 8),
                          Semantics(
                            header: true,
                            child: Text(
                              context.tr(
                                '月結與發票',
                                'Monthly statements and invoices',
                              ),
                              style: Theme.of(context).textTheme.headlineMedium
                                  ?.copyWith(
                                    color: Colors.white,
                                    fontWeight: FontWeight.w700,
                                  ),
                            ),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            context.tr(
                              '核對扣佣、已付款與待付金額 · 港幣 HKD',
                              'Review commission, paid amounts and outstanding balances · HKD',
                            ),
                            style: TextStyle(
                              color: Colors.white.withValues(alpha: .84),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  if (_loading)
                    LinearProgressIndicator(
                      semanticsLabel: context.tr('載入結算單', 'Loading statements'),
                    )
                  else if (_merchantError != null || _items.isEmpty)
                    Card(
                      margin: EdgeInsets.zero,
                      child: Padding(
                        padding: const EdgeInsets.all(20),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Semantics(
                              liveRegion: true,
                              child: Text(
                                _merchantError ??
                                    context.tr('暫時沒有結算單', 'No statements yet'),
                              ),
                            ),
                            TextButton(
                              onPressed: _load,
                              child: Text(context.tr('重新載入', 'Reload')),
                            ),
                          ],
                        ),
                      ),
                    )
                  else ...[
                    for (final item in _items)
                      if (item is Map && item['id'] is int)
                        _merchantStatement(item),
                    if (_total > 20)
                      Wrap(
                        alignment: WrapAlignment.end,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        spacing: 8,
                        children: [
                          TextButton(
                            onPressed: _page <= 1
                                ? null
                                : () {
                                    _page--;
                                    _load();
                                  },
                            child: Text(context.tr('上一頁', 'Previous')),
                          ),
                          Text(context.tr('第 $_page 頁', 'Page $_page')),
                          TextButton(
                            onPressed: _page * 20 >= _total
                                ? null
                                : () {
                                    _page++;
                                    _load();
                                  },
                            child: Text(context.tr('下一頁', 'Next')),
                          ),
                        ],
                      ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _merchantStatement(Map item) {
    final balance = item['outstanding_net'];
    String amount(Object? value) =>
        value is num ? formatHkd(value.toDouble()) : '—';
    final awaiting = balance is num && balance < 0
        ? context.tr('待收回', 'Recovery due')
        : context.tr('待付', 'Outstanding');
    final status = _merchantStatus(item['status']);
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      clipBehavior: Clip.antiAlias,
      color: Colors.white,
      shape: RoundedRectangleBorder(
        side: const BorderSide(color: Color(0xFFE5DED0)),
        borderRadius: BorderRadius.circular(14),
      ),
      child: InkWell(
        onTap: () => _detail(item['id'] as int),
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '${item['shop_name'] ?? context.tr('店舖', 'Shop')}',
                          style: Theme.of(context).textTheme.titleMedium
                              ?.copyWith(fontWeight: FontWeight.w700),
                        ),
                        Text('${item['period'] ?? '—'} · #${item['id']}'),
                      ],
                    ),
                  ),
                  const SizedBox(width: 8),
                  Flexible(
                    child: MerchantStatusPill(
                      label: status,
                      active: item['status'] == 'paid',
                      listingStatus: item['status'] == 'disputed'
                          ? 'rejected'
                          : null,
                    ),
                  ),
                  const Icon(Icons.chevron_right),
                ],
              ),
              const Divider(height: 24),
              MerchantAmountRow(
                label: context.tr('核銷毛額', 'Gross amount'),
                value: amount(item['gross_amount']),
              ),
              MerchantAmountRow(
                label: context.tr('佣金', 'Commission'),
                value: amount(item['commission']),
              ),
              MerchantAmountRow(
                label: context.tr('商戶淨額', 'Merchant net'),
                value: amount(item['net_amount']),
                emphasized: true,
              ),
              MerchantAmountRow(
                label: context.tr('已付淨額', 'Paid net'),
                value: amount(item['transferred_net']),
              ),
              MerchantAmountRow(
                label: awaiting,
                value: amount(balance is num ? balance.abs() : null),
              ),
            ],
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    if (widget.merchant) return _merchantStatements();
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
            if (_canWrite) ...[
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
                      setState(() { _statusFilter = f.$1; _page = 1; });
                      _load();
                    },
                  ),
              ],
            ),
            const SizedBox(height: 12),
            TextField(
              controller: _shopCtrl,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: '商戶 ID'),
            ),
            TextField(
              controller: _periodCtrl,
              decoration: const InputDecoration(labelText: '結算週期（YYYY-MM）'),
            ),
            const SizedBox(height: 8),
            ElevatedButton(
              onPressed: () async {
                final shopId = int.tryParse(_shopCtrl.text.trim());
                if (shopId == null) {
                  showCgToast(context, '請填寫正確的商戶 ID');
                  return;
                }
                await _act(() => widget.api.generateSettlement(
                    shopId, _periodCtrl.text.trim()));
              },
              child: const Text('生成結算單'),
            ),
            ],
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
                          '毛額 ${_moneyText(s['gross_amount'])} · 佣金 ${_moneyText(s['commission'])} · 淨額 ${_moneyText(s['net_amount'])} · ${_settlementStatusText('${s['status']}')}'
                          '\n${_balanceLabel(s)} ${_moneyText((s['outstanding_net'] as num).abs())}'),
                      onTap: () => _detail(s['id'] as int),
                      trailing: !_canWrite || s['status'] == 'paid' ? null : PopupMenuButton<String>(
                        onSelected: (v) {
                          final id = s['id'] as int;
                          switch (v) {
                            case 'approve':
                              _act(() => widget.api.approveSettlement(id));
                            case 'resolve':
                              _act(() => widget.api.resolveSettlement(id, 'reopen', '重新核對結算金額'));
                            case 'pay':
                              _recordTransfer(s as Map<String, dynamic>, firstPayment: true);
                            case 'dispute':
                              _act(() => widget.api.disputeSettlement(
                                  id, '金額有疑問，請核對'));
                          }
                        },
                        itemBuilder: (_) => [
                          if (s['status'] == 'disputed') const PopupMenuItem(value: 'resolve', child: Text('退回待確認')),
                          if (s['status'] == 'pending') const PopupMenuItem(value: 'approve', child: Text('確認')),
                          if (s['status'] == 'pending' || s['status'] == 'approved') const PopupMenuItem(value: 'dispute', child: Text('提出爭議')),
                          if (s['status'] == 'approved') const PopupMenuItem(value: 'pay', child: Text('登記付款')),
                        ],
                      ),
                    ),
                  )),
            if (_total > 20) Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
              TextButton(onPressed: _page <= 1 ? null : () { _page--; _load(); }, child: const Text('上一頁')),
              TextButton(onPressed: _page * 20 >= _total ? null : () { _page++; _load(); }, child: const Text('下一頁')),
            ]),
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

  Future<void> _showDifferences(int reportId) async {
    try {
      final data = await widget.api.get('/admin/reconciliation/reports/$reportId');
      if (!mounted) return;
      final rows = data['report']['discrepancies'] as List;
      const labels = {
        'missing_settlement_line': '缺少結算明細', 'missing_redemption': '缺少核銷記錄',
        'revoked_but_settled': '已撤銷核銷仍有應收', 'amount_mismatch': '核銷金額不符',
        'commission_mismatch': '佣金不符', 'incorrect_reversal': '沖回金額不符',
        'reversed_commission_mismatch': '沖回佣金不符', 'unexpected_reversal': '核銷仍有效但已沖回',
        'gross_total_mismatch': '毛額合計不符', 'commission_total_mismatch': '佣金合計不符',
        'net_total_mismatch': '商家應收合計不符', 'count_total_mismatch': '有效核銷筆數不符',
        'payment_entry_count': '付款流水筆數不符', 'invoice_over_settlement': '已開票金額超過商家應收',
      };
      await showDialog<void>(context: context, builder: (ctx) => AlertDialog(
        title: const Text('對賬差異'),
        content: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start, children: [
            for (final d in rows) Padding(padding: const EdgeInsets.only(bottom: 12), child: Text(
              "${labels[d['discrepancy_type']] ?? '結算差異'} · ${d['status'] == 'resolved' ? '已處理' : '待處理'}"
              "${d['redemption_id'] == null ? '' : ' · 核銷 #${d['redemption_id']}'}"
              "\n預期 ${d['expected_amount']} · 記錄 ${d['actual_amount']}"
              "${(d['resolution'] as String? ?? '').isEmpty ? '' : '\n${d['resolution']}'}")),
          ])),
        actions: [TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('關閉'))],
      ));
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
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
              decoration: const InputDecoration(labelText: '商戶 ID'),
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
                  showCgToast(context, '請填寫正確的商戶 ID');
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
                      onTap: (r['discrepancy_count'] as int) > 0 ? () => _showDifferences(r['id'] as int) : null,
                      trailing: (r['discrepancy_count'] as int) > 0 ? const Icon(Icons.chevron_right) : null,
                      title: Text(
                          '#${r['id']} · ${r['shop_name']} · ${r['period']}'),
                      subtitle: Text(
                          '有效核銷 ${r['redemption_count']} · 有效結算行 ${r['settlement_line_count']} · 差異 ${r['discrepancy_count']}'
                          '${r['financials'] == null || r['financials']['invoice_issued_amount'] == null ? '' : '\n已開票 ${formatHkd(r['financials']['invoice_issued_amount'])} · 已沖紅 ${formatHkd(r['financials']['invoice_credited_amount'])} · 開票淨額 ${formatHkd(r['financials']['invoice_net_issued_amount'])} · 可開票 ${formatHkd(r['financials']['invoice_available_amount'])} · 超額 ${formatHkd(r['financials']['invoice_excess_amount'])}'}'
                          '${r['financials'] == null ? '' : '\n平台佣金 ${formatHkd(r['financials']['commission'])} · 商家應收 ${formatHkd(r['financials']['net_amount'])} · 已付淨額 ${formatHkd(r['financials']['transferred_net'])} · ${(r['financials']['outstanding_net'] as num) < 0 ? '待收回' : '待付'} ${formatHkd((r['financials']['outstanding_net'] as num).abs().toDouble())}'}'),
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
  bool _acting = false;
  bool _canWrite = false;
  int _page = 1;
  int _totalPages = 1;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await widget.api.get('/admin/invoices', query: {'page': '$_page'});
      if (!mounted) return;
      setState(() {
        _items = data['invoices'] as List<dynamic>;
        _canWrite = data['can_write'] == true;
        _totalPages = data['total_pages'] as int? ?? 1;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  Future<void> _act(Future<Map<String, dynamic>> Function() call) async {
    if (_acting) return;
    setState(() => _acting = true);
    try {
      await call();
      if (!mounted) return;
      showCgToast(context, '已更新');
      await _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    } finally {
      if (mounted) setState(() => _acting = false);
    }
  }

  String _status(String status) => switch (status) {
        'pending' => '待審核',
        'approved' => '已批准',
        'issued' => '已開具',
        'rejected' => '已拒絕',
        _ => status,
      };

  String _money(dynamic value) => formatHkd((value as num? ?? 0).toDouble());

  Future<void> _showInvoice(Map<String, dynamic> inv) async {
    await showDialog<void>(context: context, builder: (ctx) => AlertDialog(
      title: Text('發票 #${inv['id']}'),
      content: SingleChildScrollView(child: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text('${inv['title']} · ${inv['shop_name']}'),
          Text('狀態 ${_status('${inv['status']}')}'),
          Text('申請金額 ${_money(inv['amount'])}'),
          if (inv['status'] == 'issued') ...[
            Text('已沖紅 ${_money(inv['credited_amount'])}'),
            Text('開票淨額 ${_money(inv['net_issued_amount'])}'),
            for (final credit in inv['credits'] as List? ?? [])
              Text('沖紅 ${_money(credit['amount'])} · ${credit['reference']}'),
          ],
        ],
      )),
      actions: [TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('關閉'))],
    ));
  }

  Future<void> _recordCredit(Map<String, dynamic> inv) async {
    var amount = '';
    var reference = '';
    final data = await showDialog<Map<String, String>>(context: context, builder: (ctx) => AlertDialog(
      title: const Text('登記已沖紅'),
      content: SingleChildScrollView(child: Column(mainAxisSize: MainAxisSize.min, children: [
        Text('開票淨額 ${_money(inv['net_issued_amount'])}'),
        TextField(onChanged: (value) => amount = value,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          decoration: const InputDecoration(labelText: '已沖紅金額')),
        TextField(maxLength: 120, onChanged: (value) => reference = value,
          decoration: const InputDecoration(labelText: '沖紅憑證編號')),
      ])),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('取消')),
        TextButton(onPressed: () => Navigator.pop(ctx, {'amount': amount.trim(), 'reference': reference.trim()}),
          child: const Text('確認登記')),
      ],
    ));
    if (!mounted || data == null) return;
    final value = double.tryParse(data['amount']!);
    if (value == null || value <= 0 || data['reference']!.isEmpty) {
      showCgToast(context, '請填寫有效金額和沖紅憑證編號');
      return;
    }
    await _act(() => widget.api.post('/admin/invoices/${inv['id']}/credits',
      body: {'amount': value, 'reference': data['reference']}));
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
                  subtitle: Text('${inv['shop_name']} · ${_money(inv['amount'])} · ${_status('${inv['status']}')}'
                    '${inv['status'] == 'issued' ? '\n已沖紅 ${_money(inv['credited_amount'])} · 開票淨額 ${_money(inv['net_issued_amount'])}' : ''}'),
                  onTap: () => _showInvoice(inv as Map<String, dynamic>),
                  trailing: !_canWrite || inv['status'] == 'rejected'
                    || (inv['status'] == 'issued' && (inv['net_issued_amount'] as num? ?? 0) <= 0)
                    ? null : PopupMenuButton<String>(
                      enabled: !_acting,
                      onSelected: (v) {
                        final id = inv['id'] as int;
                        switch (v) {
                          case 'approve': _act(() => widget.api.approveInvoice(id));
                          case 'reject': _act(() => widget.api.rejectInvoice(id, '資料不全，請補充'));
                          case 'issue': _act(() => widget.api.issueInvoice(id));
                          case 'credit': _recordCredit(inv as Map<String, dynamic>);
                        }
                      },
                      itemBuilder: (_) => [
                        if (inv['status'] == 'pending') const PopupMenuItem(value: 'approve', child: Text('批准')),
                        if (inv['status'] == 'pending' || inv['status'] == 'approved')
                          const PopupMenuItem(value: 'reject', child: Text('拒絕')),
                        if (inv['status'] == 'approved') const PopupMenuItem(value: 'issue', child: Text('開具')),
                        if (inv['status'] == 'issued') const PopupMenuItem(value: 'credit', child: Text('登記已沖紅')),
                      ],
                    ),
                ),
              )),
            if (!_loading && _items.isEmpty)
              const Padding(padding: EdgeInsets.only(top: 24), child: Text('暫時沒有發票申請')),
            if (_totalPages > 1) Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
              TextButton(onPressed: _page <= 1 ? null : () { _page--; _load(); }, child: const Text('上一頁')),
              TextButton(onPressed: _page >= _totalPages ? null : () { _page++; _load(); }, child: const Text('下一頁')),
            ]),
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
