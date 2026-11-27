import 'dart:math';

import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../widgets/merchant_layout.dart';
import '../widgets/redemption_layout.dart';

String _errorMessage(BuildContext context, Object error) =>
    error is ApiException
        ? error.message
        : context.tr('連線未完成，請稍後再試。', 'Connection failed. Try again later.');

String _newRequestKey() {
  final random = Random.secure();
  final bytes = List<int>.generate(16, (_) => random.nextInt(256));
  return 'supp-${bytes.map((n) => n.toRadixString(16).padLeft(2, '0')).join()}';
}

typedef _SupplementDetails = ({int? branchId, String reason});

class _SupplementRequest {
  _SupplementRequest(this.itemId, this.couponName, _SupplementDetails details)
      : body = Map.unmodifiable({
          'wallet_item_id': itemId,
          'idempotency_key': _newRequestKey(),
          if (details.branchId != null) 'branch_id': details.branchId,
          if (details.reason.isNotEmpty) 'reason': details.reason,
        });

  final int itemId;
  final String couponName;
  final Map<String, dynamic> body;
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
  String _kind = 'phone';
  List<Map<String, dynamic>>? _items;
  String? _error;
  String? _success;
  _SupplementRequest? _pending;
  bool _busy = false;
  bool _confirming = false;
  bool get _canSearch => !_busy && !_confirming && _pending == null;
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
    _query.clear();
    if (mounted) {
      setState(() {
        _items = null;
        _error = null;
        _success = null;
        _pending = null;
        _busy = false;
        _confirming = false;
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
    if (!_canSearch) return;
    final value = _query.text.trim();
    if (value.isEmpty ||
        (_kind == 'order_id' && (int.tryParse(value) ?? 0) <= 0)) {
      setState(() => _error = _kind == 'order_id'
          ? context.tr('請輸入有效訂單編號。', 'Enter a valid order number.')
          : context.tr('請輸入查詢資料。', 'Enter a search value.'));
      return;
    }
    FocusScope.of(context).unfocus();
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
    if (!_canSearch) return;
    final session = _session;
    final id = item['wallet_item_id'];
    if (id is! int) return;
    _SupplementDetails? confirmed;
    setState(() => _confirming = true);
    try {
      confirmed = await showDialog<_SupplementDetails>(
        context: context,
        builder: (_) => _SupplementConfirmation(item: item),
      );
    } finally {
      if (mounted && session == _session) setState(() => _confirming = false);
    }
    if (confirmed == null || !mounted || session != _session || !_canSearch) {
      return;
    }
    _pending =
        _SupplementRequest(id, '${item['coupon_name'] ?? ''}', confirmed);
    await _sendPending();
  }

  Future<void> _sendPending() async {
    final request = _pending;
    if (request == null || _busy) return;
    final session = _session;
    setState(() {
      _busy = true;
      _error = null;
      _success = null;
    });
    try {
      final response = await widget.api.post(
        '/merchant/redemptions/supplementary-scan',
        body: request.body,
      );
      if (!mounted || session != _session) return;
      final redemptionId = response['redemption_id'];
      if (redemptionId is! int || redemptionId < 1) {
        throw const FormatException('Missing redemption receipt');
      }
      setState(() {
        _pending = null;
        _items?.removeWhere((row) => row['wallet_item_id'] == request.itemId);
        _success = context.tr(
            '已補記核銷，記錄編號 $redemptionId。', 'Redemption $redemptionId recorded.');
      });
    } catch (error) {
      if (!mounted || session != _session) return;
      setState(() {
        if (error is ApiException &&
            const {400, 403, 404, 409, 422}.contains(error.status)) {
          _pending = null;
          _error = error.message;
        } else {
          _error = context.tr(
            '結果未確認。請保持此頁並使用原請求重試。',
            'The result is unconfirmed. Keep this page open and retry the original request.',
          );
        }
      });
    } finally {
      if (mounted && session == _session) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final queryLabel = switch (_kind) {
      'order_id' => context.tr('訂單編號', 'Order number'),
      'code_suffix' => context.tr('出示碼尾數', 'Last digits of the code'),
      _ => context.tr('顧客電話', 'Customer phone'),
    };
    return Scaffold(
      backgroundColor: redemptionPageColor(context),
      appBar: AppBar(title: Text(context.tr('補記核銷', 'Record a redemption'))),
      body: RedemptionWorkbench(
        entry: RedemptionPanel(
          icon: Icons.manage_search,
          title: context.tr('查找顧客的券', 'Find a customer coupon'),
          description: context.tr(
            '使用顧客提供的資料查詢，再確認要補記的券。',
            'Use the details supplied by the customer to find and confirm the right coupon.',
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              DropdownButtonFormField<String>(
                initialValue: _kind,
                isExpanded: true,
                decoration:
                    InputDecoration(labelText: context.tr('查詢方式', 'Search by')),
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
                onChanged: !_canSearch
                    ? null
                    : (value) {
                        if (value == null || value == _kind) return;
                        _query.clear();
                        setState(() {
                          _kind = value;
                          _items = null;
                          _error = null;
                          _success = null;
                        });
                      },
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _query,
                enabled: _canSearch,
                keyboardType: _kind == 'phone'
                    ? TextInputType.phone
                    : TextInputType.number,
                textInputAction: TextInputAction.search,
                onChanged: (_) {
                  if (_items != null || _error != null || _success != null) {
                    setState(() {
                      _items = null;
                      _error = null;
                      _success = null;
                    });
                  }
                },
                onSubmitted: (_) => _search(),
                decoration: InputDecoration(labelText: queryLabel),
              ),
              const SizedBox(height: 12),
              MerchantPrimaryAction(
                onPressed: _canSearch ? _search : null,
                icon: Icons.search,
                label: _busy && _pending == null
                    ? context.tr('查詢中…', 'Searching…')
                    : context.tr('查詢', 'Search'),
              ),
            ],
          ),
        ),
        result: RedemptionPanel(
          icon: Icons.receipt_long_outlined,
          title: context.tr('查詢結果', 'Search results'),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (_busy) ...[
                const LinearProgressIndicator(),
                const SizedBox(height: 12),
              ],
              if (_error != null) ...[
                RedemptionNotice(
                  message: _error!,
                  tone: _pending == null
                      ? RedemptionTone.error
                      : RedemptionTone.attention,
                ),
                const SizedBox(height: 12),
              ],
              if (_success != null)
                RedemptionNotice(
                    message: _success!, tone: RedemptionTone.success),
              if (_pending case final request?) ...[
                Text(request.couponName, style: theme.textTheme.titleSmall),
                const SizedBox(height: 8),
                MerchantPrimaryAction(
                  onPressed: _busy ? null : _sendPending,
                  icon: Icons.refresh,
                  label: _busy
                      ? context.tr('正在確認核銷…', 'Confirming redemption…')
                      : context.tr('使用原請求重試', 'Retry original request'),
                ),
              ] else if (_items == null && !_busy && _error == null)
                RedemptionNotice(
                    message: context.tr(
                  '符合條件的未使用券會顯示在這裡。',
                  'Unused coupons that match your search appear here.',
                ))
              else if (_items?.isEmpty == true && _success == null)
                RedemptionNotice(
                    message: context.tr(
                  '沒有符合條件的可用券。',
                  'No eligible coupons found.',
                )),
              if (_pending == null)
                for (final item in _items ?? <Map<String, dynamic>>[]) ...[
                  if (_success != null || item != _items!.first)
                    const Divider(height: 20),
                  _resultRow(context, item),
                ],
            ],
          ),
        ),
      ),
    );
  }

  Widget _resultRow(BuildContext context, Map<String, dynamic> item) {
    final dates = item['valid_to']?.toString().split('T').first;
    final phone = item['customer_phone_last4']?.toString();
    final order = item['order_id'];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('${item['coupon_name'] ?? ''}',
            style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 4),
        Text('${item['shop_name'] ?? ''}'),
        if (phone != null && phone.isNotEmpty)
          Text(context.tr('電話尾號 $phone', 'Phone ending $phone')),
        if (order is int) Text(context.tr('訂單 #$order', 'Order #$order')),
        if (dates != null && dates.isNotEmpty)
          Text(context.tr('有效至 $dates', 'Valid until $dates')),
        if (item['branch_required'] == true) ...[
          const SizedBox(height: 6),
          MerchantStatusPill(
            label: context.tr('補記時須選分店', 'Branch required'),
            active: false,
          ),
        ],
        const SizedBox(height: 8),
        OutlinedButton.icon(
          onPressed: _canSearch ? () => _confirm(item) : null,
          icon: const Icon(Icons.fact_check_outlined, size: 19),
          label: Text(context.tr('補記這張券', 'Record this redemption')),
        ),
      ],
    );
  }
}

class _SupplementConfirmation extends StatefulWidget {
  const _SupplementConfirmation({required this.item});

  final Map<String, dynamic> item;

  @override
  State<_SupplementConfirmation> createState() =>
      _SupplementConfirmationState();
}

class _SupplementConfirmationState extends State<_SupplementConfirmation> {
  final _reason = TextEditingController();
  int? _selectedBranch;
  String? _branchError;

  @override
  void dispose() {
    _reason.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final item = widget.item;
    final requiredBranch = item['branch_required'] == true;
    final rawBranches = item['branches'];
    final branches = (rawBranches is List ? rawBranches : const [])
        .whereType<Map>()
        .where((branch) => branch['id'] is int)
        .toList();
    return AlertDialog(
      title: Text(context.tr('確認補記核銷', 'Confirm redemption record')),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Text(
              '${item['coupon_name'] ?? ''}',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            Text('${item['shop_name'] ?? ''}'),
            const SizedBox(height: 10),
            RedemptionNotice(
                message: context.tr(
              '請與顧客核對券資料。確認後會記錄核銷及相關結算。',
              'Check the coupon with the customer. Confirmation records the redemption and its settlement.',
            )),
            if (requiredBranch) ...[
              const SizedBox(height: 16),
              DropdownButtonFormField<int>(
                initialValue: _selectedBranch,
                isExpanded: true,
                decoration: InputDecoration(
                  labelText:
                      context.tr('核銷分店（必選）', 'Redemption branch (required)'),
                  errorText: _branchError,
                ),
                items: [
                  for (final branch in branches)
                    DropdownMenuItem<int>(
                      value: branch['id'] as int,
                      child: Text(
                        '${branch['name'] ?? ''}',
                        overflow: TextOverflow.ellipsis,
                      ),
                    ),
                ],
                onChanged: branches.isEmpty
                    ? null
                    : (value) => setState(() {
                          _selectedBranch = value;
                          _branchError = null;
                        }),
              ),
              if (branches.isEmpty)
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: RedemptionNotice(
                    message: context.tr('此券暫無可用分店，請聯絡平台核對。',
                        'No branch can redeem this coupon. Contact support to check.'),
                    tone: RedemptionTone.attention,
                  ),
                ),
            ],
            const SizedBox(height: 12),
            TextField(
              controller: _reason,
              maxLength: 500,
              decoration: InputDecoration(
                labelText: context.tr('補記原因（選填）', 'Reason (optional)'),
              ),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: Text(context.tr('取消', 'Cancel')),
        ),
        FilledButton(
          onPressed: requiredBranch && branches.isEmpty
              ? null
              : () {
                  if (requiredBranch && _selectedBranch == null) {
                    setState(() => _branchError = context.tr(
                          '請選擇此券可使用的分店。',
                          'Choose a branch that accepts this coupon.',
                        ));
                    return;
                  }
                  Navigator.pop<_SupplementDetails>(
                    context,
                    (branchId: _selectedBranch, reason: _reason.text.trim()),
                  );
                },
          child: Text(context.tr('確認補記', 'Record redemption')),
        ),
      ],
    );
  }
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
  bool _mustVerify = false;
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
        _mustVerify = false;
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
    if (_busy) return;
    final id = int.tryParse(_id.text.trim());
    if (id == null || id <= 0) {
      setState(() => _error =
          context.tr('請輸入有效核銷編號。', 'Enter a valid redemption number.'));
      return;
    }
    final session = _session;
    FocusScope.of(context).unfocus();
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
      if (mounted && session == _session) {
        setState(() {
          _record = record;
          _mustVerify = false;
        });
      }
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
    if (_busy || _mustVerify || id is! int || record?['revoked'] == true) {
      return;
    }
    if (reason.isEmpty) {
      setState(() =>
          _error = context.tr('請填寫撤銷原因。', 'Enter a reason for reversal.'));
      return;
    }
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(context.tr('確認撤銷核銷', 'Confirm reversal')),
        content: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(context.tr('核銷 #$id · ${record?['coupon_name'] ?? ''}',
                  'Redemption #$id · ${record?['coupon_name'] ?? ''}')),
              const SizedBox(height: 10),
              RedemptionNotice(
                tone: RedemptionTone.attention,
                message: context.tr(
                  '撤銷將影響顧客券、積分、返現及商戶結算。',
                  'Reversal affects the customer coupon, points, cashback and merchant settlement.',
                ),
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
      final receipt = await widget.api.post(
        '/admin/redemptions/$id/force-revoke',
        body: {'reason': reason},
      );
      if (!mounted || session != _session) return;
      if (receipt['redemption_id'] != id) {
        throw const FormatException('Missing reversal receipt');
      }
      setState(() {
        _record = {...?record, 'revoked': true};
        _success = context.tr('已撤銷核銷 #$id。', 'Redemption #$id reversed.');
      });
    } catch (error) {
      if (mounted && session == _session) {
        setState(() {
          _mustVerify = true;
          _error = context.tr(
            '${_errorMessage(context, error)} 請重新查詢核銷狀態。',
            '${_errorMessage(context, error)} Look up the redemption status again.',
          );
        });
      }
    } finally {
      if (mounted && session == _session) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final record = _record;
    final theme = Theme.of(context);
    final reversed = record?['revoked'] == true;
    return Scaffold(
      backgroundColor: redemptionPageColor(context),
      appBar: AppBar(title: Text(context.tr('客服撤銷核銷', 'Reverse a redemption'))),
      body: RedemptionWorkbench(
        entry: RedemptionPanel(
          icon: Icons.manage_search,
          title: context.tr('查找核銷記錄', 'Find a redemption'),
          description: context.tr(
            '請先核對券、店舖及核銷時間，再填寫撤銷原因。',
            'Check the coupon, shop and redemption time, then enter the reason for reversal.',
          ),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              TextField(
                controller: _id,
                enabled: !_busy,
                keyboardType: TextInputType.number,
                textInputAction: TextInputAction.search,
                onChanged: (_) {
                  _reason.clear();
                  setState(() {
                    _record = null;
                    _error = null;
                    _success = null;
                  });
                },
                onSubmitted: (_) => _lookup(),
                decoration: InputDecoration(
                    labelText: context.tr('核銷編號', 'Redemption number')),
              ),
              const SizedBox(height: 12),
              MerchantPrimaryAction(
                onPressed: _busy ? null : _lookup,
                icon: Icons.search,
                label: _busy && record == null
                    ? context.tr('查詢中…', 'Searching…')
                    : context.tr('查詢', 'Search'),
              ),
            ],
          ),
        ),
        result: RedemptionPanel(
          icon: Icons.receipt_long_outlined,
          title: context.tr('核銷記錄', 'Redemption details'),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (_busy) ...[
                const LinearProgressIndicator(),
                const SizedBox(height: 12),
              ],
              if (_error != null) ...[
                RedemptionNotice(
                    message: _error!,
                    tone: _mustVerify
                        ? RedemptionTone.attention
                        : RedemptionTone.error),
                const SizedBox(height: 12),
              ],
              if (_success != null) ...[
                RedemptionNotice(
                    message: _success!, tone: RedemptionTone.success),
                const SizedBox(height: 12),
              ],
              if (record == null && !_busy && _error == null)
                RedemptionNotice(
                  message: context.tr(
                    '查詢後可核對這筆記錄，再決定是否撤銷。',
                    'Look up the record to verify its details before deciding whether to reverse it.',
                  ),
                ),
              if (record != null) ...[
                Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Text('${record['coupon_name'] ?? ''}',
                          style: theme.textTheme.titleMedium),
                    ),
                    const SizedBox(width: 8),
                    MerchantStatusPill(
                      active: !reversed,
                      label: reversed
                          ? context.tr('已撤銷', 'Reversed')
                          : context.tr('已核銷', 'Redeemed'),
                    ),
                  ],
                ),
                const SizedBox(height: 8),
                Text(context.tr('店舖：${record['shop_name'] ?? ''}',
                    'Shop: ${record['shop_name'] ?? ''}')),
                Text(_timeLabel(context, record['scanned_at'])),
                if (!reversed && !_mustVerify) ...[
                  const Divider(height: 28),
                  TextField(
                    controller: _reason,
                    enabled: !_busy,
                    maxLength: 500,
                    maxLines: 2,
                    decoration: InputDecoration(
                      labelText: context.tr(
                          '撤銷原因（必填）', 'Reason for reversal (required)'),
                    ),
                  ),
                  Align(
                    alignment: Alignment.centerLeft,
                    child: FilledButton.tonalIcon(
                      onPressed: _busy ? null : _submit,
                      icon: const Icon(Icons.undo_outlined),
                      label: Text(context.tr('繼續撤銷', 'Continue to reversal')),
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

  String _timeLabel(BuildContext context, Object? raw) {
    final parsed = DateTime.tryParse('${raw ?? ''}');
    if (parsed == null) {
      return context.tr('核銷時間：未提供', 'Redeemed at: not provided');
    }
    final utc = parsed.isUtc
        ? parsed
        : DateTime.utc(
            parsed.year,
            parsed.month,
            parsed.day,
            parsed.hour,
            parsed.minute,
          );
    final hk = utc.add(const Duration(hours: 8));
    String pad(int n) => n.toString().padLeft(2, '0');
    final formatted =
        '${hk.year}-${pad(hk.month)}-${pad(hk.day)} ${pad(hk.hour)}:${pad(hk.minute)}';
    return context.tr(
        '核銷時間：$formatted（香港時間）', 'Redeemed at: $formatted (Hong Kong time)');
  }
}
