import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../theme.dart';
import '../vendor_coupon_lifecycle.dart';
import '../widgets/merchant_layout.dart';

import '../widgets/widgets.dart';
import 'vendor_coupon_workspace.dart'
    show
        MerchantOfferEmpty,
        MerchantOfferHeading,
        MerchantOfferMetrics,
        MerchantOfferSearch,
        couponListingStatuses;

/// Merchant package workspace. Server status is always the source of truth.
class VendorPackagesScreen extends StatefulWidget {
  const VendorPackagesScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<VendorPackagesScreen> createState() => _VendorPackagesScreenState();
}

class _VendorPackagesScreenState extends State<VendorPackagesScreen> {
  final _search = TextEditingController();
  List<Map<String, dynamic>> _shops = [];
  List<Map<String, dynamic>> _packages = [];
  late final int _session;
  int _request = 0;
  bool _loading = true;
  int? _busyId;
  String? _error;
  String _query = '';
  String _statusFilter = '';
  int? _shopFilter;

  bool get _current => mounted && widget.api.sessionRevision == _session;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_accountChanged);
    _load();
  }

  void _accountChanged() {
    if (_current || !mounted) return;
    ++_request;
    setState(() {
      _shops = [];
      _packages = [];
      _loading = false;
      _error = null;
      _busyId = null;
    });
  }

  @override
  void dispose() {
    widget.api.removeListener(_accountChanged);
    _search.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    if (!_current) return;
    final request = ++_request;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await widget.api.vendorPackageWorkspace();
      if (!_current || request != _request) return;
      setState(() {
        _shops = (result['shops'] as List? ?? [])
            .whereType<Map>()
            .map((x) => Map<String, dynamic>.from(x))
            .toList();
        _packages = (result['packages'] as List? ?? [])
            .whereType<Map>()
            .map((x) => Map<String, dynamic>.from(x))
            .toList();
        if (!_shops.any((shop) => shop['id'] == _shopFilter)) {
          _shopFilter = null;
        }
        _loading = false;
      });
    } catch (e) {
      if (!_current || request != _request) return;
      setState(() {
        _loading = false;
        _error = e is ApiException
            ? e.message
            : context.tr('請稍後再試', 'Please try again');
      });
    }
  }

  Future<void> _edit([Map<String, dynamic>? item]) async {
    if (!_current || _loading || _busyId != null || _shops.isEmpty) return;
    final result = await Navigator.of(context).push<Map<String, dynamic>>(
        MaterialPageRoute(
            builder: (_) => VendorPackageEditor(
                api: widget.api, shops: _shops, package: item)));
    if (!mounted || !_current || result == null) return;
    if (result['_refresh_only'] == true) {
      await _load();
      return;
    }
    final status = '${result['status'] ?? ''}';
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(
            '${context.tr('已儲存', 'Saved')} · ${_status(context, status)}')));
    await _load();
  }

  Future<void> _action(
      Map<String, dynamic> item, VendorCouponAction action) async {
    final id = item['id'] as int;
    if (!_current || _loading || _busyId != null || !_packages.contains(item)) {
      return;
    }
    if (!VendorCouponAction.forManagementStatus('${item['status']}')
        .contains(action)) {
      return;
    }
    setState(() => _busyId = id);
    try {
      if (action == VendorCouponAction.unlist) {
        final t = context.tr;
        final confirmed = await showDialog<bool>(
          context: context,
          builder: (dialog) => AlertDialog(
            title: Text(t('下架套餐？', 'Unlist package?')),
            content: Text(t('套餐將移回草稿，顧客不能再購買。已付款的訂單仍可查看。',
                'The package will return to drafts and customers can no longer buy it. Paid orders remain available.')),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(dialog).pop(false),
                child: Text(t('取消', 'Cancel')),
              ),
              FilledButton(
                onPressed: () => Navigator.of(dialog).pop(true),
                child: Text(t('確認下架', 'Confirm unlist')),
              ),
            ],
          ),
        );
        if (confirmed != true || !_current || !_packages.contains(item)) return;
      }
      final result = await widget.api.vendorPackageAction(id, action.endpoint);
      if (!mounted || !_current) return;
      final status = result['status'];
      if (status is! String || !couponListingStatuses.contains(status)) {
        throw const FormatException('Invalid package status');
      }
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(
            '${context.tr('已更新', 'Updated')} · ${_status(context, status)}'),
      ));
      await _load();
    } catch (e) {
      if (!mounted || !_current) return;
      final uncertain = e is! ApiException || e.status >= 500;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
        content: Text(uncertain
            ? context.tr('未能確認變更，正在重新載入套餐。',
                'Could not confirm the change. Reloading packages.')
            : e.message),
      ));
      if (uncertain) await _load();
    } finally {
      if (_current) setState(() => _busyId = null);
    }
  }

  List<Map<String, dynamic>> get _visiblePackages {
    final query = _query.trim().toLowerCase();
    return _packages.where((item) {
      return (_statusFilter.isEmpty || item['status'] == _statusFilter) &&
          (_shopFilter == null || item['shop_id'] == _shopFilter) &&
          (query.isEmpty ||
              ['name', 'name_en', 'shop_name'].any(
                  (key) => '${item[key] ?? ''}'.toLowerCase().contains(query)));
    }).toList();
  }

  void _clearFilters() {
    _search.clear();
    setState(() {
      _query = '';
      _statusFilter = '';
      _shopFilter = null;
    });
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    final rows = _visiblePackages;
    return Scaffold(
      appBar: AppBar(title: Text(t('套餐管理', 'Packages'))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 1200),
          child: RefreshIndicator(
            onRefresh: () async {
              if (_busyId == null) await _load();
            },
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(CgTheme.pageInset),
              children: [
                if (!_current)
                  MerchantOfferEmpty(
                    icon: Icons.lock_outline,
                    title: t('帳戶已切換，請返回工作台。',
                        'Account changed. Return to the workspace.'),
                  )
                else ...[
                  MerchantOfferHeading(
                    title: t('我的套餐', 'My packages'),
                    subtitle: t('管理套餐內容、價格和可售份數。',
                        'Manage package details, prices and available quantities.'),
                    action: t('建立套餐', 'Create package'),
                    icon: Icons.add,
                    onPressed: _loading ||
                            _error != null ||
                            _busyId != null ||
                            _shops.isEmpty
                        ? null
                        : () => _edit(),
                  ),
                  const SizedBox(height: CgTheme.space16),
                  if (_loading)
                    const SkeletonBox(height: 164)
                  else if (_error case final error?)
                    MerchantOfferEmpty(
                      icon: Icons.cloud_off_outlined,
                      title: t('未能載入套餐', 'Could not load packages'),
                      description: error,
                      action: TextButton(
                          onPressed: _load, child: Text(t('重試', 'Retry'))),
                    )
                  else if (_shops.isEmpty)
                    MerchantOfferEmpty(
                      icon: Icons.storefront_outlined,
                      title: t('尚未有可管理的商店', 'No stores to manage'),
                      description: t('完成商戶入駐後可建立套餐。',
                          'You can create packages after merchant onboarding.'),
                    )
                  else if (_packages.isEmpty)
                    MerchantOfferEmpty(
                      icon: Icons.restaurant_menu,
                      title: t('尚未建立套餐', 'No packages yet'),
                      description: t('建立後可在這裡查看上架狀態。',
                          'Created packages and their status appear here.'),
                    )
                  else ...[
                    _filters(),
                    Padding(
                      padding: const EdgeInsets.only(
                          top: CgTheme.space8, bottom: CgTheme.space4),
                      child: Row(children: [
                        Expanded(
                          child: Semantics(
                            liveRegion: true,
                            child: Text(
                                t('共 ${rows.length} 款套餐',
                                    '${rows.length} ${rows.length == 1 ? 'package' : 'packages'}'),
                                style: Theme.of(context).textTheme.labelLarge),
                          ),
                        ),
                        IconButton(
                          onPressed: _busyId == null ? _load : null,
                          tooltip: t('重新載入', 'Reload'),
                          icon: const Icon(Icons.refresh),
                        ),
                      ]),
                    ),
                    if (rows.isEmpty)
                      MerchantOfferEmpty(
                        icon: Icons.search_off,
                        title:
                            t('沒有符合條件的套餐。', 'No packages match these filters.'),
                        action: TextButton(
                            onPressed: _clearFilters,
                            child: Text(t('清除篩選', 'Clear filters'))),
                      ),
                    for (final item in rows) _packageTile(item),
                  ],
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _filters() {
    final t = context.tr;
    final search = MerchantOfferSearch(
      fieldKey: const ValueKey('package-search'),
      controller: _search,
      enabled: _busyId == null,
      label: t('搜尋套餐或商店', 'Search packages or stores'),
      onChanged: (value) => setState(() => _query = value),
    );
    final status = DropdownButtonFormField<String>(
      key: ValueKey('package-status-$_statusFilter'),
      initialValue: _statusFilter,
      isExpanded: true,
      decoration: InputDecoration(
          isDense: true, labelText: t('上架狀態', 'Listing status')),
      items: [
        DropdownMenuItem(value: '', child: Text(t('所有狀態', 'All statuses'))),
        for (final status in couponListingStatuses)
          DropdownMenuItem(
              value: status, child: Text(_status(context, status))),
      ],
      onChanged: _busyId != null || _loading
          ? null
          : (value) => setState(() => _statusFilter = value ?? ''),
    );
    final shop = DropdownButtonFormField<int>(
      key: ValueKey('package-store-$_shopFilter'),
      initialValue: _shopFilter,
      isExpanded: true,
      decoration: InputDecoration(isDense: true, labelText: t('商店', 'Store')),
      items: [
        DropdownMenuItem(child: Text(t('所有商店', 'All stores'))),
        for (final store in _shops)
          DropdownMenuItem(
              value: store['id'] as int,
              child: Text('${store['name']}', overflow: TextOverflow.ellipsis)),
      ],
      onChanged: _busyId != null || _loading
          ? null
          : (value) => setState(() => _shopFilter = value),
    );
    return LayoutBuilder(builder: (context, bounds) {
      final narrowFilters = Row(children: [
        Expanded(child: status),
        if (_shops.length > 1) ...[
          const SizedBox(width: CgTheme.space8),
          Expanded(child: shop),
        ],
      ]);
      return bounds.maxWidth >= 660
          ? Row(children: [
              Expanded(flex: 3, child: search),
              const SizedBox(width: CgTheme.space12),
              Expanded(flex: _shops.length > 1 ? 4 : 2, child: narrowFilters),
            ])
          : Column(children: [
              search,
              const SizedBox(height: CgTheme.space8),
              narrowFilters,
            ]);
    });
  }

  Widget _packageTile(Map<String, dynamic> item) {
    final t = context.tr;
    final theme = Theme.of(context);
    final scheme = theme.colorScheme;
    final status = '${item['status'] ?? ''}';
    final id = item['id'] as int;
    final price = item['package_price'] as num?;
    final people = item['person_count'];
    final images = item['images'];
    final featuredImage =
        images is List ? images.whereType<String>().firstOrNull : null;
    final actions = VendorCouponAction.forManagementStatus(status);
    final canEdit = VendorCouponAction.canEditListing(status);
    final identity =
        Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      ClipRRect(
        borderRadius: BorderRadius.circular(10),
        child: SizedBox(
          width: 64,
          height: 58,
          child: CatalogPhoto(
              url: featuredImage ?? '',
              height: 58,
              apiBaseUrl: widget.api.baseUrl),
        ),
      ),
      const SizedBox(width: CgTheme.space12),
      Expanded(
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${item['name'] ?? ''}', style: theme.textTheme.titleMedium),
          const SizedBox(height: CgTheme.space4),
          Text('${item['shop_name'] ?? ''}',
              style: TextStyle(color: scheme.onSurfaceVariant)),
          if (people is int && people > 0)
            Text(
                t('適用 $people 人',
                    'For $people ${people == 1 ? 'person' : 'people'}'),
                style: theme.textTheme.bodySmall),
        ]),
      ),
    ]);
    final facts = MerchantOfferMetrics(values: [
      (t('套餐價', 'Price'), price == null ? '—' : formatHkd(price.toDouble())),
      (t('剩餘', 'Remaining'), '${item['stock_left'] ?? '—'}'),
      (t('已售', 'Sold'), '${item['sold_count'] ?? '—'}'),
    ]);
    final lifecycle =
        Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
      MerchantStatusPill(
          label: _status(context, status),
          active: status == 'active',
          listingStatus: status),
      if (status == 'pending_review')
        Padding(
          padding: const EdgeInsets.only(top: CgTheme.space4),
          child: Text(t('審核通過後才會上架。', 'Available after approval.'),
              style: theme.textTheme.bodySmall
                  ?.copyWith(color: scheme.onSurfaceVariant)),
        ),
      if (status == 'active' || status == 'scheduled')
        if (_hkPackageDate(item['valid_to']) case final date?)
          Padding(
            padding: const EdgeInsets.only(top: CgTheme.space4),
            child: Text(t('有效至 $date（香港）', 'Valid until $date (HKT)'),
                style: theme.textTheme.bodySmall
                    ?.copyWith(color: scheme.onSurfaceVariant)),
          ),
    ]);
    return Card(
      key: ValueKey('managed-package-$id'),
      margin: const EdgeInsets.only(bottom: CgTheme.space8),
      child: Padding(
        padding: const EdgeInsets.all(CgTheme.pageInset),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          LayoutBuilder(
              builder: (context, bounds) => bounds.maxWidth >= 760
                  ? Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                          Expanded(flex: 5, child: identity),
                          const SizedBox(width: CgTheme.space20),
                          Expanded(flex: 4, child: facts),
                          const SizedBox(width: CgTheme.space20),
                          Expanded(flex: 3, child: lifecycle),
                        ])
                  : Column(
                      crossAxisAlignment: CrossAxisAlignment.stretch,
                      children: [
                          identity,
                          const SizedBox(height: CgTheme.space12),
                          facts,
                          const SizedBox(height: CgTheme.space12),
                          lifecycle,
                        ])),
          if (canEdit || actions.isNotEmpty) ...[
            const SizedBox(height: CgTheme.space8),
            const Divider(height: 12),
            Wrap(spacing: CgTheme.space4, children: [
              if (canEdit)
                TextButton.icon(
                  onPressed: _busyId == null ? () => _edit(item) : null,
                  icon: const Icon(Icons.edit_outlined, size: 16),
                  label: Text(t('編輯', 'Edit')),
                ),
              for (final action in actions)
                TextButton(
                  onPressed:
                      _busyId == null ? () => _action(item, action) : null,
                  child: Text(_busyId == id
                      ? t('處理中…', 'Working…')
                      : switch (action) {
                          VendorCouponAction.pause => t('暫停上架', 'Pause'),
                          VendorCouponAction.resume => t('恢復上架', 'Resume'),
                          VendorCouponAction.unlist => t('下架', 'Unlist'),
                          VendorCouponAction.submit => t('提交上架', 'Submit'),
                        }),
                ),
            ]),
          ],
        ]),
      ),
    );
  }
}

String _status(BuildContext context, String value) {
  final t = context.tr;
  return switch (value) {
    'active' => t('上架中', 'Live'),
    'pending_review' => t('待審核', 'Pending review'),
    'scheduled' => t('待上架', 'Scheduled'),
    'paused' => t('已暫停', 'Paused'),
    'draft' => t('草稿', 'Draft'),
    'rejected' => t('未通過', 'Rejected'),
    'expired' => t('已過期', 'Expired'),
    'sold_out' => t('已售罄', 'Sold out'),
    _ => value.isEmpty ? t('狀態未提供', 'Status unavailable') : value,
  };
}

DateTime? _serverDate(dynamic value) {
  if (value is! String || value.isEmpty) return null;
  // API datetimes without an offset are stored as UTC.
  final hasOffset =
      value.endsWith('Z') || RegExp(r'[+-]\d\d:\d\d$').hasMatch(value);
  return DateTime.tryParse(hasOffset ? value : '${value}Z')?.toLocal();
}

String _date(DateTime value) =>
    '${value.year}-${value.month.toString().padLeft(2, '0')}-${value.day.toString().padLeft(2, '0')}';

String? _hkPackageDate(Object? value) {
  final instant = _serverDate(value);
  return instant == null
      ? null
      : _date(instant.toUtc().add(const Duration(hours: 8)));
}

class VendorPackageEditor extends StatefulWidget {
  const VendorPackageEditor(
      {super.key, required this.api, required this.shops, this.package});
  final ApiClient api;
  final List<Map<String, dynamic>> shops;
  final Map<String, dynamic>? package;
  @override
  State<VendorPackageEditor> createState() => _VendorPackageEditorState();
}

class _VendorPackageEditorState extends State<VendorPackageEditor> {
  final _form = GlobalKey<FormState>();
  late final int _session;
  bool get _current => mounted && widget.api.sessionRevision == _session;
  late final TextEditingController _name,
      _items,
      _original,
      _price,
      _stock,
      _people;
  late final Map<String, dynamic> _rules;
  late final String _initialItems;
  late int _shopId;
  late String _refund;
  late bool _booking;
  late final bool _initialBooking;
  late final bool _hasUnknownRules;
  late DateTime _from, _to;
  bool _fromChanged = false,
      _toChanged = false,
      _saving = false,
      _dirty = false;
  bool _saveUncertain = false;
  String? _error;
  static const _refundPolicies = {'anytime', 'expiry_only', 'non_refundable'};
  bool get _editing => widget.package != null;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_accountChanged);
    final p = widget.package ?? {};
    _shopId = (p['shop_id'] as int?) ?? (widget.shops.first['id'] as int);
    _name = TextEditingController(text: '${p['name'] ?? ''}');
    _initialItems = (p['items'] as List? ?? [])
        .whereType<Map>()
        .map((x) => '${x['name'] ?? ''} ×${x['qty'] ?? 1}')
        .join('\n');
    _items = TextEditingController(text: _initialItems);
    _original = TextEditingController(
        text: p['original_price'] == null ? '' : '${p['original_price']}');
    _price = TextEditingController(
        text: p['package_price'] == null ? '' : '${p['package_price']}');
    _stock = TextEditingController(
        text: p['stock_total'] == null ? '' : '${p['stock_total']}');
    _people = TextEditingController(text: '${p['person_count'] ?? 2}');
    _rules = p['usage_rules'] is Map
        ? Map<String, dynamic>.from(p['usage_rules'] as Map)
        : {};
    _booking = _rules['need_booking'] == true;
    _initialBooking = _booking;
    const supportedRules = {
      'weekday_only',
      'holiday',
      'need_booking',
      'includes',
      'excludes',
      'time_slots',
      'stackable',
      'per_user_limit'
    };
    _hasUnknownRules = _rules.keys.any((key) => !supportedRules.contains(key));
    _refund = '${p['refund_policy'] ?? 'anytime'}';
    _from = _serverDate(p['valid_from']) ?? DateTime.now();
    _to = _serverDate(p['valid_to']) ??
        DateTime.now().add(const Duration(days: 30));
    for (final c in [_name, _items, _original, _price, _stock, _people]) {
      c.addListener(_markDirty);
    }
  }

  void _markDirty() {
    if (!_dirty) setState(() => _dirty = true);
  }

  void _accountChanged() {
    if (mounted && !_current) setState(() => _saving = false);
  }

  @override
  void dispose() {
    widget.api.removeListener(_accountChanged);
    for (final c in [_name, _items, _original, _price, _stock, _people]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<bool> _canLeave() async {
    if (!_current || !_dirty) return true;
    if (_saving) return false;
    return await showDialog<bool>(
            context: context,
            builder: (c) => AlertDialog(
                    title: Text(
                        context.tr('放棄尚未儲存的內容？', 'Discard unsaved changes?')),
                    actions: [
                      TextButton(
                          onPressed: () => Navigator.pop(c, false),
                          child: Text(context.tr('繼續編輯', 'Keep editing'))),
                      TextButton(
                          onPressed: () => Navigator.pop(c, true),
                          child: Text(context.tr('放棄', 'Discard')))
                    ])) ??
        false;
  }

  List<Map<String, dynamic>> _itemLines() => _items.text
          .split('\n')
          .map((s) => s.trim())
          .where((s) => s.isNotEmpty)
          .map((line) {
        final match = RegExp(r'^(.*?)(?:\s*[×xX]\s*(\d+))?$').firstMatch(line)!;
        return <String, dynamic>{
          'name': match.group(1)!.trim(),
          'qty': int.tryParse(match.group(2) ?? '1') ?? 0
        };
      }).toList();

  Future<void> _pickDate(bool start) async {
    final current = start ? _from : _to;
    final now = DateTime.now();
    final defaultFirst = DateTime(now.year - 10);
    final defaultLast = DateTime(now.year + 10);
    final picked = await showDatePicker(
        context: context,
        initialDate: current,
        firstDate: current.isBefore(defaultFirst) ? current : defaultFirst,
        lastDate: current.isAfter(defaultLast) ? current : defaultLast);
    if (picked != null && _current) {
      setState(() {
        if (start) {
          _from = picked;
          _fromChanged = true;
        } else {
          _to = picked;
          _toChanged = true;
        }
        _dirty = true;
      });
    }
  }

  Future<void> _save() async {
    if (!_current || _saving || _saveUncertain) return;
    final invalidFields = _form.currentState!.validateGranularly();
    if (invalidFields.isNotEmpty) {
      final first = invalidFields.first;
      setState(
          () => _error = context.tr('請核對標示的欄位。', 'Check the marked fields.'));
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (_current && first.mounted) {
          Scrollable.ensureVisible(first.context, alignment: 0.10);
        }
      });
      return;
    }
    final from = DateTime(_from.year, _from.month, _from.day);
    final to = DateTime(_to.year, _to.month, _to.day, 23, 59, 59);
    if (!to.isAfter(from)) {
      setState(() => _error =
          context.tr('結束日期須在開始日期之後。', 'End date must follow start date.'));
      return;
    }
    if (to.isBefore(DateTime.now())) {
      setState(() => _error =
          context.tr('結束日期不能早於今天。', 'End date cannot be in the past.'));
      return;
    }
    if (_itemLines()
        .any((x) => (x['name'] as String).isEmpty || (x['qty'] as int) < 1)) {
      setState(() => _error =
          context.tr('請檢查套餐項目及數量。', 'Check item names and quantities.'));
      return;
    }
    final body = <String, dynamic>{
      if (!_editing) 'shop_id': _shopId,
      'name': _name.text.trim(),
      if (!_editing || _items.text != _initialItems) 'items': _itemLines(),
      'original_price': double.parse(_original.text),
      'package_price': double.parse(_price.text),
      'stock_total': int.parse(_stock.text),
      'person_count': int.parse(_people.text),
      if (!_editing || _fromChanged)
        'valid_from': from.toUtc().toIso8601String().replaceFirst('Z', ''),
      if (!_editing || _toChanged)
        'valid_to': to.toUtc().toIso8601String().replaceFirst('Z', ''),
      if (_refundPolicies.contains(_refund)) 'refund_policy': _refund,
      if (!_editing || _booking != _initialBooking)
        'usage_rules': {..._rules, 'need_booking': _booking},
    };
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final response = _editing
          ? await widget.api
              .editVendorPackage(widget.package!['id'] as int, body)
          : await widget.api.createVendorPackage(body);
      final package = response['package'];
      if (package is! Map ||
          package['status'] is! String ||
          (_editing && package['id'] != widget.package!['id'])) {
        throw const FormatException('Invalid package receipt');
      }
      if (!mounted || !_current) return;
      Navigator.of(context).pop(Map<String, dynamic>.from(package));
    } catch (e) {
      if (!mounted || !_current) return;
      setState(() {
        _saving = false;
        _saveUncertain = e is! ApiException || e.status >= 500;
        _error = _saveUncertain
            ? context.tr('無法確認是否已儲存，請返回列表重新載入後再試。',
                'Could not confirm the save. Return to the list and refresh before retrying.')
            : (e as ApiException).message;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    String? requiredText(String? value) =>
        value == null || value.trim().isEmpty ? t('請填寫此項', 'Required') : null;
    String? price(String? value) {
      if (value == null ||
          !RegExp(r'^\d+(\.\d{1,2})?$').hasMatch(value) ||
          (double.tryParse(value) ?? 0) <= 0 ||
          (double.tryParse(value) ?? 0) > 99999999.99) {
        return t(
            '請輸入有效金額，最多兩位小數', 'Enter a valid amount with up to two decimals');
      }
      return null;
    }

    String? integer(String? value, {bool allowZero = false}) {
      final number = int.tryParse(value ?? '');
      return number == null || number < (allowZero ? 0 : 1) || number > 1000000
          ? t('請輸入有效數量', 'Enter a valid quantity')
          : null;
    }

    Widget pair(Widget first, Widget second) {
      return LayoutBuilder(builder: (context, bounds) {
        if (bounds.maxWidth >= 480) {
          return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Expanded(child: first),
            const SizedBox(width: CgTheme.space12),
            Expanded(child: second),
          ]);
        }
        return Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              first,
              const SizedBox(height: CgTheme.space12),
              second,
            ]);
      });
    }

    final save = MerchantPrimaryAction(
      onPressed: _saving || _saveUncertain ? null : _save,
      label: _saving ? t('儲存中…', 'Saving…') : t('儲存套餐', 'Save package'),
    );
    final theme = Theme.of(context);
    final shopName = widget.package?['shop_name'];
    final currentStatus = widget.package?['status'];
    return PopScope(
      canPop: !_current || (!_dirty && !_saving),
      onPopInvokedWithResult: (didPop, result) async {
        if (!didPop && await _canLeave() && context.mounted) {
          Navigator.of(context).pop();
        }
      },
      child: Scaffold(
        appBar: AppBar(
            title: Text(_editing
                ? t('編輯套餐', 'Edit package')
                : t('建立套餐', 'Create package'))),
        body: !_current
            ? Center(
                child: MerchantOfferEmpty(
                icon: Icons.lock_outline,
                title:
                    t('帳戶已切換，請重新開啟套餐。', 'Account changed. Reopen the package.'),
              ))
            : Form(
                key: _form,
                child: Center(
                  child: ConstrainedBox(
                    constraints:
                        const BoxConstraints(maxWidth: CgTheme.formMaxWidth),
                    child: SingleChildScrollView(
                      padding: const EdgeInsets.all(CgTheme.pageInset),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          if (_editing &&
                              (shopName is String ||
                                  currentStatus is String)) ...[
                            Wrap(
                              spacing: CgTheme.space12,
                              runSpacing: CgTheme.space8,
                              crossAxisAlignment: WrapCrossAlignment.center,
                              children: [
                                if (shopName is String && shopName.isNotEmpty)
                                  Text(shopName,
                                      style: theme.textTheme.titleMedium),
                                if (currentStatus is String)
                                  MerchantStatusPill(
                                    active: currentStatus == 'active',
                                    label: _status(context, currentStatus),
                                    listingStatus: currentStatus,
                                  ),
                              ],
                            ),
                            const SizedBox(height: CgTheme.space12),
                          ],
                          _PackageEditorSection(
                            title: t('套餐內容', 'Package details'),
                            children: [
                              if (!_editing) ...[
                                DropdownButtonFormField<int>(
                                  initialValue: _shopId,
                                  isExpanded: true,
                                  decoration: InputDecoration(
                                      labelText: t('商店', 'Store')),
                                  items: widget.shops
                                      .map((shop) => DropdownMenuItem<int>(
                                          value: shop['id'] as int,
                                          child: Text('${shop['name']}',
                                              overflow: TextOverflow.ellipsis)))
                                      .toList(),
                                  onChanged: _saving
                                      ? null
                                      : (value) {
                                          if (value == null) return;
                                          setState(() {
                                            _shopId = value;
                                            _dirty = true;
                                          });
                                        },
                                ),
                                const SizedBox(height: CgTheme.space12),
                              ],
                              TextFormField(
                                controller: _name,
                                enabled: !_saving,
                                maxLength: 160,
                                validator: requiredText,
                                decoration: InputDecoration(
                                    labelText: t('套餐名稱', 'Package name')),
                              ),
                              const SizedBox(height: CgTheme.space8),
                              TextFormField(
                                controller: _items,
                                enabled: !_saving,
                                minLines: 2,
                                maxLines: 5,
                                decoration: InputDecoration(
                                  labelText: t('套餐項目', 'Items'),
                                  hintText: t('每行一項，例如：主菜 ×2',
                                      'One per line, e.g. Main course ×2'),
                                  helperText: t('可留空；數量預設為 1',
                                      'Optional; quantity defaults to 1'),
                                ),
                              ),
                            ],
                          ),
                          _PackageEditorSection(
                            title: t('價格與數量', 'Price and quantity'),
                            children: [
                              pair(
                                TextFormField(
                                  controller: _original,
                                  enabled: !_saving,
                                  keyboardType:
                                      const TextInputType.numberWithOptions(
                                          decimal: true),
                                  validator: price,
                                  decoration: InputDecoration(
                                      labelText: t(
                                          '原價（HK\$）', 'Original price (HK\$)')),
                                ),
                                TextFormField(
                                  controller: _price,
                                  enabled: !_saving,
                                  keyboardType:
                                      const TextInputType.numberWithOptions(
                                          decimal: true),
                                  validator: (value) {
                                    final problem = price(value);
                                    if (problem != null) return problem;
                                    final original =
                                        double.tryParse(_original.text);
                                    return original != null &&
                                            double.parse(value!) > original
                                        ? t('套餐價不可高於原價',
                                            'Package price cannot exceed original price')
                                        : null;
                                  },
                                  decoration: InputDecoration(
                                      labelText: t(
                                          '套餐價（HK\$）', 'Package price (HK\$)')),
                                ),
                              ),
                              const SizedBox(height: CgTheme.space12),
                              pair(
                                TextFormField(
                                  controller: _stock,
                                  enabled: !_saving,
                                  keyboardType: TextInputType.number,
                                  validator: (value) =>
                                      integer(value, allowZero: _editing),
                                  decoration: InputDecoration(
                                      labelText: t('總份數', 'Total quantity')),
                                ),
                                TextFormField(
                                  controller: _people,
                                  enabled: !_saving,
                                  keyboardType: TextInputType.number,
                                  validator: (value) => integer(value),
                                  decoration: InputDecoration(
                                      labelText: t('適用人數', 'People')),
                                ),
                              ),
                            ],
                          ),
                          _PackageEditorSection(
                            title: t('有效期與退款', 'Validity and refunds'),
                            children: [
                              pair(
                                OutlinedButton.icon(
                                  onPressed:
                                      _saving ? null : () => _pickDate(true),
                                  icon:
                                      const Icon(Icons.calendar_today_outlined),
                                  label: Text(
                                      '${t('開始日期', 'Start date')} · ${_date(_from)}'),
                                ),
                                OutlinedButton.icon(
                                  onPressed:
                                      _saving ? null : () => _pickDate(false),
                                  icon: const Icon(Icons.event_outlined),
                                  label: Text(
                                      '${t('結束日期', 'End date')} · ${_date(_to)}'),
                                ),
                              ),
                              const SizedBox(height: CgTheme.space12),
                              DropdownButtonFormField<String>(
                                initialValue: _refund,
                                isExpanded: true,
                                decoration: InputDecoration(
                                    labelText: t('退款規則', 'Refund policy')),
                                items: [
                                  if (!_refundPolicies.contains(_refund))
                                    DropdownMenuItem(
                                        value: _refund,
                                        child: Text(t('原有規則（保留）',
                                            'Existing policy (preserved)'))),
                                  DropdownMenuItem(
                                      value: 'anytime',
                                      child: Text(t('未使用可退，過期自動退',
                                          'Unused or expired: refund'))),
                                  DropdownMenuItem(
                                      value: 'expiry_only',
                                      child: Text(t(
                                          '僅過期自動退', 'Refund on expiry only'))),
                                  DropdownMenuItem(
                                      value: 'non_refundable',
                                      child: Text(t('不設退款', 'Non-refundable'))),
                                ],
                                onChanged: _saving
                                    ? null
                                    : (value) {
                                        if (value == null) return;
                                        setState(() {
                                          _refund = value;
                                          _dirty = true;
                                        });
                                      },
                              ),
                              SwitchListTile(
                                contentPadding: EdgeInsets.zero,
                                title: Text(t('需要預約', 'Reservation required')),
                                subtitle: _hasUnknownRules
                                    ? Text(
                                        t('部分現有使用規則不能在此修改。',
                                            'Some existing rules cannot be changed here.'),
                                      )
                                    : null,
                                value: _booking,
                                onChanged: _saving || _hasUnknownRules
                                    ? null
                                    : (value) {
                                        setState(() {
                                          _booking = value;
                                          _dirty = true;
                                        });
                                      },
                              ),
                            ],
                          ),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
        bottomNavigationBar: !_current
            ? null
            : ColoredBox(
                color: theme.colorScheme.surface,
                child: SafeArea(
                  top: false,
                  minimum: const EdgeInsets.symmetric(
                      horizontal: CgTheme.space16, vertical: CgTheme.space12),
                  child: Center(
                    heightFactor: 1,
                    child: ConstrainedBox(
                      constraints:
                          const BoxConstraints(maxWidth: CgTheme.formMaxWidth),
                      child: Column(
                        mainAxisSize: MainAxisSize.min,
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          if (_error case final error?)
                            Semantics(
                              liveRegion: true,
                              child: Padding(
                                padding: const EdgeInsets.only(
                                    bottom: CgTheme.space8),
                                child: Text(error,
                                    style: TextStyle(
                                        color: theme.colorScheme.error)),
                              ),
                            ),
                          if (_saveUncertain)
                            TextButton(
                              onPressed: () => Navigator.of(context)
                                  .pop({'_refresh_only': true}),
                              child: Text(t('返回並重新載入', 'Return and refresh')),
                            ),
                          save,
                        ],
                      ),
                    ),
                  ),
                ),
              ),
      ),
    );
  }
}

class _PackageEditorSection extends StatelessWidget {
  const _PackageEditorSection({required this.title, required this.children});

  final String title;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Card(
        margin: const EdgeInsets.only(bottom: CgTheme.space12),
        child: Padding(
          padding: const EdgeInsets.all(CgTheme.pageInset),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
            Semantics(
                header: true,
                child: Text(title,
                    style: Theme.of(context).textTheme.titleMedium)),
            const SizedBox(height: CgTheme.space12),
            ...children,
          ]),
        ),
      );
}
