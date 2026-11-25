import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../theme.dart';
import '../widgets/merchant_layout.dart';

/// null means the selection has not loaded or is incomplete; [] means all branches.
class BranchScopePicker extends StatefulWidget {
  const BranchScopePicker(
      {super.key,
      required this.api,
      required this.shopId,
      required this.initial,
      required this.onChanged});
  final ApiClient api;
  final int shopId;
  final List<int> initial;
  final ValueChanged<List<int>?> onChanged;
  @override
  State<BranchScopePicker> createState() => _BranchScopePickerState();
}

class _BranchScopePickerState extends State<BranchScopePicker> {
  List<Map<String, dynamic>>? _branches;
  late Set<int> _selected;
  late bool _limited;
  String? _error;
  int _generation = 0;
  @override
  void initState() {
    super.initState();
    _selected = widget.initial.toSet();
    _limited = _selected.isNotEmpty;
    _load();
  }

  Future<void> _load() async {
    final generation = ++_generation;
    final session = widget.api.sessionRevision;
    setState(() {
      _branches = null;
      _error = null;
    });
    try {
      final result =
          await widget.api.get('/merchant/shops/${widget.shopId}/branches');
      if (!mounted ||
          generation != _generation ||
          session != widget.api.sessionRevision) {
        return;
      }
      final rows = result['branches'];
      if (rows is! List ||
          rows.any(
              (v) => v is! Map || v['id'] is! int || v['name'] is! String)) {
        throw const FormatException('Missing branches');
      }
      final branches =
          rows.map((v) => Map<String, dynamic>.from(v as Map)).toList();
      final ids = branches.map((v) => v['id'] as int).toSet();
      if (!_selected.every(ids.contains)) {
        throw const FormatException('Unknown branches');
      }
      setState(() => _branches = branches);
      _report();
    } catch (e) {
      if (!mounted ||
          generation != _generation ||
          session != widget.api.sessionRevision) {
        return;
      }
      setState(() => _error = e is ApiException
          ? e.message
          : context.tr('未能讀取分店，請重試', 'Could not load branches. Try again.'));
      widget.onChanged(null);
    }
  }

  void _report() => widget.onChanged(_limited && _selected.isEmpty
      ? null
      : (_limited ? (_selected.toList()..sort()) : <int>[]));
  @override
  Widget build(BuildContext context) =>
      Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(context.tr('適用分店', 'Available at'),
            style: Theme.of(context).textTheme.titleMedium),
        if (_branches == null && _error == null)
          const Padding(
              padding: EdgeInsets.all(12), child: LinearProgressIndicator()),
        if (_error != null) ...[
          Text(_error!),
          TextButton(onPressed: _load, child: Text(context.tr('重試', 'Retry')))
        ],
        if (_branches != null) ...[
          SegmentedButton<bool>(
            segments: [
              ButtonSegment(
                  value: false,
                  label: Text(context.tr('所有分店', 'All branches'))),
              if (_branches!.isNotEmpty)
                ButtonSegment(
                    value: true,
                    label: Text(context.tr('指定分店', 'Selected branches'))),
            ],
            selected: {_limited},
            onSelectionChanged: (values) {
              setState(() {
                _limited = values.single;
                if (!_limited) _selected.clear();
              });
              _report();
            },
          ),
          if (_limited)
            for (final branch in _branches!)
              CheckboxListTile(
                  contentPadding: EdgeInsets.zero,
                  title: Text(branch['name'] as String),
                  subtitle: branch['address'] is String &&
                          (branch['address'] as String).isNotEmpty
                      ? Text(branch['address'] as String)
                      : null,
                  value: _selected.contains(branch['id']),
                  onChanged: (checked) {
                    setState(() {
                      if (checked == true) {
                        _selected.add(branch['id'] as int);
                      } else {
                        _selected.remove(branch['id']);
                      }
                    });
                    _report();
                  }),
          if (_limited && _selected.isEmpty)
            Text(context.tr('請至少選擇一間分店。', 'Select at least one branch.')),
          if (_branches!.isEmpty)
            Text(context.tr('此商店暫無分店；新增分店後可指定適用範圍。',
                'Add a branch first to choose where this voucher can be used.')),
        ],
      ]);
}

class CouponBranchEditScreen extends StatefulWidget {
  const CouponBranchEditScreen(
      {super.key,
      required this.api,
      required this.couponId,
      required this.shopId,
      required this.couponName});
  final ApiClient api;
  final int couponId, shopId;
  final String couponName;
  @override
  State<CouponBranchEditScreen> createState() => _CouponBranchEditScreenState();
}

class _CouponBranchEditScreenState extends State<CouponBranchEditScreen> {
  List<int>? _initial, _selection;
  String? _error;
  bool _busy = false;
  late int _session;
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
    if (mounted) {
      setState(() {
        _initial = null;
        _selection = null;
        _busy = false;
        _error = context.tr(
            '登入狀態已變更，請重新讀取優惠券。', 'Your session changed. Reload the voucher.');
      });
    }
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    super.dispose();
  }

  Future<void> _load() async {
    final session = _session;
    setState(() {
      _initial = null;
      _error = null;
    });
    try {
      final result = await widget.api.get('/coupon-details-new',
          query: {'coupon_id': '${widget.couponId}'});
      final coupon = result['coupon'];
      if (coupon is! Map ||
          coupon['shop_id'] != widget.shopId ||
          coupon['branch_ids'] is! List ||
          (coupon['branch_ids'] as List).any((id) => id is! int)) {
        throw const FormatException();
      }
      if (!mounted || session != _session) return;
      setState(() {
        _initial = (coupon['branch_ids'] as List).cast<int>();
        _selection = null;
      });
    } catch (e) {
      if (mounted && session == _session) {
        setState(() => _error = e is ApiException
            ? e.message
            : context.tr(
                '未能讀取優惠券，請重試', 'Could not load the voucher. Try again.'));
      }
    }
  }

  Future<void> _save() async {
    final ids = _selection;
    final session = _session;
    if (_busy || ids == null) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.post('/assign-branches',
          body: {'coupon_id': widget.couponId, 'branch_ids': ids});
      if (mounted && session == _session) Navigator.pop(context, true);
    } on ApiException catch (e) {
      if (mounted && session == _session) setState(() => _error = e.message);
    } catch (_) {
      if (mounted && session == _session) {
        setState(() =>
            _error = context.tr('未能保存，請重試', 'Could not save. Try again.'));
      }
    } finally {
      if (mounted && session == _session) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
      appBar:
          AppBar(title: Text(context.tr('編輯適用分店', 'Edit applicable branches'))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: CgTheme.formMaxWidth),
          child: ListView(
              padding: const EdgeInsets.all(CgTheme.pageInset),
              children: [
                Text(widget.couponName,
                    style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: CgTheme.space8),
                Text(context.tr('已有訂單的優惠券不能更改適用分店。',
                    'Branches cannot be changed once orders exist for the voucher.')),
                if (_error != null) ...[
                  const SizedBox(height: CgTheme.space12),
                  Text(_error!,
                      style: TextStyle(
                          color: Theme.of(context).colorScheme.error)),
                  if (_initial == null)
                    TextButton(
                        onPressed: _load,
                        child: Text(context.tr('重試', 'Retry')))
                ],
                if (_initial == null && _error == null)
                  const LinearProgressIndicator(),
                if (_initial != null) ...[
                  const SizedBox(height: CgTheme.space20),
                  BranchScopePicker(
                      api: widget.api,
                      shopId: widget.shopId,
                      initial: _initial!,
                      onChanged: (ids) => setState(() => _selection = ids)),
                  const SizedBox(height: CgTheme.space12),
                  MerchantPrimaryAction(
                      onPressed: _busy || _selection == null ? null : _save,
                      label: _busy
                          ? context.tr('保存中…', 'Saving…')
                          : context.tr('保存', 'Save')),
                ],
              ]),
        ),
      ));
}
