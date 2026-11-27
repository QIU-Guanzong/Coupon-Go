import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../models.dart';
import '../theme.dart';
import '../vendor_coupon_lifecycle.dart';
import '../widgets/merchant_layout.dart';
import '../widgets/widgets.dart';
import 'coupon_branch_scope.dart';
import 'vendor_coupon_form.dart';

const couponListingStatuses = [
  'active',
  'pending_review',
  'paused',
  'draft',
  'rejected',
  'expired',
  'sold_out',
  'scheduled',
];

String listingStatusLabel(BuildContext context, String status) =>
    switch (status) {
      'active' => context.tr('上架中', 'Live'),
      'pending_review' => context.tr('待審核', 'Pending review'),
      'paused' => context.tr('已暫停', 'Paused'),
      'draft' => context.tr('草稿', 'Draft'),
      'rejected' => context.tr('未通過', 'Rejected'),
      'expired' => context.tr('已過期', 'Expired'),
      'sold_out' => context.tr('已售罄', 'Sold out'),
      'scheduled' => context.tr('待上架', 'Scheduled'),
      _ => context.tr('未知狀態', 'Unknown status'),
    };

String _actionLabel(BuildContext context, VendorCouponAction action) =>
    switch (action) {
      VendorCouponAction.pause => context.tr('暫停上架', 'Pause'),
      VendorCouponAction.resume => context.tr('恢復上架', 'Resume'),
      VendorCouponAction.unlist => context.tr('下架', 'Unlist'),
      VendorCouponAction.submit => context.tr('提交審核', 'Submit for review'),
    };

DateTime? _hkDate(Object? value) {
  if (value is! String || value.isEmpty) return null;
  final offset =
      value.endsWith('Z') || RegExp(r'[+-]\d{2}:?\d{2}$').hasMatch(value);
  return DateTime.tryParse(offset ? value : '${value}Z')
      ?.toUtc()
      .add(const Duration(hours: 8));
}

String _shortDate(DateTime d, {bool time = false}) {
  final date =
      '${d.year}-${d.month.toString().padLeft(2, '0')}-${d.day.toString().padLeft(2, '0')}';
  return time
      ? '$date ${d.hour.toString().padLeft(2, '0')}:${d.minute.toString().padLeft(2, '0')}'
      : date;
}

class _CouponRowData {
  _CouponRowData(Map<String, dynamic> raw)
      : json = Map.unmodifiable(raw),
        coupon = Coupon.fromJson(raw) {
    if (coupon.id < 1 ||
        coupon.shopId < 1 ||
        coupon.name.trim().isEmpty ||
        coupon.packageId != null) {
      throw const FormatException('Invalid merchant coupon');
    }
  }
  final Map<String, dynamic> json;
  final Coupon coupon;
  _CouponRowData useStatus(String value) =>
      _CouponRowData({...json, 'status': value});
  String text(String key) => json[key] is String ? json[key] as String : '';
  String count(String key) => json[key] is int ? '${json[key]}' : '—';
  String money(String key) =>
      json[key] is num ? formatHkd((json[key] as num).toDouble()) : '—';
  String get dateRange {
    final from = _hkDate(json['valid_from']);
    final to = _hkDate(json['valid_to']);
    if (from == null || to == null) return '—';
    return '${_shortDate(from)} – ${_shortDate(to)}';
  }
}

/// Shared by the home shortcut and the full coupon workspace.
Future<bool> confirmVendorCouponAction(
  BuildContext context,
  String name,
  VendorCouponAction action,
) async {
  final label = _actionLabel(context, action);
  final accepted = await showDialog<bool>(
    context: context,
    builder: (dialog) {
      final consequences = switch (action) {
        VendorCouponAction.pause => dialog.tr(
            '「$name」將不再出現在公開列表，顧客不能再領取或購買。',
            '“$name” will be removed from public lists and cannot be newly claimed or purchased.',
          ),
        VendorCouponAction.unlist => dialog.tr(
            '「$name」將移回草稿、不再出現在公開列表，且不可新領或購買。可編輯後重新提交審核。',
            '“$name” will return to drafts, be removed from public lists and cannot be newly claimed or purchased. You can edit it and submit it again.',
          ),
        VendorCouponAction.resume => dialog.tr(
            '在有效期內而且仍有庫存時，顧客可再次看到「$name」。未到生效日期則回到待上架。',
            '“$name” can appear again when it is valid and in stock. If its start date is in the future, it will be scheduled.',
          ),
        VendorCouponAction.submit => dialog.tr(
            '「$name」將進入待審核。通過審核後才可上架。',
            '“$name” will go to review. Approval is required before it can go live.',
          ),
      };
      return AlertDialog(
        title: Text(action == VendorCouponAction.unlist
            ? dialog.tr('下架優惠券？', 'Unlist coupon?')
            : label),
        content: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(consequences),
              if (action == VendorCouponAction.pause ||
                  action == VendorCouponAction.unlist) ...[
                const SizedBox(height: CgTheme.space12),
                Text(dialog.tr(
                  '非購買取得的券不可核銷。已付款的券仍按原有核銷條件處理。',
                  'Coupons acquired without a purchase cannot be redeemed. Paid coupons remain subject to the original redemption checks.',
                )),
              ],
            ]),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(dialog, false),
              child: Text(dialog.tr('取消', 'Cancel'))),
          FilledButton(
              onPressed: () => Navigator.pop(dialog, true),
              child: Text(action == VendorCouponAction.unlist
                  ? dialog.tr('確認下架', 'Confirm unlist')
                  : dialog.tr('確認', 'Confirm'))),
        ],
      );
    },
  );
  return accepted == true;
}

void _success(BuildContext context, String status) {
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(context.tr('已更新 · ${listingStatusLabel(context, status)}',
          'Updated · ${listingStatusLabel(context, status)}'))));
}

void _failure(BuildContext context, Object error) {
  ScaffoldMessenger.of(context).showSnackBar(SnackBar(
      content: Text(error is ApiException
          ? error.message
          : context.tr('未能確認優惠券狀態，請重新載入。',
              'Could not confirm the coupon status. Reload the page.'))));
}

/// The same search and overview controls keep both offer inventories familiar.
class MerchantOfferHeading extends StatelessWidget {
  const MerchantOfferHeading({
    super.key,
    required this.title,
    required this.subtitle,
    required this.action,
    required this.icon,
    required this.onPressed,
  });

  final String title, subtitle, action;
  final IconData icon;
  final VoidCallback? onPressed;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final dark = theme.brightness == Brightness.dark;
    final foreground = dark ? theme.colorScheme.onSurface : CgColors.vendorInk;
    return Container(
      padding: const EdgeInsets.all(CgTheme.space20),
      decoration: BoxDecoration(
        color:
            dark ? theme.colorScheme.surfaceContainer : const Color(0xFFFFF4D9),
        borderRadius: BorderRadius.circular(16),
        border: Border.all(
            color: dark
                ? theme.colorScheme.outlineVariant
                : const Color(0xFFF1DBAC)),
      ),
      child: LayoutBuilder(builder: (context, bounds) {
        final heading = Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Semantics(
              header: true,
              child: Text(title,
                  style: theme.textTheme.headlineSmall
                      ?.copyWith(color: foreground)),
            ),
            const SizedBox(height: CgTheme.space4),
            Text(subtitle,
                style: theme.textTheme.bodyMedium?.copyWith(color: foreground)),
          ],
        );
        final create = FilledButton.icon(
          style: FilledButton.styleFrom(
            minimumSize: const Size(0, 50),
            backgroundColor: CgColors.vendorGold,
            foregroundColor: CgColors.vendorInk,
            padding: const EdgeInsets.symmetric(horizontal: CgTheme.space20),
          ),
          onPressed: onPressed,
          icon: Icon(icon),
          label: Text(action),
        );
        return bounds.maxWidth >= 570
            ? Row(children: [
                Expanded(child: heading),
                const SizedBox(width: CgTheme.space20),
                create,
              ])
            : Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
                heading,
                const SizedBox(height: CgTheme.space16),
                create,
              ]);
      }),
    );
  }
}

class MerchantOfferSearch extends StatelessWidget {
  const MerchantOfferSearch({
    super.key,
    required this.fieldKey,
    required this.controller,
    required this.label,
    required this.enabled,
    this.onChanged,
    this.onSearch,
  });

  final Key fieldKey;
  final TextEditingController controller;
  final String label;
  final bool enabled;
  final ValueChanged<String>? onChanged;
  final VoidCallback? onSearch;

  @override
  Widget build(BuildContext context) => ValueListenableBuilder(
        valueListenable: controller,
        builder: (context, value, _) => TextField(
          key: fieldKey,
          controller: controller,
          enabled: enabled,
          maxLength: 160,
          textInputAction: TextInputAction.search,
          decoration: InputDecoration(
            isDense: true,
            labelText: label,
            counterText: '',
            prefixIcon: const Icon(Icons.search),
            suffixIcon: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                if (value.text.isNotEmpty)
                  IconButton(
                    icon: const Icon(Icons.close),
                    tooltip: context.tr('清除搜尋', 'Clear search'),
                    onPressed: enabled
                        ? () {
                            controller.clear();
                            onChanged?.call('');
                            onSearch?.call();
                          }
                        : null,
                  ),
                if (onSearch != null)
                  IconButton(
                    tooltip: context.tr('搜尋', 'Search'),
                    onPressed: enabled ? onSearch : null,
                    icon: const Icon(Icons.arrow_forward),
                  ),
              ],
            ),
          ),
          onChanged: onChanged,
          onSubmitted: onSearch == null ? null : (_) => onSearch?.call(),
        ),
      );
}

class MerchantOfferEmpty extends StatelessWidget {
  const MerchantOfferEmpty({
    super.key,
    required this.icon,
    required this.title,
    this.description,
    this.action,
  });

  final IconData icon;
  final String title;
  final String? description;
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return Card(
      margin: EdgeInsets.zero,
      child: Padding(
        padding: const EdgeInsets.symmetric(
            horizontal: CgTheme.space20, vertical: 36),
        child: Column(
          children: [
            Icon(icon, color: theme.colorScheme.primary, size: 32),
            const SizedBox(height: CgTheme.space12),
            Text(title,
                textAlign: TextAlign.center,
                style: theme.textTheme.titleMedium),
            if (description != null) ...[
              const SizedBox(height: CgTheme.space4),
              Text(description!,
                  textAlign: TextAlign.center,
                  style: TextStyle(color: theme.colorScheme.onSurfaceVariant)),
            ],
            if (action != null) ...[
              const SizedBox(height: CgTheme.space8),
              action!,
            ],
          ],
        ),
      ),
    );
  }
}

class VendorCouponWorkspace extends StatefulWidget {
  const VendorCouponWorkspace({super.key, required this.api});
  final ApiClient api;

  @override
  State<VendorCouponWorkspace> createState() => _VendorCouponWorkspaceState();
}

class _VendorCouponWorkspaceState extends State<VendorCouponWorkspace> {
  final _search = TextEditingController();
  static const _pageSize = 20;
  late int _session;
  int _request = 0;
  String? _status;
  String _query = '';
  int _page = 1;
  int _total = 0;
  bool _loading = true;
  bool _error = false;
  bool _denied = false;
  List<_CouponRowData> _coupons = [];
  final _busy = <int>{};
  final _confirming = <int>{};
  final _verify = <int>{};
  bool _current(int session) =>
      mounted && session == _session && widget.api.sessionRevision == session;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_accountChanged);
    _load();
  }

  @override
  void dispose() {
    widget.api.removeListener(_accountChanged);
    _search.dispose();
    super.dispose();
  }

  void _accountChanged() {
    if (widget.api.sessionRevision == _session) return;
    ++_request;
    setState(() {
      _coupons = [];
      _loading = false;
      _denied = true;
      _busy.clear();
      _confirming.clear();
      _verify.clear();
    });
  }

  Future<void> _load() async {
    final session = _session;
    if (!_current(session)) return;
    final request = ++_request;
    setState(() {
      _loading = true;
      _error = false;
      _denied = false;
    });
    try {
      final result = await widget.api.vendorCouponPage(
          page: _page, pageSize: _pageSize, status: _status, query: _query);
      if (!_current(session) || request != _request) return;
      final raw = result['coupons'];
      final total = result['total'];
      if (raw is! List ||
          total is! int ||
          total < 0 ||
          result['page'] != _page ||
          result['page_size'] != _pageSize ||
          raw.length > _pageSize ||
          raw.length > total) {
        throw const FormatException('Invalid vendor coupon page');
      }
      final rows = raw
          .map((row) => _CouponRowData(Map<String, dynamic>.from(row as Map)))
          .toList();
      if (rows.map((row) => row.coupon.id).toSet().length != rows.length ||
          (_status != null &&
              rows.any((row) => row.coupon.status != _status))) {
        throw const FormatException('Inconsistent coupon page');
      }
      setState(() {
        _coupons = rows;
        _total = total;
        _verify.clear();
      });
    } catch (error) {
      if (!_current(session) || request != _request) return;
      setState(() {
        _denied = error is ApiException &&
            (error.status == 401 || error.status == 403);
        _error = !_denied;
        _coupons = [];
      });
    } finally {
      if (_current(session) && request == _request) {
        setState(() => _loading = false);
      }
    }
  }

  void _filters({required String? status, required String query}) {
    setState(() {
      _status = status;
      _query = query;
      _page = 1;
    });
    _load();
  }

  void _goPage(int page) {
    setState(() => _page = page);
    _load();
  }

  Future<void> _action(_CouponRowData item, VendorCouponAction action) async {
    final id = item.coupon.id;
    final request = _request;
    final session = _session;
    bool current() => _current(session) && request == _request;
    bool eligible() =>
        current() &&
        !_verify.contains(id) &&
        _coupons.any((row) => identical(row, item)) &&
        VendorCouponAction.forManagementStatus(item.coupon.status)
            .contains(action);
    if (!eligible() || _busy.contains(id) || _confirming.contains(id)) return;
    setState(() => _confirming.add(id));
    try {
      final confirmed =
          await confirmVendorCouponAction(context, item.coupon.name, action);
      if (!confirmed || !eligible()) return;
      setState(() {
        _confirming.remove(id);
        _busy.add(id);
      });
      final status = await widget.api.updateVendorCouponStatus(id, action);
      if (mounted && current()) {
        _patchStatus(id, status);
        _success(context, status);
      }
    } catch (error) {
      if (mounted && current()) {
        setState(() => _verify.add(id));
        _failure(context, error);
      }
    } finally {
      if (_current(session)) {
        setState(() {
          _busy.remove(id);
          _confirming.remove(id);
        });
      }
    }
  }

  void _patchStatus(int id, String status) {
    setState(() {
      final index = _coupons.indexWhere((row) => row.coupon.id == id);
      if (index < 0) return;
      if (_status != null && _status != status) {
        _coupons.removeAt(index);
        if (_total > 0) _total--;
      } else {
        _coupons[index] = _coupons[index].useStatus(status);
      }
    });
  }

  Future<void> _openDetail(_CouponRowData item) async {
    final session = _session;
    final result = await Navigator.of(context)
        .pushNamed<String>('/vendor/coupons/${item.coupon.id}');
    if (result != null && _current(session)) {
      _patchStatus(item.coupon.id, result);
      // The detail screen can also return after editing names, amounts or dates.
      await _load();
    }
  }

  Future<void> _issue() async {
    await Navigator.of(context).pushNamed('/vendor/issue');
    if (_current(_session)) await _load();
  }

  bool get _hasFilters => _status != null || _query.isNotEmpty;

  void _clearFilters() {
    _search.clear();
    _filters(status: null, query: '');
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    return Scaffold(
      appBar: AppBar(
        title: Text(t('券管理', 'Coupon management')),
        actions: [
          IconButton(
            tooltip: t('重新載入', 'Reload'),
            onPressed: _loading || _denied || _busy.isNotEmpty ? null : _load,
            icon: const Icon(Icons.refresh),
          ),
        ],
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 1200),
          child: RefreshIndicator(
            onRefresh: _load,
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(CgTheme.pageInset),
              children: [
                if (_denied)
                  MerchantOfferEmpty(
                    icon: Icons.lock_outline,
                    title: t('沒有權限查看優惠券。',
                        'You do not have access to these coupons.'),
                  )
                else ...[
                  MerchantOfferHeading(
                    title: t('優惠券庫存', 'Coupon inventory'),
                    subtitle: t('查看庫存、核銷和上架狀態。',
                        'Track remaining stock, redemptions and listing status.'),
                    action: t('發券', 'Issue coupon'),
                    icon: Icons.add,
                    onPressed:
                        _loading || _error || _busy.isNotEmpty ? null : _issue,
                  ),
                  const SizedBox(height: CgTheme.space16),
                  Card(
                    margin: EdgeInsets.zero,
                    child: Padding(
                      padding: const EdgeInsets.all(CgTheme.space12),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          MerchantOfferSearch(
                            fieldKey: const ValueKey('coupon-workspace-search'),
                            controller: _search,
                            enabled: _busy.isEmpty,
                            label: t('搜尋券名', 'Search coupon names'),
                            onSearch: () => _filters(
                                status: _status, query: _search.text.trim()),
                          ),
                          const SizedBox(height: CgTheme.space8),
                          SingleChildScrollView(
                            scrollDirection: Axis.horizontal,
                            child: Row(children: [
                              for (final status in <String?>[
                                null,
                                ...couponListingStatuses
                              ])
                                Padding(
                                  padding: const EdgeInsetsDirectional.only(
                                      end: CgTheme.space8),
                                  child: ChoiceChip(
                                    key: ValueKey(
                                        'coupon-filter-${status ?? 'all'}'),
                                    label: Text(status == null
                                        ? t('全部', 'All')
                                        : listingStatusLabel(context, status)),
                                    selected: _status == status,
                                    onSelected: _busy.isNotEmpty
                                        ? null
                                        : (_) => _filters(
                                            status: status,
                                            query: _search.text.trim()),
                                  ),
                                ),
                            ]),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: CgTheme.space16),
                  if (_loading)
                    const SkeletonBox(height: 164)
                  else if (_error)
                    MerchantOfferEmpty(
                      icon: Icons.cloud_off_outlined,
                      title: t('無法讀取優惠券。', 'Could not load coupons.'),
                      action: TextButton(
                          onPressed: _load, child: Text(t('重試', 'Retry'))),
                    )
                  else if (_coupons.isEmpty && _total == 0)
                    MerchantOfferEmpty(
                      icon: _hasFilters
                          ? Icons.search_off
                          : Icons.local_offer_outlined,
                      title: _hasFilters
                          ? t('沒有符合條件的優惠券。', 'No coupons match these filters.')
                          : t('尚未建立優惠券', 'No coupons yet'),
                      description: _hasFilters
                          ? null
                          : t('發券後可在此查看庫存和核銷。',
                              'Issued coupons appear here with stock and redemption counts.'),
                      action: _hasFilters
                          ? TextButton(
                              onPressed: _clearFilters,
                              child: Text(t('清除篩選', 'Clear filters')))
                          : null,
                    )
                  else ...[
                    Padding(
                      padding: const EdgeInsets.only(bottom: CgTheme.space8),
                      child: Semantics(
                        liveRegion: true,
                        child: Text(
                          t('共 $_total 張優惠券',
                              '$_total ${_total == 1 ? 'coupon' : 'coupons'}'),
                          style: Theme.of(context).textTheme.labelLarge,
                        ),
                      ),
                    ),
                    for (final row in _coupons)
                      _CouponCard(
                        key: ValueKey('managed-coupon-${row.coupon.id}'),
                        row: row,
                        baseUrl: widget.api.baseUrl,
                        onDetail: _busy.contains(row.coupon.id)
                            ? null
                            : () => _openDetail(row),
                        busy: _busy.contains(row.coupon.id),
                        verify: _verify.contains(row.coupon.id),
                        onAction: (action) => _action(row, action),
                      ),
                    if (_page > 1 || _total > _pageSize)
                      Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            IconButton(
                              onPressed: _busy.isNotEmpty || _page < 2
                                  ? null
                                  : () => _goPage(_page - 1),
                              icon: const Icon(Icons.chevron_left),
                              tooltip: t('上一頁', 'Previous page'),
                            ),
                            Text(t('第 $_page 頁', 'Page $_page')),
                            IconButton(
                              onPressed: _busy.isNotEmpty ||
                                      _page * _pageSize >= _total
                                  ? null
                                  : () => _goPage(_page + 1),
                              icon: const Icon(Icons.chevron_right),
                              tooltip: t('下一頁', 'Next page'),
                            ),
                          ]),
                  ],
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Compact numeric cells use tabular figures and keep their labels at any width.
class MerchantOfferMetrics extends StatelessWidget {
  const MerchantOfferMetrics({super.key, required this.values});

  final List<(String, String)> values;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    return DecoratedBox(
      decoration: BoxDecoration(
        color: theme.colorScheme.surfaceContainerLow,
        borderRadius: BorderRadius.circular(10),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(
            horizontal: CgTheme.space8, vertical: CgTheme.space8),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            for (final (label, value) in values)
              Expanded(
                child: Padding(
                  padding:
                      const EdgeInsets.symmetric(horizontal: CgTheme.space4),
                  child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(value,
                            style: theme.textTheme.titleSmall?.copyWith(
                              fontFeatures: tabularFigures.fontFeatures,
                              fontWeight: FontWeight.w700,
                            )),
                        Text(label,
                            style: theme.textTheme.labelSmall?.copyWith(
                              color: theme.colorScheme.onSurfaceVariant,
                            )),
                      ]),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class _CouponCard extends StatelessWidget {
  const _CouponCard(
      {super.key,
      required this.row,
      required this.baseUrl,
      required this.onAction,
      this.onDetail,
      this.onEditBranches,
      this.onEdit,
      this.detailed = false,
      required this.busy,
      required this.verify});
  final _CouponRowData row;
  final String baseUrl;
  final ValueChanged<VendorCouponAction> onAction;
  final VoidCallback? onDetail, onEditBranches, onEdit;
  final bool detailed, busy, verify;

  @override
  Widget build(BuildContext context) {
    final c = row.coupon;
    final nameEn = row.text('name_en').trim();
    final reason = row.text('rejection_reason');
    final start = _hkDate(row.json['valid_from']);
    final type = switch (c.type) {
      'Cash' => context.tr('現金券', 'Cash coupon'),
      'Discount' => context.tr('折扣券', 'Discount'),
      'Gift' => context.tr('贈品券', 'Gift'),
      'Price' => context.tr('價格券', 'Price coupon'),
      _ => context.tr('優惠券', 'Coupon'),
    };
    final amount = context.tr(c.amountText, c.amountTextEnglish);
    final price = c.isPaid ? row.money('sale_price') : context.tr('免費', 'Free');
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final t = context.tr;
    final actions = VendorCouponAction.forManagementStatus(c.status);
    final summary = Row(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        ClipRRect(
          borderRadius: BorderRadius.circular(10),
          child: SizedBox(
            width: detailed ? 68 : 54,
            height: detailed ? 68 : 54,
            child: CatalogPhoto(
                url: c.image, height: detailed ? 68 : 54, apiBaseUrl: baseUrl),
          ),
        ),
        const SizedBox(width: CgTheme.space12),
        Expanded(
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Semantics(
                header: detailed,
                child: Text(c.name,
                    style: detailed
                        ? theme.textTheme.titleLarge
                        : theme.textTheme.titleMedium)),
            if (nameEn.isNotEmpty && nameEn != c.name)
              Text(nameEn, style: theme.textTheme.bodySmall),
            const SizedBox(height: CgTheme.space4),
            Text(c.shopName, style: TextStyle(color: scheme.onSurfaceVariant)),
            const SizedBox(height: CgTheme.space4),
            Text(
                [type, if (amount.isNotEmpty && amount != c.name) amount, price]
                    .join(' · '),
                style: theme.textTheme.bodySmall),
          ]),
        ),
        if (onDetail != null) ...[
          const SizedBox(width: CgTheme.space4),
          Icon(Icons.chevron_right, color: scheme.onSurfaceVariant),
        ],
      ],
    );
    final identity = onDetail == null
        ? summary
        : InkWell(
            onTap: onDetail,
            borderRadius: BorderRadius.circular(10),
            child: Padding(
                padding: const EdgeInsets.symmetric(vertical: 2),
                child: summary),
          );
    final details =
        Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      MerchantStatusPill(
        label: listingStatusLabel(context, c.status),
        active: c.status == 'active',
        listingStatus: c.status,
      ),
      const SizedBox(height: CgTheme.space8),
      Text(t('有效期（香港時間）', 'Validity (Hong Kong time)'),
          style: theme.textTheme.labelSmall
              ?.copyWith(color: scheme.onSurfaceVariant)),
      Text(row.dateRange, style: theme.textTheme.bodySmall),
      if (c.status == 'scheduled' && start != null)
        Text(
            t('待上架 · ${_shortDate(start, time: true)}',
                'Scheduled · ${_shortDate(start, time: true)}'),
            style: theme.textTheme.bodySmall),
      if (c.status == 'rejected' && reason.trim().isNotEmpty)
        Text(reason,
            style: theme.textTheme.bodySmall?.copyWith(color: scheme.error)),
    ]);
    final metrics = MerchantOfferMetrics(values: [
      (t('剩餘', 'Remaining'), row.count('remaining')),
      (t('已領', 'Claimed'), row.count('downloads')),
      (t('已核銷', 'Redeemed'), row.count('used_count')),
    ]);
    return Card(
      margin: const EdgeInsets.only(bottom: CgTheme.space8),
      clipBehavior: Clip.antiAlias,
      child: Padding(
        padding: const EdgeInsets.all(CgTheme.pageInset),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          LayoutBuilder(builder: (context, bounds) {
            return bounds.maxWidth >= 760
                ? Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                    Expanded(flex: 5, child: identity),
                    const SizedBox(width: CgTheme.space20),
                    Expanded(flex: 4, child: metrics),
                    const SizedBox(width: CgTheme.space20),
                    Expanded(flex: 3, child: details),
                  ])
                : Column(
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                        identity,
                        const SizedBox(height: CgTheme.space12),
                        metrics,
                        const SizedBox(height: CgTheme.space12),
                        details,
                      ]);
          }),
          if (detailed) ...[
            const SizedBox(height: CgTheme.space16),
            const Divider(),
            const SizedBox(height: CgTheme.space8),
            Text(t('使用條件', 'Terms of use'), style: theme.textTheme.titleMedium),
            MerchantAmountRow(
                label: t('每人限領', 'Limit per person'),
                value: row.count('per_user_limit')),
            MerchantAmountRow(
                label: t('需消費金額', 'Minimum spend'),
                value: row.money('min_spend')),
            if (c.isPaid)
              MerchantAmountRow(
                label: t('退款政策', 'Refund policy'),
                value: switch (row.text('refund_policy')) {
                  'anytime' => t('未用可退', 'Refundable before use'),
                  'expiry_only' => t('到期可退', 'Refundable on expiry'),
                  'non_refundable' => t('不可退款', 'Non-refundable'),
                  _ => '—',
                },
              ),
            if (c.terms.trim().isNotEmpty) Text(c.terms),
            if (row.json['branch_ids'] is List)
              MerchantAmountRow(
                label: t('適用分店', 'Branches'),
                value: _branches(context, row.json),
              ),
            if (VendorCouponAction.canEditListing(c.status) &&
                onEdit != null) ...[
              const SizedBox(height: CgTheme.space16),
              MerchantPrimaryAction(
                label: t('編輯優惠券', 'Edit coupon'),
                onPressed: busy || verify ? null : onEdit,
              ),
            ],
            if (VendorCouponAction.canEditListing(c.status) &&
                onEditBranches != null)
              TextButton(
                onPressed: busy || verify ? null : onEditBranches,
                child: Text(t('編輯適用分店', 'Edit eligible branches')),
              ),
          ],
          if (actions.isNotEmpty) ...[
            const SizedBox(height: CgTheme.space8),
            const Divider(height: 12),
            Wrap(spacing: CgTheme.space4, children: [
              for (final action in actions)
                TextButton(
                  key: ValueKey('coupon-${c.id}-${action.endpoint}'),
                  onPressed: busy || verify ? null : () => onAction(action),
                  child: Text(busy
                      ? t('處理中…', 'Working…')
                      : _actionLabel(context, action)),
                ),
            ]),
          ],
        ]),
      ),
    );
  }

  String _branches(BuildContext context, Map<String, dynamic> json) {
    final ids = json['branch_ids'] as List;
    if (ids.isEmpty) return context.tr('所有分店', 'All branches');
    final raw = json['branches'];
    if (raw is! List || raw.isEmpty) return '—';
    return raw
        .whereType<Map>()
        .map((row) => row['name'])
        .whereType<String>()
        .join(' · ');
  }
}

class VendorCouponDetail extends StatefulWidget {
  const VendorCouponDetail(
      {super.key, required this.api, required this.couponId});
  final ApiClient api;
  final int couponId;

  @override
  State<VendorCouponDetail> createState() => _VendorCouponDetailState();
}

class _VendorCouponDetailState extends State<VendorCouponDetail> {
  late final int _session;
  _CouponRowData? _row;
  bool _loading = true, _busy = false, _confirming = false, _verify = false;
  String? _changedStatus;
  bool get _current => mounted && widget.api.sessionRevision == _session;
  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_onAccountChanged);
    _load();
  }

  @override
  void dispose() {
    widget.api.removeListener(_onAccountChanged);
    super.dispose();
  }

  void _onAccountChanged() {
    if (!_current && mounted) {
      setState(() {
        _row = null;
        _loading = false;
      });
    }
  }

  Future<void> _load() async {
    if (!_current) return;
    setState(() => _loading = true);
    try {
      final data = await widget.api.vendorCouponDetails(widget.couponId);
      if (!_current) return;
      final item =
          _CouponRowData(Map<String, dynamic>.from(data['coupon'] as Map));
      if (item.coupon.id != widget.couponId) {
        throw const FormatException('Mismatched coupon');
      }
      setState(() {
        _row = item;
        _verify = false;
      });
    } catch (error) {
      if (_current) setState(() => _row = null);
    } finally {
      if (_current) setState(() => _loading = false);
    }
  }

  Future<void> _editCoupon(_CouponRowData row) async {
    if (!_current ||
        _busy ||
        _confirming ||
        _verify ||
        !VendorCouponAction.canEditListing(row.coupon.status)) {
      return;
    }
    setState(() => _confirming = true);
    try {
      final saved = await Navigator.of(context).push<Map<String, dynamic>>(
        MaterialPageRoute(
            builder: (_) => IssueCouponScreen(
                  api: widget.api,
                  editCoupon: row.json,
                )),
      );
      if (!mounted || !_current || saved == null) return;
      final updated = _CouponRowData(saved);
      if (updated.coupon.id != row.coupon.id ||
          updated.coupon.shopId != row.coupon.shopId) {
        throw const FormatException('Mismatched coupon receipt');
      }
      setState(() {
        _row = updated;
        _changedStatus = updated.coupon.status;
      });
      _success(context, updated.coupon.status);
    } catch (error) {
      if (mounted && _current) {
        setState(() => _verify = true);
        _failure(context, error);
      }
    } finally {
      if (_current) setState(() => _confirming = false);
    }
  }

  Future<void> _editBranches(_CouponRowData row) async {
    if (!_current || _busy || _verify) return;
    final coupon = row.coupon;
    final changed = await Navigator.of(context).push<bool>(MaterialPageRoute(
      builder: (_) => CouponBranchEditScreen(
        api: widget.api,
        couponId: coupon.id,
        shopId: coupon.shopId,
        couponName: coupon.name,
      ),
    ));
    if (_current && changed == true) await _load();
  }

  Future<void> _action(VendorCouponAction action) async {
    final row = _row;
    if (!_current ||
        _busy ||
        _confirming ||
        _verify ||
        row == null ||
        !VendorCouponAction.forManagementStatus(row.coupon.status)
            .contains(action)) {
      return;
    }
    setState(() => _confirming = true);
    try {
      final confirmed =
          await confirmVendorCouponAction(context, row.coupon.name, action);
      if (!confirmed || !_current || !identical(row, _row)) return;
      setState(() {
        _confirming = false;
        _busy = true;
      });
      final status =
          await widget.api.updateVendorCouponStatus(row.coupon.id, action);
      if (mounted && _current) {
        setState(() {
          _row = row.useStatus(status);
          _changedStatus = status;
        });
        _success(context, status);
      }
    } catch (error) {
      if (mounted && _current) {
        setState(() => _verify = true);
        _failure(context, error);
      }
    } finally {
      if (_current) {
        setState(() {
          _busy = false;
          _confirming = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => PopScope<String>(
        canPop: _changedStatus == null && !_busy,
        onPopInvokedWithResult: (didPop, _) {
          if (!didPop && !_busy && Navigator.of(context).canPop()) {
            Navigator.of(context).pop(_changedStatus);
          }
        },
        child: Scaffold(
          appBar: AppBar(
              title: Text(context.tr('優惠券詳情', 'Coupon details')),
              actions: [
                IconButton(
                    tooltip: context.tr('重新載入', 'Reload'),
                    onPressed: !_current || _busy || _loading ? null : _load,
                    icon: const Icon(Icons.refresh)),
              ]),
          body: Center(
              child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 1200),
            child: ListView(
                padding: const EdgeInsets.all(CgTheme.pageInset),
                children: [
                  if (!_current)
                    Text(context.tr(
                        '沒有權限查看優惠券。', 'You do not have access to this coupon.'))
                  else if (_loading)
                    const SkeletonBox(height: 200)
                  else if (_row case final row?)
                    _CouponCard(
                        row: row,
                        baseUrl: widget.api.baseUrl,
                        onAction: _action,
                        onEditBranches: () => _editBranches(row),
                        onEdit: () => _editCoupon(row),
                        detailed: true,
                        busy: _busy || _confirming,
                        verify: _verify)
                  else ...[
                    Text(context.tr(
                        '無法讀取優惠券詳情。', 'Could not load coupon details.')),
                    TextButton(
                        onPressed: _load,
                        child: Text(context.tr('重試', 'Retry'))),
                  ],
                ]),
          )),
        ),
      );
}
