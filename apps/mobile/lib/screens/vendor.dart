import 'dart:async';
import 'dart:math';

import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../api_client.dart';
import '../vendor_coupon_lifecycle.dart';
import '../models.dart';
import '../language.dart';
import '../theme.dart';
import '../widgets/merchant_layout.dart';
import '../widgets/widgets.dart';
import 'merchant_review_shops.dart';
import 'vendor_packages.dart';
import 'coupon_branch_scope.dart';
import 'redemption_recovery.dart';
import 'merchant_dashboard.dart';
import 'merchant_account_overview.dart';
import 'scan_batch.dart';
import 'merchant_branches.dart';
import 'merchant_staff.dart';

/// Vendor home: usage summary and coupon-management entry.
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
  bool _allows(String permission) =>
      widget.showAdmin || _shops.any((shop) => shop[permission] == true);

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
      final shops = (data['shops'] as List? ?? [])
          .whereType<Map>()
          .map((s) => Map<String, dynamic>.from(s))
          .toList();
      final isOwner =
          widget.showAdmin || shops.any((s) => s['is_owner'] == true);
      final canManage =
          widget.showAdmin || shops.any((s) => s['can_manage_coupons'] == true);
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
    final read =
        _couponStatus == null ? _overviewCouponRead : _filteredCouponRead;
    return receipt != null && receipt.$1 > read ? receipt.$2 : null;
  }

  bool _canChangeCoupon(Coupon coupon) =>
      widget.api.isLoggedIn &&
      coupon.id > 0 &&
      (widget.showAdmin ||
          _shops.any((shop) =>
              shop['id'] == coupon.shopId &&
              shop['can_manage_coupons'] == true));

  String _actionLabel(BuildContext context, VendorCouponAction action) =>
      switch (action) {
        VendorCouponAction.pause => context.tr('暫停', 'Pause'),
        VendorCouponAction.resume => context.tr('恢復上架', 'Resume'),
        VendorCouponAction.unlist => context.tr('下架', 'Unlist'),
        VendorCouponAction.submit => context.tr('提交審核', 'Submit for review'),
      };

  Future<void> _changeCoupon(Coupon coupon, VendorCouponAction action) async {
    final session = _session;
    final api = widget.api;
    bool current() =>
        mounted &&
        identical(api, widget.api) &&
        session == _session &&
        session == api.sessionRevision;
    bool canSubmit() {
      if (!current() ||
          !_canChangeCoupon(coupon) ||
          _savingCoupons.contains(coupon.id)) {
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
            TextButton(
                onPressed: () => Navigator.of(dialog).pop(false),
                child: Text(dialog.tr('取消', 'Cancel'))),
            TextButton(
                onPressed: () => Navigator.of(dialog).pop(true),
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
            : context.tr('未能確認優惠券狀態，請重新整理。',
                'Could not confirm coupon status. Refresh the list.'),
      );
      await _refresh();
    } finally {
      if (current()) setState(() => _savingCoupons.remove(coupon.id));
    }
  }

  @override
  Widget build(BuildContext context) {
    final shownCoupons = (_couponStatus == null ? _coupons : _filteredCoupons)
        .where(
          (coupon) =>
              _couponStatus == null ||
              _pendingStatus(coupon) == null ||
              _pendingStatus(coupon) == _couponStatus,
        )
        .toList();
    final colors = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(
        leading: widget.onOpenMenu == null
            ? null
            : IconButton(
                icon: const Icon(Icons.menu),
                tooltip: MaterialLocalizations.of(context).openAppDrawerTooltip,
                onPressed: widget.onOpenMenu,
              ),
        title: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (MediaQuery.sizeOf(context).width >= 360) ...[
              Image.asset(
                'assets/images/img_logo.png',
                height: 32,
                excludeFromSemantics: true,
                errorBuilder: (_, __, ___) => const SizedBox.shrink(),
              ),
              const SizedBox(width: CgTheme.space8),
            ],
            Flexible(
              child: Text(
                context.tr('企業版', 'Business'),
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
              ),
            ),
          ],
        ),
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: LayoutBuilder(
          builder: (context, viewport) {
            const gutter = CgTheme.pageInset;
            final side = gutter +
                max(0.0, (viewport.maxWidth - CgTheme.listMaxWidth) / 2);
            final primary = <Widget>[
              if (_allows('can_manage_coupons'))
                MerchantPrimaryAction(
                  onPressed: () => Navigator.of(context)
                      .pushNamed('/vendor/issue')
                      .then((_) async {
                    if (mounted) await _refresh();
                  }),
                  icon: Icons.add,
                  label: context.tr('發券', 'Issue coupon'),
                ),
            ];
            final operations = <Widget>[
              if (_allows('is_owner'))
                OutlinedButton.icon(
                  style: _toolButtonStyle,
                  onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute(
                      builder: (_) => VendorPackagesScreen(api: widget.api),
                    ),
                  ),
                  icon: const Icon(Icons.restaurant_menu),
                  label: _toolLabel(context.tr('套餐管理', 'Packages')),
                ),
              if (_allows('can_view_settlement'))
                OutlinedButton.icon(
                  style: _toolButtonStyle,
                  onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute(
                      builder: (_) => MerchantDashboardScreen(api: widget.api),
                    ),
                  ),
                  icon: const Icon(Icons.insights_outlined),
                  label: _toolLabel(context.tr('經營與餘額', 'Overview')),
                ),
              if (_allows('is_owner'))
                OutlinedButton.icon(
                  style: _toolButtonStyle,
                  onPressed: () => Navigator.of(context).push(
                    MaterialPageRoute(
                      builder: (_) =>
                          MerchantRedemptionRecoveryScreen(api: widget.api),
                    ),
                  ),
                  icon: const Icon(Icons.manage_search),
                  label: _toolLabel(context.tr('補錄核銷', 'Record use')),
                ),
              OutlinedButton.icon(
                style: _toolButtonStyle,
                onPressed: () => Navigator.of(context).push(
                  MaterialPageRoute(
                    builder: (_) => MerchantBranchesScreen(
                      api: widget.api,
                      staffBuilder: (shopId) =>
                          MerchantStaffScreen(api: widget.api, shopId: shopId),
                    ),
                  ),
                ),
                icon: const Icon(Icons.storefront_outlined),
                label: _toolLabel(context.tr('商店與分店', 'Branches')),
              ),
              if (_allows('is_owner'))
                OutlinedButton.icon(
                  style: _toolButtonStyle,
                  onPressed: () =>
                      Navigator.of(context).pushNamed('/vendor/records'),
                  icon: const Icon(Icons.receipt_long_outlined),
                  label: _toolLabel(context.tr('核銷記錄', 'History')),
                ),
              if (!_loading &&
                  _loadError == null &&
                  MerchantReviewShopPicker.allowedShops(
                    _shops,
                    canAdministerAllShops: widget.showAdmin,
                  ).isNotEmpty)
                OutlinedButton.icon(
                  style: _toolButtonStyle,
                  key: const Key('vendor-review-shops'),
                  onPressed: () {
                    final api = widget.api;
                    final session = _session;
                    if (session != api.sessionRevision) return;
                    final roster = _shops;
                    final admin = widget.showAdmin;
                    Navigator.of(context).push(
                      MaterialPageRoute<void>(
                        builder: (_) => MerchantReviewShopPicker(
                          api: api,
                          shopRoster: roster,
                          sessionRevision: session,
                          canAdministerAllShops: admin,
                        ),
                      ),
                    );
                  },
                  icon: const Icon(Icons.reviews_outlined),
                  label: _toolLabel(
                    const bool.fromEnvironment('PUBLIC_TRIAL')
                        ? context.tr('顧客評價', 'Reviews')
                        : context.tr('評價回覆（差評）', 'Reply to reviews'),
                  ),
                ),
              if (widget.showAdmin)
                OutlinedButton(
                  style: _toolButtonStyle,
                  onPressed: () => Navigator.of(context).pushNamed('/admin'),
                  child: _toolLabel(context.tr('管理後台', 'Admin tools')),
                ),
              if (_allows('can_view_settlement'))
                OutlinedButton.icon(
                  style: _toolButtonStyle,
                  onPressed: () =>
                      Navigator.of(context).pushNamed('/vendor/settlements'),
                  icon: const Icon(Icons.description_outlined),
                  label: _toolLabel(context.tr('商家結算單', 'Settlements')),
                ),
            ];
            return ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: EdgeInsets.fromLTRB(side, gutter, side, gutter * 2),
              children: [
                if (_loading) const Center(child: CircularProgressIndicator()),
                if (_loadError != null) ...[
                  Text(_loadError!),
                  TextButton(
                    onPressed: _refresh,
                    child: Text(context.tr('重試', 'Retry')),
                  ),
                ],
                if (_usage != null && _allows('is_owner'))
                  Row(
                    children: [
                      Expanded(
                        child: _stat(
                          context.tr('總下載次數', 'Total downloads'),
                          '${_usage?['total_downloads'] ?? 0}',
                        ),
                      ),
                      const SizedBox(width: CgTheme.space12),
                      Expanded(
                        child: _stat(
                          context.tr('總使用量', 'Total redemptions'),
                          '${_usage?['total_usage'] ?? 0}',
                        ),
                      ),
                    ],
                  ),
                if (primary.isNotEmpty) ...[
                  const SizedBox(height: CgTheme.space16),
                  ...primary,
                ],
                if (operations.isNotEmpty) ...[
                  const SizedBox(height: CgTheme.space20),
                  Text(
                    context.tr('商戶工具', 'Business tools'),
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  const SizedBox(height: CgTheme.space8),
                  LayoutBuilder(
                    builder: (context, grid) {
                      final scale =
                          MediaQuery.textScalerOf(context).scale(14) / 14;
                      final two = grid.maxWidth >= 340 * max(1.0, scale);
                      final width = two
                          ? (grid.maxWidth - CgTheme.space8) / 2
                          : grid.maxWidth;
                      return Wrap(
                        key: const ValueKey('vendor-tools'),
                        spacing: CgTheme.space8,
                        runSpacing: CgTheme.space8,
                        children: [
                          for (final item in operations)
                            SizedBox(
                              width: width,
                              height: max(
                                CgTheme.buttonHeight,
                                20 * scale + CgTheme.space16,
                              ),
                              child: item,
                            ),
                        ],
                      );
                    },
                  ),
                  const SizedBox(height: CgTheme.space20),
                ],
                if (_allows('can_manage_coupons')) ...[
                  Text(
                    context.tr('我的優惠券', 'My coupons'),
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  DropdownButton<String>(
                    key: const ValueKey('vendor-coupon-status-filter'),
                    value: _couponStatus ?? '',
                    isExpanded: true,
                    onChanged: (value) {
                      if (value != null) {
                        _filterCoupons(value.isEmpty ? null : value);
                      }
                    },
                    items: [
                      DropdownMenuItem(
                        value: '',
                        child: Text(context.tr('全部狀態', 'All statuses')),
                      ),
                      for (final status in [
                        'pending_review',
                        'active',
                        'paused',
                        'draft',
                        'rejected',
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
                    TextButton(
                      onPressed: () => _filterCoupons(_couponStatus),
                      child: Text(context.tr('重試', 'Retry')),
                    ),
                  ],
                ],
                if (_allows('can_manage_coupons'))
                  ...shownCoupons.map((c) {
                    final status = _pendingStatus(c) ?? c.status;
                    final actions = _canChangeCoupon(c)
                        ? VendorCouponAction.forStatus(status)
                        : const <VendorCouponAction>[];
                    return Card(
                      margin: const EdgeInsets.only(top: CgTheme.space8),
                      child: ListTile(
                        contentPadding: const EdgeInsets.symmetric(
                            horizontal: CgTheme.pageInset),
                        leading: Icon(
                          Icons.local_offer_outlined,
                          color: colors.primary,
                        ),
                        title: Text(c.name),
                        subtitle: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            if (status.isNotEmpty)
                              MerchantStatusPill(
                                labelKey:
                                    ValueKey('vendor-coupon-status-${c.id}'),
                                label: _statusLabel(context, status),
                                active: status == 'active',
                              ),
                            Text(
                              context.tr(
                                '剩餘 ${c.remaining} · 已核銷 ${c.usedCount ?? '—'}',
                                '${c.remaining} left · ${c.usedCount ?? '—'} redeemed',
                              ),
                            ),
                          ],
                        ),
                        trailing: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            IconButton(
                              tooltip: context.tr(
                                '編輯適用分店',
                                'Edit eligible branches',
                              ),
                              icon: const Icon(Icons.storefront_outlined),
                              onPressed: () => Navigator.of(context).push(
                                MaterialPageRoute(
                                  builder: (_) => CouponBranchEditScreen(
                                    api: widget.api,
                                    couponId: c.id,
                                    shopId: c.shopId,
                                    couponName: c.name,
                                  ),
                                ),
                              ),
                            ),
                            if (actions.isNotEmpty)
                              PopupMenuButton<VendorCouponAction>(
                                key: ValueKey('vendor-coupon-actions-${c.id}'),
                                enabled: !_savingCoupons.contains(c.id),
                                tooltip: context.tr('管理優惠券', 'Manage coupon'),
                                onSelected: (action) =>
                                    _changeCoupon(c, action),
                                itemBuilder: (menuContext) => [
                                  for (final action in actions)
                                    PopupMenuItem(
                                      value: action,
                                      child: Text(
                                        _actionLabel(menuContext, action),
                                      ),
                                    ),
                                ],
                              ),
                          ],
                        ),
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
                    child: Text(
                      _couponStatus == null
                          ? context.tr('還沒有優惠券。', 'No coupons yet.')
                          : context.tr(
                              '這個狀態沒有優惠券。',
                              'No coupons with this status.',
                            ),
                    ),
                  ),
              ],
            );
          },
        ),
      ),
    );
  }

  static final _toolButtonStyle = OutlinedButton.styleFrom(
    padding: const EdgeInsets.symmetric(horizontal: CgTheme.space8),
  );

  Widget _toolLabel(String label) => FittedBox(
        fit: BoxFit.scaleDown,
        child: Text(label, maxLines: 1),
      );

  Widget _stat(String label, String value) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(CgTheme.pageInset),
        child: Column(
          children: [
            Text(value, style: Theme.of(context).textTheme.headlineSmall),
            Text(
              label,
              textAlign: TextAlign.center,
              style: TextStyle(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class VendorAccountScreen extends StatelessWidget {
  const VendorAccountScreen({super.key, required this.api});

  final ApiClient api;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('商戶帳戶', 'Merchant account'))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: CgTheme.listMaxWidth),
          child: ListView(
            padding: const EdgeInsets.symmetric(horizontal: CgTheme.pageInset),
            children: [
              MerchantAccountOverview(api: api),
              const SizedBox(height: CgTheme.space8),
              Card(
                margin: EdgeInsets.zero,
                child: ListTile(
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: CgTheme.pageInset),
                  leading: const Icon(Icons.credit_card),
                  title: Text(context.tr('訂閱方案', 'Subscription plans')),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.of(context).pushNamed('/vendor/plans'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Issue coupon: two-step — fill form -> preview -> submit for review.
class IssueCouponScreen extends StatefulWidget {
  const IssueCouponScreen({super.key, required this.api, this.now});

  final ApiClient api;
  final DateTime Function()? now;

  @override
  State<IssueCouponScreen> createState() => _IssueCouponScreenState();
}

class _IssueCouponScreenState extends State<IssueCouponScreen> {
  final _name = TextEditingController();
  final _nameEn = TextEditingController();
  final _perUserLimit = TextEditingController(text: '1');
  List<Map<String, dynamic>>? _categories;
  int? _categoryId;
  bool _categoriesFailed = false;

  int? get _validatedLimit {
    final text = _perUserLimit.text.trim();
    if (!RegExp(r'^[0-9]+$').hasMatch(text)) return null;
    final value = int.tryParse(text);
    return value != null && value > 0 ? value : null;
  }

  String _type = 'Discount';
  final _percentage = TextEditingController(text: '85');
  final _cash = TextEditingController();
  final _salePrice = TextEditingController();
  final _gift = TextEditingController();
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
  DateTime? _validFrom;
  late DateTime _validTo;
  String? _salePriceError;
  String? _dateError;
  static const _hkOffset = Duration(hours: 8);

  DateTime _nowUtc() => (widget.now?.call() ?? DateTime.now()).toUtc();

  DateTime _hongKongClock(DateTime instant) => instant.toUtc().add(_hkOffset);

  DateTime _fromHongKong(DateTime day, TimeOfDay clock) =>
      DateTime.utc(day.year, day.month, day.day, clock.hour, clock.minute)
          .subtract(_hkOffset);

  String _formatHongKong(DateTime instant) => _hongKongClock(instant)
      .toIso8601String()
      .substring(0, 16)
      .replaceFirst('T', ' ');

  double? _parseSalePrice() {
    final text = _salePrice.text.trim();
    if (!RegExp(r'^\d+(?:\.\d{1,2})?$').hasMatch(text)) return null;
    final price = double.tryParse(text);
    return price != null && price.isFinite && price > 0 && price <= 99999999.99
        ? price
        : null;
  }

  @override
  void initState() {
    super.initState();
    _validTo = _nowUtc().add(const Duration(days: 30));
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    for (final controller in [
      _name,
      _nameEn,
      _perUserLimit,
      _percentage,
      _cash,
      _salePrice,
      _gift,
      _total,
      _minSpend,
      _terms,
    ]) {
      controller.addListener(_markDirty);
    }
    _loadShops();
    _loadCategories();
  }

  String _categoryLabel(Map<String, dynamic> row) => context.tr(
        row['name'] as String,
        row['name_en'] is String && (row['name_en'] as String).trim().isNotEmpty
            ? row['name_en'] as String
            : row['name'] as String,
      );

  Future<void> _loadCategories() async {
    setState(() => _categoriesFailed = false);
    try {
      final payload = await widget.api.get('/get-category-list');
      final rows = payload['categories'];
      if (rows is! List) throw const FormatException();
      final ids = <int>{};
      final categories = <Map<String, dynamic>>[];
      for (final row in rows) {
        if (row is! Map ||
            row['id'] is! int ||
            row['name'] is! String ||
            (row['name'] as String).trim().isEmpty ||
            !ids.add(row['id'] as int)) {
          throw const FormatException();
        }
        categories.add(Map<String, dynamic>.from(row));
      }
      if (!mounted) return;
      setState(() => _categories = categories);
    } catch (_) {
      if (!mounted) return;
      setState(() => _categoriesFailed = true);
    }
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
              TextButton(
                  onPressed: () => Navigator.pop(dialogContext, false),
                  child: Text(context.tr('繼續編輯', 'Keep editing'))),
              TextButton(
                  onPressed: () => Navigator.pop(dialogContext, true),
                  child: Text(context.tr('放棄', 'Discard'))),
            ],
          ),
        ) ??
        false;
  }

  void _sessionChanged() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    if (mounted) {
      setState(() {
        _shops = null;
        _shopId = null;
        _branchIds = null;
        _submitting = false;
        _createUncertain = false;
        _shopsError = context.tr(
            '登入狀態已變更，請重新讀取商店。', 'Your session changed. Reload shops.');
      });
    }
  }

  Future<void> _loadShops() async {
    final session = _session;
    setState(() {
      _shops = null;
      _shopsError = null;
      _shopId = null;
      _branchIds = null;
    });
    try {
      final data = await widget.api.get('/merchant/shops');
      final rows = data['shops'];
      if (rows is! List ||
          rows.any(
              (v) => v is! Map || v['id'] is! int || v['name'] is! String)) {
        throw const FormatException();
      }
      if (!mounted || session != _session) return;
      setState(() {
        _shops = rows
            .where((v) => (v as Map)['can_manage_coupons'] == true)
            .map((v) => Map<String, dynamic>.from(v as Map))
            .toList();
        if (_shops!.isNotEmpty) _shopId = _shops!.first['id'] as int;
      });
    } catch (e) {
      if (mounted && session == _session) {
        setState(() => _shopsError = e is ApiException
            ? e.message
            : context.tr('未能讀取商店，請重試', 'Could not load shops. Try again.'));
      }
    }
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    for (final controller in [
      _name,
      _nameEn,
      _perUserLimit,
      _percentage,
      _cash,
      _salePrice,
      _gift,
      _total,
      _minSpend,
      _terms,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _pickValidFrom() async {
    final session = _session;
    final today = _hongKongClock(_nowUtc());
    final previous = _validFrom == null ? today : _hongKongClock(_validFrom!);
    final initial = previous.isBefore(today) ? today : previous;
    final day = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: today,
      currentDate: today,
      lastDate: today.add(const Duration(days: 365)),
    );
    if (day == null || !mounted || session != _session) return;
    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(initial),
    );
    if (time == null || !mounted || session != _session) return;
    setState(() {
      _validFrom = _fromHongKong(day, time);
      _dateError = null;
      _dirty = true;
    });
  }

  Map<String, dynamic> _body(DateTime validFrom, double? salePrice) => {
        'shop_id': _shopId,
        'branch_ids': List<int>.unmodifiable(_branchIds!),
        'coupon_type': _type,
        'coupon_name': _name.text,
        'name_en': _nameEn.text.trim(),
        if (_categoryId != null) 'category_id': _categoryId,
        'per_user_limit': _validatedLimit,
        'percentage':
            _type == 'Discount' ? double.tryParse(_percentage.text) : null,
        'cash_amount': _type == 'Cash' ? double.tryParse(_cash.text) : null,
        if (_type == 'Price') 'sale_price': salePrice,
        if (_type == 'Gift') 'gift': _gift.text.trim(),
        'min_spend': double.tryParse(_minSpend.text) ?? 0,
        'total': int.tryParse(_total.text) ?? 100,
        'additional_terms': _terms.text,
        'valid_from': validFrom.toIso8601String(),
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
            title: Text(context.tr('發券', 'Issue coupon')),
          ),
          body: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: CgTheme.formMaxWidth),
              child: ListView(
                padding: const EdgeInsets.all(CgTheme.pageInset),
                children: [
                  Text(context.tr('券基本資料', 'Coupon details'),
                      style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: CgTheme.space12),
                  if (_shopsError != null) ...[
                    Text(_shopsError!),
                    TextButton(
                        onPressed: _loadShops,
                        child: Text(context.tr('重試', 'Retry')))
                  ],
                  if (_shops == null && _shopsError == null)
                    const LinearProgressIndicator(),
                  if (_shops != null && _shops!.isEmpty)
                    Text(
                        context.tr('此帳戶尚未有可管理的商店。', 'No shops to manage yet.')),
                  if (_shops != null && _shops!.isNotEmpty)
                    DropdownButtonFormField<int>(
                      initialValue: _shopId,
                      decoration:
                          InputDecoration(labelText: context.tr('商店', 'Shop')),
                      items: _shops!
                          .map((shop) => DropdownMenuItem<int>(
                              value: shop['id'] as int,
                              child: Text(shop['name'] as String)))
                          .toList(),
                      onChanged: (id) => setState(() {
                        if (id != _shopId) _dirty = true;
                        _shopId = id;
                        _branchIds = null;
                      }),
                    ),
                  const SizedBox(height: CgTheme.space12),
                  TextField(
                      controller: _name,
                      decoration: InputDecoration(
                          labelText: context.tr('優惠券名稱', 'Coupon name'))),
                  const SizedBox(height: CgTheme.space12),
                  TextField(
                    controller: _nameEn,
                    maxLength: 160,
                    decoration: InputDecoration(
                        labelText: context.tr(
                            '優惠券英文名稱（選填）', 'English coupon name (optional)')),
                  ),
                  if (_categoriesFailed)
                    TextButton(
                      onPressed: _loadCategories,
                      child: Text(context.tr('重新讀取分類', 'Reload categories')),
                    ),
                  if (_categories?.isNotEmpty == true) ...[
                    const SizedBox(height: CgTheme.space12),
                    DropdownButtonFormField<int>(
                      key: const ValueKey('issue-category'),
                      initialValue: _categoryId,
                      isExpanded: true,
                      decoration: InputDecoration(
                          labelText: context.tr(
                              '優惠券分類（選填）', 'Coupon category (optional)')),
                      items: [
                        DropdownMenuItem<int>(
                          child: Text(context.tr('不設定分類', 'No category')),
                        ),
                        for (final row in _categories!)
                          DropdownMenuItem<int>(
                            value: row['id'] as int,
                            child: Text(_categoryLabel(row)),
                          ),
                      ],
                      onChanged: (value) {
                        if (value == _categoryId) return;
                        setState(() {
                          _categoryId = value;
                          _dirty = true;
                        });
                      },
                    ),
                  ],
                  const SizedBox(height: CgTheme.space24),
                  Text(context.tr('價格與數量', 'Price and quantity'),
                      style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: CgTheme.space12),
                  TextField(
                    controller: _perUserLimit,
                    keyboardType: TextInputType.number,
                    onChanged: (_) => setState(() {}),
                    decoration: InputDecoration(
                      labelText: context.tr('每人限領', 'Claim limit per person'),
                      errorText: _validatedLimit == null
                          ? context.tr(
                              '請輸入大於 0 的整數', 'Enter a positive whole number')
                          : null,
                    ),
                  ),
                  const SizedBox(height: CgTheme.space12),
                  DropdownButtonFormField<String>(
                    initialValue: _type,
                    decoration: InputDecoration(
                        labelText: context.tr('券類型', 'Coupon type')),
                    items: [
                      DropdownMenuItem(
                          value: 'Cash',
                          child: Text(context.tr('現金優惠券', 'Cash coupon'))),
                      DropdownMenuItem(
                          value: 'Discount',
                          child: Text(context.tr('折扣優惠券', 'Discount coupon'))),
                      DropdownMenuItem(
                          value: 'Gift',
                          child: Text(context.tr('贈品優惠券', 'Gift coupon'))),
                      DropdownMenuItem(
                          value: 'Price',
                          child: Text(context.tr('付費優惠券', 'Paid coupon'))),
                    ],
                    onChanged: (v) => setState(() {
                      final next = v ?? 'Discount';
                      if (next != _type) _dirty = true;
                      _type = next;
                      _salePriceError = null;
                    }),
                  ),
                  const SizedBox(height: CgTheme.space12),
                  if (_type == 'Discount')
                    TextField(
                        controller: _percentage,
                        keyboardType: TextInputType.number,
                        decoration: InputDecoration(
                            labelText: context.tr('付款比例（85 即 8.5 折）',
                                'Percent of regular price (85 = 15% off)'))),
                  if (_type == 'Cash')
                    TextField(
                        controller: _cash,
                        keyboardType: TextInputType.number,
                        decoration: InputDecoration(
                            labelText: context.tr('現金金額', 'Cash amount'))),
                  if (_type == 'Price')
                    TextField(
                      key: const ValueKey('issue-sale-price'),
                      controller: _salePrice,
                      keyboardType:
                          const TextInputType.numberWithOptions(decimal: true),
                      decoration: InputDecoration(
                        labelText: context.tr('售價（港幣）', 'Sale price (HKD)'),
                        errorText: _salePriceError,
                      ),
                      onChanged: (_) {
                        if (_salePriceError != null) {
                          setState(() => _salePriceError = null);
                        }
                      },
                    ),
                  if (_type == 'Gift')
                    TextField(
                      key: const ValueKey('issue-gift'),
                      controller: _gift,
                      decoration: InputDecoration(
                        labelText: context.tr('贈品內容', 'Gift details'),
                      ),
                    ),
                  const SizedBox(height: CgTheme.space12),
                  TextField(
                      controller: _total,
                      keyboardType: TextInputType.number,
                      decoration: InputDecoration(
                          labelText: context.tr('數量', 'Quantity'))),
                  const SizedBox(height: CgTheme.space12),
                  TextField(
                      controller: _minSpend,
                      keyboardType: TextInputType.number,
                      decoration: InputDecoration(
                          labelText: context.tr('需消費金額', 'Minimum spend'))),
                  const SizedBox(height: CgTheme.space24),
                  Text(context.tr('有效期與使用條款', 'Validity and terms'),
                      style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: CgTheme.space12),
                  TextField(
                      controller: _terms,
                      decoration: InputDecoration(
                          labelText:
                              context.tr('條款和條件', 'Terms and conditions'))),
                  if (_shopId != null) const SizedBox(height: CgTheme.space12),
                  if (_shopId != null)
                    BranchScopePicker(
                      key: ValueKey(_shopId),
                      api: widget.api,
                      shopId: _shopId!,
                      initial: const [],
                      onChanged: (ids) => setState(() {
                        if (_branchIds != null && !listEquals(_branchIds, ids)) {
                          _dirty = true;
                        }
                        _branchIds = ids;
                      }),
                    ),
                  const SizedBox(height: CgTheme.space12),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    key: const ValueKey('issue-valid-from'),
                    title: Text(context.tr(
                        '生效時間（香港時間）', 'Start time (Hong Kong time)')),
                    subtitle: Text(_validFrom == null
                        ? context.tr('審核通過後上架', 'List after approval')
                        : _formatHongKong(_validFrom!)),
                    trailing: _validFrom == null
                        ? const Icon(Icons.schedule)
                        : IconButton(
                            tooltip:
                                context.tr('取消定時上架', 'Remove scheduled start'),
                            onPressed: _submitting
                                ? null
                                : () => setState(() {
                                      _validFrom = null;
                                      _dateError = null;
                                      _dirty = true;
                                    }),
                            icon: const Icon(Icons.clear),
                          ),
                    onTap: _submitting ? null : _pickValidFrom,
                  ),
                  const SizedBox(height: CgTheme.space12),
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: Text(context.tr(
                        '到期日期（香港時間）', 'Expiry date (Hong Kong time)')),
                    subtitle: Text(_formatHongKong(_validTo)),
                    trailing: const Icon(Icons.calendar_today),
                    onTap: _submitting
                        ? null
                        : () async {
                            final session = _session;
                            final today = _hongKongClock(_nowUtc());
                            final previous = _hongKongClock(_validTo);
                            final picked = await showDatePicker(
                              context: context,
                              initialDate:
                                  previous.isBefore(today) ? today : previous,
                              firstDate: today,
                              currentDate: today,
                              lastDate: today.add(const Duration(days: 365)),
                            );
                            if (picked != null &&
                                mounted &&
                                session == _session) {
                              setState(() {
                                _validTo = _fromHongKong(picked,
                                    const TimeOfDay(hour: 0, minute: 0));
                                _dateError = null;
                                _dirty = true;
                              });
                            }
                          },
                  ),
                  if (_dateError != null)
                    Text(_dateError!,
                        style: TextStyle(
                            color: Theme.of(context).colorScheme.error)),
                  const SizedBox(height: CgTheme.space16),
                  if (_createUncertain)
                    Padding(
                      padding: const EdgeInsets.only(bottom: CgTheme.space12),
                      child: Text(context.tr(
                        '提交結果未確認，請重新載入優惠券列表核對。',
                        'Refresh the coupon list to check whether the coupon was submitted.',
                      )),
                    ),
                  MerchantPrimaryAction(
                    onPressed: _shopId == null ||
                            _branchIds == null ||
                            _submitting ||
                            _createUncertain
                        ? null
                        : () async {
                            final session = _session;
                            if (_validatedLimit == null) {
                              showCgToast(
                                  context,
                                  context.tr('請輸入每人限領數量',
                                      'Enter the claim limit per person'));
                              return;
                            }
                            final now = _nowUtc();
                            final scheduledStart = _validFrom;
                            final validFrom = scheduledStart ?? now;
                            final price =
                                _type == 'Price' ? _parseSalePrice() : null;
                            final priceError = _type == 'Price' && price == null
                                ? context.tr('請輸入有效售價（最少 HK\$0.01，最多兩位小數）',
                                    'Enter a valid sale price (at least HK\$0.01, up to two decimals).')
                                : null;
                            final dateError = scheduledStart != null &&
                                    !validFrom.isAfter(now)
                                ? context.tr('定時上架時間必須晚於現在',
                                    'Choose a start time in the future.')
                                : !_validTo.isAfter(validFrom)
                                    ? context.tr('到期日期必須晚於生效時間',
                                        'The expiry must be after the start time.')
                                    : null;
                            setState(() {
                              _salePriceError = priceError;
                              _dateError = dateError;
                            });
                            if (priceError != null || dateError != null) return;
                            final draft = _body(validFrom, price);
                            final englishName = _nameEn.text.trim();
                            final category = _categories
                                ?.where((row) => row['id'] == _categoryId)
                                .map(_categoryLabel)
                                .firstOrNull;
                            final branches = _branchIds!.length;
                            final scope = branches == 0
                                ? context.tr('所有分店', 'All branches')
                                : context.tr('指定 $branches 間分店',
                                    '$branches selected branches');
                            var creating = false;
                            setState(() => _submitting = true);
                            try {
                              final preview =
                                  await widget.api.couponPreview(draft);
                              if (!context.mounted || session != _session) {
                                return;
                              }
                              final previewAmount = price == null
                                  ? preview['amount_text']
                                  : context.tr('售價：${formatHkd(price)}',
                                      'Sale price: ${formatHkd(price)}');
                              final ok = await showDialog<bool>(
                                context: context,
                                builder: (c) => AlertDialog(
                                  title: Text(
                                      context.tr('預覽優惠券', 'Review coupon')),
                                  content: Text([
                                    '${preview['coupon_name']}',
                                    '$previewAmount',
                                    context.tr('商店：${preview['shop_name']}',
                                        'Shop: ${preview['shop_name']}'),
                                    context.tr(
                                        '適用：$scope', 'Available at: $scope'),
                                    context.tr('數量：${preview['total']}',
                                        'Quantity: ${preview['total']}'),
                                    '${context.tr('每人限領', 'Claim limit per person')}：${draft['per_user_limit']}',
                                    if (englishName.isNotEmpty)
                                      '${context.tr('英文名稱', 'English name')}：$englishName',
                                    if (category != null)
                                      '${context.tr('分類', 'Category')}：$category',
                                    if (scheduledStart != null)
                                      context.tr(
                                          '生效時間：${_formatHongKong(validFrom)}（香港時間）',
                                          'Start: ${_formatHongKong(validFrom)} (Hong Kong time)'),
                                  ].join('\n')),
                                  actions: [
                                    TextButton(
                                        onPressed: () =>
                                            Navigator.pop(c, false),
                                        child:
                                            Text(context.tr('取消', 'Cancel'))),
                                    TextButton(
                                        onPressed: () => Navigator.pop(c, true),
                                        child: Text(context.tr(
                                            '提交審核', 'Submit for review'))),
                                  ],
                                ),
                              );
                              if (ok == true &&
                                  context.mounted &&
                                  session == _session) {
                                if (scheduledStart != null &&
                                    !scheduledStart.isAfter(_nowUtc())) {
                                  setState(() => _dateError = context.tr(
                                      '定時上架時間已過，請重新選擇',
                                      'The scheduled start has passed. Choose another time.'));
                                  return;
                                }
                                creating = true;
                                final res =
                                    await widget.api.couponCreate(draft);
                                if (context.mounted && session == _session) {
                                  showCgToast(
                                      context,
                                      res['message'] as String? ??
                                          context.tr(
                                              '已提交審核', 'Submitted for review'));
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
                                if (creating) {
                                  setState(() => _createUncertain = true);
                                } else {
                                  showCgToast(
                                      context,
                                      context.tr('未能連線，請重試',
                                          'Connection failed. Try again.'));
                                }
                              }
                            } finally {
                              if (mounted && session == _session) {
                                setState(() => _submitting = false);
                              }
                            }
                          },
                    label: context.tr('檢查並提交', 'Review and submit'),
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
        title: Text(context.tr('撤銷核銷', 'Reverse redemption')),
        content: Text(c.tr(
          '確定撤銷「$coupon」的這筆核銷？顧客的積分及相關返現會一併更新。',
          'Reverse this redemption for “$coupon”? The customer’s points and any related cashback will be updated.',
        )),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: Text(context.tr('取消', 'Cancel'))),
          TextButton(
              onPressed: () => Navigator.pop(c, true),
              child: Text(context.tr('確認撤銷', 'Confirm reversal'))),
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
    return context.tr('${d.year}年${d.month}月${d.day}日',
        '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}');
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
      setState(() => _errorReason = context.tr(
          '請輸入券上的六位兌換碼', 'Enter the six-digit code shown on the coupon.'));
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
            _errorReason = context.tr('此券需要指定分店，但暫無可選分店，請聯絡平台。',
                'A branch is required for this coupon, but none are available. Contact support.');
          }
        } else {
          // Invalid / already used / other-shop code: full-screen red page.
          _errorReason = res['reason'] as String? ??
              context.tr('無法核銷', 'Unable to redeem');
          _prepared = null;
          _pendingCode = null;
        }
        _lastRedemptionId = null;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _errorReason = e.message);
    } catch (_) {
      if (mounted) {
        setState(() => _errorReason =
            context.tr('未能連線，請重試', 'Connection failed. Try again.'));
      }
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
          _branchError = result['reason'] as String? ??
              context.tr('此分店未能核銷，請重新選擇。',
                  'This branch cannot redeem the coupon. Choose another branch.');
        }
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _branchError = e.message);
    } catch (_) {
      if (mounted) {
        setState(() => _branchError =
            context.tr('未能連線，請重試', 'Connection failed. Try again.'));
      }
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
        showCgToast(
            context,
            context.tr('未能確認結果，請再次確認核銷',
                'Could not confirm the result. Confirm the redemption again.'));
      } else if (mounted) {
        setState(() {
          _errorReason = e.message;
          _prepared = null;
          _pendingCode = null;
        });
      }
    } catch (_) {
      if (mounted) {
        showCgToast(
            context,
            context.tr('未能確認結果，請再次確認核銷',
                'Could not confirm the result. Confirm the redemption again.'));
      }
    } finally {
      if (mounted) setState(() => _handled = false);
    }
  }

  Future<void> _revoke() async {
    final rid = _lastRedemptionId;
    if (rid == null) return;
    final ok = await _confirmRevoke(
        context, _successCouponName ?? context.tr('優惠券', 'coupon'));
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
      if (mounted) {
        showCgToast(
            context,
            context.tr(
                '未能確認撤銷結果，請重試', 'Could not confirm the reversal. Try again.'));
      }
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
      padding: const EdgeInsets.symmetric(vertical: CgTheme.space4),
      child: LayoutBuilder(
        builder: (context, constraints) {
          final textScale = MediaQuery.textScalerOf(context).scale(14) / 14;
          final heading = Text(
            label,
            style: TextStyle(
                color: Theme.of(context).colorScheme.onSurfaceVariant),
          );
          if (constraints.maxWidth < 300 * max(1.0, textScale)) {
            return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [heading, Text(value)],
            );
          }
          return Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(child: heading),
              const SizedBox(width: CgTheme.space12),
              Expanded(child: Text(value)),
            ],
          );
        },
      ),
    );
  }

  Widget _successPage() {
    final left = _revokeLeft;
    final showRevoke = _lastRedemptionId != null && left > 0;
    return Container(
      color: Theme.of(context).colorScheme.surface,
      padding: const EdgeInsets.all(CgTheme.space24),
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(_revokedMessage == null ? Icons.check_circle : Icons.undo,
              size: 72,
              color: _revokedMessage == null
                  ? CgColors.success
                  : CgColors.textSecondary),
          const SizedBox(height: CgTheme.space16),
          Text(_revokedMessage ?? context.tr('已核銷', 'Redeemed'),
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: CgTheme.space8),
          Text(_successCouponName ?? '',
              textAlign: TextAlign.center,
              style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: CgTheme.space24),
          if (showRevoke)
            SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                onPressed: _revoking ? null : _revoke,
                child: Text(_revoking
                    ? context.tr('撤銷中…', 'Reversing…')
                    : context.tr(
                        '撤銷核銷（剩餘 ${formatCountdown(Duration(seconds: left))}）',
                        'Reverse (${formatCountdown(Duration(seconds: left))} left)',
                      )),
              ),
            ),
          const SizedBox(height: CgTheme.space8),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: _resetScan,
              child: Text(context.tr('繼續掃碼', 'Scan again')),
            ),
          ),
        ],
      ),
    );
  }

  Widget _errorPage() {
    final scheme = Theme.of(context).colorScheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(CgTheme.space24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.error_outline, size: 44, color: scheme.error),
            const SizedBox(height: CgTheme.space12),
            Text(context.tr('無法核銷', 'Unable to redeem'),
                style: Theme.of(context).textTheme.titleLarge),
            if (_errorReason != null) ...[
              const SizedBox(height: CgTheme.space8),
              Text(_errorReason!, textAlign: TextAlign.center),
            ],
            const SizedBox(height: CgTheme.space16),
            ElevatedButton(
              onPressed: _resetScan,
              child: Text(context.tr('繼續掃碼', 'Scan again')),
            ),
          ],
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(context.tr('掃碼核銷', 'Scan to redeem')),
      ),
      body: _errorReason != null
          ? _errorPage()
          : _successCouponName != null
              ? _successPage()
              : Center(
                  child: ConstrainedBox(
                    constraints:
                        const BoxConstraints(maxWidth: CgTheme.formMaxWidth),
                    child: ListView(
                      children: [
                        if (_prepared == null && _branchChoices.isEmpty)
                          SizedBox(
                            height: MediaQuery.sizeOf(context).height * .48,
                            child: Stack(
                              children: [
                                MobileScanner(
                                  errorBuilder: (_, __, ___) => ColoredBox(
                                    color: Colors.black,
                                    child: Center(
                                        child: Text(
                                            context.tr('相機未能使用，請手動輸入兌換碼',
                                                'Camera unavailable. Enter the code below.'),
                                            style: const TextStyle(
                                                color: Colors.white))),
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
                          Padding(
                            padding: const EdgeInsets.all(CgTheme.space8),
                            child: Text(context.tr(
                                '請將 QR 碼放入方框中並進行掃描\n先預核銷確認券面與門店，再按確認核銷',
                                'Place the QR code in the frame. Check the coupon and branch before confirming.')),
                          ),
                        if (_prepared == null && _branchChoices.isNotEmpty)
                          Padding(
                            padding: const EdgeInsets.all(CgTheme.pageInset),
                            child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                      context.tr(
                                          '選擇核銷分店', 'Choose redemption branch'),
                                      style: Theme.of(context)
                                          .textTheme
                                          .titleLarge),
                                  const SizedBox(height: CgTheme.space8),
                                  Text(context.tr('此券只適用於以下分店，請選擇顧客所在分店。',
                                      'Choose the branch where the customer is using this coupon.')),
                                  if (_branchError != null)
                                    Text(_branchError!,
                                        style: TextStyle(
                                            color: Theme.of(context)
                                                .colorScheme
                                                .error)),
                                  for (final branch in _branchChoices)
                                    ListTile(
                                      contentPadding: EdgeInsets.zero,
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
                                      child: Text(context.tr('取消', 'Cancel'))),
                                ]),
                          ),
                        if (_prepared != null &&
                            _prepared!['can_redeem'] == true)
                          Padding(
                            padding: const EdgeInsets.symmetric(
                                horizontal: CgTheme.pageInset),
                            child: Card(
                              child: Padding(
                                padding:
                                    const EdgeInsets.all(CgTheme.pageInset),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    Text(
                                        _prepared!['coupon_name'] as String? ??
                                            '',
                                        style: Theme.of(context)
                                            .textTheme
                                            .titleMedium),
                                    const SizedBox(height: CgTheme.space8),
                                    _detailRow(context.tr('門店', 'Shop'),
                                        '${_prepared!['shop_name'] ?? ''}'),
                                    if (_selectedBranchName != null)
                                      _detailRow(context.tr('核銷分店', 'Branch'),
                                          _selectedBranchName!),
                                    // Customer phone last-4 / validity come
                                    // from prepare_scan when the backend
                                    // sends them; '—' until then.
                                    _detailRow(
                                        context.tr('顧客手機後四位', 'Phone (last 4)'),
                                        _prepared!['customer_phone_last4']
                                                as String? ??
                                            '—'),
                                    _detailRow(
                                        context.tr('有效期', 'Valid until'),
                                        _dateText(
                                            _prepared!['valid_to'] as String?)),
                                    if (_prepared!['package_quantity'] != null)
                                      _detailRow(
                                          context.tr('整券核銷', 'Whole package'),
                                          context.tr(
                                              "${_prepared!['package_quantity']} 份",
                                              "${_prepared!['package_quantity']} units")),
                                    _detailRow(context.tr('是否已使用', 'Status'),
                                        context.tr('未使用', 'Unused')),
                                    const SizedBox(height: CgTheme.space12),
                                    Row(
                                      children: [
                                        Expanded(
                                          child: ElevatedButton(
                                            onPressed: _handled
                                                ? null
                                                : _confirmConsume,
                                            child: Text(_handled
                                                ? context.tr(
                                                    '核銷中…', 'Redeeming…')
                                                : context.tr('確認核銷',
                                                    'Confirm redemption')),
                                          ),
                                        ),
                                        const SizedBox(width: CgTheme.space8),
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
                                          child:
                                              Text(context.tr('取消', 'Cancel')),
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
                            padding: const EdgeInsets.symmetric(
                                horizontal: CgTheme.pageInset),
                            child: OutlinedButton(
                              onPressed: _handled
                                  ? null
                                  : () => Navigator.of(context).push(
                                      MaterialPageRoute(
                                          builder: (_) => ScanBatchScreen(
                                              api: widget.api))),
                              child:
                                  Text(context.tr('批量核銷', 'Batch redemption')),
                            ),
                          ),
                        if (_prepared == null && _branchChoices.isEmpty)
                          Padding(
                            padding: const EdgeInsets.all(CgTheme.pageInset),
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
                                    decoration: InputDecoration(
                                        hintText: context.tr('或手動輸入兌換碼',
                                            'Or enter the redemption code')),
                                  ),
                                ),
                                const SizedBox(width: CgTheme.space8),
                                ElevatedButton(
                                  style: ElevatedButton.styleFrom(
                                      minimumSize: const Size(88, 56)),
                                  onPressed: _handled
                                      ? null
                                      : () => _prepare(_code.text),
                                  child: Text(context.tr('預核銷', 'Check code')),
                                ),
                              ],
                            ),
                          ),
                      ],
                    ),
                  ),
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
          _error = context.tr('網絡不穩定，請重試', 'Network unavailable. Try again.');
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
        showCgToast(
            context,
            e is ApiException
                ? e.message
                : context.tr('未能確認撤銷結果，請重試',
                    'Could not confirm the reversal. Try again.'));
      }
    } finally {
      if (mounted) setState(() => _revokingId = null);
    }
  }

  String _dateLabel(DateTime? date) {
    if (date == null) return context.tr('不限日期', 'Any date');
    final day = date.day.toString().padLeft(2, '0');
    final month = date.month.toString().padLeft(2, '0');
    return '${date.year}-$month-$day';
  }

  Widget _dateFilter(bool isFrom) {
    final label = isFrom ? context.tr('開始', 'Start') : context.tr('結束', 'End');
    final date = _dateLabel(isFrom ? _from : _to);
    return OutlinedButton(
      key: ValueKey(isFrom ? 'vendor-date-start' : 'vendor-date-end'),
      style: OutlinedButton.styleFrom(
        padding: const EdgeInsets.symmetric(
          horizontal: CgTheme.space8,
          vertical: CgTheme.space8,
        ),
      ),
      onPressed: () => _pickDate(isFrom),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          Text(label, maxLines: 1),
          FittedBox(
            fit: BoxFit.scaleDown,
            child: Text(date,
                maxLines: 1, style: Theme.of(context).textTheme.bodySmall),
          ),
        ],
      ),
    );
  }

  Future<void> _pickDate(bool isFrom) async {
    final picked = await showDatePicker(
      context: context,
      initialDate: (isFrom ? _from : _to) ?? DateTime.now(),
      firstDate: DateTime.now().subtract(const Duration(days: 365)),
      lastDate: DateTime.now(),
    );
    if (picked == null || !mounted) return;
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
          title: Text(c.tr('核銷記錄 CSV（共 ${_records.length} 條）',
              'Redemption history CSV (${_records.length} records)')),
          content: SingleChildScrollView(
            child: SelectableText(
                csv.length > 2000 ? '${csv.substring(0, 2000)}…' : csv),
          ),
          actions: [
            TextButton(
              onPressed: () {
                Clipboard.setData(ClipboardData(text: csv));
                Navigator.pop(c);
                showCgToast(context, context.tr('已複製 CSV', 'CSV copied'));
              },
              child: Text(c.tr('複製全部', 'Copy all')),
            ),
            TextButton(
                onPressed: () => Navigator.pop(c),
                child: Text(c.tr('關閉', 'Close'))),
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
        title: Text(context.tr('核銷記錄', 'Redemption history')),
        actions: [
          Semantics(
            label: context.tr('匯出 CSV', 'Export CSV'),
            button: true,
            child: IconButton(
              icon: const Icon(Icons.download),
              tooltip: context.tr('匯出 CSV', 'Export CSV'),
              onPressed: _records.isEmpty ? null : _exportCsv,
            ),
          ),
        ],
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: CgTheme.listMaxWidth),
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(
                  CgTheme.pageInset,
                  CgTheme.space12,
                  CgTheme.pageInset,
                  0,
                ),
                child: Row(
                  children: [
                    Expanded(child: _dateFilter(true)),
                    const SizedBox(width: CgTheme.space8),
                    Expanded(child: _dateFilter(false)),
                  ],
                ),
              ),
              if (_from != null || _to != null)
                Align(
                  alignment: AlignmentDirectional.centerEnd,
                  child: Padding(
                    padding: const EdgeInsetsDirectional.only(
                        end: CgTheme.pageInset),
                    child: TextButton(
                      key: const ValueKey('vendor-date-clear'),
                      onPressed: () {
                        setState(() {
                          _from = null;
                          _to = null;
                        });
                        _load();
                      },
                      child: Text(context.tr('清除', 'Clear')),
                    ),
                  ),
                ),
              Expanded(
                child: _loading
                    ? ListView(
                        padding: const EdgeInsets.all(CgTheme.pageInset),
                        children: const [
                          SkeletonBox(height: 72),
                          SizedBox(height: CgTheme.space8),
                          SkeletonBox(height: 72),
                        ],
                      )
                    : _error != null
                        ? Center(
                            child: Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                Text(context.tr('網絡不穩定，請重試',
                                    'Network unavailable. Try again.')),
                                const SizedBox(height: CgTheme.space12),
                                ElevatedButton(
                                    onPressed: _load,
                                    child: Text(context.tr('重試', 'Retry'))),
                              ],
                            ),
                          )
                        : _records.isEmpty
                            ? Center(
                                child: Column(
                                  mainAxisAlignment: MainAxisAlignment.center,
                                  children: [
                                    Text(context.tr('所選日期內暫無核銷記錄。',
                                        'No redemptions in this date range.')),
                                    const SizedBox(height: CgTheme.space12),
                                    ElevatedButton(
                                        onPressed: _load,
                                        child:
                                            Text(context.tr('重新載入', 'Reload'))),
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
                                      margin: const EdgeInsets.fromLTRB(
                                          CgTheme.pageInset,
                                          CgTheme.space8,
                                          CgTheme.pageInset,
                                          0),
                                      child: ListTile(
                                        contentPadding:
                                            const EdgeInsets.symmetric(
                                                horizontal: CgTheme.pageInset),
                                        title:
                                            Text('${r['coupon_name'] ?? ''}'),
                                        subtitle: Text(
                                            '${r['shop_name'] ?? ''} · ${r['scanned_at'] ?? ''}'),
                                        trailing: canRevoke
                                            ? TextButton(
                                                onPressed: _revokingId == null
                                                    ? () => _revokeRecord(r)
                                                    : null,
                                                child: Text(context.tr('撤銷核銷',
                                                    'Reverse redemption')),
                                              )
                                            : MerchantStatusPill(
                                                label: revoked
                                                    ? context.tr(
                                                        '已撤銷', 'Reversed')
                                                    : context.tr(
                                                        '已核銷', 'Redeemed'),
                                                active: !revoked,
                                              ),
                                      ),
                                    );
                                  },
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

/// Usage data: totals + per-coupon downloads / redemptions.
class UsageScreen extends StatelessWidget {
  const UsageScreen({super.key, required this.api});

  final ApiClient api;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(context.tr('使用數據', 'Usage statistics')),
      ),
      body: FutureBuilder<Map<String, dynamic>>(
        future: api.usageData(),
        builder: (context, snap) {
          if (!snap.hasData) {
            return const Padding(
              padding: EdgeInsets.all(CgTheme.pageInset),
              child: Column(children: [SkeletonBox(height: 120)]),
            );
          }
          final data = snap.data!;
          final coupons = (data['coupons'] as List?) ?? [];
          return Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: CgTheme.listMaxWidth),
              child: ListView(
                padding: const EdgeInsets.all(CgTheme.pageInset),
                children: [
                  Row(
                    children: [
                      Expanded(
                          child: Card(
                              child: Padding(
                        padding: const EdgeInsets.all(CgTheme.pageInset),
                        child: Column(children: [
                          Text('${data['total_downloads']}',
                              style: Theme.of(context).textTheme.headlineSmall),
                          Text(context.tr('總下載次數', 'Total downloads')),
                        ]),
                      ))),
                      const SizedBox(width: CgTheme.space12),
                      Expanded(
                          child: Card(
                              child: Padding(
                        padding: const EdgeInsets.all(CgTheme.pageInset),
                        child: Column(children: [
                          Text('${data['total_usage']}',
                              style: Theme.of(context).textTheme.headlineSmall),
                          Text(context.tr('總使用量', 'Total redemptions')),
                        ]),
                      ))),
                    ],
                  ),
                  const SizedBox(height: CgTheme.space16),
                  ...coupons.map((c) => Card(
                        margin: const EdgeInsets.only(top: CgTheme.space8),
                        child: ListTile(
                          contentPadding: const EdgeInsets.symmetric(
                              horizontal: CgTheme.pageInset),
                          title: Text(c['coupon_name'] as String),
                          subtitle: Text(context.tr(
                            '下載 ${c['downloads']} · 已用 ${c['used']} · 剩 ${c['remaining']}',
                            '${c['downloads']} downloads · ${c['used']} used · ${c['remaining']} left',
                          )),
                        ),
                      )),
                ],
              ),
            ),
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
        title: Text(context.tr('訂閱計劃', 'Subscription plans')),
      ),
      body: FutureBuilder<List<dynamic>>(
        future: api.subscriptionPlans(),
        builder: (context, snap) {
          if (!snap.hasData) {
            return const Padding(
              padding: EdgeInsets.all(CgTheme.pageInset),
              child: Column(children: [SkeletonBox(height: 120)]),
            );
          }
          return Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: CgTheme.listMaxWidth),
              child: ListView(
                padding: const EdgeInsets.all(CgTheme.pageInset),
                children: [
                  Image.asset('assets/images/ic_program_card.png',
                      height: 120,
                      fit: BoxFit.cover,
                      errorBuilder: (_, __, ___) => const SizedBox()),
                  const SizedBox(height: CgTheme.space12),
                  ...snap.data!.map((p) => Card(
                        margin: const EdgeInsets.only(top: CgTheme.space8),
                        child: ListTile(
                          contentPadding: const EdgeInsets.symmetric(
                              horizontal: CgTheme.pageInset),
                          title: Text(p['name'] as String),
                          subtitle: Text(p['description'] as String? ?? ''),
                          trailing: Text(
                              context.tr(
                                '\$${(p['monthly_fee'] as num).toStringAsFixed(0)}/月',
                                '\$${(p['monthly_fee'] as num).toStringAsFixed(0)}/mo',
                              ),
                              style:
                                  const TextStyle(fontWeight: FontWeight.bold)),
                        ),
                      )),
                  Padding(
                    padding: const EdgeInsets.only(top: CgTheme.space12),
                    child: Text(
                        context.tr(
                          '月費計劃可隨時取消，每月從帳戶扣除，無隱藏費用。',
                          'Cancel anytime. Monthly fees are charged to your account, with no hidden fees.',
                        ),
                        style: TextStyle(
                            color: Theme.of(context)
                                .colorScheme
                                .onSurfaceVariant)),
                  ),
                ],
              ),
            ),
          );
        },
      ),
    );
  }
}
