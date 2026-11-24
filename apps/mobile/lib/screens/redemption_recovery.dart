import 'dart:math';

import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';

String _errorMessage(Object error) =>
    error is ApiException ? error.message : '連線未完成，請稍後再試。';

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
      setState(() => _error = _kind == 'order_id' ? '請輸入有效訂單編號。' : '請輸入查詢資料。');
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
        setState(() => _error = _errorMessage(error));
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
          title: const Text('確認補記核銷'),
          content: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '${item['coupon_name'] ?? ''} · ${item['shop_name'] ?? ''}',
                ),
                const SizedBox(height: 8),
                const Text('請先與顧客核對券資料。確認後會記錄核銷及相關結算。'),
                if (requiredBranch) ...[
                  const SizedBox(height: 12),
                  DropdownButtonFormField<int>(
                    initialValue: selectedBranch,
                    decoration: InputDecoration(
                      labelText: '核銷分店（必選）',
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
                  if (branches.isEmpty) const Text('此券暫無可用分店，請聯絡平台核對。'),
                ],
                const SizedBox(height: 12),
                TextField(
                  controller: reason,
                  maxLength: 500,
                  decoration: const InputDecoration(labelText: '補記原因（選填）'),
                ),
              ],
            ),
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(context, false),
              child: const Text('取消'),
            ),
            FilledButton(
              onPressed: () {
                if (requiredBranch && selectedBranch == null) {
                  updateDialog(() => branchError = '請選擇此券可使用的分店。');
                  return;
                }
                Navigator.pop(context, true);
              },
              child: const Text('確認補記'),
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
        _success = '已補記核銷，記錄編號 ${response['redemption_id']}。';
      });
    } catch (error) {
      if (mounted && session == _session) {
        setState(() => _error = '${_errorMessage(error)} 如結果未確認，可再次補記同一張券。');
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
            constraints: const BoxConstraints(maxWidth: 720),
            child: ListView(
              padding: const EdgeInsets.all(20),
              children: [
                Text('查找顧客的券', style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 8),
                const Text('以顧客提供的電話、訂單編號或出示碼尾數查詢，核對後再補記。'),
                const SizedBox(height: 16),
                DropdownButtonFormField<String>(
                  initialValue: _kind,
                  decoration: const InputDecoration(
                    labelText: '查詢方式',
                    border: OutlineInputBorder(),
                  ),
                  items: const [
                    DropdownMenuItem(value: 'phone', child: Text('電話號碼')),
                    DropdownMenuItem(value: 'order_id', child: Text('訂單編號')),
                    DropdownMenuItem(
                        value: 'code_suffix', child: Text('出示碼尾數')),
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
                const SizedBox(height: 12),
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
                  decoration: const InputDecoration(
                    labelText: '查詢資料',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                Align(
                  alignment: Alignment.centerLeft,
                  child: FilledButton(
                    onPressed: _busy ? null : _search,
                    child: Text(_busy ? '處理中…' : '查詢'),
                  ),
                ),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(
                      _error!,
                      style:
                          TextStyle(color: Theme.of(context).colorScheme.error),
                    ),
                  ),
                if (_success != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(_success!),
                  ),
                if (_items != null) ...[
                  const SizedBox(height: 24),
                  Text('查詢結果', style: Theme.of(context).textTheme.titleMedium),
                  if (_items!.isEmpty)
                    const Padding(
                      padding: EdgeInsets.only(top: 12),
                      child: Text('沒有符合條件的可用券。'),
                    ),
                  ..._items!.map(
                    (item) => Card(
                      child: Padding(
                        padding: const EdgeInsets.all(16),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              '${item['coupon_name'] ?? ''}',
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                            const SizedBox(height: 4),
                            Text(
                              '${item['shop_name'] ?? ''} · 電話尾號 ${item['customer_phone_last4'] ?? '未提供'}',
                            ),
                            Text('有效至 ${item['valid_to'] ?? '未提供'}'),
                            const SizedBox(height: 8),
                            OutlinedButton(
                              onPressed: _busy ? null : () => _confirm(item),
                              child: const Text('補記這張券'),
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
      setState(() => _error = '請輸入有效核銷編號。');
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
        setState(() => _error = _errorMessage(error));
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
      setState(() => _error = '請填寫撤銷原因。');
      return;
    }
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('確認撤銷核銷'),
        content: Text(
          '核銷 #$id · ${record?['coupon_name'] ?? ''}\n\n撤銷會同步調整顧客券、積分、返現及商戶結算。',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('確認撤銷'),
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
        _success = '已撤銷核銷 #$id。';
      });
    } catch (error) {
      if (mounted && session == _session) {
        setState(() => _error = '${_errorMessage(error)} 如結果未確認，請重新查詢狀態。');
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
            constraints: const BoxConstraints(maxWidth: 720),
            child: ListView(
              padding: const EdgeInsets.all(20),
              children: [
                Text('查找核銷記錄', style: Theme.of(context).textTheme.titleLarge),
                const SizedBox(height: 8),
                const Text('請先核對券、店舖及核銷時間，再填寫撤銷原因。'),
                const SizedBox(height: 16),
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
                  decoration: const InputDecoration(
                    labelText: '核銷編號',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 12),
                Align(
                  alignment: Alignment.centerLeft,
                  child: FilledButton(
                    onPressed: _busy ? null : _lookup,
                    child: Text(_busy ? '處理中…' : '查詢'),
                  ),
                ),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(
                      _error!,
                      style:
                          TextStyle(color: Theme.of(context).colorScheme.error),
                    ),
                  ),
                if (_success != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 12),
                    child: Text(_success!),
                  ),
                if (_record != null) ...[
                  const SizedBox(height: 20),
                  Text(
                    '${_record!['coupon_name'] ?? ''}',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  Text('店舖：${_record!['shop_name'] ?? ''}'),
                  Text('核銷時間：${_record!['scanned_at'] ?? '未提供'}'),
                  Text(_record!['revoked'] == true ? '狀態：已撤銷' : '狀態：已核銷'),
                  if (_record!['revoked'] != true) ...[
                    const SizedBox(height: 16),
                    TextField(
                      controller: _reason,
                      enabled: !_busy,
                      maxLength: 500,
                      maxLines: 3,
                      decoration: const InputDecoration(
                        labelText: '撤銷原因（必填）',
                        border: OutlineInputBorder(),
                      ),
                    ),
                    Align(
                      alignment: Alignment.centerLeft,
                      child: FilledButton(
                        onPressed: _busy ? null : _submit,
                        child: const Text('繼續撤銷'),
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
