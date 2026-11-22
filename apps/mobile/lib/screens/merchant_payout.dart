import 'dart:convert';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api_client.dart';
import '../theme.dart';

/// Test-mode Connect payout. A durable client request id is saved before
/// sending, and server-side unresolved operations are loaded on every visit.
class MerchantPayoutScreen extends StatefulWidget {
  const MerchantPayoutScreen(
      {super.key,
      required this.api,
      required this.shopId,
      required this.shopName});
  final ApiClient api;
  final int shopId;
  final String shopName;
  @override
  State<MerchantPayoutScreen> createState() => _MerchantPayoutScreenState();
}

class _MerchantPayoutScreenState extends State<MerchantPayoutScreen> {
  final _amount = TextEditingController();
  Map<String, dynamic>? _balance;
  Map<String, dynamic>? _localRequest;
  List<Map<String, dynamic>> _active = [];
  String? _key;
  String? _message;
  String? _error;
  bool _loading = true;
  bool _busy = false;
  bool _invalidSavedRequest = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _amount.dispose();
    super.dispose();
  }

  String _errorText(Object e) => e is ApiException ? e.message : '連線失敗，請稍後重試';

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final values = await Future.wait([
        widget.api.get('/me'),
        widget.api.merchantConnectBalance(widget.shopId),
        widget.api.activeMerchantPayouts(widget.shopId),
      ]);
      final userId = values[0]['id'];
      if (userId is! int) throw const FormatException('Missing user');
      final key =
          'merchant_payout:${widget.api.baseUrl}:$userId:${widget.shopId}';
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getString(key);
      Map<String, dynamic>? local;
      var invalid = false;
      if (saved != null) {
        try {
          final decoded = jsonDecode(saved);
          if (decoded is! Map ||
              decoded['request_id'] is! String ||
              decoded['amount'] is! String ||
              !RegExp(r'^[A-Za-z0-9_-]{16,64}$')
                  .hasMatch(decoded['request_id'] as String) ||
              !_validAmount(decoded['amount'] as String))
            throw const FormatException();
          local = Map<String, dynamic>.from(decoded);
        } catch (_) {
          invalid = true;
        }
      }
      final raw = values[2]['payouts'];
      if (raw is! List || raw.any((item) => item is! Map))
        throw const FormatException('Missing payout list');
      if (!mounted) return;
      setState(() {
        _key = key;
        _balance = values[1];
        _active = raw.map((v) => Map<String, dynamic>.from(v as Map)).toList();
        _localRequest = local;
        _invalidSavedRequest = invalid;
        _loading = false;
      });
    } catch (e) {
      if (mounted)
        setState(() {
          _loading = false;
          _error = _errorText(e);
        });
    }
  }

  bool _validAmount(String value) {
    if (!RegExp(r'^\d{1,10}(?:\.\d{1,2})?$').hasMatch(value)) return false;
    final parts = value.split('.');
    final cents = BigInt.parse(parts[0]) * BigInt.from(100) +
        BigInt.parse(parts.length == 2 ? parts[1].padRight(2, '0') : '0');
    return cents > BigInt.zero;
  }

  bool get _localCanRetry {
    final local = _localRequest;
    if (local == null) return false;
    if (_active.isEmpty) return true;
    return _active.length == 1 &&
        _active.first['request_id'] == local['request_id'];
  }

  Future<void> _send(Map<String, dynamic> operation) async {
    if (_busy) return;
    setState(() {
      _busy = true;
      _error = null;
      _message = null;
    });
    try {
      final result = await widget.api.createMerchantPayout(widget.shopId,
          operation['amount'] as String, operation['request_id'] as String);
      final status = result['status'];
      if (status == 'paid_out' || status == 'failed') {
        final prefs = await SharedPreferences.getInstance();
        if (!await prefs.remove(_key!))
          throw const FormatException('Could not update local request');
        _localRequest = null;
      }
      if (!mounted) return;
      setState(() {
        _message = switch (status) {
          'paid_out' => '測試提款已完成；不涉及真實款項。',
          'failed' => '測試提款失敗，請核對後再試。',
          'submitted' || 'processing' => '測試提款處理中，請稍後刷新狀態。',
          _ => '提款結果待核對，請勿另建請求。',
        };
      });
      await _load();
    } catch (e) {
      if (mounted)
        setState(() {
          _error = '${_errorText(e)}。請保留原請求並核對狀態。';
        });
    } finally {
      if (mounted)
        setState(() {
          _busy = false;
        });
    }
  }

  Future<void> _create() async {
    if (_busy ||
        _key == null ||
        _invalidSavedRequest ||
        _localRequest != null ||
        _active.isNotEmpty) return;
    final amount = _amount.text.trim();
    if (!_validAmount(amount)) {
      setState(() {
        _error = '請輸入大於零、最多兩位小數的金額';
      });
      return;
    }
    final confirm = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
                title: const Text('確認測試提款'),
                content: Text(
                    '為 ${widget.shopName} 提交 HK\$$amount 的測試提款？此流程不涉及真實款項。'),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context, false),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () => Navigator.pop(context, true),
                      child: const Text('確認提交'))
                ]));
    if (confirm != true || !mounted || _busy) return;
    try {
      final random = Random.secure();
      final id = List.generate(
              16, (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'))
          .join();
      final op = {'request_id': id, 'amount': amount};
      final prefs = await SharedPreferences.getInstance();
      if (!await prefs.setString(_key!, jsonEncode(op)))
        throw const FormatException('Save failed');
      if (!mounted) return;
      setState(() {
        _localRequest = op;
      });
      await _send(op);
    } catch (_) {
      if (mounted)
        setState(() {
          _error = '無法保存提款請求，請稍後重試';
        });
    }
  }

  @override
  Widget build(BuildContext context) {
    final mode = _balance?['connect_mode'];
    final ready = _balance?['account_ready'] == true &&
        _balance?['connect_configured'] == true &&
        (mode == 'simulated' || mode == 'stripe_test');
    final available = _balance?['available'];
    return Scaffold(
        appBar: AppBar(
            title: const Text('Stripe 測試提款'),
            backgroundColor: CgColors.vendorGold),
        body: Center(
            child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 680),
                child: ListView(padding: const EdgeInsets.all(20), children: [
                  Text(widget.shopName,
                      style: const TextStyle(
                          fontSize: 23, fontWeight: FontWeight.bold)),
                  const SizedBox(height: 8),
                  const Text('目前只支援測試或模擬，不會進行正式提款。'),
                  if (_loading) const LinearProgressIndicator(),
                  if (_error != null) ...[
                    const SizedBox(height: 12),
                    Semantics(
                        liveRegion: true,
                        child: Text(_error!,
                            style: TextStyle(
                                color: Theme.of(context).colorScheme.error))),
                    TextButton(
                        onPressed: _busy ? null : _load,
                        child: const Text('刷新狀態'))
                  ],
                  if (_message != null) ...[
                    const SizedBox(height: 12),
                    Semantics(liveRegion: true, child: Text(_message!))
                  ],
                  if (!_loading && _error == null && _balance != null) ...[
                    const SizedBox(height: 16),
                    Text(switch (mode) {
                      'simulated' => '本地模擬環境',
                      'stripe_test' => 'Stripe 測試環境',
                      'unavailable' => '收款服務尚未配置',
                      _ => '收款環境待確認',
                    }),
                    Text(
                        '已接入流水可提取：${available is num ? 'HK\$${available.toStringAsFixed(2)}' : '—'}'),
                    if (_invalidSavedRequest) const Text('本機提款記錄無法讀取，請聯絡平台核對。'),
                    if (_active.isNotEmpty) ...[
                      const SizedBox(height: 16),
                      const Text('待完成提款',
                          style: TextStyle(
                              fontSize: 18, fontWeight: FontWeight.bold)),
                      for (final row in _active)
                        ListTile(
                            contentPadding: EdgeInsets.zero,
                            title: Text(
                                '金額：${row['amount'] is num ? 'HK\$${(row['amount'] as num).toStringAsFixed(2)}' : '待確認'}'),
                            subtitle: Text(_payoutStatus(row['status']))),
                    ],
                    if (_localRequest != null) ...[
                      const SizedBox(height: 12),
                      Text(
                          '本機保留 HK\$${_localRequest!['amount']} 的提款請求；若上次未送達，重試將提交該請求。'),
                      OutlinedButton(
                          onPressed: _busy || !_localCanRetry || !ready
                              ? null
                              : () => _send(_localRequest!),
                          child: const Text('用同一請求核對或重試')),
                    ],
                    if (_active.isEmpty &&
                        _localRequest == null &&
                        !_invalidSavedRequest) ...[
                      const SizedBox(height: 18),
                      TextField(
                          controller: _amount,
                          keyboardType: const TextInputType.numberWithOptions(
                              decimal: true),
                          decoration:
                              const InputDecoration(labelText: '提款金額（HK\$）')),
                      const SizedBox(height: 14),
                      FilledButton(
                          onPressed: ready && !_busy ? _create : null,
                          child: const Text('提交測試提款')),
                      if (!ready) const Text('收款帳戶尚未就緒，暫不能提款。'),
                    ],
                    const SizedBox(height: 12),
                    TextButton(
                        onPressed: _busy ? null : _load,
                        child: const Text('刷新狀態')),
                  ],
                ]))));
  }
}

String _payoutStatus(Object? status) => switch (status) {
      'processing' || 'submitted' => '處理中',
      'payout_unknown' => '結果待核對，請勿重複提款',
      _ => '狀態待確認，請聯絡平台',
    };
