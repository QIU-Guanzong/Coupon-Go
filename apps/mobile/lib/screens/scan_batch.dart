import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../language.dart';
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
  bool _confirming = false;
  late int _sessionRevision;

  bool get _canEdit => mounted && !_busy && !_confirming && _pendingKey == null;

  bool _current(int revision) =>
      mounted && widget.api.sessionRevision == revision;

  void _sessionChanged() {
    if (!mounted || _sessionRevision == widget.api.sessionRevision) return;
    _sessionRevision = widget.api.sessionRevision;
    setState(() {
      _items.clear();
      _code.clear();
      _pendingKey = null;
      _busy = false;
      _confirming = false;
      _success = null;
      _error = context.tr(
          '登入狀態已變更，請重新預檢。', 'Your session changed. Check the codes again.');
    });
  }

  @override
  void initState() {
    super.initState();
    _sessionRevision = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    _code.dispose();
    super.dispose();
  }

  Future<void> _add() async {
    final code = _code.text.trim();
    final revision = widget.api.sessionRevision;
    if (!_canEdit) return;
    if (!RegExp(r'^\d{6}$').hasMatch(code)) {
      setState(() => _error = context.tr('請輸入六位兌換碼', 'Enter a six-digit redemption code'));
      return;
    }
    if (_items.any((item) => item.code == code) || _items.length >= 20) {
      setState(() => _error = context.tr('不能加入重複兌換碼，每批最多 20 張券', 'Each code can be added once, up to 20 vouchers per batch'));
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
      if (!mounted || !_current(revision)) return;
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
            ListTile(
                title: Text(context.tr('選擇核銷分店', 'Choose a branch')),
                subtitle: Text(context.tr('請選擇顧客所在的適用分店', 'Choose the branch the customer is visiting'))),
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
        if (!_current(revision) || branch == null) return;
        branchId = branch['id'] as int;
        branchName = branch['name'] as String;
        result = await widget.api.prepareScan(code, branchId: branchId);
        if (!_current(revision)) return;
      }
      if (result['can_redeem'] != true) {
        setState(() => _error = result['reason'] as String? ?? context.tr('此券無法核銷', 'This voucher cannot be redeemed'));
        return;
      }
      final token = result['prepare_token'];
      if (token is! String || token.isEmpty) {
        throw const FormatException('預核銷資料不完整');
      }
      setState(() {
        _items.add(_BatchItem(code, result['coupon_name'] as String? ?? context.tr('優惠券', 'Voucher'),
            token, branchId, branchName));
        _code.clear();
      });
    } on ApiException catch (e) {
      if (_current(revision)) setState(() => _error = e.message);
    } catch (_) {
      if (_current(revision)) setState(() => _error = context.tr('未能確認券資料，請重試', 'Could not confirm voucher details. Try again.'));
    } finally {
      if (_current(revision)) setState(() => _busy = false);
    }
  }

  void _remove(_BatchItem item) {
    if (!_canEdit) return;
    setState(() => _items.remove(item));
  }

  Future<void> _submit() async {
    if (!mounted || _busy || _confirming || _items.isEmpty) return;
    final revision = widget.api.sessionRevision;
    // Freeze the exact order of codes, tokens and branches the merchant sees.
    final submitted = List<_BatchItem>.unmodifiable(_items);
    if (_pendingKey == null) {
      bool? ok;
      var answered = false;
      setState(() => _confirming = true);
      try {
        ok = await showDialog<bool>(
          context: context,
          builder: (context) {
            void answer(bool accepted) {
              if (answered || ModalRoute.of(context)?.isCurrent != true) {
                return;
              }
              answered = true;
              Navigator.pop(context, accepted);
            }

            return AlertDialog(
              title: Text(context.tr('確認批量核銷', 'Confirm batch redemption')),
              content: Text(context.tr(
                '將一次核銷 ${submitted.length} 張券。請確認顧客與分店資料。',
                '${submitted.length} vouchers will be redeemed. Check the customer and branch details.',
              )),
              actions: [
                TextButton(
                  onPressed: () => answer(false),
                  child: Text(context.tr('取消', 'Cancel')),
                ),
                FilledButton(
                  onPressed: () => answer(true),
                  child: Text(context.tr('確認核銷', 'Confirm redemption')),
                ),
              ],
            );
          },
        );
      } finally {
        if (_current(revision)) setState(() => _confirming = false);
      }
      if (ok != true || !_current(revision)) return;
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
          submitted.map((v) => v.code).toList(),
          submitted.map((v) => v.token).toList(),
          submitted.map((v) => v.branchId).toList(),
          _pendingKey!);
      if (!_current(revision)) return;
      setState(() {
        final count = result['total'] ?? submitted.length;
        _success = context.tr('已完成 $count 張券核銷', 'Redeemed $count vouchers');
        _items.clear();
        _pendingKey = null;
      });
    } on ApiException catch (e) {
      if (!_current(revision)) return;
      setState(() {
        if (e.status == 400 || e.status == 422) {
          _pendingKey = null;
          _error = '${e.message}。本批未核銷，請核對後再試。';
        } else {
          _error = '未能確認結果：${e.message}。請用原請求重試。';
        }
      });
    } catch (_) {
      if (_current(revision)) {
        setState(() => _error = context.tr('未能確認結果，請用原請求重試。',
            'Could not confirm the result. Retry the original request.'));
      }
    } finally {
      if (_current(revision)) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
      appBar: AppBar(
          title: Text(context.tr('批量核銷', 'Batch redemption'))),
      body: ListView(padding: const EdgeInsets.all(CgTheme.pageInset), children: [
        Text(context.tr('逐張輸入兌換碼並核對券及分店；提交時整批核銷。', 'Enter each code and check the voucher and branch before redeeming the batch.')),
        if (_error != null)
          Padding(
              padding: const EdgeInsets.only(top: CgTheme.space12),
              child: Text(_error!,
                  style:
                      TextStyle(color: Theme.of(context).colorScheme.error))),
        if (_success != null)
          Padding(
              padding: const EdgeInsets.only(top: CgTheme.space12), child: Text(_success!)),
        const SizedBox(height: CgTheme.space16),
        Row(children: [
          Expanded(
              child: TextField(
                  controller: _code,
                  enabled: _canEdit,
                  keyboardType: TextInputType.number,
                  inputFormatters: [
                    FilteringTextInputFormatter.digitsOnly,
                    LengthLimitingTextInputFormatter(6)
                  ],
                  decoration: InputDecoration(labelText: context.tr('六位兌換碼', 'Six-digit code')),
                  onSubmitted: (_) => _add())),
          const SizedBox(width: CgTheme.space8),
          FilledButton(
              onPressed: _canEdit ? _add : null,
              child: Text(context.tr('預檢加入', 'Check and add')))
        ]),
        const SizedBox(height: CgTheme.space16),
        Text(context.tr('已預檢 ${_items.length} / 20', 'Checked ${_items.length} / 20')),
        for (final item in _items)
          ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(item.name),
              subtitle: Text(item.branchName == null
                  ? context.tr('兌換碼 ${item.code}', 'Code ${item.code}')
                  : context.tr('兌換碼 ${item.code} · ${item.branchName}', 'Code ${item.code} · ${item.branchName}')),
              trailing: IconButton(
                  tooltip: context.tr('移除', 'Remove'),
                  icon: const Icon(Icons.close),
                  onPressed: _canEdit ? () => _remove(item) : null)),
        const SizedBox(height: CgTheme.space16),
        FilledButton(
            onPressed:
                _busy || _confirming || _items.isEmpty ? null : _submit,
            child: Text(_busy
                ? context.tr('處理中…', 'Processing…')
                : _pendingKey == null
                    ? context.tr('確認批量核銷', 'Confirm batch redemption')
                    : context.tr('使用原請求重試', 'Retry original request'))),
        if (_pendingKey != null)
          Padding(
              padding: const EdgeInsets.only(top: CgTheme.space8),
              child: Text(context.tr('結果尚未確認，請保持此頁並使用原請求重試。', 'The result is unconfirmed. Keep this page open and retry the original request.'))),
      ]));
}
