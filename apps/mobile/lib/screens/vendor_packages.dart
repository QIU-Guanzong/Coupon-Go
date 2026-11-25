import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../theme.dart';
import '../widgets/merchant_layout.dart';


/// Merchant package workspace. Server status is always the source of truth.
class VendorPackagesScreen extends StatefulWidget {
  const VendorPackagesScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<VendorPackagesScreen> createState() => _VendorPackagesScreenState();
}

class _VendorPackagesScreenState extends State<VendorPackagesScreen> {
  List<Map<String, dynamic>> _shops = [];
  List<Map<String, dynamic>> _packages = [];
  bool _loading = true;
  int? _busyId;
  String? _error;

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
      final result = await widget.api.vendorPackageWorkspace();
      if (!mounted) return;
      setState(() {
        _shops = (result['shops'] as List? ?? [])
            .whereType<Map>()
            .map((x) => Map<String, dynamic>.from(x))
            .toList();
        _packages = (result['packages'] as List? ?? [])
            .whereType<Map>()
            .map((x) => Map<String, dynamic>.from(x))
            .toList();
        _loading = false;
      });
    } catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e is ApiException
            ? e.message
            : context.tr('請稍後再試', 'Please try again');
      });
    }
  }

  Future<void> _edit([Map<String, dynamic>? item]) async {
    final result = await Navigator.of(context).push<Map<String, dynamic>>(
        MaterialPageRoute(
            builder: (_) => VendorPackageEditor(
                api: widget.api, shops: _shops, package: item)));
    if (!mounted || result == null) return;
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

  Future<void> _action(Map<String, dynamic> item, String action) async {
    final id = item['id'] as int;
    setState(() => _busyId = id);
    try {
      final result = await widget.api.vendorPackageAction(id, action);
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(
              '${context.tr('已更新', 'Updated')} · ${_status(context, '${result['status'] ?? ''}')}')));
      await _load();
    } catch (e) {
      if (!mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(e is ApiException
              ? e.message
              : context.tr('更新失敗，請重試', 'Update failed. Please retry.'))));
    } finally {
      if (mounted) setState(() => _busyId = null);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    return Scaffold(
      appBar: AppBar(
          title: Text(t('套餐管理', 'Packages'))),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : _error != null
              ? Center(
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                  Text(t('未能載入套餐', 'Could not load packages')),
                  const SizedBox(height: CgTheme.space8),
                  Text(_error!),
                  TextButton(onPressed: _load, child: Text(t('重試', 'Retry'))),
                ]))
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.fromLTRB(CgTheme.pageInset, CgTheme.space12, CgTheme.pageInset, CgTheme.space16),
                    children: [
                      Center(
                          child: ConstrainedBox(
                              constraints: const BoxConstraints(maxWidth: CgTheme.listMaxWidth),
                              child: Column(
                                  crossAxisAlignment:
                                      CrossAxisAlignment.stretch,
                                  children: [
                                    Text(t('我的套餐', 'My packages'),
                                        style: Theme.of(context)
                                            .textTheme
                                            .titleLarge),
                                    const SizedBox(height: CgTheme.space4),
                                    Text(
                                        t('建立套餐、調整內容及管理上架狀態。',
                                            'Create packages, update details and manage availability.'),
                                        style: TextStyle(
                                            color: Theme.of(context)
                                                .colorScheme
                                                .onSurfaceVariant)),
                                    const SizedBox(height: CgTheme.space16),
                                    MerchantPrimaryAction(
                                      label: t('建立套餐', 'Create package'),
                                      onPressed: _shops.isEmpty ? null : () => _edit(),
                                      icon: Icons.add,
                                    ),
                                    if (_shops.isEmpty)
                                      Padding(
                                          padding:
                                              const EdgeInsets.only(top: 16),
                                          child: Text(t(
                                              '此帳戶尚未有可管理的商店，請先完成商戶入駐。',
                                              'This account has no store yet. Complete merchant onboarding first.'))),
                                    if (_packages.isEmpty && _shops.isNotEmpty)
                                      Padding(
                                          padding: const EdgeInsets.symmetric(
                                              vertical: 36),
                                          child: Column(children: [
                                            Icon(Icons.storefront_outlined,
                                                size: 40,
                                                color: Theme.of(context)
                                                    .colorScheme
                                                    .onSurfaceVariant),
                                            const SizedBox(height: CgTheme.space12),
                                            Text(
                                                t('尚未建立套餐', 'No packages yet')),
                                            const SizedBox(height: CgTheme.space4),
                                            Text(t('建立後可在這裡查看上架狀態。',
                                                'Created packages and their status appear here.')),
                                          ])),
                                    for (final item in _packages) ...[
                                      const SizedBox(height: CgTheme.space12),
                                      _packageTile(item)
                                    ],
                                  ])))
                    ],
                  )),
    );
  }

  Widget _packageTile(Map<String, dynamic> item) {
    final t = context.tr;
    final status = '${item['status'] ?? ''}';
    final id = item['id'] as int;
    final actions = <String, String>{
      if (!['expired', 'sold_out'].contains(status)) 'edit': t('編輯', 'Edit'),
      if (status == 'active') 'pause': t('暫停上架', 'Pause'),
      if (status == 'paused') 'resume': t('恢復上架', 'Resume'),
      if (['active', 'paused', 'scheduled', 'pending_review', 'rejected']
          .contains(status))
        'unlist': t('下架', 'Unlist'),
      if (['draft', 'rejected'].contains(status)) 'submit': t('提交上架', 'Submit'),
    };
    return Card(
        child: Padding(
            padding: const EdgeInsets.all(16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Expanded(
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                        Text('${item['name'] ?? ''}',
                            style: Theme.of(context)
                                .textTheme
                                .titleMedium
                                ?.copyWith(fontWeight: FontWeight.bold)),
                        const SizedBox(height: CgTheme.space4),
                        Text('${item['shop_name'] ?? ''}',
                            style: TextStyle(
                                color: Theme.of(context)
                                    .colorScheme
                                    .onSurfaceVariant)),
                      ])),
                  const SizedBox(width: 8),
                  Flexible(child: MerchantStatusPill(
                      label: _status(context, status),
                      active: status == 'active',
                  )),
                ]),
                const SizedBox(height: CgTheme.space12),
                Wrap(spacing: 16, runSpacing: 4, children: [
                  Text(
                      formatHkd(
                          (item['package_price'] as num?)?.toDouble() ?? 0),
                      style: const TextStyle(fontWeight: FontWeight.bold)),
                  Text('${t('剩餘', 'Remaining')} ${item['stock_left'] ?? '—'}'),
                  Text('${t('已售', 'Sold')} ${item['sold_count'] ?? '—'}'),
                ]),
                if (status == 'pending_review')
                  Padding(
                      padding: const EdgeInsets.only(top: 8),
                      child: Text(t('審核通過後才會上架。', 'Available after approval.'),
                          style: TextStyle(
                              color: Theme.of(context)
                                  .colorScheme
                                  .onSurfaceVariant))),
                if (actions.isNotEmpty) ...[
                  const SizedBox(height: CgTheme.space12),
                  Wrap(spacing: 8, children: [
                    for (final entry in actions.entries)
                      TextButton(
                          onPressed: _busyId != null
                              ? null
                              : () => entry.key == 'edit'
                                  ? _edit(item)
                                  : _action(item, entry.key),
                          child: Text(_busyId == id && entry.key != 'edit'
                              ? t('處理中…', 'Working…')
                              : entry.value)),
                  ])
                ],
              ],
            )));
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

  @override
  void dispose() {
    for (final c in [_name, _items, _original, _price, _stock, _people]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<bool> _canLeave() async {
    if (!_dirty || _saving) return !_saving;
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
    if (picked != null && mounted) {
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
    if (!_form.currentState!.validate()) return;
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
      if (package is! Map || package['status'] is! String) {
        throw StateError('missing package status');
      }
      if (!mounted) return;
      Navigator.of(context).pop(Map<String, dynamic>.from(package));
    } catch (e) {
      if (!mounted) return;
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

    return PopScope(
        canPop: !_dirty && !_saving,
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
          body: Form(
              key: _form,
              child: ListView(
                  padding: const EdgeInsets.fromLTRB(CgTheme.pageInset, CgTheme.space12, CgTheme.pageInset, CgTheme.space16),
                  children: [
                    Center(
                        child: ConstrainedBox(
                            constraints: const BoxConstraints(maxWidth: CgTheme.formMaxWidth),
                            child: Column(
                                crossAxisAlignment: CrossAxisAlignment.stretch,
                                children: [
                                  if (!_editing)
                                    DropdownButtonFormField<int>(
                                        initialValue: _shopId,
                                        decoration: InputDecoration(
                                            labelText: t('商店', 'Store')),
                                        items: widget.shops
                                            .map((s) => DropdownMenuItem<int>(
                                                value: s['id'] as int,
                                                child: Text('${s['name']}')))
                                            .toList(),
                                        onChanged: _saving
                                            ? null
                                            : (v) {
                                                if (v != null) {
                                                  setState(() {
                                                    _shopId = v;
                                                    _dirty = true;
                                                  });
                                                }
                                              }),
                                  if (!_editing) const SizedBox(height: CgTheme.space16),
                                  Text(t('套餐內容', 'Package details'),
                                      style: Theme.of(context)
                                          .textTheme
                                          .titleMedium
                                          ?.copyWith(
                                              fontWeight: FontWeight.bold)),
                                  const SizedBox(height: CgTheme.space12),
                                  TextFormField(
                                      controller: _name,
                                      enabled: !_saving,
                                      maxLength: 160,
                                      validator: requiredText,
                                      decoration: InputDecoration(
                                          labelText:
                                              t('套餐名稱', 'Package name'))),
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
                                              'Optional; quantity defaults to 1'))),
                                  const SizedBox(height: CgTheme.space16),
                                  Text(t('價格與數量', 'Price and quantity'),
                                      style: Theme.of(context)
                                          .textTheme
                                          .titleMedium
                                          ?.copyWith(
                                              fontWeight: FontWeight.bold)),
                                  const SizedBox(height: CgTheme.space12),
                                  TextFormField(
                                      controller: _original,
                                      enabled: !_saving,
                                      keyboardType:
                                          const TextInputType.numberWithOptions(
                                              decimal: true),
                                      validator: price,
                                      decoration: InputDecoration(
                                          labelText: t('原價（HK\$）',
                                              'Original price (HK\$)'))),
                                  const SizedBox(height: CgTheme.space12),
                                  TextFormField(
                                      controller: _price,
                                      enabled: !_saving,
                                      keyboardType:
                                          const TextInputType.numberWithOptions(
                                              decimal: true),
                                      validator: (v) {
                                        final problem = price(v);
                                        if (problem != null) return problem;
                                        final original =
                                            double.tryParse(_original.text);
                                        return original != null &&
                                                double.parse(v!) > original
                                            ? t('套餐價不可高於原價',
                                                'Package price cannot exceed original price')
                                            : null;
                                      },
                                      decoration: InputDecoration(
                                          labelText: t('套餐價（HK\$）',
                                              'Package price (HK\$)'))),
                                  const SizedBox(height: CgTheme.space12),
                                  TextFormField(
                                      controller: _stock,
                                      enabled: !_saving,
                                      keyboardType: TextInputType.number,
                                      validator: (v) =>
                                          integer(v, allowZero: _editing),
                                      decoration: InputDecoration(
                                          labelText:
                                              t('總份數', 'Total quantity'))),
                                  const SizedBox(height: CgTheme.space12),
                                  TextFormField(
                                      controller: _people,
                                      enabled: !_saving,
                                      keyboardType: TextInputType.number,
                                      validator: (v) => integer(v),
                                      decoration: InputDecoration(
                                          labelText: t('適用人數', 'People'))),
                                  const SizedBox(height: CgTheme.space20),
                                  Text(t('有效期與退款', 'Validity and refunds'),
                                      style: Theme.of(context)
                                          .textTheme
                                          .titleMedium
                                          ?.copyWith(
                                              fontWeight: FontWeight.bold)),
                                  const SizedBox(height: CgTheme.space8),
                                  OutlinedButton.icon(
                                      onPressed: _saving
                                          ? null
                                          : () => _pickDate(true),
                                      icon: const Icon(
                                          Icons.calendar_today_outlined),
                                      label: Text(
                                          '${t('開始日期', 'Start date')}：${_date(_from)}')),
                                  OutlinedButton.icon(
                                      onPressed: _saving
                                          ? null
                                          : () => _pickDate(false),
                                      icon: const Icon(Icons.event_outlined),
                                      label: Text(
                                          '${t('結束日期', 'End date')}：${_date(_to)}')),
                                  const SizedBox(height: CgTheme.space12),
                                  DropdownButtonFormField<String>(
                                      initialValue: _refund,
                                      decoration: InputDecoration(
                                          labelText:
                                              t('退款規則', 'Refund policy')),
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
                                            child: Text(t('僅過期自動退',
                                                'Refund on expiry only'))),
                                        DropdownMenuItem(
                                            value: 'non_refundable',
                                            child: Text(
                                                t('不設退款', 'Non-refundable')))
                                      ],
                                      onChanged: _saving
                                          ? null
                                          : (v) {
                                              if (v != null) {
                                                setState(() {
                                                  _refund = v;
                                                  _dirty = true;
                                                });
                                              }
                                            }),
                                  SwitchListTile(
                                      contentPadding: EdgeInsets.zero,
                                      title: Text(
                                          t('需要預約', 'Reservation required')),
                                      subtitle: _hasUnknownRules
                                          ? Text(t('部分現有使用規則不能在此修改。',
                                              'Some existing rules cannot be changed here.'))
                                          : null,
                                      value: _booking,
                                      onChanged: _saving || _hasUnknownRules
                                          ? null
                                          : (v) => setState(() {
                                                _booking = v;
                                                _dirty = true;
                                              })),
                                  if (_error != null)
                                    Padding(
                                        padding:
                                            const EdgeInsets.only(bottom: 12),
                                        child: Text(_error!,
                                            style: TextStyle(
                                                color: Theme.of(context)
                                                    .colorScheme
                                                    .error))),
                                  if (_saveUncertain)
                                    TextButton(
                                      onPressed: () => Navigator.of(context)
                                          .pop({'_refresh_only': true}),
                                      child: Text(
                                          t('返回並重新載入', 'Return and refresh')),
                                    ),
                                  MerchantPrimaryAction(
                                      onPressed: _saving || _saveUncertain
                                          ? null
                                          : _save,
                                      label: _saving
                                          ? t('儲存中…', 'Saving…')
                                          : t('儲存套餐', 'Save package')),
                                ])))
                  ])),
        ));
  }
}
