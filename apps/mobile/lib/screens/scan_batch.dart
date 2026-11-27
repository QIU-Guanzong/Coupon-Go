import 'dart:math';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../language.dart';
import '../theme.dart';
import '../widgets/merchant_layout.dart';
import '../widgets/redemption_layout.dart';

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
  final _codeFocus = FocusNode();
  final List<_BatchItem> _items = [];
  String? _error;
  String? _success;
  String? _pendingKey;
  bool _busy = false;
  bool _confirming = false;
  bool _choosingBranch = false;
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
      _choosingBranch = false;
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
    _codeFocus.dispose();
    super.dispose();
  }

  Future<void> _add({bool keepFocus = false}) async {
    final code = _code.text.trim();
    final revision = widget.api.sessionRevision;
    if (!_canEdit) return;
    if (!RegExp(r'^\d{6}$').hasMatch(code)) {
      setState(() =>
          _error = context.tr('請輸入六位兌換碼', 'Enter a six-digit redemption code'));
      return;
    }
    if (_items.any((item) => item.code == code) || _items.length >= 20) {
      setState(() => _error = context.tr('不能加入重複兌換碼，每批最多 20 張券',
          'Each code can be added once, up to 20 vouchers per batch'));
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
        setState(() => _choosingBranch = true);
        final branch = await showModalBottomSheet<Map<String, dynamic>>(
          context: context,
          isScrollControlled: true,
          showDragHandle: true,
          builder: (context) => SafeArea(
            child: ConstrainedBox(
              constraints: BoxConstraints(
                  maxHeight: MediaQuery.sizeOf(context).height * .72),
              child: ListView(
                shrinkWrap: true,
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                children: [
                  ListTile(
                    title: Text(context.tr('選擇核銷分店', 'Choose a branch'),
                        style: Theme.of(context).textTheme.titleMedium),
                    subtitle: Text(context.tr('請選擇顧客所在的適用分店',
                        'Choose the branch the customer is visiting')),
                  ),
                  for (final item in raw)
                    ListTile(
                      leading: const Icon(Icons.storefront_outlined),
                      title: Text(item['name'] as String),
                      subtitle: item['address'] is String &&
                              (item['address'] as String).isNotEmpty
                          ? Text(item['address'] as String)
                          : null,
                      trailing: const Icon(Icons.chevron_right),
                      onTap: () => Navigator.pop(
                          context, Map<String, dynamic>.from(item as Map)),
                    ),
                ],
              ),
            ),
          ),
        );
        if (!_current(revision)) return;
        setState(() => _choosingBranch = false);
        if (branch == null) return;
        branchId = branch['id'] as int;
        branchName = branch['name'] as String;
        result = await widget.api.prepareScan(code, branchId: branchId);
        if (!_current(revision)) return;
      }
      if (result['can_redeem'] != true) {
        setState(() => _error = result['reason'] as String? ??
            context.tr('此券無法核銷', 'This voucher cannot be redeemed'));
        return;
      }
      final token = result['prepare_token'];
      if (token is! String || token.isEmpty) {
        throw const FormatException('預核銷資料不完整');
      }
      setState(() {
        _items.add(_BatchItem(
            code,
            result['coupon_name'] as String? ?? context.tr('優惠券', 'Voucher'),
            token,
            branchId,
            branchName));
        _code.clear();
      });
    } on ApiException catch (e) {
      if (_current(revision)) setState(() => _error = e.message);
    } catch (_) {
      if (_current(revision)) {
        setState(() => _error = context.tr(
            '未能確認券資料，請重試', 'Could not confirm voucher details. Try again.'));
      }
    } finally {
      if (_current(revision)) {
        setState(() {
          _busy = false;
          _choosingBranch = false;
        });
        if (keepFocus && _canEdit) _codeFocus.requestFocus();
      }
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
                '將一次核銷 ${submitted.length} 張券。若任何一張未通過驗證，整批都不會核銷。',
                '${submitted.length} vouchers will be redeemed together. If any fails validation, none will be redeemed.',
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
          _error = context.tr(
            '${e.message}。本批未核銷，請核對後再試。',
            '${e.message} This batch was not redeemed. Check the vouchers and try again.',
          );
        } else {
          _error = context.tr(
            '未能確認結果。請用原請求重試。',
            'Could not confirm the result. Retry the original request.',
          );
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
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final submitting = _busy && _pendingKey != null;
    final checking = _busy && _pendingKey == null && !_choosingBranch;
    final unfinished = _pendingKey != null && !_busy;
    final count = context.tr(
        '已預檢 ${_items.length} / 20', 'Checked ${_items.length} / 20');

    return Scaffold(
      backgroundColor: redemptionPageColor(context),
      appBar: AppBar(title: Text(context.tr('批量核銷', 'Batch redemption'))),
      body: RedemptionWorkbench(
        entry: RedemptionPanel(
          icon: Icons.pin_outlined,
          title: context.tr('加入優惠券', 'Add a voucher'),
          description: context.tr('逐張輸入六位兌換碼。預檢不會使用券。',
              'Enter each six-digit code. Checking does not redeem the voucher.'),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                controller: _code,
                focusNode: _codeFocus,
                enabled: _canEdit,
                keyboardType: TextInputType.number,
                textInputAction: TextInputAction.done,
                style: theme.textTheme.titleLarge?.merge(tabularFigures),
                inputFormatters: [
                  FilteringTextInputFormatter.digitsOnly,
                  LengthLimitingTextInputFormatter(6),
                ],
                decoration: InputDecoration(
                    labelText: context.tr('六位兌換碼', 'Six-digit code')),
                onSubmitted: (_) => _add(keepFocus: true),
              ),
              const SizedBox(height: 10),
              OutlinedButton.icon(
                onPressed: _canEdit ? _add : null,
                icon: const Icon(Icons.add_task_outlined),
                label: Text(checking
                    ? context.tr('預檢中…', 'Checking…')
                    : context.tr('預檢加入', 'Check and add')),
              ),
              if (checking) ...[
                const SizedBox(height: 10),
                const LinearProgressIndicator(),
              ],
            ],
          ),
        ),
        result: RedemptionPanel(
          icon: Icons.playlist_add_check,
          title: context.tr('本批優惠券', 'Vouchers in this batch'),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (_error != null) ...[
                RedemptionNotice(
                  message: _error!,
                  tone: unfinished
                      ? RedemptionTone.attention
                      : RedemptionTone.error,
                ),
                const SizedBox(height: 12),
              ],
              if (_success != null) ...[
                RedemptionNotice(
                    message: _success!, tone: RedemptionTone.success),
                const SizedBox(height: 12),
              ],
              Text(count,
                  style: theme.textTheme.labelLarge?.merge(tabularFigures)),
              const SizedBox(height: 6),
              ExcludeSemantics(
                child: LinearProgressIndicator(value: _items.length / 20),
              ),
              const SizedBox(height: 12),
              if (_items.isEmpty && _success == null)
                RedemptionNotice(
                  message: context.tr(
                      '預檢通過的券會顯示在這裡。', 'Checked vouchers appear here.'),
                ),
              for (final item in _items) ...[
                if (item != _items.first) const Divider(height: 1),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.confirmation_number_outlined),
                  title: Text(item.name, style: theme.textTheme.titleSmall),
                  subtitle: Text(item.branchName == null
                      ? context.tr('兌換碼 ${item.code}', 'Code ${item.code}')
                      : context.tr('兌換碼 ${item.code} · ${item.branchName}',
                          'Code ${item.code} · ${item.branchName}')),
                  trailing: IconButton(
                    tooltip: context.tr('移除', 'Remove'),
                    icon: const Icon(Icons.remove_circle_outline),
                    onPressed: _canEdit ? () => _remove(item) : null,
                  ),
                ),
              ],
            ],
          ),
        ),
      ),
      bottomNavigationBar: SafeArea(
        top: false,
        child: DecoratedBox(
          decoration: BoxDecoration(
            color: theme.colorScheme.surface,
            border: Border(
                top: BorderSide(color: theme.colorScheme.outlineVariant)),
          ),
          child: Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
            child: Center(
              heightFactor: 1,
              child: ConstrainedBox(
                constraints:
                    const BoxConstraints(maxWidth: CgTheme.formMaxWidth),
                child: MerchantPrimaryAction(
                  onPressed:
                      _busy || _confirming || _items.isEmpty ? null : _submit,
                  icon: unfinished ? Icons.refresh : Icons.task_alt,
                  label: submitting
                      ? context.tr('核銷中…', 'Redeeming…')
                      : unfinished
                          ? context.tr('使用原請求重試', 'Retry original request')
                          : context.tr('確認批量核銷', 'Confirm batch redemption'),
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
