import 'dart:async';

import 'package:flutter/material.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../api_client.dart';
import '../app_config.dart';
import '../feature_flags.dart';
import '../language.dart';
export 'qbot.dart' show QBotScreen;
export 'guest_auth.dart' show AuthScreen;
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import '../widgets/wallet_updates.dart';
import 'cashback.dart';
import 'reviews_admin.dart';
import 'merchant_staff.dart';

/// Q Wallet combines points, orders and the existing coupon actions.
class WalletScreen extends StatefulWidget {
  const WalletScreen({
    super.key,
    required this.api,
    this.active = true,
    this.guest = false,
    required this.onShowQr,
    this.onOpenFavorites,
    this.onInviteFriends,
    this.onRequireSignIn,
    this.showProfile = false,
    this.showPoints = true,
    this.initialStatus = 1,
    this.initialPendingReviews = false,
    this.onOpenCorporate,
    this.onOpenMyReviews,
    this.onOpenBrowsingHistory,
    this.onOpenSupport,
    this.onOpenRefunds,
  });

  final ApiClient api;
  final bool active;
  final bool guest;
  final Future<void> Function(WalletItem) onShowQr;
  final VoidCallback? onOpenFavorites, onInviteFriends, onRequireSignIn;
  final VoidCallback? onOpenCorporate,
      onOpenMyReviews,
      onOpenBrowsingHistory,
      onOpenSupport,
      onOpenRefunds;
  final bool showProfile, showPoints, initialPendingReviews;
  final int initialStatus;

  @override
  State<WalletScreen> createState() => _WalletScreenState();
}

class _WalletScreenState extends State<WalletScreen> with WalletUpdates<WalletScreen> {
  int? _balance, _expiring, _expiryDays, _orderCount, _refundCount, _availableCount, _usedCount;
  String? _name;
  List<Map<String, dynamic>>? _transactions;
  double? _cashbackWithdrawable, _cashbackPending;
  List<WalletItem> _items = [], _used = [];
  bool _loading = false, _moreBusy = false, _pendingReviews = false, _usedComplete = false;
  int _request = 0, _overviewRequest = 0, _page = 1, _pages = 1, _status = 1;
  bool _error = false;
  late int _session;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    _status = widget.initialStatus;
    _pendingReviews = widget.initialPendingReviews;
    widget.api.addListener(_sessionChanged);
    if (widget.active) _refresh();
  }

  void _sessionChanged() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    _reset();
    if (widget.active && !widget.guest) unawaited(_refresh());
  }

  void _reset() {
    _request++;
    _overviewRequest++;
    setState(() {
      _balance = _expiring =
          _expiryDays = _orderCount = _refundCount = _availableCount = _usedCount = null;
      _name = null;
      _transactions = null;
      _cashbackPending = _cashbackWithdrawable = null;
      _items = [];
      _used = [];
      _page = _pages = 1;
      _status = widget.initialStatus;
      _pendingReviews = widget.initialPendingReviews;
      _loading = _moreBusy = _usedComplete = _error = false;
    });
  }

  @override
  void didUpdateWidget(WalletScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    final changed = !identical(widget.api, oldWidget.api);
    if (changed) {
      oldWidget.api.removeListener(_sessionChanged);
      _session = widget.api.sessionRevision;
      widget.api.addListener(_sessionChanged);
    }
    if (changed || widget.guest != oldWidget.guest) _reset();
    if (widget.active && !widget.guest && (changed || !oldWidget.active || oldWidget.guest)) {
      unawaited(_refresh());
    }
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    super.dispose();
  }

  @override
  bool get walletPageActive => widget.active && !widget.guest;

  @override
  Future<void> syncWallet() async {
    if (_loading || _moreBusy) return;
    await refreshOnReceiptChange(widget.api, () async {
      await _refresh(quiet: true);
      return mounted && !_error;
    });
  }

  Future<void> _refresh({bool quiet = false}) async {
    if (widget.guest) return;
    await Future.wait([_load(quiet: quiet), _loadOverview()]);
  }

  Future<void> _loadOverview() async {
    final token = ++_overviewRequest, session = _session, api = widget.api;
    bool current() => _current(api, session) && token == _overviewRequest;
    Future<void> load<T>(
      Future<T> Function() get,
      void Function(T) apply,
    ) async {
      try {
        final value = await get();
        if (current()) setState(() => apply(value));
      } catch (_) {
        // A failed read must never turn a balance or count into zero.
      }
    }

    await Future.wait([
      load(api.points, (data) {
        _balance = data['balance'] as int?;
        _expiring = data['expiring_soon'] as int?;
        _expiryDays = data['expiring_soon_days'] as int?;
        final rows = data['transactions'];
        _transactions = rows is List ? rows.whereType<Map<String, dynamic>>().toList() : null;
      }),
      if (widget.showProfile)
        load(api.orders, (rows) {
          final orders = rows
              .whereType<Map<String, dynamic>>()
              .map(PackageOrder.fromJson)
              .where((e) => e.id > 0)
              .toList();
          _orderCount = orders.length;
          _refundCount = orders.where((order) => order.hasRefundActivity).length;
        }),
      if (widget.showProfile)
        load(() => api.get('/me'), (data) {
          if (data['consent_required'] == true) return;
          _name = (data['name'] as String? ?? data['registered_name'] as String?)?.trim();
        }),
      if (_status != 1)
        load(() => api.wallet(status: 1), (data) {
          _availableCount = data['total'] as int?;
        }),
      load(() => _loadUsed(api, current), (result) {
        if (result == null) return;
        _used = result.$1;
        _usedCount = result.$2;
        _usedComplete = true;
      }),
      if (cashbackEnabled)
        load(api.cashback, (data) {
          _cashbackPending = (data['pending_balance'] as num?)?.toDouble();
          _cashbackWithdrawable = (data['withdrawable_balance'] as num?)?.toDouble();
        }),
    ]);
  }

  Future<(List<WalletItem>, int)?> _loadUsed(
    ApiClient api,
    bool Function() current,
  ) async {
    final first = await api.wallet(status: 2);
    if (!current()) return null;
    final used = _walletRows(first, 2);
    final pages = first['total_pages'] as int? ?? 1;
    for (var page = 2; page <= pages; page++) {
      final data = await api.wallet(status: 2, page: page);
      if (!current()) return null;
      used.addAll(_walletRows(data, 2));
    }
    final seen = <int>{};
    final unique = used.where((item) => seen.add(item.id)).toList();
    // The reader cannot safely publish a partial pending-review count.
    final total = first['total'] as int? ?? unique.length;
    if (unique.length != total) return null;
    return (unique, total);
  }

  bool _current(ApiClient api, int session) =>
      mounted &&
      !widget.guest &&
      identical(widget.api, api) &&
      api.sessionRevision == session &&
      _session == session;

  List<WalletItem> _walletRows(Map<String, dynamic> data, int status) => (data['items'] as List)
      .whereType<Map<String, dynamic>>()
      .map(WalletItem.fromJson)
      .where((item) => item.id > 0 && item.status == status)
      .toList();

  Future<void> _load({bool quiet = false}) async {
    final request = ++_request, api = widget.api, session = _session, status = _status;
    if (widget.guest || status == 2) return;
    bool current() => _current(api, session) && request == _request && _status == status;
    setState(() {
      if (!quiet) _loading = true;
      _error = _moreBusy = false;
    });
    try {
      final wallet = await api.wallet(status: status);
      if (!current()) return;
      final pages = wallet['total_pages'] as int? ?? 1;
      final last = (quiet ? _page : 1).clamp(1, pages);
      final items = _walletRows(wallet, status);
      for (var page = 2; page <= last; page++) {
        final more = await api.wallet(status: status, page: page);
        if (!current()) return;
        items.addAll(_walletRows(more, status));
      }
      setState(() {
        final seen = <int>{};
        _items = items.where((item) => seen.add(item.id)).toList();
        _page = last;
        _pages = pages;
        _loading = false;
        if (status == 1) {
          _availableCount = wallet['total'] as int? ?? (pages == 1 ? _items.length : null);
        }
      });
    } catch (_) {
      if (current()) {
        setState(() {
          _error = true;
          _loading = false;
        });
      }
    }
  }

  Future<void> _more() async {
    if (_moreBusy) return;
    final request = _request, api = widget.api, session = _session, status = _status;
    bool current() => _current(api, session) && request == _request && _status == status;
    setState(() => _moreBusy = true);
    try {
      final data = await api.wallet(status: status, page: _page + 1);
      if (!current()) return;
      final known = _items.map((item) => item.id).toSet();
      setState(() {
        _items.addAll(
          _walletRows(data, status).where((item) => known.add(item.id)),
        );
        _page++;
        _pages = data['total_pages'] as int? ?? _pages;
      });
    } catch (_) {
      if (mounted && current()) {
        showCgToast(
          context,
          context.tr(
            '未能載入下一頁，請重試。',
            'Could not load the next page. Try again.',
          ),
        );
      }
    } finally {
      if (current()) setState(() => _moreBusy = false);
    }
  }

  void _select(int status, {bool pending = false}) {
    if (widget.guest) {
      widget.onRequireSignIn?.call();
      return;
    }
    if (widget.showProfile) {
      unawaited(
        _openWallet(status: status, pending: pending, showPoints: false),
      );
      return;
    }
    if (_status == status && _pendingReviews == pending) return;
    ++_request;
    setState(() {
      if (_status != status) _items = [];
      _status = status;
      _pendingReviews = pending;
      _page = _pages = 1;
      _error = _loading = _moreBusy = false;
    });
    if (status != 2) {
      unawaited(_load());
    } else if (!_usedComplete) {
      unawaited(_loadOverview());
    }
  }

  Future<void> _open(String route) async {
    if (widget.guest && (route == '/orders' || route == '/notifications')) {
      widget.onRequireSignIn?.call();
      return;
    }
    await Navigator.of(context).pushNamed(route);
    if (mounted && !widget.guest) unawaited(_refresh());
  }

  Future<void> _pointsHistory() async {
    if (widget.guest) {
      widget.onRequireSignIn?.call();
      return;
    }
    final api = widget.api, session = _session, transactions = _transactions;
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => _WalletHistory(
          api: api,
          session: session,
          initialPoints: transactions,
          points: true,
        ),
      ),
    );
  }

  Future<void> _exchangePoints() async {
    final api = widget.api, session = _session, balance = _balance;
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => _PointsExchange(
          api: api,
          session: session,
          initialBalance: balance,
          onExchanged: (balance) {
            if (!_current(api, session)) return;
            ++_overviewRequest;
            setState(() => _balance = balance);
            _select(1);
            unawaited(_refresh());
          },
        ),
      ),
    );
  }

  Future<void> _records() async {
    if (widget.guest) {
      widget.onRequireSignIn?.call();
      return;
    }
    final api = widget.api, session = _session;
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => _WalletHistory(api: api, session: session),
      ),
    );
  }

  String _couponStatus(BuildContext context, WalletItem item) {
    if (item.refundStatus == 'refunded') return context.tr('已退款', 'Refunded');
    if (item.refundStatus == 'requested') {
      return context.tr('退款處理中', 'Refund in progress');
    }
    if (item.status == 2) return context.tr('已使用', 'Used');
    final base = item.status == 1 ? context.tr('未使用', 'Unused') : context.tr('已失效', 'Expired');
    return item.refundStatus == 'rejected'
        ? '$base · ${context.tr('退款未成功', 'Refund unsuccessful')}'
        : base;
  }

  Future<void> _refund(WalletItem item) async {
    final api = widget.api, session = _session;
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(ctx.tr('申請退款', 'Request a refund')),
        content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(ctx.tr(
                '${item.couponName} · 訂單共 ${item.orderQuantity} 份。只有整筆訂單及其所有券都符合退款條件才可申請。',
                '${item.couponName} · ${item.orderQuantity} in this order. The entire order and all its vouchers must be eligible.',
              )),
              if (publicTrial)
                Text(ctx.tr('訂單未扣款。', 'This order was not charged.'))
              else if (item.orderPaidAmount case final amount?)
                Text(ctx.tr('整單實付 ${formatHkd(amount)}',
                    'Paid for the entire order: ${formatHkd(amount)}')),
            ]),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: Text(ctx.tr('取消', 'Cancel')),
          ),
          ElevatedButton(
            style: ElevatedButton.styleFrom(minimumSize: const Size(80, 44)),
            onPressed: () => Navigator.pop(ctx, true),
            child: Text(ctx.tr('確認退款', 'Confirm refund')),
          ),
        ],
      ),
    );
    if (confirmed != true || !_current(api, session)) return;
    try {
      await api.refundOrder(item.orderId!);
      if (!mounted || !_current(api, session)) return;
      showCgToast(context, context.tr('退款申請已提交', 'Refund request sent'));
      await _refresh();
    } on ApiException catch (error) {
      if (mounted && _current(api, session)) {
        final detail = error.message == '訂單已有券使用，無法退款'
            ? context.tr('訂單已有券使用，無法退款',
                'A voucher from this order has already been used. Refund unavailable.')
            : context.tr('未能提交退款，請查看訂單狀態。',
                'Could not request a refund. Check your order status.');
        showCgToast(context, detail);
      }
    }
  }

  Widget _couponTile(BuildContext context, WalletItem item) {
    return Card(
      child: ListTile(
        leading: const Icon(Icons.local_offer, color: CgColors.primary),
        title: Text(item.couponName),
        subtitle: Text(
          '${item.shopName} · ${_couponStatus(context, item)}'
          '${item.orderNeedsReview ? ' · ${context.tr('券資料待核對', 'Voucher details need review')}' : item.isPackage ? ' · ${context.tr('套餐券', 'Package vouchers')} ×${item.quantity}' : ''}',
        ),
        trailing: item.status == 1
            ? Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (item.canRefund)
                    IconButton(
                      tooltip: context.tr('申請退款', 'Request a refund'),
                      onPressed: () => _refund(item),
                      icon: const Icon(Icons.assignment_return_outlined),
                    ),
                  ElevatedButton(
                    style: ElevatedButton.styleFrom(minimumSize: const Size(64, 44), maximumSize: const Size(96, 56)),
                    onPressed: item.orderNeedsReview
                        ? null
                        : () async {
                            final api = widget.api, session = _session;
                            await widget.onShowQr(item);
                            if (_current(api, session)) await _refresh();
                          },
                    child: Text(context.tr('出示', 'Show')),
                  ),
                ],
              )
            : item.status == 2 && !item.reviewed
                ? TextButton(
                    onPressed: () async {
                      final api = widget.api, session = _session;
                      final ok = await Navigator.of(context).push<bool>(
                        MaterialPageRoute(
                          builder: (_) => ReviewFormScreen(api: api, item: item),
                        ),
                      );
                      if (ok == true && _current(api, session)) await _refresh();
                    },
                    child: Text(context.tr('去評價', 'Review')),
                  )
                : null,
      ),
    );
  }

  Widget _countEntry(
    String id,
    IconData icon,
    String label,
    int? count,
    VoidCallback tap, {
    bool selected = false,
  }) {
    return Expanded(
      child: Semantics(
        selected: selected,
        button: true,
        child: InkWell(
          key: ValueKey('wallet-order-$id'),
          onTap: tap,
          borderRadius: BorderRadius.circular(12),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 10, horizontal: 2),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                Icon(icon, color: selected ? CgColors.primary : null),
                const SizedBox(height: 4),
                Text(
                  label,
                  textAlign: TextAlign.center,
                  style: Theme.of(context).textTheme.labelMedium,
                ),
                if (count != null)
                  Text(
                    '$count',
                    key: ValueKey('wallet-order-count-$id'),
                    style: const TextStyle(
                      color: CgColors.primary,
                      fontWeight: FontWeight.w700,
                    ),
                  ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _section(String title) => Padding(
        padding: const EdgeInsets.fromLTRB(14, 12, 14, 4),
        child: Text(title, style: Theme.of(context).textTheme.titleMedium),
      );

  Widget _tile(
    BuildContext context,
    IconData icon,
    String label,
    VoidCallback? onTap,
  ) =>
      ListTile(leading: Icon(icon), title: Text(label), onTap: onTap);

  Widget _pointsCard(BuildContext context) {
    return Container(
      width: double.infinity,
      color: CgColors.primary,
      padding: EdgeInsets.fromLTRB(
        16,
        12 + MediaQuery.paddingOf(context).top,
        16,
        14,
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          InkWell(
            key: const ValueKey('wallet-points-history'),
            onTap: _pointsHistory,
            child: Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        context.tr('Q Wallet 積分', 'Q Wallet points'),
                        style: const TextStyle(color: Colors.white),
                      ),
                      Text(
                        context.tr(
                          "${_balance ?? '—'} 分",
                          "${_balance ?? '—'} points",
                        ),
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 32,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      if (_expiring != null && _expiryDays != null)
                        Text(
                          context.tr(
                            '$_expiryDays 日內到期：$_expiring 分',
                            '$_expiring points expire within $_expiryDays days',
                          ),
                          style: const TextStyle(color: Colors.white),
                        ),
                    ],
                  ),
                ),
                Text(
                  context.tr('積分流水', 'Activity'),
                  style: const TextStyle(color: Colors.white),
                ),
              ],
            ),
          ),
          if (cashbackEnabled) ...[
            const SizedBox(height: 10),
            InkWell(
              onTap: widget.guest
                  ? null
                  : () async {
                      await Navigator.of(context).push(
                        MaterialPageRoute(
                          builder: (_) => CashbackScreen(api: widget.api),
                        ),
                      );
                      if (mounted) await _refresh();
                    },
              child: Row(
                children: [
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          context.tr(
                            '返現（可提現）',
                            'Cashback available to withdraw',
                          ),
                          style: const TextStyle(color: Colors.white),
                        ),
                        Text(
                          _cashbackWithdrawable == null ? '—' : formatHkd(_cashbackWithdrawable!),
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 22,
                            fontWeight: FontWeight.bold,
                          ),
                        ),
                        Text(
                          context.tr(
                            '待確認 ${_cashbackPending == null ? '—' : formatHkd(_cashbackPending!)}',
                            'Pending ${_cashbackPending == null ? '—' : formatHkd(_cashbackPending!)}',
                          ),
                          style: const TextStyle(color: Colors.white),
                        ),
                      ],
                    ),
                  ),
                  if (!widget.guest) const Icon(Icons.chevron_right, color: Colors.white),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  Future<void> _openWallet({
    int status = 1,
    bool pending = false,
    bool showPoints = true,
  }) async {
    if (widget.guest) {
      widget.onRequireSignIn?.call();
      return;
    }
    final api = widget.api, session = _session;
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (context) => _ProfileWalletRoute(
          api: api,
          session: session,
          title: showPoints ? 'Q Wallet' : context.tr('我的優惠券', 'My coupons'),
          child: WalletScreen(
            api: api,
            onShowQr: widget.onShowQr,
            initialStatus: status,
            initialPendingReviews: pending,
            showPoints: showPoints,
          ),
        ),
      ),
    );
    if (_current(api, session)) await _refresh();
  }

  Widget _pointsRules() => ValueListenableBuilder<AppConfig>(
        valueListenable: widget.api.appConfig,
        builder: (context, config, _) {
          final rules = [
            if (config.pointsClaim case final points?) context.tr('領券 +$points', 'Claim +$points'),
            if (config.pointsRedeem case final points?)
              context.tr('核銷免費券 +$points', 'Use a free coupon +$points'),
            if (config.pointsReview case final points?)
              context.tr('評價 +$points', 'Review +$points'),
            if (config.pointsExpireMonths case final months?)
              context.tr('$months 個月到期', 'Expires after $months months'),
          ];
          return rules.isEmpty
              ? const SizedBox.shrink()
              : Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16),
                  child: Text(
                    rules.join(' · '),
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                );
        },
      );

  Widget _myPage(BuildContext context) {
    final name = _name != null && _name!.isNotEmpty ? _name : null;
    final label = name ??
        (widget.guest ? context.tr('未登入', 'Not signed in') : context.tr('我的帳戶', 'My account'));
    return ListView(
      key: const ValueKey('my-page'),
      physics: const AlwaysScrollableScrollPhysics(),
      padding: EdgeInsets.fromLTRB(
        12,
        12 + MediaQuery.paddingOf(context).top,
        12,
        18,
      ),
      children: [
        ListTile(
          contentPadding: const EdgeInsets.symmetric(horizontal: 8),
          leading: CircleAvatar(
            backgroundColor: Theme.of(context).colorScheme.primaryContainer,
            child: name == null
                ? const Icon(Icons.person_outline)
                : Text(name.characters.first.toUpperCase()),
          ),
          title: Text(label, style: Theme.of(context).textTheme.titleLarge),
          trailing: widget.guest && widget.onRequireSignIn != null
              ? TextButton(
                  onPressed: widget.onRequireSignIn,
                  child: Text(context.tr('登入', 'Sign in')),
                )
              : null,
        ),
        Card(
          child: Column(
            children: [
              _section(context.tr('我的訂單', 'My orders')),
              Row(
                children: [
                  _countEntry(
                    'all',
                    Icons.receipt_long,
                    context.tr('全部', 'All'),
                    _orderCount,
                    () => _open('/orders'),
                  ),
                  _countEntry(
                    'unused',
                    Icons.confirmation_number_outlined,
                    context.tr('待使用', 'To use'),
                    _availableCount,
                    () => _select(1),
                  ),
                  _countEntry(
                    'used',
                    Icons.check_circle_outline,
                    context.tr('已使用', 'Used'),
                    _usedCount,
                    () => _select(2),
                  ),
                  _countEntry(
                    'review',
                    Icons.rate_review_outlined,
                    context.tr('待評價', 'To review'),
                    _usedComplete ? _used.where((item) => !item.reviewed).length : null,
                    () => _select(2, pending: true),
                  ),
                  if (widget.onOpenRefunds != null)
                    _countEntry(
                      'refund',
                      Icons.assignment_return_outlined,
                      context.tr('退款', 'Refunds'),
                      _refundCount,
                      () {
                        if (widget.guest) {
                          widget.onRequireSignIn?.call();
                          return;
                        }
                        widget.onOpenRefunds!();
                      },
                    ),
                ],
              ),
              Row(
                children: [
                  Expanded(
                    child: TextButton(
                      onPressed: () => _open('/orders'),
                      child: Text(context.tr('購買記錄', 'Purchases')),
                    ),
                  ),
                  Expanded(
                    child: TextButton(
                      onPressed: _records,
                      child: Text(context.tr('使用記錄', 'Usage history')),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
        Card(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              _section(context.tr('我的資產', 'My assets')),
              Row(
                children: [
                  Expanded(
                    child: _assetTile(
                      context,
                      key: const ValueKey('my-q-wallet'),
                      icon: Icons.account_balance_wallet_outlined,
                      label: 'Q Wallet',
                      value:
                          _balance != null ? context.tr('$_balance 分', '$_balance points') : null,
                      onTap: () => _openWallet(),
                    ),
                  ),
                  Expanded(
                    child: _assetTile(
                      context,
                      key: const ValueKey('my-coupons'),
                      icon: Icons.confirmation_number_outlined,
                      label: context.tr('我的優惠券', 'My coupons'),
                      value: _availableCount != null
                          ? context.tr(
                              '$_availableCount 張待使用',
                              '$_availableCount to use',
                            )
                          : null,
                      onTap: () => _openWallet(showPoints: false),
                    ),
                  ),
                ],
              ),
            ],
          ),
        ),
        Card(
          child: Column(
            children: [
              if (widget.onOpenFavorites != null)
                _tile(
                  context,
                  Icons.favorite_border,
                  context.tr('收藏', 'Favorites'),
                  widget.onOpenFavorites,
                ),
              if (widget.onOpenMyReviews != null)
                _tile(
                  context,
                  Icons.rate_review_outlined,
                  context.tr('我的評價', 'My reviews'),
                  widget.onOpenMyReviews,
                ),
              if (widget.onOpenBrowsingHistory != null)
                _tile(
                  context,
                  Icons.history,
                  context.tr('瀏覽記錄', 'Browsing history'),
                  widget.onOpenBrowsingHistory,
                ),
            ],
          ),
        ),
        Card(
          child: Column(
            children: [
              _section(context.tr('常用服務', 'Quick links')),
              if (widget.onInviteFriends != null)
                _tile(
                  context,
                  Icons.person_add_outlined,
                  context.tr('邀請好友', 'Invite friends'),
                  widget.onInviteFriends,
                ),
              if (widget.onOpenSupport != null)
                _tile(
                  context,
                  Icons.support_agent,
                  context.tr('聯絡支援', 'Contact support'),
                  widget.onOpenSupport,
                ),
              _tile(
                context,
                Icons.help_outline,
                context.tr('新手指南與常見問題', 'Guide and FAQs'),
                () => _open('/guide'),
              ),
              _tile(
                context,
                Icons.notifications_none,
                context.tr('通知', 'Notifications'),
                () => _open('/notifications'),
              ),
              _tile(
                context,
                Icons.settings_outlined,
                context.tr('設定', 'Settings'),
                () => _open('/settings'),
              ),
            ],
          ),
        ),
        if (widget.onOpenCorporate != null)
          Card(
            child: _tile(
              context,
              Icons.storefront_outlined,
              context.tr('企業服務', 'Business services'),
              widget.onOpenCorporate,
            ),
          ),
      ],
    );
  }

  Widget _assetTile(
    BuildContext context, {
    required Key key,
    required IconData icon,
    required String label,
    String? value,
    required VoidCallback onTap,
  }) =>
      InkWell(
        key: key,
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(icon, color: CgColors.primary),
              const SizedBox(height: 7),
              Text(label, style: Theme.of(context).textTheme.titleSmall),
              if (value != null) Text(value, style: Theme.of(context).textTheme.bodySmall),
            ],
          ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    if (widget.showProfile) {
      return Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 760),
          child: RefreshIndicator(onRefresh: _refresh, child: _myPage(context)),
        ),
      );
    }
    final used = _pendingReviews ? _used.where((item) => !item.reviewed).toList() : _used;
    final items = _status == 2 ? used : _items;
    return Center(
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 760),
        child: RefreshIndicator(
          onRefresh: _refresh,
          child: ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: EdgeInsets.zero,
            children: [
              if (widget.showPoints) _pointsCard(context),
              _section(context.tr('我的優惠券', 'My coupons')),
              Row(
                children: [
                  _countEntry(
                    'unused',
                    Icons.confirmation_number_outlined,
                    context.tr('待使用', 'To use'),
                    _availableCount,
                    () => _select(1),
                    selected: _status == 1,
                  ),
                  _countEntry(
                    'used',
                    Icons.check_circle_outline,
                    context.tr('已使用', 'Used'),
                    _usedCount,
                    () => _select(2),
                    selected: _status == 2 && !_pendingReviews,
                  ),
                  _countEntry(
                    'review',
                    Icons.rate_review_outlined,
                    context.tr('待評價', 'To review'),
                    _usedComplete ? _used.where((item) => !item.reviewed).length : null,
                    () => _select(2, pending: true),
                    selected: _status == 2 && _pendingReviews,
                  ),
                ],
              ),
              const Divider(height: 8),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 14),
                child: Row(
                  children: [
                    Expanded(
                      child: Text(
                        _pendingReviews
                            ? context.tr('待評價優惠券', 'Coupons to review')
                            : _status == 2
                                ? context.tr('已使用優惠券', 'Used coupons')
                                : _status == 3
                                    ? context.tr('已失效優惠券', 'Expired coupons')
                                    : context.tr('待使用優惠券', 'Coupons to use'),
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                    ),
                    TextButton(
                      onPressed: () => _select(_status == 3 ? 1 : 3),
                      child: Text(
                        _status == 3 ? context.tr('待使用', 'To use') : context.tr('已失效', 'Expired'),
                      ),
                    ),
                  ],
                ),
              ),
              if (widget.guest)
                Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    children: [
                      Text(
                        context.tr(
                          '登入後可查看你的訂單及優惠券。',
                          'Sign in to view your orders and coupons.',
                        ),
                      ),
                      if (widget.onRequireSignIn != null)
                        TextButton(
                          onPressed: widget.onRequireSignIn,
                          child: Text(context.tr('登入', 'Sign in')),
                        ),
                    ],
                  ),
                )
              else if (_loading)
                const Padding(
                  padding: EdgeInsets.all(12),
                  child: SkeletonBox(height: 76),
                )
              else if (_error || (_status == 2 && !_usedComplete))
                Padding(
                  padding: const EdgeInsets.all(12),
                  child: Column(
                    children: [
                      Text(context.tr('未能載入優惠券。', 'Could not load coupons.')),
                      TextButton(
                        onPressed: _refresh,
                        child: Text(context.tr('重試', 'Retry')),
                      ),
                    ],
                  ),
                )
              else if (items.isEmpty)
                Padding(
                  padding: const EdgeInsets.all(14),
                  child: Text(
                    context.tr('沒有符合條件的優惠券。', 'No coupons in this category.'),
                  ),
                )
              else
                for (final item in items) _couponTile(context, item),
              if (!widget.guest && !_loading && !_error && _status != 2 && _page < _pages)
                TextButton(
                  onPressed: _moreBusy ? null : _more,
                  child: Text(context.tr('載入更多', 'Load more')),
                ),
              const Divider(),
              if (widget.showPoints && publicTrial && !widget.guest)
                _tile(
                  context,
                  Icons.redeem_outlined,
                  context.tr('積分兌換', 'Redeem points'),
                  _exchangePoints,
                ),
              if (widget.showPoints) ...[
                _tile(
                  context,
                  Icons.help_outline,
                  context.tr('積分規則與常見問題', 'Points rules and FAQs'),
                  () => _open('/guide'),
                ),
                _pointsRules(),
              ],
              const SizedBox(height: 14),
            ],
          ),
        ),
      ),
    );
  }
}

class _ProfileWalletRoute extends StatelessWidget {
  const _ProfileWalletRoute({
    required this.api,
    required this.session,
    required this.title,
    required this.child,
  });
  final ApiClient api;
  final int session;
  final String title;
  final Widget child;

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: Text(title)),
        body: ListenableBuilder(
          listenable: api,
          builder: (context, _) => api.sessionRevision == session
              ? child
              : Center(
                  child: Text(
                    context.tr('帳戶已切換，請返回。', 'The account changed. Go back.'),
                  ),
                ),
        ),
      );
}

String _walletTime(dynamic raw) {
  final parsed = raw is String ? DateTime.tryParse(raw) : null;
  if (parsed == null) return '';
  // API timestamps without a suffix are UTC. Display Hong Kong civil time.
  final utc = parsed.isUtc
      ? parsed
      : DateTime.utc(
          parsed.year,
          parsed.month,
          parsed.day,
          parsed.hour,
          parsed.minute,
        );
  final value = utc.add(const Duration(hours: 8));
  String two(int number) => number.toString().padLeft(2, '0');
  return '${value.year}-${two(value.month)}-${two(value.day)} ${two(value.hour)}:${two(value.minute)}';
}

class _WalletHistory extends StatefulWidget {
  const _WalletHistory({
    required this.api,
    required this.session,
    this.points = false,
    this.initialPoints,
  });
  final ApiClient api;
  final int session;
  final bool points;
  final List<Map<String, dynamic>>? initialPoints;

  @override
  State<_WalletHistory> createState() => _WalletHistoryState();
}

class _WalletHistoryState extends State<_WalletHistory> {
  int get _session => widget.session;
  late Future<List<Map<String, dynamic>>> _history;

  @override
  void initState() {
    super.initState();
    _history = _fetch();
  }

  Future<List<Map<String, dynamic>>> _fetch() async {
    if (_session != widget.api.sessionRevision) return [];
    final rows = widget.points
        ? widget.initialPoints ?? (await widget.api.points())['transactions'] as List
        : await widget.api.records();
    final key = widget.points ? 'created_at' : 'scanned_at';
    return rows.whereType<Map<String, dynamic>>().toList()
      ..sort((a, b) => _walletTime(b[key]).compareTo(_walletTime(a[key])));
  }

  String _reason(BuildContext context, dynamic value) => switch (value) {
        'claim' => context.tr('領券', 'Coupon claimed'),
        'redeem' => context.tr('核銷', 'Coupon used'),
        'review' => context.tr('評價', 'Review'),
        'exchange' => context.tr('積分兌換', 'Points redeemed'),
        'expire' => context.tr('積分到期', 'Points expired'),
        'refund_clawback' => context.tr('退款調整', 'Refund adjustment'),
        'redeem_revoke' || 'revoke' => context.tr('撤銷核銷', 'Redemption reversed'),
        _ => context.tr('積分調整', 'Points adjustment'),
      };

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          title: Text(
            widget.points
                ? context.tr('積分流水', 'Points activity')
                : context.tr('使用記錄', 'Usage history'),
          ),
        ),
        body: ListenableBuilder(
          listenable: widget.api,
          builder: (context, _) {
            if (_session != widget.api.sessionRevision) {
              return Center(
                child: Text(
                  context.tr('帳戶已切換，請返回。', 'The account changed. Go back.'),
                ),
              );
            }
            return FutureBuilder<List<Map<String, dynamic>>>(
              future: _history,
              builder: (context, snapshot) {
                if (snapshot.connectionState != ConnectionState.done) {
                  return const Center(child: CircularProgressIndicator());
                }
                if (snapshot.hasError) {
                  return Center(
                    child: TextButton(
                      onPressed: () => setState(() => _history = _fetch()),
                      child: Text(context.tr('未能載入，重試', 'Could not load. Retry')),
                    ),
                  );
                }
                final rows = snapshot.data ?? [];
                if (rows.isEmpty) {
                  return Center(
                    child: Text(context.tr('暫時沒有記錄。', 'No records yet.')),
                  );
                }
                return ListView(
                  children: [
                    for (final row in rows)
                      if (widget.points && row['delta'] is int)
                        ListTile(
                          title: Text(_reason(context, row['reason'])),
                          subtitle: Text(_walletTime(row['created_at'])),
                          trailing: Text(
                            '${(row['delta'] as int) > 0 ? '+' : ''}${row['delta']}',
                          ),
                        )
                      else if (!widget.points)
                        ListTile(
                          title: Text(row['coupon_name'] as String? ?? ''),
                          subtitle: Text(
                            '${row['shop_name'] ?? ''} · ${_walletTime(row['scanned_at'])}',
                          ),
                          trailing:
                              row['revoked'] == true ? Text(context.tr('已撤銷', 'Reversed')) : null,
                        ),
                  ],
                );
              },
            );
          },
        ),
      );
}

class _PointsExchange extends StatefulWidget {
  const _PointsExchange({
    required this.api,
    required this.session,
    required this.initialBalance,
    required this.onExchanged,
  });
  final ApiClient api;
  final int session;
  final int? initialBalance;
  final ValueChanged<int?> onExchanged;

  @override
  State<_PointsExchange> createState() => _PointsExchangeState();
}

class _PointsExchangeState extends State<_PointsExchange> {
  int get _session => widget.session;
  late int? _balance = widget.initialBalance;
  late Future<Map<String, dynamic>> _options;
  final Set<int> _exchanged = {};
  bool _busy = false;

  bool get _current => mounted && _session == widget.api.sessionRevision;

  @override
  void initState() {
    super.initState();
    _options = _current ? widget.api.get('/points/exchange-options') : Future.value({});
  }

  String? _exchangeState;
  bool _uncertain = false;

  Future<void> _submit(int id, int cost) async {
    if (!_current || _busy || _uncertain || _exchanged.contains(id)) return;
    if (_balance != null && _balance! < cost) {
      setState(() => _exchangeState = 'insufficient');
      return;
    }
    setState(() {
      _busy = true;
      _exchangeState = null;
    });
    try {
      final result = await widget.api.exchangePoints(id);
      if (!_current) return;
      setState(() {
        _balance = result['balance'] as int?;
        _exchanged.add(id);
        _exchangeState = 'redeemed';
      });
      widget.onExchanged(_balance);
    } on ApiException catch (error) {
      if (!_current) return;
      setState(() {
        _uncertain = error.status <= 0 || error.status >= 500;
        _exchangeState = error.code == 'points_insufficient'
            ? 'insufficient'
            : _uncertain
                ? 'uncertain'
                : 'failed';
      });
    } catch (_) {
      if (_current) {
        setState(() {
          _exchangeState = 'uncertain';
          _uncertain = true;
        });
      }
    } finally {
      if (_current) setState(() => _busy = false);
    }
  }

  String? _notice(BuildContext context) => switch (_exchangeState) {
        'insufficient' => context.tr('積分不足。', 'Not enough points.'),
        'redeemed' => context.tr('已兌換，優惠券已存入 Q Wallet。', 'Redeemed. Your coupon is in Q Wallet.'),
        'uncertain' =>
          context.tr('未能確認兌換結果，請返回錢包查看。', 'Could not confirm redemption. Check your wallet.'),
        'failed' =>
          context.tr('未能兌換，請重新查看可兌換優惠。', 'Could not redeem. Check the available rewards again.'),
        _ => null,
      };

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: Text(context.tr('積分兌換', 'Redeem points'))),
        body: ListenableBuilder(
          listenable: widget.api,
          builder: (context, _) {
            if (!_current) {
              return Center(
                child: Text(
                  context.tr('帳戶已切換，請返回。', 'The account changed. Go back.'),
                ),
              );
            }
            return FutureBuilder<Map<String, dynamic>>(
              future: _options,
              builder: (context, snapshot) {
                if (snapshot.connectionState != ConnectionState.done) {
                  return const Center(child: CircularProgressIndicator());
                }
                final cost = snapshot.data?['cost'];
                final coupons = snapshot.data?['coupons'];
                if (cost is! int || cost < 1 || coupons is! List) {
                  return Center(
                    child: TextButton(
                      onPressed: () => setState(
                        () => _options = widget.api.get('/points/exchange-options'),
                      ),
                      child: Text(
                        context.tr('未能載入可兌換優惠，重試', 'Could not load rewards. Retry'),
                      ),
                    ),
                  );
                }
                final available = coupons
                    .whereType<Map<String, dynamic>>()
                    .where(
                      (row) =>
                          row['id'] is int &&
                          (row['id'] as int) > 0 &&
                          row['is_paid'] != true &&
                          !((row['sale_price'] as num? ?? 0) > 0) &&
                          !_exchanged.contains(row['id']),
                    )
                    .toList();
                return ListView(
                  children: [
                    ListTile(
                      title: Text(
                        context.tr(
                          "積分餘額：${_balance ?? '—'}",
                          "Points balance: ${_balance ?? '—'}",
                        ),
                      ),
                      subtitle: Text(
                        context.tr('每張優惠券 $cost 分', '$cost points per coupon'),
                      ),
                    ),
                    if (_notice(context) case final notice?)
                      ListTile(title: Semantics(liveRegion: true, child: Text(notice))),
                    if (available.isEmpty)
                      ListTile(
                        title: Text(
                          context.tr('沒有可兌換的優惠。', 'No rewards available.'),
                        ),
                      ),
                    for (final row in available)
                      ListTile(
                        title: Text(row['coupon_name'] as String? ?? ''),
                        subtitle: Text(row['shop_name'] as String? ?? ''),
                        trailing: ElevatedButton(
                          style: ElevatedButton.styleFrom(minimumSize: const Size(72, 44), maximumSize: const Size(112, 56)),
                          key: ValueKey('wallet-exchange-${row['id']}'),
                          onPressed:
                              _busy || _uncertain ? null : () => _submit(row['id'] as int, cost),
                          child: Text(context.tr('兌換', 'Redeem')),
                        ),
                      ),
                  ],
                );
              },
            );
          },
        ),
      );
}

/// QR screen: QR code, manual code and countdown from the issued code.
class QrShowScreen extends StatefulWidget {
  const QrShowScreen({super.key, required this.api, required this.item});

  final ApiClient api;
  final WalletItem item;

  @override
  State<QrShowScreen> createState() => _QrShowScreenState();
}

class _QrShowScreenState extends State<QrShowScreen> {
  String? _code;
  String? _payload;
  int _secondsLeft = 0;
  int _totalSeconds = 0;
  Timer? _timer;
  Timer? _pollTimer;
  bool _justRefreshed = false;
  bool _redeemed = false;
  bool _generating = false, _polling = false, _expired = false;
  String? _qrError;

  @override
  void initState() {
    super.initState();
    _generate();
    // UI_SPEC §8: poll until the voucher is redeemed, then close this
    // page and open the redemption-success page.
    _pollTimer = Timer.periodic(const Duration(seconds: 5), (_) async {
      if (_redeemed || _polling || !mounted) return;
      _polling = true;
      try {
        final result = await widget.api.get('/wallet/items/${widget.item.id}/status');
        if (!mounted) return;
        final reversedBeforePoll = result['revoked'] == true &&
            result['has_code'] == false && _code != null && !_generating;
        if ((result['status'] == 2 || reversedBeforePoll) &&
            ModalRoute.of(context)?.isCurrent == true) {
          _redeemed = true;
          _timer?.cancel();
          _pollTimer?.cancel();
          Navigator.of(context).pushReplacement(MaterialPageRoute(
              builder: (_) => RedemptionSuccessScreen(
                api: widget.api, item: widget.item,
                redemptionId: result['redemption_id'] as int?,
                initiallyRevoked: reversedBeforePoll,
                initialStatus: result['status'] as int,
              )));
        } else if (result['status'] == 3) {
          _timer?.cancel();
          _pollTimer?.cancel();
          setState(() { _expired = true; _payload = null; _code = null; _secondsLeft = 0; _qrError = '此優惠券已到期'; });
        }
      } catch (_) {
        // Retry the authoritative status on the next tick.
      } finally {
        _polling = false;
      }
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    _pollTimer?.cancel();
    // Brightness is restored here. NOTE: there is no brightness plugin
    // wired in (no screen_brightness in the pub cache and no native
    // platform code); the page uses a full-white card on a bright
    // background instead. See the batch report — this needs the plugin
    // + a real-device check before it can be claimed as done.
    super.dispose();
  }

  Future<void> _generate({bool auto = false}) async {
    if (_generating || _redeemed || _expired) return;
    _timer?.cancel();
    setState(() { _generating = true; _qrError = null; _payload = null; _code = null; _secondsLeft = 0; });
    try {
      final data = await widget.api.generateQr(widget.item.id);
      if (!mounted || _expired || _redeemed) return;
      final ttl = data['expires_in_seconds'];
      final payload = data['qr_payload'];
      final code = data['code'];
      if (ttl is! int || ttl <= 0) {
        setState(() => _qrError = context.tr(
            '未能確認兌換碼有效時間，請重試',
            'Could not confirm when the code expires. Try again.'));
        return;
      }
      if (payload is! String || payload.isEmpty || code is! String || code.isEmpty) {
        setState(() => _qrError = context.tr('未能取得兌換碼，請重試', 'Could not get the coupon code. Try again.'));
        return;
      }
      setState(() {
        _code = code;
        _payload = payload;
        _secondsLeft = ttl;
        _totalSeconds = _secondsLeft;
        if (auto) _justRefreshed = true;
      });
      if (auto) {
        Future.delayed(const Duration(seconds: 2), () {
          if (mounted) setState(() => _justRefreshed = false);
        });
      }
      _timer?.cancel();
      final deadline = DateTime.now().add(Duration(seconds:_secondsLeft));
      _timer = Timer.periodic(const Duration(seconds: 1), (t) {
        final remaining = (deadline.difference(DateTime.now()).inMilliseconds / 1000).ceil();
        if (remaining <= 0) {
          t.cancel();
          _generate(auto: true); // expired: rotate a fresh code
          return;
        }
        if (mounted) setState(() => _secondsLeft = remaining);
      });
    } catch (e) {
      if (mounted) setState(() => _qrError = e is ApiException ? e.message : '無法取得兌換碼，請重試');
    } finally {
      if (mounted) setState(() => _generating = false);
    }
  }

  String get _countdown => formatCountdown(
      Duration(seconds: _secondsLeft));

  String? get _validToText {
    final date = widget.item.validTo?.toLocal().toString().split(' ').first;
    return date == null ? null : context.tr('有效期至 $date', 'Valid until $date');
  }

  @override
  Widget build(BuildContext context) {
    final progress =
        _totalSeconds == 0 ? 0.0 : _secondsLeft / _totalSeconds;
    return Scaffold(
      backgroundColor: CgColors.primary,
      appBar: AppBar(title: Text(context.tr('出示優惠券', 'Show coupon'))),
      body: Center(
        child: SingleChildScrollView(
          child: Card(
            margin: const EdgeInsets.all(24),
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (_justRefreshed)
                    Container(
                      margin: const EdgeInsets.only(bottom: 12),
                      padding: const EdgeInsets.symmetric(
                          horizontal: 12, vertical: 6),
                      decoration: BoxDecoration(
                        color: const Color(0xFFE8F5E9),
                        borderRadius: BorderRadius.circular(20),
                      ),
                      child: Text(context.tr('QR 碼已更新', 'QR code refreshed'),
                          style: const TextStyle(color: CgColors.success)),
                    ),
                  Text(widget.item.couponName,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                          fontSize: 18, fontWeight: FontWeight.bold)),
                  const SizedBox(height: 4),
                  Text(widget.item.shopName,
                      textAlign: TextAlign.center,
                      style:
                          const TextStyle(color: CgColors.textSecondary)),
                  if (_validToText case final validTo?)
                  Text(validTo,
                      textAlign: TextAlign.center,
                      style:
                          const TextStyle(color: CgColors.textSecondary)),
                  const SizedBox(height: 16),
                  if (_payload != null)
                    QrImageView(data: _payload!, size: 220,backgroundColor: Colors.white)
                  else if (_qrError != null)
                    Text(_qrError!,textAlign:TextAlign.center)
                  else
                    const SkeletonBox(height: 220, width: 220),
                  const SizedBox(height: 12),
                  if (_code case final code?)
                  Text(code,
                      style: const TextStyle(
                          fontSize: 32,
                          fontWeight: FontWeight.bold,
                          letterSpacing: 6,
                          fontFeatures: [FontFeature.tabularFigures()])),
                  const SizedBox(height: 12),
                  // Show a countdown only after the server reports the actual expiry.
                  if (_payload != null) SizedBox(
                    width: 96,
                    height: 96,
                    child: Stack(
                      alignment: Alignment.center,
                      children: [
                        SizedBox(
                          width: 96,
                          height: 96,
                          child: CircularProgressIndicator(
                            value: progress,
                            strokeWidth: 6,
                            backgroundColor: const Color(0xFFE8E8E8),
                            color: CgColors.primary,
                          ),
                        ),
                        Text(_countdown,
                            style: const TextStyle(
                                color: CgColors.primary,
                                fontSize: 20,
                                fontWeight: FontWeight.bold,
                                fontFeatures: [
                                  FontFeature.tabularFigures()
                                ])),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(context.tr('請讓店員掃描 QR 碼以使用優惠券',
                      'Ask the staff to scan this QR code to use the coupon.'),
                      textAlign: TextAlign.center,
                      style: const TextStyle(color: CgColors.textSecondary)),
                  const SizedBox(height: 12),
                  Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: const Color(0xFFFFF3CD),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: Text(context.tr(
                        '請勿轉發 QR 碼。每次出示都會產生新碼。',
                        'Do not share this QR code. A new one is created each time.'),
                        textAlign: TextAlign.center,
                        style: const TextStyle(fontSize: 13)),
                  ),
                  const SizedBox(height: 8),
                  TextButton(
                    onPressed: _generating || _expired ? null : () => _generate(),
                    child: Text(context.tr('取得新碼', 'Get a new code')),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Redemption success (UI_SPEC §8): the QR page closes itself on
/// redemption and lands here — voucher name, shop, cashback note,
/// and the "去評價" entry (only for a not-yet-reviewed voucher).
class RedemptionSuccessScreen extends StatefulWidget {
  const RedemptionSuccessScreen({super.key, required this.api, required this.item,
    this.redemptionId, this.initiallyRevoked = false, this.initialStatus = 2});
  final ApiClient api;
  final WalletItem item;
  final int? redemptionId;
  final bool initiallyRevoked;
  final int initialStatus;
  @override
  State<RedemptionSuccessScreen> createState() => _ReceiptState();
}

class _ReceiptState extends State<RedemptionSuccessScreen> with WalletUpdates<RedemptionSuccessScreen> {
  late bool _revoked = widget.initiallyRevoked;
  late int _status = widget.initialStatus;
  late bool _reviewed = widget.item.reviewed;

  @override
  Future<void> syncWallet() async {
    final data = await widget.api.get('/wallet/items/${widget.item.id}/status',
        query: {if (widget.redemptionId != null) 'redemption_id': '${widget.redemptionId}'});
    if (!mounted) return;
    setState(() {
      _revoked = data['revoked'] == true;
      _status = data['status'] as int;
      _reviewed = data['reviewed'] == true;
    });
  }

  @override
  Widget build(BuildContext context) {
    final title = _revoked ? '商家已撤銷核銷' : '核銷成功';
    return Scaffold(
      appBar: AppBar(title: const Text('核銷結果')),
      body: Center(child: SingleChildScrollView(padding: const EdgeInsets.all(24),
        child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 440),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(_revoked ? Icons.undo : Icons.check_circle, size: 64,
                color: _revoked ? CgColors.textSecondary : CgColors.success),
            const SizedBox(height: 16),
            Semantics(liveRegion: true, child: Text(title, style: Theme.of(context).textTheme.headlineSmall)),
            const SizedBox(height: 12),
            Text(widget.item.couponName, textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            Text(widget.item.shopName, style: const TextStyle(color: CgColors.textSecondary)),
            if (_revoked) Padding(padding: const EdgeInsets.only(top: 12),
              child: Text(switch (_status) {
                1 => '優惠券已退回錢包，可重新出示。',
                3 => '優惠券已失效，可在錢包查看最新狀態。',
                _ => '這張券其後已再次使用。'
              }, textAlign: TextAlign.center)),
            const SizedBox(height: 24),
            if (!_revoked && !_reviewed)
              ElevatedButton(onPressed: () async {
                final done = await Navigator.of(context).push(MaterialPageRoute(builder: (_) =>
                    ReviewFormScreen(api: widget.api, item: widget.item, redemptionId: widget.redemptionId)));
                if (mounted && done == true) setState(() => _reviewed = true);
              }, child: const Text('去評價')),
            OutlinedButton(onPressed: () => Navigator.of(context).pop(), child: const Text('返回錢包')),
          ]),
        ),
      )),
    );
  }
}

/// Notifications: unread badge, mark-all-read, clear.
class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key, required this.api, this.guest = false});

  final ApiClient api;
  final bool guest;

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  List<AppNotification> _items = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (widget.guest) return;
    try {
      final data = await widget.api.notifications();
      if (!mounted) return;
      setState(() => _items = (data['notifications'] as List)
          .map((e) => AppNotification.fromJson(e))
          .toList());
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('通知'),
        actions: [
          if (!widget.guest) TextButton(
            onPressed: () => Navigator.of(context).push(MaterialPageRoute(
              builder: (_) => ShopInvitationsScreen(api: widget.api))),
            child: const Text('店員邀請', style: TextStyle(color: Colors.white))),
          if (!widget.guest) TextButton(
            onPressed: () async {
              await widget.api.markAllNotificationsRead();
              _load();
            },
            child: const Text('全部標示已讀',
                style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
      body: _items.isEmpty
          ? Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Image.asset('assets/images/ic_notification_cat.png',
                      height: 120,
                      errorBuilder: (_, __, ___) => const Icon(
                          Icons.notifications_none,
                          size: 64,
                          color: CgColors.textSecondary)),
                  const SizedBox(height: 12),
                  Text(widget.guest
                      ? context.tr('登入後可查看你的通知。', 'Sign in to view your notifications.')
                      : context.tr('沒有新通知。', 'No new notifications.')),
                ],
              ),
            )
          : ListView.builder(
              itemCount: _items.length,
              itemBuilder: (_, i) {
                final n = _items[i];
                return Card(
                  margin: const EdgeInsets.fromLTRB(12, 8, 12, 0),
                  child: ListTile(
                    title: Text(n.title,
                        style: TextStyle(
                            fontWeight: n.isRead
                                ? FontWeight.normal
                                : FontWeight.bold)),
                    subtitle: Text(n.body),
                    trailing: n.isRead
                        ? null
                        : const Icon(Icons.circle,
                            size: 10, color: CgColors.primary),
                    onTap: () async {
                      final session = widget.api.sessionRevision;
                      if (!n.isRead) {
                        await widget.api.markNotificationRead(n.id);
                        _load();
                      }
                      if (context.mounted && session == widget.api.sessionRevision &&
                          n.type == 'shop_member_invitation') {
                        await Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => ShopInvitationsScreen(api: widget.api)));
                        if (mounted && session == widget.api.sessionRevision) _load();
                      }
                    },
                  ),
                );
              },
            ),
    );
  }
}

/// Settings: profile, language, notification toggles, logout / delete.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.api, required this.onLogout});

  final ApiClient api;
  final VoidCallback onLogout;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

enum _DeleteAccountAction { withdraw, reviewCashback, confirm }

class _SettingsScreenState extends State<SettingsScreen> {
  bool _deleting = false;

  Future<void> _deleteAccount() async {
    if (_deleting || !widget.api.isLoggedIn) return;
    setState(() => _deleting = true);
    try {
      double? balance;
      double? minimum;
      double? dailyLimit;
      if (cashbackEnabled) {
        try {
          final cashback = await widget.api.cashback();
          balance = (cashback['withdrawable_balance'] as num?)?.toDouble();
          minimum = (cashback['min_withdraw_hkd'] as num?)?.toDouble();
          dailyLimit = (cashback['daily_withdraw_limit_hkd'] as num?)?.toDouble();
        } catch (_) {
          // An unavailable balance must never be treated as zero.
        }
      }
      if (!mounted || !widget.api.isLoggedIn ||
          ModalRoute.of(context)?.isCurrent == false) {
        return;
      }
      final t = context.tr;
      final currentBalance = balance;
      final canWithdraw = currentBalance != null && currentBalance > 0 &&
          minimum != null && minimum > 0 && currentBalance >= minimum;
      final action = await showDialog<_DeleteAccountAction>(
        context: context,
        builder: (dialog) => AlertDialog(
          title: Text(t('刪除帳戶', 'Delete account')),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(t(
                  '刪除後，免費優惠券、積分及待確認或追蹤中的返現會作廢。',
                  'Deleting your account voids free vouchers, points, and any pending or tracked cashback.',
                )),
                Text(t('未使用的付費優惠券，請先退款或使用。',
                    'Refund or use any unused paid vouchers first.')),
                Text(t('如有可提現返現，請先提現。',
                    'Withdraw any available cashback first.')),
                if (cashbackEnabled && currentBalance == null) ...[
                  const SizedBox(height: 12),
                  Text(t('暫時無法核對返現餘額，請先查看返現或稍後再試。',
                      'We could not check your balance. Review your cashback or try again later.')),
                ] else if (cashbackEnabled && currentBalance != null && currentBalance > 0) ...[
                  const SizedBox(height: 12),
                  Text(t('可提現餘額：${formatHkd(currentBalance)}',
                      'Available cashback: ${formatHkd(currentBalance)}')),
                  if (minimum != null && currentBalance < minimum)
                    Text(t('尚未達到 ${formatHkd(minimum)} 提現門檻。',
                        'Below the ${formatHkd(minimum)} withdrawal minimum.')),
                ],
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialog),
              child: Text(t('取消', 'Cancel')),
            ),
            if (cashbackEnabled && (currentBalance == null || currentBalance > 0))
              TextButton(
                onPressed: () => Navigator.pop(dialog, canWithdraw
                    ? _DeleteAccountAction.withdraw
                    : _DeleteAccountAction.reviewCashback),
                child: Text(canWithdraw
                    ? t('去提現', 'Withdraw cashback')
                    : t('查看返現', 'Review cashback')),
              ),
            TextButton(
              onPressed: () => Navigator.pop(dialog, _DeleteAccountAction.confirm),
              child: Text(t('繼續刪除', 'Continue to delete')),
            ),
          ],
        ),
      );
      if (!mounted || !widget.api.isLoggedIn) return;
      switch (action) {
        case _DeleteAccountAction.withdraw:
          await Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => WithdrawScreen(
              api: widget.api,
              balance: currentBalance,
              minWithdrawHkd: minimum!,
              dailyWithdrawLimitHkd: dailyLimit ?? CashbackLimits.dailyWithdrawLimitHkd,
            ),
          ));
          return;
        case _DeleteAccountAction.reviewCashback:
          await Navigator.of(context).pushNamed('/cashback');
          return;
        case _DeleteAccountAction.confirm:
          break;
        case null:
          return;
      }
      if (!mounted) return;
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (dialog) => AlertDialog(
          title: Text(t('確認刪除帳戶？', 'Confirm account deletion?')),
          content: Text(t('提交後，你將無法再登入此帳戶。',
              'You will no longer be able to sign in to this account.')),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(t('取消', 'Cancel')),
            ),
            FilledButton(
              style: FilledButton.styleFrom(
                backgroundColor: Theme.of(dialog).colorScheme.error,
                foregroundColor: Theme.of(dialog).colorScheme.onError,
              ),
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(t('確認刪除', 'Confirm deletion')),
            ),
          ],
        ),
      );
      if (confirmed != true || !mounted || !widget.api.isLoggedIn) return;
      await widget.api.post('/delete-account', body: {});
      await widget.api.clearToken();
      if (mounted) widget.onLogout();
    } on ApiException catch (error) {
      if (!mounted) return;
      if (error.status == 409) {
        await _showDeletionBlocker(error);
      } else {
        showCgToast(context, context.tr('未能刪除帳戶，請重試。', 'Could not delete your account. Try again.'));
      }
    } catch (_) {
      if (mounted) {
        showCgToast(context, context.tr('未能刪除帳戶，請重試。', 'Could not delete your account. Try again.'));
      }
    } finally {
      if (mounted) setState(() => _deleting = false);
    }
  }

  Future<void> _showDeletionBlocker(ApiException error) async {
    final t = context.tr;
    final english = switch (error.code) {
      'unused_paid_voucher' => 'Refund or use any unused paid vouchers first.',
      'payment_in_flight' => 'Cancel or pay for the pending order in My orders.',
      'refund_in_flight' => 'Check the refund in My orders. Delete your account once the refund is complete.',
      'withdrawal_in_flight' => 'Check your withdrawal history. Delete your account once the withdrawal is complete.',
      'withdrawable_cashback' => 'Withdraw your available cashback first.',
      'withdrawal_unreconciled' => 'Give support the withdrawal reference so they can reconcile the payment.',
      'cashback_adjustment' => 'Give support the cashback reference so they can reconcile the balance.',
      'merchant_application' => 'Wait for your merchant application decision or ask support to withdraw it.',
      'active_shop' => 'Close the shop in the merchant dashboard or ask support to arrange a transfer.',
      'unsettled_merchant_funds' => 'Review the funds in the merchant dashboard. Delete your account after settlement.',
      'merchant_orders' => 'Complete or refund outstanding orders in the merchant dashboard.',
      'admin_role' => 'Transfer and remove this account’s administrator access in admin settings.',
      _ => error.message,
    };
    final hasOrder = const {'unused_paid_voucher', 'payment_in_flight', 'refund_in_flight'}.contains(error.code);
    final hasWithdrawal = cashbackEnabled && error.code == 'withdrawable_cashback';
    final hasHistory = error.code == 'withdrawal_in_flight';
    final navigate = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(t('暫時無法刪除帳戶', 'Can’t delete account yet')),
        content: Text(t(error.message, english)),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: Text(t('關閉', 'Close')),
          ),
          if (hasOrder || hasWithdrawal || hasHistory)
            TextButton(
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(hasOrder
                  ? t('查看訂單', 'View orders')
                  : hasWithdrawal
                      ? t('去提現', 'Withdraw cashback')
                      : t('查看提現紀錄', 'View withdrawal history')),
            ),
        ],
      ),
    );
    if (navigate == true && mounted && widget.api.isLoggedIn) {
      if (hasOrder) {
        await Navigator.of(context).pushNamed('/orders');
      } else {
        await Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => hasWithdrawal
              ? WithdrawScreen(api: widget.api)
              : WithdrawalHistoryScreen(api: widget.api),
        ));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final language = LanguageScope.of(context);
    final t = context.tr;
    final draft = language.draft;
    return Scaffold(
      appBar: AppBar(title: Text(t('設置','Settings'))),
      body: Stack(children: [ListView(
        children: [
          ListTile(title: Text(t('語言','Languages')),
            subtitle: Text((draft ?? language.english) ? 'English' : '繁體中文'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => setState(() => language.draft = language.english)),
          ListTile(
            title: Text(t('新手指南 / 常見問題', 'Guide and FAQs')),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => Navigator.of(context).pushNamed('/guide'),
          ),
          ListTile(
            title: Text(t('關於 Coupon GO!', 'About Coupon GO!')),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => Navigator.of(context).push(MaterialPageRoute(
              builder: (_) => const CouponGoAboutScreen(),
            )),
          ),
          if (widget.api.isLoggedIn && !publicTrial) ...[
            const Divider(),
            ListTile(
              title: Text(t('登出', 'Log out'),
                  style: const TextStyle(color: CgColors.primary)),
              onTap: () async {
                await widget.api.clearToken();
                if (mounted) widget.onLogout();
              },
            ),
            ListTile(
              title: Text(t('刪除帳戶', 'Delete account'),
                  style: TextStyle(color: Theme.of(context).colorScheme.error)),
              enabled: !_deleting,
              onTap: _deleting ? null : _deleteAccount,
            ),
          ],
        ],
      ),
      if (draft != null) ...[
        ModalBarrier(color: Colors.black38, dismissible: true,
          onDismiss: () => setState(() => language.draft = null)),
        Align(alignment: Alignment.bottomCenter, child: Material(
          borderRadius: const BorderRadius.vertical(top: Radius.circular(18)),
          child: SafeArea(child: Column(mainAxisSize: MainAxisSize.min, children: [
            for (final english in [true,false]) ListTile(
              title: Text(english ? 'English' : '繁體中文'),
              trailing: Icon(draft == english ? Icons.radio_button_checked : Icons.radio_button_off),
              onTap: () => setState(() => language.draft = english)),
            Padding(padding: const EdgeInsets.all(12), child: FilledButton(
              onPressed: () async { await language.commit(draft); if (mounted) setState(() {}); },
              child: Text(t('提交','Submit')))),
          ])),
        )),
      ],
      ]),
    );
  }
}

/// Legal document links will be shown only when real published documents are available.
class CouponGoAboutScreen extends StatelessWidget {
  const CouponGoAboutScreen({super.key});

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: Text(context.tr('關於 Coupon GO!', 'About Coupon GO!'))),
        body: ListView(
          children: [
            ListTile(
              title: Text(context.tr('開源授權', 'Open source licenses')),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => showLicensePage(context: context, applicationName: 'Coupon GO!'),
            ),
          ],
        ),
      );
}

/// Guide + FAQ (local, behaviour-based).
class GuideScreen extends StatelessWidget {
  const GuideScreen({super.key, this.api});

  final ApiClient? api;

  String _qrLifetime(BuildContext context, int seconds) => seconds % 60 == 0
      ? context.tr('兌換碼最長有效期：${seconds ~/ 60} 分鐘。',
          'Maximum code lifetime: ${seconds ~/ 60} min.')
      : context.tr('兌換碼最長有效期：$seconds 秒。',
          'Maximum code lifetime: $seconds sec.');

  static const _faqs = [
    (
      '如何領取優惠券？', 'How do I claim a coupon?',
      '在首頁或優惠券詳情點「立即領取」，優惠券便會存入 Q Wallet。',
      'Tap Claim on Home or the coupon details page to save it to Q Wallet.',
    ),
    (
      '如何使用優惠券？', 'How do I use a coupon?',
      '在 Q Wallet 點「出示」，請店員掃描 QR 碼。',
      'Tap Show in Q Wallet and ask the staff to scan the QR code.',
    ),
    (
      '積分可以怎樣用？', 'What are points for?',
      '在 Q Wallet 查看積分餘額及可兌換的優惠。',
      'Check Q Wallet for your points balance and available rewards.',
    ),
    (
      'QR 碼有效期多久？', 'How long is the QR code valid?',
      '以出示頁上的倒數為準；到期後請重新出示。',
      'Use the countdown on the code screen. Show a fresh code if it expires.',
    ),
    (
      '商戶如何發券？', 'How do merchants issue coupons?',
      '切換到企業版，填寫優惠資料，檢查並提交審核。',
      'Switch to the business workspace, enter the offer details, and submit them for review.',
    ),
    (
      '忘記密碼怎麼辦？', 'What if I forget my password?',
      '在登入頁選擇「忘記密碼」，以電郵驗證碼重設。',
      'Select Forgot password on the login screen and use the code sent to your email.',
    ),
  ];

  @override
  Widget build(BuildContext context) {
    final config = api?.appConfig;
    if (config == null) return _guide(context, const AppConfig());
    return ValueListenableBuilder<AppConfig>(
      valueListenable: config,
      builder: (context, rules, _) => _guide(context, rules),
    );
  }

  Widget _guide(BuildContext context, AppConfig rules) => Scaffold(
        appBar: AppBar(title: Text(context.tr('新手指南 / 常見問題', 'Guide and FAQs'))),
        body: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Text(context.tr('消費三步', 'Three steps to use an offer'),
                style: const TextStyle(fontWeight: FontWeight.bold)),
            ListTile(
              leading: const Icon(Icons.search),
              title: Text(context.tr('1. 搜尋或瀏覽優惠券', '1. Find a coupon')),
            ),
            ListTile(
              leading: const Icon(Icons.local_offer),
              title: Text(context.tr('2. 領取存入 Q Wallet', '2. Claim it in Q Wallet')),
            ),
            ListTile(
              leading: const Icon(Icons.qr_code),
              title: Text(context.tr('3. 到店出示 QR 碼核銷', '3. Show the QR code in store')),
            ),
            const SizedBox(height: 8),
            Text(context.tr('常見問題', 'FAQs'),
                style: const TextStyle(fontWeight: FontWeight.bold)),
            for (final item in _faqs)
              ExpansionTile(
                title: Text(context.tr(item.$1, item.$2)),
                children: [
                  ListTile(title: Text(context.tr(item.$3, item.$4))),
                  if (item == _faqs[2]) ...[
                    if (rules.pointsClaim case final points?)
                      ListTile(title: Text(context.tr('領券 +$points 積分', 'Claiming a coupon: +$points points'))),
                    if (rules.pointsRedeem case final points?)
                      ListTile(title: Text(context.tr('核銷 +$points 積分', 'Redeeming a coupon: +$points points'))),
                    if (rules.pointsReview case final points?)
                      ListTile(title: Text(context.tr('合資格評價 +$points 積分', 'Eligible review: +$points points'))),
                  ],
                  if (item == _faqs[3] && rules.qrTtlSeconds != null)
                    ListTile(title: Text(_qrLifetime(context, rules.qrTtlSeconds!))),
                ],
              ),
          ],
        ),
      );
}
