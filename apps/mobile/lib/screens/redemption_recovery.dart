import 'dart:math';

import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../theme.dart';
import '../widgets/merchant_layout.dart';

String _errorMessage(BuildContext context, Object error) =>
    error is ApiException
        ? error.message
        : context.tr('連線未完成，請稍後再試。', 'Connection failed. Try again later.');

String _newRequestKey() {
  final random = Random.secure();
  final bytes = List<int>.generate(16, (_) => random.nextInt(256));
  return 'supp-${bytes.map((n) => n.toRadixString(16).padLeft(2, '0')).join()}';
}

/// Find a customer's unused voucher and record a missed redemption.
class MerchantRedemptionRecoveryScreen extends StatefulWidget {
  const MerchantRedemptionRecoveryScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<MerchantRedemptionRecoveryScreen> createState() =>
      _MerchantRedemptionRecoveryScreenState();
}

class _MerchantRedemptionRecoveryScreenState
    extends State<MerchantRedemptionRecoveryScreen> {
  final _query = TextEditingController();
  final _keys = <String, String>{};
  String _kind = 'phone';
  List<Map<String, dynamic>>? _items;
  String? _error;
  String? _success;
  bool _busy = false;
  late int _session;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
  }

  void _sessionChanged() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    _keys.clear();
    _query.clear();
    if (mounted) {
      setState(() {
        _items = null;
        _error = null;
        _success = null;
        _busy = false;
      });
    }
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    _query.dispose();
    super.dispose();
  }

  Future<void> _search() async {
    final value = _query.text.trim();
    if (value.isEmpty || (_kind == 'order_id' && int.tryParse(value) == null)) {
      setState(() => _error = _kind == 'order_id'
          ? context.tr('請輸入有效訂單編號。', 'Enter a valid order number.')
          : context.tr('請輸入查詢資料。', 'Enter a search value.'));
      return;
    }
    final session = _session;
    setState(() {
      _busy = true;
      _error = null;
      _success = null;
      _items = null;
    });
    try {
      final response = await widget.api.get(
        '/merchant/redemptions/lookup',
        query: {_kind: value},
      );
      if (!mounted || session != _session) return;
      final rows = response['items'];
      setState(
        () => _items = rows is List
            ? rows
                .whereType<Map>()
                .map((row) => Map<String, dynamic>.from(row))
                .toList()
            : <Map<String, dynamic>>[],
      );
    } catch (error) {
      if (mounted && session == _session) {
        setState(() => _error = _errorMessage(context, error));
      }
    } finally {
      if (mounted && session == _session) setState(() => _busy = false);
    }
  }

  Future<void> _confirm(Map<String, dynamic> item) async {
    final session = _session;
    final id = item['wallet_item_id'];
    if (id is! int) return;
    final requiredBranch = item['branch_required'] == true;
    final rawBranches = item['branches'];
    final branches = rawBranches is List
        ? rawBranches
            .whereType<Map>()
            .map((row) => Map<String, dynamic>.from(row))
            .toList()
        : <Map<String, dynamic>>[];
    int? selectedBranch;
    String? branchError;
    final reason = TextEditingController();
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => StatefulBuilder(
        builder: (context, updateDialog) => AlertDialog(
          title: Text(context.tr('確認補記核銷', 'Confirm redemption record')),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${item['coupon_name'] ?? ''} · ${item['shop_name'] ?? ''}',
                ),
                const SizedBox(height: CgTheme.space8),
                Text(context.tr('請先與顧客核對券資料。確認後會記錄核銷及相關結算。',
                    'Check the coupon with the customer. Confirmation records the redemption and its settlement.')),
                if (requiredBranch) ...[
                  const SizedBox(height: CgTheme.space12),
                  DropdownButtonFormField<int>(
                    initialValue: selectedBranch,
                    decoration: InputDecoration(
                      labelText: context.tr(
                          '核銷分店（必選）', 'Redemption branch (required)'),
                      errorText: branchError,
                    ),
                    items: branches
                        .where((branch) => branch['id'] is int)
                        .map(
                          (branch) => DropdownMenuItem<int>(
                            value: branch['id'] as int,
                            child: Text(
                              '${branch['name'] ?? ''}',
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        )
                        .toList(),
                    onChanged: (value) => updateDialog(() {
                      selectedBranch = value;
                      branchError = null;
                    }),
                  ),
                  if (branches.isEmpty)
                    Text(context.tr('此券暫無可用分店，請聯絡平台核對。',
                        'No branch can redeem this coupon. Contact support to check.')),
                ],
                const SizedBox(height: CgTheme.space12),
                TextField(
                  controller: reason,
                  maxLength: 500,
                  decoration: InputDecoration(
                      labelText: context.tr('補記原因（選填）', 'Reason (optional)')),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: Text(context.tr('取消', 'Cancel')),
            ),
            FilledButton(
              onPressed: () {
                if (requiredBranch && selectedBranch == null) {
                  updateDialog(() => branchError = context.tr('請選擇此券可使用的分店。',
                      'Choose a branch that accepts this coupon.'));
                  return;
                }
                Navigator.pop(context, true);
              },
              child: Text(context.tr('確認補記', 'Record redemption')),
            ),
          ],
        ),
      ),
    );
    final note = reason.text.trim();
    WidgetsBinding.instance.addPostFrameCallback((_) => reason.dispose());
    if (confirmed != true || !mounted || session != _session || _busy) return;
    final key = _keys.putIfAbsent(
      '$id:${selectedBranch ?? ''}',
      _newRequestKey,
    );
    setState(() {
      _busy = true;
      _error = null;
      _success = null;
    });
    try {
      final response = await widget.api.post(
        '/merchant/redemptions/supplementary-scan',
        body: {
          'wallet_item_id': id,
          'idempotency_key': key,
          if (selectedBranch != null) 'branch_id': selectedBranch,
          if (note.isNotEmpty) 'reason': note,
        },
      );
      if (!mounted || session != _session) return;
      setState(() {
        _items?.removeWhere((row) => row['wallet_item_id'] == id);
        _success = context.tr('已補記核銷，記錄編號 ${response['redemption_id']}。',
            'Redemption ${response['redemption_id']} recorded.');
      });
    } catch (error) {
      if (mounted && session == _session) {
        setState(() => _error = context.tr(
            '${_errorMessage(context, error)} 如結果未確認，可再次補記同一張券。',
            '${_errorMessage(context, error)} If the result is unconfirmed, retry with the same coupon.'));
      }
    } finally {
      if (mounted && session == _session) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          title: Text(context.tr('補記核銷', 'Record a redemption')),
        ),
        body: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: CgTheme.formMaxWidth),
            child: ListView(
              padding: const EdgeInsets.all(CgTheme.pageInset),
              children: [
                Text(context.tr('查找顧客的券', 'Find a customer coupon'),
                    style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: CgTheme.space8),
                Text(context.tr('以顧客提供的電話、訂單編號或出示碼尾數查詢，核對後再補記。',
                    'Search by customer phone, order number or redemption code ending. Verify before recording.')),
                const SizedBox(height: CgTheme.space16),
                DropdownButtonFormField<String>(
                  initialValue: _kind,
                  decoration: InputDecoration(
                    labelText: context.tr('查詢方式', 'Search by'),
                    border: const OutlineInputBorder(),
                  ),
                  items: [
                    DropdownMenuItem(
                        value: 'phone',
                        child: Text(context.tr('電話號碼', 'Phone number'))),
                    DropdownMenuItem(
                        value: 'order_id',
                        child: Text(context.tr('訂單編號', 'Order number'))),
                    DropdownMenuItem(
                        value: 'code_suffix',
                        child: Text(context.tr('出示碼尾數', 'Code ending'))),
                  ],
                  onChanged: _busy
                      ? null
                      : (value) {
                          if (value != null) {
                            setState(() {
                              _kind = value;
                              _items = null;
                              _error = null;
                            });
                          }
                        },
                ),
                const SizedBox(height: CgTheme.space12),
                TextField(
                  controller: _query,
                  enabled: !_busy,
                  keyboardType: TextInputType.number,
                  onChanged: (_) {
                    if (_items != null || _error != null || _success != null) {
                      setState(() {
                        _items = null;
                        _error = null;
                        _success = null;
                      });
                    }
                  },
                  onSubmitted: (_) {
                    if (!_busy) _search();
                  },
                  decoration: InputDecoration(
                    labelText: context.tr('查詢資料', 'Search value'),
                    border: const OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: CgTheme.space12),
                MerchantPrimaryAction(
                  onPressed: _busy ? null : _search,
                  label: _busy
                      ? context.tr('處理中…', 'Working…')
                      : context.tr('查詢', 'Search'),
                ),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: CgTheme.space12),
                    child: Text(
                      _error!,
                      style:
                          TextStyle(color: Theme.of(context).colorScheme.error),
                    ),
                  ),
                if (_success != null)
                  Padding(
                    padding: const EdgeInsets.only(top: CgTheme.space12),
                    child: Text(_success!),
                  ),
                if (_items != null) ...[
                  const SizedBox(height: CgTheme.space24),
                  Text(context.tr('查詢結果', 'Search results'),
                      style: Theme.of(context).textTheme.titleMedium),
                  if (_items!.isEmpty)
                    Padding(
                      padding: const EdgeInsets.only(top: CgTheme.space12),
                      child: Text(context.tr(
                          '沒有符合條件的可用券。', 'No eligible coupons found.')),
                    ),
                  ..._items!.map(
                    (item) => Card(
                      margin: const EdgeInsets.only(top: CgTheme.space12),
                      child: Padding(
                        padding: const EdgeInsets.all(CgTheme.pageInset),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              '${item['coupon_name'] ?? ''}',
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                            const SizedBox(height: CgTheme.space4),
                            Text(
                              context.tr(
                                '${item['shop_name'] ?? ''} · 電話尾號 ${item['customer_phone_last4'] ?? '未提供'}',
                                '${item['shop_name'] ?? ''} · Phone ending ${item['customer_phone_last4'] ?? 'not provided'}',
                              ),
                            ),
                            Text(context.tr('有效至 ${item['valid_to'] ?? '未提供'}',
                                'Valid until ${item['valid_to'] ?? 'not provided'}')),
                            const SizedBox(height: CgTheme.space8),
                            OutlinedButton(
                              onPressed: _busy ? null : () => _confirm(item),
                              child: Text(context.tr(
                                  '補記這張券', 'Record this redemption')),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      );
}

/// Support reviews a specific redemption before requesting a forced revoke.
class ForceRevokeRedemptionScreen extends StatefulWidget {
  const ForceRevokeRedemptionScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<ForceRevokeRedemptionScreen> createState() =>
      _ForceRevokeRedemptionScreenState();
}

class _ForceRevokeRedemptionScreenState
    extends State<ForceRevokeRedemptionScreen> {
  final _id = TextEditingController();
  final _reason = TextEditingController();
  Map<String, dynamic>? _record;
  String? _error;
  String? _success;
  bool _busy = false;
  late int _session;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
  }

  void _sessionChanged() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    _id.clear();
    _reason.clear();
    if (mounted) {
      setState(() {
        _record = null;
        _error = null;
        _success = null;
        _busy = false;
      });
    }
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    _id.dispose();
    _reason.dispose();
    super.dispose();
  }

  Future<void> _lookup() async {
    final id = int.tryParse(_id.text.trim());
    if (id == null || id <= 0) {
      setState(() => _error =
          context.tr('請輸入有效核銷編號。', 'Enter a valid redemption number.'));
      return;
    }
    final session = _session;
    setState(() {
      _busy = true;
      _error = null;
      _success = null;
      _record = null;
    });
    try {
      final record = await widget.api.get(
        '/admin/redemptions/$id/force-review',
      );
      if (mounted && session == _session) setState(() => _record = record);
    } catch (error) {
      if (mounted && session == _session) {
        setState(() => _error = _errorMessage(context, error));
      }
    } finally {
      if (mounted && session == _session) setState(() => _busy = false);
    }
  }

  Future<void> _submit() async {
    final session = _session;
    final record = _record;
    final id = record?['redemption_id'];
    final reason = _reason.text.trim();
    if (id is! int || record?['revoked'] == true) return;
    if (reason.isEmpty) {
      setState(() =>
          _error = context.tr('請填寫撤銷原因。', 'Enter a reason for reversal.'));
      return;
    }
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(context.tr('確認撤銷核銷', 'Confirm reversal')),
        content: Text(
          context.tr(
            '核銷 #$id · ${record?['coupon_name'] ?? ''}\n\n撤銷會同步調整顧客券、積分、返現及商戶結算。',
            'Redemption #$id · ${record?['coupon_name'] ?? ''}\n\nReversing updates the customer coupon, points, cashback and merchant settlement.',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: Text(context.tr('取消', 'Cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: Text(context.tr('確認撤銷', 'Reverse redemption')),
          ),
        ],
      ),
    );
    if (confirmed != true || !mounted || session != _session || _busy) return;
    setState(() {
      _busy = true;
      _error = null;
      _success = null;
    });
    try {
      await widget.api.post(
        '/admin/redemptions/$id/force-revoke',
        body: {'reason': reason},
      );
      if (!mounted || session != _session) return;
      setState(() {
        _record = {...?record, 'revoked': true};
        _success = context.tr('已撤銷核銷 #$id。', 'Redemption #$id reversed.');
      });
    } catch (error) {
      if (mounted && session == _session) {
        setState(() => _error = context.tr(
            '${_errorMessage(context, error)} 如結果未確認，請重新查詢狀態。',
            '${_errorMessage(context, error)} If the result is unconfirmed, look up the status again.'));
      }
    } finally {
      if (mounted && session == _session) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          title: Text(context.tr('客服撤銷核銷', 'Reverse a redemption')),
        ),
        body: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: CgTheme.formMaxWidth),
            child: ListView(
              padding: const EdgeInsets.all(CgTheme.pageInset),
              children: [
                Text(context.tr('查找核銷記錄', 'Find a redemption'),
                    style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: CgTheme.space8),
                Text(context.tr('請先核對券、店舖及核銷時間，再填寫撤銷原因。',
                    'Check the coupon, shop and redemption time, then enter the reason for reversal.')),
                const SizedBox(height: CgTheme.space16),
                TextField(
                  controller: _id,
                  enabled: !_busy,
                  keyboardType: TextInputType.number,
                  onChanged: (_) {
                    if (_record != null) {
                      setState(() {
                        _record = null;
                        _success = null;
                      });
                    }
                  },
                  onSubmitted: (_) {
                    if (!_busy) _lookup();
                  },
                  decoration: InputDecoration(
                    labelText: context.tr('核銷編號', 'Redemption number'),
                    border: const OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: CgTheme.space12),
                MerchantPrimaryAction(
                  onPressed: _busy ? null : _lookup,
                  label: _busy
                      ? context.tr('處理中…', 'Working…')
                      : context.tr('查詢', 'Search'),
                ),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: CgTheme.space12),
                    child: Text(
                      _error!,
                      style:
                          TextStyle(color: Theme.of(context).colorScheme.error),
                    ),
                  ),
                if (_success != null)
                  Padding(
                    padding: const EdgeInsets.only(top: CgTheme.space12),
                    child: Text(_success!),
                  ),
                if (_record != null) ...[
                  const SizedBox(height: CgTheme.space20),
                  Text(
                    '${_record!['coupon_name'] ?? ''}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  Text(context.tr('店舖：${_record!['shop_name'] ?? ''}',
                      'Shop: ${_record!['shop_name'] ?? ''}')),
                  Text(context.tr('核銷時間：${_record!['scanned_at'] ?? '未提供'}',
                      'Redeemed at: ${_record!['scanned_at'] ?? 'not provided'}')),
                  const SizedBox(height: CgTheme.space8),
                  Align(
                      alignment: Alignment.centerLeft,
                      child: MerchantStatusPill(
                        active: _record!['revoked'] != true,
                        label: _record!['revoked'] == true
                            ? context.tr('已撤銷', 'Reversed')
                            : context.tr('已核銷', 'Redeemed'),
                      )),
                  if (_record!['revoked'] != true) ...[
                    const SizedBox(height: CgTheme.space16),
                    TextField(
                      controller: _reason,
                      enabled: !_busy,
                      maxLength: 500,
                      maxLines: 3,
                      decoration: InputDecoration(
                        labelText: context.tr(
                            '撤銷原因（必填）', 'Reason for reversal (required)'),
                        border: const OutlineInputBorder(),
                      ),
                    ),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: FilledButton(
                        onPressed: _busy ? null : _submit,
                        child: Text(context.tr('繼續撤銷', 'Continue to reversal')),
                      ),
                    ),
                  ],
                ],
              ],
            ),
          ),
        ),
      );
}
