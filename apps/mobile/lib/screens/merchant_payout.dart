import 'dart:convert';
import 'dart:math';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api_client.dart';
import '../language.dart';

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
  int _generation = 0;
  late int _sessionRevision;

  bool _isCurrent(int revision) =>
      mounted && revision == widget.api.sessionRevision;

  void _sessionChanged() {
    if (!mounted || _sessionRevision == widget.api.sessionRevision) return;
    _sessionRevision = widget.api.sessionRevision;
    _generation++;
    setState(() {
      _balance = null;
      _localRequest = null;
      _active = [];
      _key = null;
      _message = null;
      _loading = false;
      _busy = false;
      _error = '登入狀態已變更，請刷新後再操作。';
    });
  }

  @override
  void initState() {
    super.initState();
    _sessionRevision = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    _load();
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    _amount.dispose();
    super.dispose();
  }

  String _errorText(Object e) => e is ApiException ? e.message : '連線失敗，請稍後重試';

  Future<void> _load() async {
    final revision = widget.api.sessionRevision;
    final generation = ++_generation;
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
      if (!_isCurrent(revision) || generation != _generation) return;
      final userId = values[0]['id'];
      if (userId is! int) throw const FormatException('Missing user');
      final key =
          'merchant_payout:${widget.api.baseUrl}:$userId:${widget.shopId}';
      final prefs = await SharedPreferences.getInstance();
      if (!_isCurrent(revision) || generation != _generation) return;
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
              !_validAmount(decoded['amount'] as String)) {
            throw const FormatException();
          }
          local = Map<String, dynamic>.from(decoded);
        } catch (_) {
          invalid = true;
        }
      }
      final raw = values[2]['payouts'];
      if (raw is! List || raw.any((item) => item is! Map)) {
        throw const FormatException('Missing payout list');
      }
      if (!_isCurrent(revision) || generation != _generation) return;
      setState(() {
        _key = key;
        _balance = values[1];
        _active = raw.map((v) => Map<String, dynamic>.from(v as Map)).toList();
        _localRequest = local;
        _invalidSavedRequest = invalid;
        _loading = false;
      });
    } catch (e) {
      if (_isCurrent(revision) && generation == _generation) {
        setState(() {
          _loading = false;
          _error = _errorText(e);
        });
      }
    }
  }

  BigInt? _cents(String value) {
    if (!RegExp(r'^\d{1,10}(?:\.\d{1,2})?$').hasMatch(value)) return null;
    final parts = value.split('.');
    return BigInt.parse(parts[0]) * BigInt.from(100) +
        BigInt.parse(parts.length == 2 ? parts[1].padRight(2, '0') : '0');
  }

  bool _validAmount(String value) =>
      (_cents(value) ?? BigInt.zero) > BigInt.zero;

  bool get _localCanRetry {
    final local = _localRequest;
    if (local == null) return false;
    if (_active.isEmpty) return true;
    final amount = _active.length == 1 ? _active.first['amount'] : null;
    return _active.length == 1 &&
        _active.first['request_id'] == local['request_id'] &&
        amount is num &&
        amount.isFinite &&
        _cents(amount.toStringAsFixed(2)) == _cents(local['amount'] as String);
  }

  Future<void> _send(Map<String, dynamic> operation) async {
    if (_busy || _key == null) return;
    final revision = widget.api.sessionRevision;
    final key = _key!;
    setState(() {
      _busy = true;
      _error = null;
      _message = null;
    });
    try {
      final result = await widget.api.createMerchantPayout(widget.shopId,
          operation['amount'] as String, operation['request_id'] as String);
      if (!_isCurrent(revision)) return;
      final status = result['status'];
      if (status == 'paid_out' || status == 'failed') {
        final prefs = await SharedPreferences.getInstance();
        if (!_isCurrent(revision)) return;
        if (!await prefs.remove(key)) {
          throw const FormatException('Could not update local request');
        }
      }
      if (!mounted || !_isCurrent(revision)) return;
      final t = context.tr;
      setState(() {
        if (status == 'paid_out' || status == 'failed') _localRequest = null;
        _message = switch (status) {
          'paid_out' => t('核對已完成，沒有轉移款項。', 'Verification complete. No funds were moved.'),
          'failed' => t('未能完成提款核對，請重試。', 'Verification could not complete. Try again.'),
          'submitted' || 'processing' => t('核對處理中，請稍後刷新狀態。', 'Verification in progress. Refresh the status shortly.'),
          _ => t('結果待核對，請勿另建請求。', 'Verification pending. Do not start another request.'),
        };
      });
      await _load();
    } catch (e) {
      if (_isCurrent(revision)) {
        setState(() {
          _error = '${_errorText(e)}。請保留原請求並核對狀態。';
        });
      }
    } finally {
      if (_isCurrent(revision)) {
        setState(() {
          _busy = false;
        });
      }
    }
  }

  Future<void> _create() async {
    if (_busy ||
        _key == null ||
        _invalidSavedRequest ||
        _localRequest != null ||
        _active.isNotEmpty) {
      return;
    }
    final revision = widget.api.sessionRevision;
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
                title: Text(context.tr('確認提款核對', 'Confirm payout verification')),
                content: Text(
                    context.tr(
                      '${widget.shopName}：確認核對 HK\$$amount？不會轉移款項。',
                      '${widget.shopName}: verify HK\$$amount? No funds will be moved.',
                    )),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(context, false),
                      child: Text(context.tr('取消', 'Cancel'))),
                  FilledButton(
                      onPressed: () => Navigator.pop(context, true),
                      child: Text(context.tr('確認提交', 'Confirm request')))
                ]));
    if (confirm != true ||
        !_isCurrent(revision) ||
        _busy ||
        _localRequest != null ||
        _active.isNotEmpty) {
      return;
    }
    setState(() {
      _busy = true;
    });
    try {
      final latest = await widget.api.activeMerchantPayouts(widget.shopId);
      if (!_isCurrent(revision)) return;
      final payouts = latest['payouts'];
      if (payouts is! List || payouts.any((row) => row is! Map)) {
        throw const FormatException('Missing payout list');
      }
      if (payouts.isNotEmpty) {
        setState(() {
          _active =
              payouts.map((v) => Map<String, dynamic>.from(v as Map)).toList();
          _busy = false;
          _message = '已有未完成提款，請先核對。';
        });
        return;
      }
      final random = Random.secure();
      final id = List.generate(
              16, (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'))
          .join();
      final op = {'request_id': id, 'amount': amount};
      final prefs = await SharedPreferences.getInstance();
      if (!_isCurrent(revision) || _key == null) return;
      final key = _key!;
      if (!await prefs.setString(key, jsonEncode(op))) {
        throw const FormatException('Save failed');
      }
      if (!_isCurrent(revision)) return;
      setState(() {
        _localRequest = op;
        _busy = false;
      });
      await _send(op);
    } catch (e) {
      if (_isCurrent(revision)) {
        setState(() {
          _error = e is ApiException ? e.message : '無法確認或保存提款請求，請稍後重試';
          _busy = false;
        });
      }
    }
  }

  Future<void> _restore(Map<String, dynamic> row) async {
    if (_busy || _key == null || _localRequest != null || _active.length != 1) {
      return;
    }
    final revision = widget.api.sessionRevision;
    final id = row['request_id'];
    final amount = row['amount'];
    if (id is! String ||
        !RegExp(r'^[A-Za-z0-9_-]{16,64}$').hasMatch(id) ||
        amount is! num ||
        !amount.isFinite) {
      return;
    }
    final value = amount.toStringAsFixed(2);
    if (!_validAmount(value)) return;
    setState(() {
      _busy = true;
    });
    try {
      final op = {'request_id': id, 'amount': value};
      final prefs = await SharedPreferences.getInstance();
      if (!_isCurrent(revision) || _key == null) return;
      final key = _key!;
      if (!await prefs.setString(key, jsonEncode(op))) {
        throw const FormatException('Save failed');
      }
      if (_isCurrent(revision)) {
        setState(() {
          _localRequest = op;
        });
      }
    } catch (_) {
      if (_isCurrent(revision)) {
        setState(() {
          _error = '無法保存提款請求，請稍後重試';
        });
      }
    } finally {
      if (_isCurrent(revision)) {
        setState(() {
          _busy = false;
        });
      }
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
            title: Text(context.tr('提款核對', 'Payout verification'))),
        body: Center(
            child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 680),
                child: ListView(padding: const EdgeInsets.all(20), children: [
                  Text(widget.shopName,
                      style: const TextStyle(
                          fontSize: 23, fontWeight: FontWeight.bold)),
                  const SizedBox(height: 8),
                  Text(context.tr('此流程不會轉移款項。款項狀態以實際流水為準。',
                      'This process does not move funds. Check the ledger for the actual status.')),
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
                        child: Text(context.tr('刷新狀態', 'Refresh status')))
                  ],
                  if (_message != null) ...[
                    const SizedBox(height: 12),
                    Semantics(liveRegion: true, child: Text(_message!))
                  ],
                  if (!_loading && _error == null && _balance != null) ...[
                    const SizedBox(height: 16),
                    Text(switch (mode) {
                      'simulated' || 'stripe_test' => context.tr('核對渠道已連接', 'Verification channel connected'),
                      'unavailable' => context.tr('未連接收款渠道', 'No payout channel connected'),
                      _ => context.tr('收款狀態待確認', 'Payout status unconfirmed'),
                    }),
                    Text(
                        '已接入流水可提取：${available is num ? 'HK\$${available.toStringAsFixed(2)}' : '—'}'),
                    if (_invalidSavedRequest) const Text('本機提款記錄無法讀取，請聯絡平台核對。'),
                    if (_active.isNotEmpty) ...[
                      const SizedBox(height: 16),
                      Text(context.tr('待完成提款', 'Pending payout'),
                          style: const TextStyle(
                              fontSize: 18, fontWeight: FontWeight.bold)),
                      for (final row in _active)
                        ListTile(
                            contentPadding: EdgeInsets.zero,
                            title: Text(
                                '金額：${row['amount'] is num ? 'HK\$${(row['amount'] as num).toStringAsFixed(2)}' : '待確認'}'),
                            subtitle: Text(_payoutStatus(row['status']))),
                    ],
                    if (_active.length == 1 &&
                        _localRequest == null &&
                        !_invalidSavedRequest &&
                        _active.first['request_id'] is String &&
                        _active.first['amount'] is num)
                      Align(
                          alignment: Alignment.centerLeft,
                          child: TextButton(
                              onPressed:
                                  _busy ? null : () => _restore(_active.first),
                              child: Text(context.tr('恢復原提款請求', 'Resume payout request')))),
                    if (_active.any((row) => row['request_id'] == null))
                      const Text('舊提款缺少請求編號，請聯絡平台核對。'),
                    if (_localRequest != null) ...[
                      const SizedBox(height: 12),
                      Text(
                          '本機保留 HK\$${_localRequest!['amount']} 的提款請求；若上次未送達，重試將提交該請求。'),
                      OutlinedButton(
                          onPressed: _busy || !_localCanRetry || !ready
                              ? null
                              : () => _send(_localRequest!),
                          child: Text(context.tr('用同一請求核對或重試', 'Check or retry this request'))),
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
                          child: Text(context.tr('提交提款核對', 'Submit payout verification'))),
                      if (!ready) const Text('收款帳戶尚未就緒，暫不能提款。'),
                    ],
                    const SizedBox(height: 12),
                    TextButton(
                        onPressed: _busy ? null : _load,
                        child: Text(context.tr('刷新狀態', 'Refresh status'))),
                  ],
                ]))));
  }
}

String _payoutStatus(Object? status) => switch (status) {
      'processing' || 'submitted' => '處理中',
      'payout_unknown' => '結果待核對，請勿重複提款',
      _ => '狀態待確認，請聯絡平台',
    };
