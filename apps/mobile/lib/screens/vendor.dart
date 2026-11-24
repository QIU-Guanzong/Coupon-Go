import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../api_client.dart';
import '../feature_flags.dart' show publicPreview;
import '../vendor_coupon_lifecycle.dart';
import '../models.dart';
import '../language.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import 'reviews_admin.dart';
import 'vendor_packages.dart';
import 'coupon_branch_scope.dart';
import 'redemption_recovery.dart';
import 'merchant_dashboard.dart';
import 'scan_batch.dart';
import 'merchant_branches.dart';
import 'merchant_staff.dart';

const _trialMode = bool.fromEnvironment('PUBLIC_TRIAL');

/// Vendor home: gold theme, usage summary, issue-coupon entry.
class VendorHomeScreen extends StatefulWidget {
  const VendorHomeScreen(
      {super.key, required this.api, this.showAdmin = false, this.onOpenMenu});

  final ApiClient api;
  final bool showAdmin;
  final VoidCallback? onOpenMenu;

  @override
  State<VendorHomeScreen> createState() => _VendorHomeScreenState();
}

class _VendorHomeScreenState extends State<VendorHomeScreen> {
  Map<String, dynamic>? _usage;
  List<Coupon> _coupons = [];
  List<Map<String, dynamic>> _shops = [];
  String? _loadError;
  bool _loading = true;
  // The unfiltered list also supplies the existing review-reply shortcut.
  // A coupon filter must never change that shortcut's shop.
  String? _couponStatus;
  List<Coupon> _filteredCoupons = [];
  bool _filterLoading = false;
  String? _filterError;
  int _overviewRequest = 0;
  int _filterRequest = 0;
  int _couponChange = 0;
  int _overviewCouponRead = 0;
  int _filteredCouponRead = 0;
  final _savingCoupons = <int>{};
  final _couponReceipts = <int, (int, String)>{};
  late int _session;
  bool _allows(String permission) => widget.showAdmin || _shops.any((shop) => shop[permission] == true);

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    _load();
  }

  void _sessionChanged() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    _filterRequest++;
    setState(() {
      _shops = [];
      _coupons = [];
      _usage = null;
      _loading = true;
      _loadError = null;
      _couponStatus = null;
      _filteredCoupons = [];
      _filterLoading = false;
      _filterError = null;
      _couponChange = _overviewCouponRead = _filteredCouponRead = 0;
      _savingCoupons.clear();
      _couponReceipts.clear();
    });
    _load();
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    super.dispose();
  }

  Future<void> _load() async {
    if (!mounted) return;
    final session = _session;
    final request = ++_overviewRequest;
    final couponRead = _couponChange;
    bool current() =>
        mounted && session == _session && request == _overviewRequest;
    try {
      final data = await widget.api.get('/merchant/shops');
      if (!current()) return;
      final shops = (data['shops'] as List? ?? []).whereType<Map>().map((s) => Map<String, dynamic>.from(s)).toList();
      final isOwner = widget.showAdmin || shops.any((s) => s['is_owner'] == true);
      final canManage = widget.showAdmin || shops.any((s) => s['can_manage_coupons'] == true);
      final usage = isOwner ? await widget.api.usageData() : null;
      if (!current()) return;
      final list = canManage ? await widget.api.vendorCoupons() : <dynamic>[];
      if (!current()) return;
      setState(() {
        _shops = shops;
        _usage = usage;
        _coupons = list.map((e) => Coupon.fromJson(e)).toList();
        _overviewCouponRead = couponRead;
        _loading = false;
        _loadError = null;
      });
    } catch (error) {
      if (!current()) return;
      setState(() {
        _loading = false;
        _loadError = error is ApiException
            ? error.message
            : context.tr('未能載入，請重試。', 'Could not load. Try again.');
      });
    }
  }

  Future<void> _filterCoupons(String? status) async {
    if (!_allows('can_manage_coupons')) return;
    final session = _session;
    final request = ++_filterRequest;
    final couponRead = _couponChange;
    bool current() =>
        mounted && session == _session && request == _filterRequest;
    setState(() {
      _couponStatus = status;
      _filteredCoupons = [];
      _filterLoading = status != null;
      _filterError = null;
    });
    if (status == null) return;
    try {
      final rows = await widget.api.vendorCoupons(status: status);
      if (!current()) return;
      final coupons = rows.map((row) => Coupon.fromJson(row)).toList();
      setState(() {
        _filteredCoupons = coupons;
        _filteredCouponRead = couponRead;
        _filterLoading = false;
      });
    } catch (error) {
      if (!current()) return;
      setState(() {
        _filterLoading = false;
        _filterError = error is ApiException
            ? error.message
            : context.tr('未能載入優惠券。', 'Could not load coupons.');
      });
    }
  }

  Future<void> _refresh() async {
    final session = _session;
    final filterRequest = _filterRequest;
    final loadingOverview = _load();
    final overviewRequest = _overviewRequest;
    await loadingOverview;
    if (mounted &&
        session == _session &&
        filterRequest == _filterRequest &&
        overviewRequest == _overviewRequest &&
        _loadError == null &&
        _couponStatus != null) {
      await _filterCoupons(_couponStatus);
    }
  }

  String _statusLabel(BuildContext context, String status) => switch (status) {
    'pending_review' => context.tr('待審核', 'Pending review'),
    'active' => context.tr('已上架', 'Listed'),
    'paused' => context.tr('已暫停', 'Paused'),
    'draft' => context.tr('草稿', 'Draft'),
    'rejected' => context.tr('已拒絕', 'Rejected'),
    'scheduled' => context.tr('待上架', 'Scheduled'),
    'sold_out' => context.tr('已售罄', 'Sold out'),
    'expired' => context.tr('已過期', 'Expired'),
    _ => context.tr('狀態未明', 'Unknown status'),
  };

  // Only overlay an acknowledged change on a list fetched before it.
  String? _pendingStatus(Coupon coupon) {
    final receipt = _couponReceipts[coupon.id];
    final read = _couponStatus == null ? _overviewCouponRead : _filteredCouponRead;
    return receipt != null && receipt.$1 > read ? receipt.$2 : null;
  }

  bool _canChangeCoupon(Coupon coupon) => !_trialMode &&
      !publicPreview &&
      widget.api.isLoggedIn &&
      coupon.id > 0 &&
      (widget.showAdmin || _shops.any((shop) =>
          shop['id'] == coupon.shopId && shop['can_manage_coupons'] == true));

  String _actionLabel(BuildContext context, VendorCouponAction action) => switch (action) {
        VendorCouponAction.pause => context.tr('暫停', 'Pause'),
        VendorCouponAction.resume => context.tr('恢復上架', 'Resume'),
        VendorCouponAction.unlist => context.tr('下架', 'Unlist'),
        VendorCouponAction.submit => context.tr('提交審核', 'Submit for review'),
      };

  Future<void> _changeCoupon(Coupon coupon, VendorCouponAction action) async {
    final session = _session;
    final api = widget.api;
    bool current() =>
        mounted && identical(api, widget.api) && session == _session && session == api.sessionRevision;
    bool canSubmit() {
      if (!current() || !_canChangeCoupon(coupon) || _savingCoupons.contains(coupon.id)) {
        return false;
      }
      final displayed = _couponStatus == null ? _coupons : _filteredCoupons;
      for (final item in displayed) {
        if (item.id != coupon.id || item.shopId != coupon.shopId) continue;
        final status = _pendingStatus(item) ?? item.status;
        return (_couponStatus == null || status == _couponStatus) &&
            VendorCouponAction.forStatus(status).contains(action);
      }
      return false;
    }
    if (!canSubmit()) return;
    if (action == VendorCouponAction.unlist) {
      final confirm = await showDialog<bool>(
        context: context,
        builder: (dialog) => AlertDialog(
          title: Text(dialog.tr('下架優惠券？', 'Unlist coupon?')),
          content: Text(dialog.tr(
            '「${coupon.name}」會移回草稿，顧客將看不到這張券。',
            '"${coupon.name}" will return to drafts and no longer appear to customers.',
          )),
          actions: [
            TextButton(onPressed: () => Navigator.of(dialog).pop(false),
                child: Text(dialog.tr('取消', 'Cancel'))),
            TextButton(onPressed: () => Navigator.of(dialog).pop(true),
                child: Text(dialog.tr('確認下架', 'Confirm unlist'))),
          ],
        ),
      );
      if (confirm != true || !canSubmit()) return;
    }
    setState(() => _savingCoupons.add(coupon.id));
    try {
      final status = await api.updateVendorCouponStatus(coupon.id, action);
      if (!current()) return;
      setState(() => _couponReceipts[coupon.id] = (++_couponChange, status));
      await _refresh();
    } catch (error) {
      if (!mounted || !current()) return;
      showCgToast(
        context,
        error is ApiException && error.status < 500
            ? error.message
            : context.tr('未能確認優惠券狀態，請重新整理。', 'Could not confirm coupon status. Refresh the list.'),
      );
      await _refresh();
    } finally {
      if (current()) setState(() => _savingCoupons.remove(coupon.id));
    }
  }

  @override
  Widget build(BuildContext context) {
    final shownCoupons = (_couponStatus == null ? _coupons : _filteredCoupons)
        .where((coupon) => _couponStatus == null || _pendingStatus(coupon) == null ||
            _pendingStatus(coupon) == _couponStatus)
        .toList();
    return Scaffold(
      appBar: AppBar(
        backgroundColor: CgColors.vendorGold,
        foregroundColor: CgColors.textPrimary,
        leading: widget.onOpenMenu == null ? null : IconButton(
          icon: const Icon(Icons.menu),
          tooltip: MaterialLocalizations.of(context).openAppDrawerTooltip,
          onPressed: widget.onOpenMenu,
        ),
        title: Text(context.tr('企業版', 'Business')),
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(16),
          children: [
            if (_loading) const Center(child: CircularProgressIndicator()),
            if (_loadError != null) ...[
              Text(_loadError!),
              TextButton(onPressed: _refresh, child: Text(context.tr('重試', 'Retry'))),
            ],
            if (_usage != null && _allows('is_owner')) Row(
              children: [
                Expanded(
                    child:
                        _stat(context.tr('總下載次數', 'Total downloads'), '${_usage?['total_downloads'] ?? 0}')),
                const SizedBox(width: 12),
                Expanded(
                    child: _stat(context.tr('總使用量', 'Total redemptions'), '${_usage?['total_usage'] ?? 0}')),
              ],
            ),
            const SizedBox(height: 16),
            if (_allows('can_manage_coupons')) ElevatedButton(
              style: ElevatedButton.styleFrom(
                  backgroundColor: CgColors.vendorGold),
              onPressed: () => Navigator.of(context)
                  .pushNamed('/vendor/issue')
                  .then((_) async {
                if (mounted) await _refresh();
              }),
              child: Text(context.tr('發券', 'Issue coupon'),
                  style: const TextStyle(color: CgColors.textPrimary)),
            ),
            const SizedBox(height: 8),
            if (_allows('is_owner')) OutlinedButton.icon(
              onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => VendorPackagesScreen(api: widget.api))),
              icon: const Icon(Icons.restaurant_menu),
              label: Text(context.tr('套餐管理', 'Packages')),
            ),
            const SizedBox(height: 8),
            if (_allows('can_view_settlement')) OutlinedButton.icon(
              onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => MerchantDashboardScreen(api: widget.api))),
              icon: const Icon(Icons.insights_outlined),
              label: Text(context.tr('經營與餘額', 'Business and balance')),
            ),
            const SizedBox(height: 8),
            if (_allows('is_owner')) OutlinedButton.icon(
              onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) => MerchantRedemptionRecoveryScreen(api: widget.api))),
              icon: const Icon(Icons.manage_search),
              label: Text(context.tr('補錄核銷', 'Record redemption')),
            ),
            const SizedBox(height: 8),
            OutlinedButton.icon(
              onPressed: () => Navigator.of(context).push(MaterialPageRoute(builder: (_) => MerchantBranchesScreen(api: widget.api, staffBuilder: (shopId) => MerchantStaffScreen(api: widget.api, shopId: shopId)))),
              icon: const Icon(Icons.storefront_outlined),
              label: Text(context.tr('店舖與分店', 'Shops and branches')),
            ),
            const SizedBox(height: 8),
            if (_allows('is_owner')) OutlinedButton(
              onPressed: () =>
                  Navigator.of(context).pushNamed('/vendor/records'),
              child: Text(context.tr('核銷記錄', 'Redemption history')),
            ),
            const SizedBox(height: 8),
            if (_allows('is_owner')) OutlinedButton(
              onPressed: () {
                if (_coupons.isEmpty) {
                  showCgToast(context, context.tr('暫無商戶資料，無法查看評價', 'No shop information for reviews.'));
                  return;
                }
                final shop = _coupons.first;
                Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => MerchantReviewReplyScreen(
                        api: widget.api,
                        shopId: shop.shopId,
                        shopName: shop.shopName)));
              },
              child: Text(context.tr('評價回覆（差評）', 'Reply to negative reviews')),
            ),
            const SizedBox(height: 8),
            if (widget.showAdmin)
              OutlinedButton(
                onPressed: () => Navigator.of(context).pushNamed('/admin'),
                child: Text(context.tr('管理後台（結算 / 對賬 / 發票 / 內容 / 角色）', 'Admin tools')),
              ),
            const SizedBox(height: 16),
            if (_allows('can_view_settlement')) OutlinedButton(
                onPressed: () =>
                    Navigator.of(context).pushNamed('/vendor/settlements'),
                child: Text(context.tr('商家結算單', 'Settlement statements'))),
            if (_allows('can_manage_coupons')) ...[
              Text(context.tr('我的優惠券', 'My coupons'),
                  style: const TextStyle(fontWeight: FontWeight.bold)),
              DropdownButton<String>(
                key: const ValueKey('vendor-coupon-status-filter'),
                value: _couponStatus ?? '',
                isExpanded: true,
                onChanged: (value) {
                  if (value != null) _filterCoupons(value.isEmpty ? null : value);
                },
                items: [
                  DropdownMenuItem(
                    value: '',
                    child: Text(context.tr('全部狀態', 'All statuses')),
                  ),
                  for (final status in [
                    'pending_review', 'active', 'paused', 'draft', 'rejected',
                  ])
                    DropdownMenuItem(
                      value: status,
                      child: Text(_statusLabel(context, status)),
                    ),
                ],
              ),
              if (_filterLoading) const LinearProgressIndicator(),
              if (_filterError != null) ...[
                Text(_filterError!),
                TextButton(onPressed: () => _filterCoupons(_couponStatus),
                    child: Text(context.tr('重試', 'Retry'))),
              ],
            ],
            if (_allows('can_manage_coupons') && _trialMode)
              Text(context.tr('體驗版暫不支援修改優惠券的適用分店。',
                  'Changing coupon branches is unavailable in the trial.')),
            if (_allows('can_manage_coupons'))
              ...shownCoupons.map((c) {
                final status = _pendingStatus(c) ?? c.status;
                final actions = _canChangeCoupon(c)
                    ? VendorCouponAction.forStatus(status)
                    : const <VendorCouponAction>[];
                return Card(
                  margin: const EdgeInsets.only(top: 8),
                  child: ListTile(
                    leading:
                        const Icon(Icons.local_offer, color: CgColors.amber),
                    title: Text(c.name),
                    subtitle: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        if (status.isNotEmpty)
                          Text(_statusLabel(context, status),
                              key: ValueKey('vendor-coupon-status-${c.id}'),
                              style: Theme.of(context).textTheme.labelLarge),
                        Text(context.tr(
                          '剩餘 ${c.remaining} · 已核銷 ${c.usedCount ?? '—'}',
                          '${c.remaining} left · ${c.usedCount ?? '—'} redeemed',
                        )),
                      ],
                    ),
                    trailing: Row(mainAxisSize: MainAxisSize.min, children: [
                      IconButton(
                        tooltip: context.tr('編輯適用分店', 'Edit eligible branches'),
                        icon: const Icon(Icons.storefront_outlined),
                        onPressed: _trialMode ? null : () => Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => CouponBranchEditScreen(api: widget.api, couponId: c.id,
                            shopId: c.shopId, couponName: c.name))),
                      ),
                      if (actions.isNotEmpty)
                        PopupMenuButton<VendorCouponAction>(
                          key: ValueKey('vendor-coupon-actions-${c.id}'),
                          enabled: !_savingCoupons.contains(c.id),
                          tooltip: context.tr('管理優惠券', 'Manage coupon'),
                          onSelected: (action) => _changeCoupon(c, action),
                          itemBuilder: (menuContext) => [
                            for (final action in actions)
                              PopupMenuItem(
                                value: action,
                                child: Text(_actionLabel(menuContext, action)),
                              ),
                          ],
                        ),
                    ]),
                  ),
                );
              }),
            if (_allows('can_manage_coupons') &&
                shownCoupons.isEmpty &&
                !_loading &&
                !_filterLoading &&
                _loadError == null &&
                _filterError == null)
              Padding(
                padding: const EdgeInsets.only(top: 24),
                child: Text(_couponStatus == null
                    ? context.tr('還沒有優惠券。', 'No coupons yet.')
                    : context.tr('這個狀態沒有優惠券。', 'No coupons with this status.')),
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
                style:
                    const TextStyle(fontSize: 28, fontWeight: FontWeight.bold)),
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
                  title:
                      Text(context.tr('未能載入帳戶', 'Could not load the account')),
                  trailing: TextButton(
                    onPressed: () {
                      final result = widget.api.get('/me');
                      setState(() {
                        _profile = result;
                      });
                    },
                    child: Text(context.tr('重試', 'Retry')),
                  ),
                );
              }
              final name = snapshot.data?['name'] as String? ?? '';
              final email = snapshot.data?['email'] as String? ?? '';
              if (name.isEmpty && email.isEmpty) {
                return ListTile(
                    title: Text(context.tr('未有帳戶資料', 'No account details')));
              }
              return ListTile(
                leading: const Icon(Icons.storefront),
                title: Text(name.isEmpty ? email : name),
                subtitle:
                    name.isNotEmpty && email.isNotEmpty ? Text(email) : null,
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
  int? _shopId;
  List<Map<String, dynamic>>? _shops;
  List<int>? _branchIds;
  String? _shopsError;
  bool _submitting = false;
  bool _createUncertain = false;
  bool _dirty = false;
  late int _session;
  DateTime _validTo = DateTime.now().add(const Duration(days: 30));

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    for (final controller in [_name, _percentage, _cash, _total, _minSpend, _terms]) {
      controller.addListener(_markDirty);
    }
    _loadShops();
  }

  void _markDirty() {
    if (mounted && !_dirty) setState(() => _dirty = true);
  }

  Future<bool> _canLeave() async {
    if (_submitting) return false;
    if (!_dirty) return true;
    return await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: Text(context.tr('放棄尚未儲存的內容？', 'Discard unsaved changes?')),
            actions: [
              TextButton(onPressed: () => Navigator.pop(dialogContext, false),
                  child: Text(context.tr('繼續編輯', 'Keep editing'))),
              TextButton(onPressed: () => Navigator.pop(dialogContext, true),
                  child: Text(context.tr('放棄', 'Discard'))),
            ],
          ),
        ) ?? false;
  }

  void _sessionChanged() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    if (mounted) {
      setState(() {
      _shops = null; _shopId = null; _branchIds = null; _submitting = false; _createUncertain = false;
      _shopsError = '登入狀態已變更，請重新讀取商店。';
    });
    }
  }

  Future<void> _loadShops() async {
    final session = _session;
    setState(() { _shops = null; _shopsError = null; _shopId = null; _branchIds = null; });
    try {
      final data = await widget.api.get('/merchant/shops');
      final rows = data['shops'];
      if (rows is! List || rows.any((v) => v is! Map || v['id'] is! int || v['name'] is! String)) {
        throw const FormatException();
      }
      if (!mounted || session != _session) return;
      setState(() {
        _shops = rows.where((v) => (v as Map)['can_manage_coupons'] == true)
            .map((v) => Map<String, dynamic>.from(v as Map)).toList();
        if (_shops!.isNotEmpty) _shopId = _shops!.first['id'] as int;
      });
    } catch (e) {
      if (mounted && session == _session) setState(() => _shopsError = e is ApiException ? e.message : '未能讀取商店，請重試');
    }
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    _name.dispose(); _percentage.dispose(); _cash.dispose(); _total.dispose(); _minSpend.dispose(); _terms.dispose();
    super.dispose();
  }

  Map<String, dynamic> _body() => {
        'shop_id': _shopId,
        'branch_ids': _branchIds,
        'coupon_type': _type,
        'coupon_name': _name.text,
        'percentage':
            _type == 'Discount' ? double.tryParse(_percentage.text) : null,
        'cash_amount': _type == 'Cash' ? double.tryParse(_cash.text) : null,
        'min_spend': double.tryParse(_minSpend.text) ?? 0,
        'total': int.tryParse(_total.text) ?? 100,
        'additional_terms': _terms.text,
        'valid_from': DateTime.now().toIso8601String(),
        'valid_to': _validTo.toIso8601String(),
      };

  @override
  Widget build(BuildContext context) {
    return PopScope(
      canPop: !_dirty && !_submitting,
      onPopInvokedWithResult: (didPop, _) async {
        if (!didPop && await _canLeave() && context.mounted) {
          Navigator.of(context).pop();
        }
      },
      child: Scaffold(
      appBar: AppBar(
        backgroundColor: CgColors.vendorGold,
        foregroundColor: CgColors.textPrimary,
        title: const Text('發券'),
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 720),
          child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_trialMode) ...[
            Text(context.tr('體驗版可填寫內容，暫不支援提交優惠券。',
                'You can fill in coupon details in the trial, but submission is unavailable.')),
            const SizedBox(height: 12),
          ],
          if (_shopsError != null) ...[Text(_shopsError!), TextButton(onPressed: _loadShops, child: const Text('重試'))],
          if (_shops == null && _shopsError == null) const LinearProgressIndicator(),
          if (_shops != null && _shops!.isEmpty) const Text('此帳戶尚未有可管理的商店。'),
          if (_shops != null && _shops!.isNotEmpty) DropdownButtonFormField<int>(
            initialValue: _shopId, decoration: const InputDecoration(labelText: '商店'),
            items: _shops!.map((shop) => DropdownMenuItem<int>(value: shop['id'] as int, child: Text(shop['name'] as String))).toList(),
            onChanged: (id) => setState(() {
              if (id != _shopId) _dirty = true;
              _shopId = id; _branchIds = null;
            }),
          ),
          const SizedBox(height: 12),
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
            onChanged: (v) => setState(() {
              final next = v ?? 'Discount';
              if (next != _type) _dirty = true;
              _type = next;
            }),
          ),
          const SizedBox(height: 12),
          if (_type == 'Discount')
            TextField(
                controller: _percentage,
                keyboardType: TextInputType.number,
                decoration: InputDecoration(labelText: context.tr(
                    '付款百分比（85 即 8.5 折）',
                    'Percent of regular price (85 = 15% off)'))),
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
          const SizedBox(height: 20),
          if (_shopId != null) BranchScopePicker(
            key: ValueKey(_shopId), api: widget.api, shopId: _shopId!, initial: const [],
            onChanged: (ids) => setState(() {
              if (_branchIds != null && !listEquals(_branchIds, ids)) _dirty = true;
              _branchIds = ids;
            }),
          ),
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
              if (picked != null) setState(() { _validTo = picked; _dirty = true; });
            },
          ),
          const SizedBox(height: 16),
          if (_createUncertain) const Padding(
            padding: EdgeInsets.only(bottom: 12),
            child: Text('提交結果未確認，請返回券列表刷新核對，勿重複建立。'),
          ),
          ElevatedButton(
            style:
                ElevatedButton.styleFrom(backgroundColor: CgColors.vendorGold),
            onPressed: _trialMode || _shopId == null || _branchIds == null || _submitting || _createUncertain ? null : () async {
              final session = _session;
              final draft = _body();
              final scope = _branchIds!.isEmpty ? '所有分店' : '指定 ${_branchIds!.length} 間分店';
              var creating = false;
              setState(() => _submitting = true);
              try {
                final preview = await widget.api.couponPreview(draft);
                if (!context.mounted || session != _session) return;
                final ok = await showDialog<bool>(
                  context: context,
                  builder: (c) => AlertDialog(
                    title: const Text('預覽優惠券'),
                    content: Text(
                        '${preview['coupon_name']}\n${preview['amount_text']}\n商店：${preview['shop_name']}\n適用：$scope\n數量：${preview['total']}'),
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
                if (ok == true && session == _session) {
                  creating = true;
                  final res = await widget.api.couponCreate(draft);
                  if (context.mounted && session == _session) {
                    showCgToast(context, res['message'] as String? ?? '已提交審核');
                    Navigator.of(context).pop();
                  }
                }
              } on ApiException catch (e) {
                if (context.mounted && session == _session) {
                  if (creating && e.status >= 500) {
                    setState(() => _createUncertain = true);
                  } else {
                    showCgToast(context, e.message);
                  }
                }
              } catch (_) {
                if (context.mounted && session == _session) {
                  if (creating) { setState(() => _createUncertain = true); }
                  else { showCgToast(context, '未能連線，請重試'); }
                }
              } finally {
                if (mounted && session == _session) setState(() => _submitting = false);
              }
            },
            child: Text(context.tr('檢查並提交', 'Review and submit'),
                style: const TextStyle(color: CgColors.textPrimary)),
          ),
        ],
      ),
        ),
      ),
    ));
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
  List<Map<String, dynamic>> _branchChoices = [];
  int? _selectedBranchId;
  String? _selectedBranchName;
  String? _branchError;
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
      _branchChoices = [];
      _selectedBranchId = null;
      _selectedBranchName = null;
      _branchError = null;
      _attemptKey = null;
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
    if (_handled ||
        _prepared != null ||
        _branchChoices.isNotEmpty ||
        _errorReason != null ||
        _successCouponName != null) {
      return;
    }
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
          _attemptKey = List.generate(16,
                  (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'))
              .join();
          _errorReason = null;
        } else if (res['branch_selection_required'] == true) {
          final branches = res['allowed_branches'];
          if (branches is List &&
              branches.isNotEmpty &&
              branches.every(
                  (b) => b is Map && b['id'] is int && b['name'] is String)) {
            _branchChoices = branches
                .map((b) => Map<String, dynamic>.from(b as Map))
                .toList();
            _pendingCode = code;
            _branchError = null;
          } else {
            _errorReason = '此券需要指定分店，但暫無可選分店，請聯絡平台。';
          }
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

  Future<void> _chooseBranch(Map<String, dynamic> branch) async {
    final code = _pendingCode;
    final id = branch['id'];
    if (_handled || code == null || id is! int) return;
    setState(() {
      _handled = true;
      _branchError = null;
    });
    try {
      final result = await widget.api.prepareScan(code, branchId: id);
      if (!mounted) return;
      setState(() {
        if (result['can_redeem'] == true) {
          _prepared = result;
          _selectedBranchId = id;
          _selectedBranchName = branch['name'] as String;
          final random = Random.secure();
          _attemptKey = List.generate(16,
                  (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'))
              .join();
        } else {
          _branchError = result['reason'] as String? ?? '此分店未能核銷，請重新選擇。';
        }
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _branchError = e.message);
    } catch (_) {
      if (mounted) setState(() => _branchError = '未能連線，請重試');
    } finally {
      if (mounted) setState(() => _handled = false);
    }
  }

  Future<void> _confirmConsume() async {
    final code = _pendingCode;
    final token = _prepared?['prepare_token'] as String?;
    if (_handled || code == null || token == null || _attemptKey == null) {
      return;
    }
    setState(() => _handled = true);
    try {
      final res = await widget.api.scanCoupon(code,
          prepareToken: token,
          branchId: _selectedBranchId,
          idempotencyKey: _attemptKey!);
      if (!mounted) return;
      setState(() {
        _successCouponName = res['coupon_name'] as String? ??
            _prepared?['coupon_name'] as String? ??
            '';
        _lastRedemptionId = res['redemption_id'] as int?;
        _revokeSeconds = ((res['revoke_window_minutes'] as int?) ?? 10) * 60;
        _redeemedAt = DateTime.tryParse(res['scanned_at'] as String? ?? '') ??
            DateTime.now();
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
          Icon(_revokedMessage == null ? Icons.check_circle : Icons.undo,
              size: 72,
              color: _revokedMessage == null
                  ? CgColors.success
                  : CgColors.textSecondary),
          const SizedBox(height: 16),
          Text(_revokedMessage ?? '已核銷',
              textAlign: TextAlign.center,
              style:
                  const TextStyle(fontSize: 24, fontWeight: FontWeight.bold)),
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
              child: const Text('繼續掃碼', style: TextStyle(color: Colors.red)),
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
                    if (_prepared == null && _branchChoices.isEmpty)
                      SizedBox(
                        height: MediaQuery.sizeOf(context).height * .48,
                        child: Stack(
                          children: [
                            MobileScanner(
                              errorBuilder: (_, __, ___) => const ColoredBox(
                                color: Colors.black,
                                child: Center(
                                    child: Text('相機未能使用，請手動輸入兌換碼',
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
                    if (_prepared == null && _branchChoices.isEmpty)
                      const Padding(
                        padding: EdgeInsets.all(8),
                        child: Text('請將 QR 碼放入方框中並進行掃描\n先預核銷確認券面與門店，再按確認核銷'),
                      ),
                    if (_prepared == null && _branchChoices.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text('選擇核銷分店',
                                  style:
                                      Theme.of(context).textTheme.titleLarge),
                              const SizedBox(height: 8),
                              const Text('此券只適用於以下分店，請選擇顧客所在分店。'),
                              if (_branchError != null)
                                Text(_branchError!,
                                    style: TextStyle(
                                        color: Theme.of(context)
                                            .colorScheme
                                            .error)),
                              for (final branch in _branchChoices)
                                ListTile(
                                  title: Text(branch['name'] as String),
                                  subtitle: branch['address'] is String &&
                                          (branch['address'] as String)
                                              .isNotEmpty
                                      ? Text(branch['address'] as String)
                                      : null,
                                  trailing: const Icon(Icons.chevron_right),
                                  enabled: !_handled,
                                  onTap: () => _chooseBranch(branch),
                                ),
                              TextButton(
                                  onPressed: _handled ? null : _resetScan,
                                  child: const Text('取消')),
                            ]),
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
                                        fontWeight: FontWeight.bold,
                                        fontSize: 16)),
                                const SizedBox(height: 8),
                                _detailRow(
                                    '門店', '${_prepared!['shop_name'] ?? ''}'),
                                if (_selectedBranchName != null)
                                  _detailRow('核銷分店', _selectedBranchName!),
                                // Customer phone last-4 / validity come
                                // from prepare_scan when the backend
                                // sends them; '—' until then.
                                _detailRow(
                                    '顧客手機後四位',
                                    _prepared!['customer_phone_last4']
                                            as String? ??
                                        '—'),
                                _detailRow(
                                    '有效期',
                                    _dateText(
                                        _prepared!['valid_to'] as String?)),
                                if (_prepared!['package_quantity'] != null)
                                  _detailRow('整券核銷',
                                      "${_prepared!['package_quantity']} 份"),
                                _detailRow('是否已使用', '未使用'),
                                const SizedBox(height: 12),
                                Row(
                                  children: [
                                    Expanded(
                                      child: ElevatedButton(
                                        onPressed:
                                            _handled ? null : _confirmConsume,
                                        child: Text(_handled ? '核銷中…' : '確認核銷'),
                                      ),
                                    ),
                                    const SizedBox(width: 8),
                                    TextButton(
                                      onPressed: _handled
                                          ? null
                                          : () => setState(() {
                                                _prepared = null;
                                                _pendingCode = null;
                                                _branchChoices = [];
                                                _selectedBranchId = null;
                                                _selectedBranchName = null;
                                                _branchError = null;
                                                _attemptKey = null;
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
                    if (_prepared == null && _branchChoices.isEmpty)
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 16),
                        child: OutlinedButton(
                          onPressed: _handled ? null : () => Navigator.of(context).push(
                            MaterialPageRoute(builder: (_) => ScanBatchScreen(api: widget.api))),
                          child: const Text('批量核銷'),
                        ),
                      ),
                    if (_prepared == null && _branchChoices.isEmpty)
                      Padding(
                        padding: const EdgeInsets.all(16),
                        child: Row(
                          children: [
                            Expanded(
                              child: TextField(
                                controller: _code,
                                inputFormatters: [
                                  FilteringTextInputFormatter.digitsOnly,
                                  LengthLimitingTextInputFormatter(6)
                                ],
                                onSubmitted: _prepare,
                                keyboardType: TextInputType.number,
                                decoration:
                                    const InputDecoration(hintText: '或手動輸入兌換碼'),
                              ),
                            ),
                            const SizedBox(width: 8),
                            ElevatedButton(
                              style: ElevatedButton.styleFrom(
                                  minimumSize: const Size(88, 56)),
                              onPressed: _handled
                                  ? null
                                  : () => _prepare(_code.text),
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
      final records = await widget.api.vendorRedemptions(from: _from, to: _to);
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
      if (mounted) {
        showCgToast(context, e is ApiException ? e.message : '未能確認撤銷結果，請重試');
      }
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
                onPressed: () => Navigator.pop(c), child: const Text('關閉')),
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
                                final deadline = DateTime.tryParse(
                                    r['revoke_until'] as String? ?? '');
                                final canRevoke = !revoked &&
                                    deadline != null &&
                                    deadline.isAfter(DateTime.now());
                                return Card(
                                  margin:
                                      const EdgeInsets.fromLTRB(12, 8, 12, 0),
                                  child: ListTile(
                                    title: Text('${r['coupon_name'] ?? ''}'),
                                    subtitle: Text(
                                        '${r['shop_name'] ?? ''} · ${r['scanned_at'] ?? ''}'),
                                    trailing: canRevoke
                                        ? TextButton(
                                            onPressed: _revokingId == null
                                                ? () => _revokeRecord(r)
                                                : null,
                                            child: const Text('撤銷核銷'),
                                          )
                                        : Text(revoked ? '已撤銷' : '已核銷',
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
