import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../models.dart';
import '../theme.dart';
import '../vendor_coupon_lifecycle.dart';
import '../widgets/merchant_layout.dart';
import '../widgets/widgets.dart';
import 'coupon_branch_scope.dart';

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
    }
  }

  Future<void> _issue() async {
    await Navigator.of(context).pushNamed('/vendor/issue');
    if (_current(_session)) await _load();
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          title: Text(context.tr('券管理', 'Coupon management')),
          actions: [
            IconButton(
                tooltip: context.tr('重新載入', 'Reload'),
                onPressed:
                    _loading || _denied || _busy.isNotEmpty ? null : _load,
                icon: const Icon(Icons.refresh))
          ],
        ),
        body: Center(
            child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: CgTheme.listMaxWidth),
          child: ListView(
              padding: const EdgeInsets.all(CgTheme.pageInset),
              children: [
                if (_denied)
                  Text(context.tr(
                      '沒有權限查看優惠券。', 'You do not have access to these coupons.'))
                else ...[
                  MerchantPrimaryAction(
                      label: context.tr('發券', 'Issue coupon'),
                      icon: Icons.add,
                      onPressed: _loading || _error || _busy.isNotEmpty
                          ? null
                          : _issue),
                  const SizedBox(height: CgTheme.space12),
                  TextField(
                      key: const ValueKey('coupon-workspace-search'),
                      controller: _search,
                      enabled: _busy.isEmpty,
                      maxLength: 160,
                      textInputAction: TextInputAction.search,
                      decoration: InputDecoration(
                          counterText: '',
                          labelText: context.tr('搜尋券名', 'Search coupon names'),
                          suffixIcon: IconButton(
                              icon: const Icon(Icons.search),
                              tooltip: context.tr('搜尋', 'Search'),
                              onPressed: _busy.isNotEmpty
                                  ? null
                                  : () => _filters(
                                      status: _status,
                                      query: _search.text.trim()))),
                      onSubmitted: (_) => _filters(
                          status: _status, query: _search.text.trim())),
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
                                    ? context.tr('全部', 'All')
                                    : listingStatusLabel(context, status)),
                                selected: _status == status,
                                onSelected: _busy.isNotEmpty
                                    ? null
                                    : (_) =>
                                        _filters(status: status, query: _query),
                              )),
                      ])),
                  const SizedBox(height: CgTheme.space12),
                  if (_loading)
                    const SkeletonBox(height: 164)
                  else if (_error) ...[
                    Text(context.tr('無法讀取優惠券。', 'Could not load coupons.')),
                    TextButton(
                        onPressed: _load,
                        child: Text(context.tr('重試', 'Retry'))),
                  ] else ...[
                    if (_coupons.isEmpty)
                      Text(_status == null && _query.isEmpty
                          ? context.tr('暫時沒有優惠券，點上面發第一張吧！',
                              'No coupons yet. Issue your first coupon above.')
                          : context.tr('沒有符合條件的優惠券。',
                              'No coupons match these filters.')),
                    if (_coupons.isEmpty &&
                        (_status != null || _query.isNotEmpty))
                      TextButton(
                          onPressed: () {
                            _search.clear();
                            _filters(status: null, query: '');
                          },
                          child: Text(context.tr('清除篩選', 'Clear filters'))),
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
                          onAction: (action) => _action(row, action)),
                    if (_page > 1 || _total > _pageSize)
                      Row(
                          mainAxisAlignment: MainAxisAlignment.spaceBetween,
                          children: [
                            IconButton(
                                onPressed: _busy.isNotEmpty || _page < 2
                                    ? null
                                    : () => _goPage(_page - 1),
                                icon: const Icon(Icons.chevron_left),
                                tooltip: context.tr('上一頁', 'Previous page')),
                            Text(context.tr('第 $_page 頁', 'Page $_page')),
                            IconButton(
                                onPressed: _busy.isNotEmpty ||
                                        _page * _pageSize >= _total
                                    ? null
                                    : () => _goPage(_page + 1),
                                icon: const Icon(Icons.chevron_right),
                                tooltip: context.tr('下一頁', 'Next page')),
                          ]),
                  ],
                ],
              ]),
        )),
      );
}

class _CouponCard extends StatelessWidget {
  const _CouponCard(
      {super.key,
      required this.row,
      required this.baseUrl,
      required this.onAction,
      this.onDetail,
      this.onEditBranches,
      this.detailed = false,
      required this.busy,
      required this.verify});
  final _CouponRowData row;
  final String baseUrl;
  final ValueChanged<VendorCouponAction> onAction;
  final VoidCallback? onDetail, onEditBranches;
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
    final scheme = Theme.of(context).colorScheme;
    return Card(
      margin: const EdgeInsets.only(bottom: CgTheme.space8),
      clipBehavior: Clip.antiAlias,
      child: Padding(
          padding: const EdgeInsets.all(CgTheme.pageInset),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            InkWell(
              onTap: onDetail,
              child:
                  Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                ClipRRect(
                    borderRadius: BorderRadius.circular(8),
                    child: SizedBox(
                        width: 52,
                        height: 52,
                        child: CatalogPhoto(
                            url: c.image, height: 52, apiBaseUrl: baseUrl))),
                const SizedBox(width: CgTheme.space12),
                Expanded(
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                      Text(c.name,
                          style: Theme.of(context).textTheme.titleMedium),
                      if (nameEn.isNotEmpty && nameEn != c.name) Text(nameEn),
                      Text(c.shopName,
                          style: TextStyle(color: scheme.onSurfaceVariant)),
                    ])),
                const SizedBox(width: CgTheme.space8),
                Flexible(
                    child: MerchantStatusPill(
                        label: listingStatusLabel(context, c.status),
                        active: c.status == 'active',
                        listingStatus: c.status)),
                if (onDetail != null) const Icon(Icons.chevron_right),
              ]),
            ),
            const SizedBox(height: CgTheme.space8),
            Text([
              type,
              if (amount.isNotEmpty && amount != c.name) amount,
              price
            ].join(' · ')),
            Text(context.tr(
                '剩餘 ${row.count('remaining')} · 已領 ${row.count('downloads')} · 已核銷 ${row.count('used_count')}',
                '${row.count('remaining')} left · ${row.count('downloads')} claimed · ${row.count('used_count')} redeemed')),
            Text(context.tr('有效期（香港時間） ${row.dateRange}',
                'Validity (Hong Kong time) ${row.dateRange}')),
            if (c.status == 'scheduled' && start != null)
              Text(context.tr('待上架 · ${_shortDate(start, time: true)}',
                  'Scheduled · ${_shortDate(start, time: true)}')),
            if (c.status == 'rejected' && reason.trim().isNotEmpty)
              Text(reason,
                  style: Theme.of(context)
                      .textTheme
                      .bodySmall
                      ?.copyWith(color: scheme.error)),
            if (detailed) ...[
              const SizedBox(height: CgTheme.space16),
              Text(context.tr('使用條件', 'Terms of use'),
                  style: Theme.of(context).textTheme.titleMedium),
              MerchantAmountRow(
                  label: context.tr('每人限領', 'Limit per person'),
                  value: row.count('per_user_limit')),
              MerchantAmountRow(
                  label: context.tr('需消費金額', 'Minimum spend'),
                  value: row.money('min_spend')),
              if (c.isPaid)
                MerchantAmountRow(
                    label: context.tr('退款政策', 'Refund policy'),
                    value: switch (row.text('refund_policy')) {
                      'anytime' => context.tr('未用可退', 'Refundable before use'),
                      'expiry_only' =>
                        context.tr('到期可退', 'Refundable on expiry'),
                      'non_refundable' => context.tr('不可退款', 'Non-refundable'),
                      _ => '—',
                    }),
              if (c.terms.trim().isNotEmpty) Text(c.terms),
              if (row.json['branch_ids'] is List)
                Text(context.tr('適用分店：', 'Branches: ') +
                    _branches(context, row.json)),
              if (VendorCouponAction.canEditListing(c.status) &&
                  onEditBranches != null)
                TextButton(
                  onPressed: busy || verify ? null : onEditBranches,
                  child: Text(context.tr('編輯適用分店', 'Edit eligible branches')),
                ),
            ],
            if (VendorCouponAction.forManagementStatus(c.status).isNotEmpty)
              Wrap(spacing: CgTheme.space4, children: [
                for (final action
                    in VendorCouponAction.forManagementStatus(c.status))
                  TextButton(
                      key: ValueKey('coupon-${c.id}-${action.endpoint}'),
                      onPressed: busy || verify ? null : () => onAction(action),
                      child: Text(busy
                          ? context.tr('處理中…', 'Working…')
                          : _actionLabel(context, action))),
              ]),
          ])),
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
            constraints: const BoxConstraints(maxWidth: CgTheme.listMaxWidth),
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
                        detailed: true,
                        busy: _busy,
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
