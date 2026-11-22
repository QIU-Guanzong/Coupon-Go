import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../theme.dart';

/// Batch redemption uses the same read-only check as single scans.
class ScanBatchScreen extends StatefulWidget {
  const ScanBatchScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<ScanBatchScreen> createState() => _ScanBatchScreenState();
}

class _BatchItem {
  const _BatchItem(
      this.code, this.name, this.token, this.branchId, this.branchName);
  final String code, name, token;
  final int? branchId;
  final String? branchName;
}

class _ScanBatchScreenState extends State<ScanBatchScreen> {
  final _code = TextEditingController();
  final List<_BatchItem> _items = [];
  String? _error;
  String? _success;
  String? _pendingKey;
  bool _busy = false;

  @override
  void dispose() {
    _code.dispose();
    super.dispose();
  }

  Future<void> _add() async {
    final code = _code.text.trim();
    if (_busy || _pendingKey != null) return;
    if (!RegExp(r'^\d{6}$').hasMatch(code)) {
      setState(() => _error = '請輸入六位兌換碼');
      return;
    }
    if (_items.any((item) => item.code == code) || _items.length >= 20) {
      setState(() => _error = '不能加入重複兌換碼，每批最多 20 張券');
      return;
    }
    FocusManager.instance.primaryFocus?.unfocus();
    setState(() {
      _busy = true;
      _error = null;
      _success = null;
    });
    try {
      var result = await widget.api.prepareScan(code);
      if (!mounted) return;
      int? branchId;
      String? branchName;
      if (result['branch_selection_required'] == true) {
        final raw = result['allowed_branches'];
        if (raw is! List ||
            raw.isEmpty ||
            raw.any(
                (v) => v is! Map || v['id'] is! int || v['name'] is! String)) {
          throw const FormatException('無可選分店');
        }
        final branch = await showModalBottomSheet<Map<String, dynamic>>(
          context: context,
          showDragHandle: true,
          builder: (context) => SafeArea(
              child: ListView(shrinkWrap: true, children: [
            const ListTile(
                title: Text('選擇核銷分店'), subtitle: Text('請選擇顧客所在的適用分店')),
            for (final item in raw)
              ListTile(
                  title: Text(item['name'] as String),
                  subtitle: item['address'] is String
                      ? Text(item['address'] as String)
                      : null,
                  onTap: () => Navigator.pop(
                      context, Map<String, dynamic>.from(item as Map))),
          ])),
        );
        if (!mounted || branch == null) return;
        branchId = branch['id'] as int;
        branchName = branch['name'] as String;
        result = await widget.api.prepareScan(code, branchId: branchId);
        if (!mounted) return;
      }
      if (result['can_redeem'] != true) {
        setState(() => _error = result['reason'] as String? ?? '此券無法核銷');
        return;
      }
      final token = result['prepare_token'];
      if (token is! String || token.isEmpty) {
        throw const FormatException('預核銷資料不完整');
      }
      setState(() {
        _items.add(_BatchItem(code, result['coupon_name'] as String? ?? '優惠券',
            token, branchId, branchName));
        _code.clear();
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } catch (_) {
      if (mounted) setState(() => _error = '未能確認券資料，請重試');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _submit() async {
    if (_busy || _items.isEmpty) return;
    if (_pendingKey == null) {
      final ok = await showDialog<bool>(
          context: context,
          builder: (context) => AlertDialog(
                  title: const Text('確認批量核銷'),
                  content: Text('將一次核銷 ${_items.length} 張券。請確認顧客與分店資料。'),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(context, false),
                        child: const Text('取消')),
                    FilledButton(
                        onPressed: () => Navigator.pop(context, true),
                        child: const Text('確認核銷'))
                  ]));
      if (ok != true || !mounted) return;
      final random = Random.secure();
      _pendingKey = List.generate(
              16, (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'))
          .join();
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await widget.api.scanBatch(
          _items.map((v) => v.code).toList(),
          _items.map((v) => v.token).toList(),
          _items.map((v) => v.branchId).toList(),
          _pendingKey!);
      if (!mounted) return;
      setState(() {
        _success = '已完成 ${result['total'] ?? _items.length} 張券核銷';
        _items.clear();
        _pendingKey = null;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        if (e.status == 400 || e.status == 422) {
          _pendingKey = null;
          _error = '${e.message}。本批未核銷，請核對後再試。';
        } else {
          _error = '未能確認結果：${e.message}。請用原請求重試。';
        }
      });
    } catch (_) {
      if (mounted) setState(() => _error = '未能確認結果，請用原請求重試。');
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
      appBar: AppBar(
          title: const Text('批量核銷'), backgroundColor: CgColors.vendorGold),
      body: ListView(padding: const EdgeInsets.all(20), children: [
        const Text('逐張輸入兌換碼並核對券及分店；提交時整批核銷。'),
        if (_error != null)
          Padding(
              padding: const EdgeInsets.only(top: 12),
              child: Text(_error!,
                  style:
                      TextStyle(color: Theme.of(context).colorScheme.error))),
        if (_success != null)
          Padding(
              padding: const EdgeInsets.only(top: 12), child: Text(_success!)),
        const SizedBox(height: 16),
        Row(children: [
          Expanded(
              child: TextField(
                  controller: _code,
                  enabled: !_busy && _pendingKey == null,
                  keyboardType: TextInputType.number,
                  inputFormatters: [
                    FilteringTextInputFormatter.digitsOnly,
                    LengthLimitingTextInputFormatter(6)
                  ],
                  decoration: const InputDecoration(labelText: '六位兌換碼'),
                  onSubmitted: (_) => _add())),
          const SizedBox(width: 8),
          FilledButton(
              onPressed: _busy || _pendingKey != null ? null : _add,
              child: const Text('預檢加入'))
        ]),
        const SizedBox(height: 16),
        Text('已預檢 ${_items.length} / 20'),
        for (final item in _items)
          ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(item.name),
              subtitle: Text(item.branchName == null
                  ? '兌換碼 ${item.code}'
                  : '兌換碼 ${item.code} · ${item.branchName}'),
              trailing: IconButton(
                  tooltip: '移除',
                  icon: const Icon(Icons.close),
                  onPressed: _busy || _pendingKey != null
                      ? null
                      : () => setState(() => _items.remove(item)))),
        const SizedBox(height: 16),
        FilledButton(
            onPressed: _busy || _items.isEmpty ? null : _submit,
            child: Text(_busy
                ? '處理中…'
                : _pendingKey == null
                    ? '確認批量核銷'
                    : '使用原請求重試')),
        if (_pendingKey != null)
          const Padding(
              padding: EdgeInsets.only(top: 8),
              child: Text('結果尚未確認，請保持此頁並使用原請求重試。')),
      ]));
}
