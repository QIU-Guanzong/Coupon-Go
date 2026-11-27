import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:mobile_scanner/mobile_scanner.dart';

import '../api_client.dart';
import '../feature_flags.dart';
import '../vendor_coupon_lifecycle.dart';
import '../models.dart';
import '../language.dart';
import '../theme.dart';
import '../widgets/merchant_layout.dart';
import '../widgets/widgets.dart';
import 'merchant_review_shops.dart';
import 'merchant_reviews.dart';
import 'coupon_branch_scope.dart';
import 'vendor_packages.dart';
import 'vendor_coupon_workspace.dart' show confirmVendorCouponAction;
import 'redemption_recovery.dart';
import 'merchant_dashboard.dart';
import 'merchant_account_overview.dart';
import 'scan_batch.dart';
import 'merchant_branches.dart';
import 'merchant_staff.dart';

export 'vendor_coupon_form.dart' show IssueCouponScreen;

/// Vendor home: usage summary and coupon-management entry.
class VendorHomeScreen extends StatefulWidget {
  const VendorHomeScreen(
      {super.key,
      required this.api,
      this.showAdmin = false,
      this.onOpenMenu,
      this.onOpenScan,
      this.active = true});

  final ApiClient api;
  final bool showAdmin;
  final VoidCallback? onOpenMenu;
  final VoidCallback? onOpenScan;
  final bool active;

  @override
  State<VendorHomeScreen> createState() => _VendorHomeScreenState();
}

class _VendorHomeScreenState extends State<VendorHomeScreen> {
  Map<String, dynamic>? _usage;
  List<Coupon> _coupons = [];
  List<Map<String, dynamic>> _shops = [];
  String? _loadError;
  bool _usageUnavailable = false;
  String? _couponListError;
  int? _pendingReviewCount;
  bool _reviewCountFromServer = false;
  int _reviewCountRequest = 0;
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

  @override
  void didUpdateWidget(covariant VendorHomeScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (widget.active && !oldWidget.active) unawaited(_refresh());
  }

  void _sessionChanged() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    _filterRequest++;
    _reviewCountRequest++;
    setState(() {
      _pendingReviewCount = null;
      _reviewCountFromServer = false;
      _shops = [];
      _coupons = [];
      _usage = null;
      _loading = true;
      _loadError = null;
      _usageUnavailable = false;
      _couponListError = null;
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
      setState(() {
        _shops = shops;
        _loadError = null;
        _usageUnavailable = false;
        _couponListError = null;
        if (!isOwner) _usage = null;
        if (!canManage) _coupons = [];
      });
      if (isOwner) unawaited(_loadPendingReviews());
      await Future.wait([
        if (isOwner)
          () async {
            try {
              final usage = await widget.api.usageData();
              if (current()) setState(() => _usage = usage);
            } catch (_) {
              if (current()) setState(() => _usageUnavailable = true);
            }
          }(),
        if (canManage)
          () async {
            try {
              final rows = await widget.api.vendorCoupons();
              final coupons = rows.map((row) => Coupon.fromJson(row)).toList();
              if (current()) {
                setState(() {
                  _coupons = coupons;
                  _overviewCouponRead = couponRead;
                });
              }
            } catch (error) {
              if (current()) {
                setState(() => _couponListError = error is ApiException
                    ? error.message
                    : context.tr('未能載入優惠券。', 'Could not load coupons.'));
              }
            }
          }(),
      ]);
      if (current()) setState(() => _loading = false);
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

  Future<void> _loadPendingReviews() async {
    if (publicTrial || !mounted) return;
    final session = _session;
    final request = ++_reviewCountRequest;
    int? total;
    try {
      total = await widget.api.merchantReviewPendingCount();
    } catch (_) {
      // Leave no full-count badge if the API is unavailable or access changed.
    }
    if (!mounted ||
        session != _session ||
        session != widget.api.sessionRevision ||
        request != _reviewCountRequest) {
      return;
    }
    setState(() {
      if (total != null || _reviewCountFromServer) {
        _pendingReviewCount = total;
      }
      _reviewCountFromServer = total != null;
    });
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
    bool canSubmit({bool afterConfirmation = false}) {
      if (!current() ||
          !_canChangeCoupon(coupon) ||
          (!afterConfirmation && _savingCoupons.contains(coupon.id))) {
        return false;
      }
      final displayed = _couponStatus == null ? _coupons : _filteredCoupons;
      for (final item in displayed) {
        if (item.id != coupon.id || item.shopId != coupon.shopId) continue;
        final status = _pendingStatus(item) ?? item.status;
        return (_couponStatus == null || status == _couponStatus) &&
            VendorCouponAction.forManagementStatus(status).contains(action);
      }
      return false;
    }

    if (!canSubmit()) return;
    setState(() => _savingCoupons.add(coupon.id));
    try {
      final confirmed =
          await confirmVendorCouponAction(context, coupon.name, action);
      if (!confirmed || !canSubmit(afterConfirmation: true)) return;
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

  Future<void> _openCouponWorkspace() async {
    final session = _session;
    await Navigator.of(context).pushNamed('/vendor/coupons');
    if (mounted &&
        session == _session &&
        widget.api.sessionRevision == session) {
      await _refresh();
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
                icon: const Icon(Icons.menu_rounded),
                tooltip: MaterialLocalizations.of(context).openAppDrawerTooltip,
                onPressed: widget.onOpenMenu,
              ),
        title: Row(mainAxisSize: MainAxisSize.min, children: [
          if (MediaQuery.sizeOf(context).width >= 360) ...[
            Image.asset('assets/images/img_logo.png', height: 26,
              excludeFromSemantics: true,
              errorBuilder: (_, __, ___) => const SizedBox.shrink()),
            const SizedBox(width: 8),
          ],
          Flexible(child: Text(context.tr('企業版', 'Business'),
            maxLines: 1, overflow: TextOverflow.ellipsis)),
        ]),
        actions: [
          IconButton(
            tooltip: context.tr('重新整理工作台', 'Refresh workspace'),
            onPressed: _refresh,
            icon: const Icon(Icons.refresh_rounded),
          ),
          const SizedBox(width: 8),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _refresh,
        child: LayoutBuilder(builder: (context, viewport) {
          final wide = viewport.maxWidth >= 900 &&
              MediaQuery.textScalerOf(context).scale(16) <= 21;
          final gutter = wide ? 28.0 : 16.0;
          final side = gutter + max(0.0, (viewport.maxWidth - 1200) / 2);
          final operations = <Widget>[
            if (_allows('can_manage_coupons'))
              OutlinedButton.icon(
                style: _toolButtonStyle,
                onPressed: _openCouponWorkspace,
                icon: const Icon(Icons.local_offer_outlined),
                label: _toolLabel(context.tr('券管理', 'Manage coupons')),
              ),
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
                      settings: const RouteSettings(name: '/vendor/reviews'),
                      builder: (_) => MerchantReviewsScreen(
                        api: api,
                        shopRoster: roster,
                        expectedSessionRevision: session,
                        canAdministerAllShops: admin,
                        onFirstPagePending: (count) {
                          if (!mounted ||
                              _session != session ||
                              api.sessionRevision != session) {
                            return;
                          }
                          if (!_reviewCountFromServer) {
                            setState(() => _pendingReviewCount = count);
                          }
                        },
                        onPendingChanged: () {
                          if (mounted && _session == session) {
                            setState(() {
                              _pendingReviewCount = null;
                              _reviewCountFromServer = false;
                            });
                            unawaited(_loadPendingReviews());
                          }
                        },
                        onPrivateAccessDenied: () {
                          if (mounted && _session == session) {
                            _reviewCountRequest++;
                            setState(() {
                              _pendingReviewCount = null;
                              _reviewCountFromServer = false;
                            });
                          }
                        },
                      ),
                    ),
                  );
                },
                icon: _pendingReviewCount != null && _pendingReviewCount! > 0
                    ? Tooltip(
                        message: _reviewCountFromServer
                            ? context.tr(
                                '$_pendingReviewCount 條評價待回覆',
                                '$_pendingReviewCount reviews awaiting reply',
                              )
                            : context.tr(
                                '已載入的評價首頁有 $_pendingReviewCount 條待回覆',
                                '$_pendingReviewCount awaiting reply on loaded first review pages',
                              ),
                        child: Badge(
                          label: Text('$_pendingReviewCount'),
                          child: const Icon(Icons.reviews_outlined),
                        ),
                      )
                    : const Icon(Icons.reviews_outlined),
                label: _toolLabel(context.tr('評價管理', 'Reviews')),
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
          final coupons = Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (_allows('can_manage_coupons')) ...[
                if (_couponStatus == null && _couponListError != null)
                  _loadNotice(_couponListError!, _refresh),
                InkWell(
                  onTap: _openCouponWorkspace,
                  child: Padding(
                    padding:
                        const EdgeInsets.symmetric(vertical: CgTheme.space8),
                    child: Row(children: [
                      Expanded(
                          child: Text(context.tr('我的優惠券', 'My coupons'),
                              style: Theme.of(context).textTheme.titleMedium)),
                      const Icon(Icons.chevron_right),
                    ]),
                  ),
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
                        'scheduled',
                        'sold_out',
                        'expired',
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
                ...shownCoupons.take(6).map((c) {
                  final status = _pendingStatus(c) ?? c.status;
                  final actions = _canChangeCoupon(c)
                      ? VendorCouponAction.forManagementStatus(status)
                      : const <VendorCouponAction>[];
                  return Card(
                    margin: const EdgeInsets.only(top: CgTheme.space8),
                    child: ListTile(
                        onTap: c.id <= 0 ? null : () async {
                          await Navigator.of(context).pushNamed('/vendor/coupons/${c.id}');
                          if (mounted) await _refresh();
                        },
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
                              listingStatus: status,
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
                              onSelected: (action) => _changeCoupon(c, action),
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
              if (shownCoupons.length > 6)
                TextButton.icon(
                  onPressed: _openCouponWorkspace,
                  icon: const Icon(Icons.arrow_forward_rounded),
                  label: Text(context.tr('查看全部優惠券', 'View all coupons')),
                ),
              if (_allows('can_manage_coupons') &&
                  shownCoupons.isEmpty &&
                  !_loading &&
                  !_filterLoading &&
                  _loadError == null &&
                  _filterError == null &&
                  _couponListError == null)
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
          final tools = _toolsPanel(operations, wide: wide);
          return ListView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: EdgeInsets.fromLTRB(side, gutter, side, gutter * 2),
            children: [
              _workspaceHero(),
              if (_loading) ...[
                const SizedBox(height: 12),
                const LinearProgressIndicator(),
              ],
              if (_loadError != null) _loadNotice(_loadError!, _refresh),
              if (!_loading &&
                  _loadError == null &&
                  _shops.isEmpty &&
                  !widget.showAdmin)
                Padding(
                  padding: const EdgeInsets.only(top: 16),
                  child: Text(context.tr(
                      '此帳戶暫未連結任何店舖。', 'No shops are linked to this account.')),
                ),
              if (_allows('is_owner')) ...[
                const SizedBox(height: 16),
                _usageUnavailable
                    ? _loadNotice(
                        context.tr(
                            '經營數據暫未載入。', 'Business figures could not load.'),
                        _refresh)
                    : _snapshot(),
              ],
              const SizedBox(height: 24),
              if (wide)
                Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Expanded(flex: 7, child: coupons),
                  const SizedBox(width: 24),
                  Expanded(flex: 4, child: tools),
                ])
              else ...[
                tools,
                const SizedBox(height: 22),
                coupons,
              ],
            ],
          );
        }),
      ),
    );
  }

  Future<void> _issue() async {
    await Navigator.of(context).pushNamed('/vendor/issue');
    if (mounted) await _refresh();
  }

  Widget _workspaceHero() {
    final text = Theme.of(context).textTheme;
    final canScan = widget.onOpenScan != null &&
        _shops.any((shop) => shop['can_redeem'] == true);
    final canIssue = _allows('can_manage_coupons');
    final name = _shops.length == 1 ? _shops.single['name'] as String? : null;
    final scope = _shops.length > 1
        ? context.tr('${_shops.length} 間店舖', '${_shops.length} shops')
        : name;
    return Container(
      padding: const EdgeInsets.all(24),
      decoration: BoxDecoration(
        color: CgColors.vendorDark,
        borderRadius: BorderRadius.circular(18),
      ),
      child: LayoutBuilder(builder: (context, bounds) {
        final heading =
            Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          if (scope != null && scope.isNotEmpty) ...[
            Text(scope,
                style:
                    text.labelLarge?.copyWith(color: const Color(0xFFE2C491))),
            const SizedBox(height: 6),
          ],
          Semantics(
            header: true,
            child: Text(context.tr('商戶工作台', 'Merchant workspace'),
                style: text.headlineMedium?.copyWith(
                    color: Colors.white,
                    fontWeight: FontWeight.w800,
                    height: 1.14)),
          ),
        ]);
        final actions = Wrap(
          spacing: 10,
          runSpacing: 8,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            if (canScan)
              FilledButton.icon(
                onPressed: widget.onOpenScan,
                icon: const Icon(Icons.qr_code_scanner_rounded),
                label: Text(context.tr('掃碼核銷', 'Redeem a voucher')),
                style: FilledButton.styleFrom(minimumSize: const Size(0, 48)),
              ),
            if (canIssue && canScan)
              OutlinedButton.icon(
                onPressed: _issue,
                icon: const Icon(Icons.add_rounded),
                label: Text(context.tr('發券', 'Issue coupon')),
                style: OutlinedButton.styleFrom(
                    foregroundColor: Colors.white,
                    side: const BorderSide(color: Color(0xFF807462)),
                    minimumSize: const Size(0, 48)),
              )
            else if (canIssue)
              FilledButton.icon(
                onPressed: _issue,
                icon: const Icon(Icons.add_rounded),
                label: Text(context.tr('發券', 'Issue coupon')),
                style: FilledButton.styleFrom(minimumSize: const Size(0, 48)),
              ),
          ],
        );
        if (bounds.maxWidth >= 760 &&
            MediaQuery.textScalerOf(context).scale(16) <= 19 &&
            (canScan || canIssue)) {
          return Row(children: [
            Expanded(child: heading),
            const SizedBox(width: 24),
            actions
          ]);
        }
        return Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          heading,
          if (canScan || canIssue) ...[const SizedBox(height: 18), actions],
        ]);
      }),
    );
  }

  Widget _loadNotice(String message, VoidCallback retry) => Semantics(
        liveRegion: true,
        child: Padding(
          padding: const EdgeInsets.only(top: 10, bottom: 6),
          child: Row(children: [
            const Icon(Icons.info_outline_rounded, size: 18),
            const SizedBox(width: 8),
            Expanded(child: Text(message)),
            TextButton(
                onPressed: retry, child: Text(context.tr('重試', 'Retry'))),
          ]),
        ),
      );

  Widget _snapshot() {
    final usage = _usage;
    if (usage == null) return const SizedBox.shrink();
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text(context.tr('累計經營數據', 'Lifetime activity'),
          style: Theme.of(context).textTheme.titleMedium),
      const SizedBox(height: 10),
      Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(
            child: _stat(
                Icons.download_outlined,
                context.tr('總下載次數', 'Total downloads'),
                usage['total_downloads'])),
        const SizedBox(width: 12),
        Expanded(
            child: _stat(
                Icons.task_alt_rounded,
                context.tr('總核銷次數', 'Total redemptions'),
                usage['total_usage'])),
      ]),
    ]);
  }

  Widget _toolsPanel(List<Widget> tools, {required bool wide}) {
    if (tools.isEmpty) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(context.tr('商戶工具', 'Business tools'),
            style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 10),
        Card(
          margin: EdgeInsets.zero,
          child: Padding(
            padding: const EdgeInsets.all(6),
            child: LayoutBuilder(builder: (context, grid) {
              final textScale = MediaQuery.textScalerOf(context).scale(14) / 14;
              final columns =
                  !wide && grid.maxWidth >= 312 * max(1.0, textScale) ? 2 : 1;
              final width = (grid.maxWidth - 4 * (columns - 1)) / columns;
              return Wrap(
                key: const ValueKey('vendor-tools'),
                spacing: 4,
                runSpacing: 4,
                children: [
                  for (final tool in tools) SizedBox(width: width, child: tool),
                ],
              );
            }),
          ),
        ),
      ],
    );
  }

  static final _toolButtonStyle = OutlinedButton.styleFrom(
    alignment: Alignment.centerLeft,
    minimumSize: const Size(0, 46),
    padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
    side: BorderSide.none,
    shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
  );

  Widget _toolLabel(String label) => Text(label, softWrap: true);

  Widget _stat(IconData icon, String label, Object? value) {
    final theme = Theme.of(context);
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.all(14),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, color: CgColors.vendorLink, size: 19),
            const SizedBox(height: 8),
            Text(value is num ? '$value' : '—',
                style: theme.textTheme.headlineSmall),
            Text(label,
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: theme.colorScheme.onSurfaceVariant)),
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
  bool _cameraOpen = false;
  bool _consumeUncertain = false;
  late int _session;
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
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_accountChanged);
  }

  void _accountChanged() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    _resetScan();
  }

  bool _current(int session) =>
      mounted && session == _session && session == widget.api.sessionRevision;

  @override
  void dispose() {
    widget.api.removeListener(_accountChanged);
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
      _cameraOpen = false;
      _consumeUncertain = false;
      _revoking = false;
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
    final session = _session;
    setState(() => _handled = true);
    try {
      final res = await widget.api.prepareScan(code);
      if (!_current(session)) return;
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
      if (_current(session)) setState(() => _errorReason = e.message);
    } catch (_) {
      if (_current(session)) {
        setState(() => _errorReason =
            context.tr('未能連線，請重試', 'Connection failed. Try again.'));
      }
    } finally {
      if (_current(session)) setState(() => _handled = false);
    }
  }

  Future<void> _chooseBranch(Map<String, dynamic> branch) async {
    final code = _pendingCode;
    final id = branch['id'];
    if (_handled || code == null || id is! int) return;
    final session = _session;
    setState(() {
      _handled = true;
      _branchError = null;
    });
    try {
      final result = await widget.api.prepareScan(code, branchId: id);
      if (!_current(session)) return;
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
      if (_current(session)) setState(() => _branchError = e.message);
    } catch (_) {
      if (_current(session)) {
        setState(() => _branchError =
            context.tr('未能連線，請重試', 'Connection failed. Try again.'));
      }
    } finally {
      if (_current(session)) setState(() => _handled = false);
    }
  }

  Future<void> _confirmConsume() async {
    final code = _pendingCode;
    final token = _prepared?['prepare_token'] as String?;
    if (_handled || code == null || token == null || _attemptKey == null) {
      return;
    }
    final session = _session;
    setState(() => _handled = true);
    try {
      final res = await widget.api.scanCoupon(code,
          prepareToken: token,
          branchId: _selectedBranchId,
          idempotencyKey: _attemptKey!);
      if (!_current(session)) return;
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
        _consumeUncertain = false;
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
      if (_current(session) && e.status >= 500) {
        setState(() => _consumeUncertain = true);
      } else if (_current(session)) {
        setState(() {
          _errorReason = e.message;
          _prepared = null;
          _pendingCode = null;
        });
      }
    } catch (_) {
      if (_current(session)) setState(() => _consumeUncertain = true);
    } finally {
      if (_current(session)) setState(() => _handled = false);
    }
  }

  Future<void> _revoke() async {
    final session = _session;
    final rid = _lastRedemptionId;
    if (rid == null) return;
    final ok = await _confirmRevoke(
        context, _successCouponName ?? context.tr('優惠券', 'coupon'));
    if (ok != true || !_current(session) || _revoking) return;
    setState(() => _revoking = true);
    try {
      final receipt = await widget.api.revokeRedemption(rid);
      if (!_current(session)) return;
      _revokeTimer?.cancel();
      setState(() {
        _lastRedemptionId = null;
        _revokedMessage = receipt['message'] as String?;
      });
    } on ApiException catch (e) {
      if (mounted && _current(session)) showCgToast(context, e.message);
    } catch (_) {
      if (mounted && _current(session)) {
        showCgToast(
            context,
            context.tr(
                '未能確認撤銷結果，請重試', 'Could not confirm the reversal. Try again.'));
      }
    } finally {
      if (_current(session)) setState(() => _revoking = false);
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
              child: Text(_revokedMessage == null
                  ? context.tr('繼續掃下一張', 'Scan next voucher')
                  : context.tr('繼續掃碼', 'Scan again')),
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

  Widget _panel({required Widget child, Color? color}) => Card(
        margin: EdgeInsets.zero,
        color: color,
        child: Padding(padding: const EdgeInsets.all(18), child: child),
      );

  Widget _manualEntry() => _panel(
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            const Icon(Icons.pin_outlined, color: CgColors.vendorLink),
            const SizedBox(width: 10),
            Expanded(
                child: Text(context.tr('輸入兌換碼', 'Enter a code'),
                    style: Theme.of(context).textTheme.titleLarge)),
          ]),
          const SizedBox(height: 12),
          TextField(
            controller: _code,
            enabled: !_handled,
            inputFormatters: [
              FilteringTextInputFormatter.digitsOnly,
              LengthLimitingTextInputFormatter(6),
            ],
            onSubmitted: _prepare,
            textInputAction: TextInputAction.done,
            keyboardType: TextInputType.number,
            decoration: InputDecoration(
              labelText: context.tr('六位兌換碼', 'Six-digit code'),
              hintText: context.tr('輸入顧客出示的號碼', 'Code shown by the customer'),
            ),
          ),
          const SizedBox(height: 12),
          FilledButton.icon(
            onPressed: _handled ? null : () => _prepare(_code.text),
            icon: const Icon(Icons.arrow_forward_rounded),
            label: Text(_handled
                ? context.tr('查核中…', 'Checking…')
                : context.tr('預核銷', 'Check code')),
            style: FilledButton.styleFrom(minimumSize: const Size(0, 48)),
          ),
          if (_handled) ...[
            const SizedBox(height: 10),
            const LinearProgressIndicator(),
          ],
          const SizedBox(height: 8),
          TextButton.icon(
            onPressed: _handled
                ? null
                : () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => ScanBatchScreen(api: widget.api))),
            icon: const Icon(Icons.playlist_add_check_rounded),
            label: Text(context.tr('批量核銷', 'Batch redemption')),
            style: TextButton.styleFrom(alignment: Alignment.centerLeft),
          ),
        ]),
      );

  Widget _cameraEntry() => _panel(
        color: CgColors.vendorDark,
        child:
            Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          Row(children: [
            const Icon(Icons.qr_code_scanner_rounded,
                color: CgColors.vendorGold),
            const SizedBox(width: 10),
            Expanded(
                child: Text(context.tr('掃描顧客二維碼', 'Scan a customer QR code'),
                    style: Theme.of(context)
                        .textTheme
                        .titleMedium
                        ?.copyWith(color: Colors.white))),
          ]),
          if (_cameraOpen) ...[
            const SizedBox(height: 14),
            ClipRRect(
              borderRadius: BorderRadius.circular(10),
              child: AspectRatio(
                aspectRatio: 4 / 3,
                child: Stack(fit: StackFit.expand, children: [
                  MobileScanner(
                    errorBuilder: (_, __, ___) =>
                        const ColoredBox(color: Colors.black),
                    onDetect: (capture) {
                      final value = capture.barcodes.firstOrNull?.rawValue;
                      if (value != null) {
                        _prepare(Uri.tryParse(value)?.queryParameters['code'] ??
                            value);
                      }
                    },
                  ),
                  IgnorePointer(
                      child: Center(
                          child: ConstrainedBox(
                    constraints:
                        const BoxConstraints(maxWidth: 180, maxHeight: 180),
                    child: Image.asset('assets/images/ic_scan_frame.png',
                        excludeFromSemantics: true,
                        errorBuilder: (_, __, ___) => Center(
                                child: Container(
                              width: 150,
                              height: 150,
                              decoration: BoxDecoration(
                                  border: Border.all(
                                      color: CgColors.vendorGold, width: 2),
                                  borderRadius: BorderRadius.circular(12)),
                            ))),
                  ))),
                ]),
              ),
            ),
            const SizedBox(height: 10),
            Text(
                context.tr('相機未能使用時，可輸入六位兌換碼。',
                    'If the camera is unavailable, use the six-digit code.'),
                style: const TextStyle(color: Color(0xFFE6DCCB))),
          ],
          const SizedBox(height: 8),
          Align(
            alignment: Alignment.centerLeft,
            child: TextButton.icon(
              onPressed: _handled
                  ? null
                  : () => setState(() => _cameraOpen = !_cameraOpen),
              style: TextButton.styleFrom(foregroundColor: CgColors.vendorGold),
              icon: Icon(_cameraOpen
                  ? Icons.videocam_off_outlined
                  : Icons.videocam_outlined),
              label: Text(_cameraOpen
                  ? context.tr('關閉相機', 'Close camera')
                  : context.tr('開啟相機', 'Open camera')),
            ),
          ),
        ]),
      );

  Widget _branchChoice() => _panel(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(context.tr('選擇核銷分店', 'Choose a branch'),
              style: Theme.of(context).textTheme.titleLarge),
          const SizedBox(height: 8),
          Text(context.tr('請選擇顧客所在的分店。',
              'Choose the branch where the customer is using this coupon.')),
          if (_branchError != null)
            Semantics(
                liveRegion: true,
                child: Text(_branchError!,
                    style:
                        TextStyle(color: Theme.of(context).colorScheme.error))),
          for (final branch in _branchChoices)
            ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(branch['name'] as String),
              subtitle: branch['address'] is String &&
                      (branch['address'] as String).isNotEmpty
                  ? Text(branch['address'] as String)
                  : null,
              trailing: const Icon(Icons.chevron_right_rounded),
              enabled: !_handled,
              onTap: () => _chooseBranch(branch),
            ),
          if (_handled) const LinearProgressIndicator(),
          TextButton(
              onPressed: _handled ? null : _resetScan,
              child: Text(context.tr('取消', 'Cancel'))),
        ]),
      );

  Widget _preparedCard(Map<String, dynamic> prepared) {
    final text = Theme.of(context).textTheme;
    return _panel(
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        Text(context.tr('核銷確認', 'Review redemption'),
            style: text.labelLarge?.copyWith(
                color: CgColors.vendorLink, fontWeight: FontWeight.w700)),
        const SizedBox(height: 6),
        Text(prepared['coupon_name'] as String? ?? '',
            style: text.headlineSmall),
        const Divider(height: 28),
        _detailRow(context.tr('門店', 'Shop'), '${prepared['shop_name'] ?? ''}'),
        if (_selectedBranchName != null)
          _detailRow(context.tr('核銷分店', 'Branch'), _selectedBranchName!),
        _detailRow(context.tr('顧客手機後四位', 'Phone (last 4)'),
            prepared['customer_phone_last4'] as String? ?? '—'),
        _detailRow(context.tr('有效期', 'Valid until'),
            _dateText(prepared['valid_to'] as String?)),
        if (prepared['package_quantity'] != null)
          _detailRow(
              context.tr('整券核銷', 'Whole package'),
              context.tr("${prepared['package_quantity']} 份",
                  "${prepared['package_quantity']} units")),
        _detailRow(
            context.tr('是否已使用', 'Status'),
            _consumeUncertain
                ? context.tr('等待確認', 'Awaiting confirmation')
                : context.tr('未使用', 'Unused')),
        if (_consumeUncertain) ...[
          const SizedBox(height: 12),
          Semantics(
              liveRegion: true,
              child: Text(context.tr('未能確認核銷結果。請重試同一筆操作，或先查看核銷記錄。',
                  'The result is unconfirmed. Retry the same redemption or check redemption history.'))),
        ],
        const SizedBox(height: 14),
        Wrap(
          spacing: 8,
          runSpacing: 6,
          crossAxisAlignment: WrapCrossAlignment.center,
          children: [
            FilledButton(
              onPressed: _handled ? null : _confirmConsume,
              style: FilledButton.styleFrom(minimumSize: const Size(0, 48)),
              child: Text(_handled
                  ? context.tr('核銷中…', 'Redeeming…')
                  : _consumeUncertain
                      ? context.tr('重試同一筆核銷', 'Retry this redemption')
                      : context.tr('確認核銷', 'Confirm redemption')),
            ),
            if (!_consumeUncertain)
              TextButton(
                  onPressed: _handled ? null : _resetScan,
                  child: Text(context.tr('取消', 'Cancel')))
            else
              TextButton(
                onPressed: _handled
                    ? null
                    : () => Navigator.of(context).pushNamed('/vendor/records'),
                child: Text(context.tr('核銷記錄', 'Redemption history')),
              ),
          ],
        ),
      ]),
    );
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('掃碼核銷', 'Scan to redeem'))),
      body: Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 990),
          child: LayoutBuilder(builder: (context, bounds) {
            final wide = bounds.maxWidth >= 710 &&
                MediaQuery.textScalerOf(context).scale(14) <= 19;
            final prepared = _prepared;
            Widget content;
            if (_errorReason != null) {
              content = _errorPage();
            } else if (_successCouponName != null) {
              content = _successPage();
            } else if (prepared != null && prepared['can_redeem'] == true) {
              content = _preparedCard(prepared);
            } else if (_branchChoices.isNotEmpty) {
              content = _branchChoice();
            } else if (wide) {
              content =
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Expanded(flex: 6, child: _manualEntry()),
                const SizedBox(width: 16),
                Expanded(flex: 5, child: _cameraEntry()),
              ]);
            } else {
              content = Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    _manualEntry(),
                    const SizedBox(height: 12),
                    _cameraEntry(),
                  ]);
            }
            return ListView(
              padding: EdgeInsets.all(wide ? 24 : 16),
              children: [
                Align(
                  alignment: Alignment.topCenter,
                  child: ConstrainedBox(
                    constraints: BoxConstraints(
                        maxWidth: wide &&
                                prepared == null &&
                                _errorReason == null &&
                                _successCouponName == null &&
                                _branchChoices.isEmpty
                            ? 990
                            : CgTheme.formMaxWidth),
                    child: content,
                  ),
                ),
              ],
            );
          }),
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

typedef _RedemptionDateRange = ({DateTime? from, DateTime? to});

class _RedemptionRecordsScreenState extends State<RedemptionRecordsScreen> {
  List<Map<String, dynamic>> _records = [];
  bool _loading = false;
  bool _exporting = false;
  String? _error;
  _RedemptionDateRange _range = (from: null, to: null);
  late final ApiClient _ownerApi;
  late final int _session;
  int _request = 0;
  int _exportRequest = 0;
  DialogRoute<void>? _csvDialog;

  bool get _sameSession =>
      mounted &&
      identical(widget.api, _ownerApi) &&
      _ownerApi.sessionRevision == _session;

  @override
  void initState() {
    super.initState();
    _ownerApi = widget.api;
    _session = _ownerApi.sessionRevision;
    _ownerApi.addListener(_clearExpiredSession);
    _load();
  }

  void _clearExpiredSession() {
    if (!mounted || _sameSession) return;
    ++_request;
    ++_exportRequest;
    final dialog = _csvDialog;
    _csvDialog = null;
    if (dialog != null) {
      // Dismiss only this screen's CSV, never an unrelated route.
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (dialog.isActive) dialog.navigator?.removeRoute(dialog);
      });
    }
    setState(() {
      _records = [];
      _loading = false;
      _exporting = false;
      _error = 'session';
    });
  }

  @override
  void didUpdateWidget(covariant RedemptionRecordsScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    _clearExpiredSession();
  }

  @override
  void dispose() {
    _ownerApi.removeListener(_clearExpiredSession);
    super.dispose();
  }

  Future<void> _load() async {
    if (!_sameSession) return;
    final range = _range;
    final request = ++_request;
    ++_exportRequest;
    setState(() {
      _loading = true;
      _exporting = false;
      _error = null;
    });
    try {
      final records =
          await widget.api.vendorRedemptions(from: range.from, to: range.to);
      if (!_sameSession || request != _request) return;
      setState(() {
        _records = records;
        _loading = false;
      });
    } catch (_) {
      if (!_sameSession || request != _request) return;
      setState(() {
        _records = [];
        _error = 'load';
        _loading = false;
      });
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
    final date = _dateLabel(isFrom ? _range.from : _range.to);
    return OutlinedButton(
      key: ValueKey(isFrom ? 'vendor-date-start' : 'vendor-date-end'),
      style: OutlinedButton.styleFrom(
        padding: const EdgeInsets.symmetric(
          horizontal: CgTheme.space8,
          vertical: CgTheme.space8,
        ),
      ),
      onPressed: _sameSession ? () => _pickDate(isFrom) : null,
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
    if (!_sameSession) return;
    final range = _range;
    final now = DateTime.now();
    final picked = await showDatePicker(
      context: context,
      initialDate:
          (isFrom ? range.from ?? range.to : range.to ?? range.from) ?? now,
      firstDate: isFrom
          ? now.subtract(const Duration(days: 365))
          : range.from ?? now.subtract(const Duration(days: 365)),
      lastDate: isFrom ? range.to ?? now : now,
    );
    if (picked == null || !_sameSession || _range != range) return;
    setState(() {
      _range = isFrom
          ? (from: picked, to: range.to)
          : (from: range.from, to: picked);
    });
    _load();
  }

  Future<void> _exportCsv() async {
    if (!_sameSession ||
        _loading ||
        _exporting ||
        _error != null ||
        _records.isEmpty) {
      return;
    }
    final range = _range;
    final request = ++_exportRequest;
    bool current() => _sameSession && request == _exportRequest;
    final scope = switch (range) {
      (from: final from?, to: final to?) => context.tr(
          '${_dateLabel(from)} 至 ${_dateLabel(to)}',
          '${_dateLabel(from)} to ${_dateLabel(to)}'),
      (from: final from?, to: null) =>
        context.tr('由 ${_dateLabel(from)} 起', 'From ${_dateLabel(from)}'),
      (from: null, to: final to?) =>
        context.tr('截至 ${_dateLabel(to)}', 'Through ${_dateLabel(to)}'),
      _ => context.tr('不限日期', 'All dates'),
    };
    setState(() => _exporting = true);
    try {
      final csv =
          await widget.api.vendorRedemptionsCsv(from: range.from, to: range.to);
      if (!mounted || !current()) return;
      final navigator = Navigator.of(context, rootNavigator: true);
      final dialog = DialogRoute<void>(
        context: context,
        themes: InheritedTheme.capture(from: context, to: navigator.context),
        builder: (c) => AlertDialog(
          title: Text(c.tr('核銷記錄 CSV', 'Redemption history CSV')),
          content: SingleChildScrollView(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(scope),
                SelectableText(
                    csv.length > 2000 ? '${csv.substring(0, 2000)}…' : csv),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () {
                if (!current()) return;
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
      _csvDialog = dialog;
      await navigator.push(dialog);
      if (identical(_csvDialog, dialog)) _csvDialog = null;
    } catch (_) {
      if (mounted && current()) {
        showCgToast(
            context, context.tr('無法匯出，請重試', 'Could not export. Try again.'));
      }
    } finally {
      if (current()) setState(() => _exporting = false);
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
              onPressed:
                  _loading || _exporting || _error != null || _records.isEmpty
                      ? null
                      : _exportCsv,
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
              if (_sameSession && (_range.from != null || _range.to != null))
                Align(
                  alignment: AlignmentDirectional.centerEnd,
                  child: Padding(
                    padding: const EdgeInsetsDirectional.only(
                        end: CgTheme.pageInset),
                    child: TextButton(
                      key: const ValueKey('vendor-date-clear'),
                      onPressed: () {
                        setState(() {
                          _range = (from: null, to: null);
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
                                Text(_error == 'session'
                                    ? context.tr('請重新開啟核銷記錄',
                                        'Reopen redemption history.')
                                    : context.tr('網絡不穩定，請重試',
                                        'Network unavailable. Try again.')),
                                if (_sameSession)
                                  const SizedBox(height: CgTheme.space12),
                                if (_sameSession)
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
