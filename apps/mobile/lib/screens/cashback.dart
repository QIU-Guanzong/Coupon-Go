import 'package:flutter/material.dart';

import '../api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';

/// Line D — cashback in Q Wallet: balance (next to points, separate ledger),
/// transaction list, withdrawal, Missing Cashback appeal.
///
/// Defaults (see README 返现一节): dine-in rate 5%, next-visit unlock
/// within 90 days (else void), online confirm 30 days, HK$50 minimum
/// withdrawal, FPS / bank. NOT verified: no `flutter analyze` / real-device
/// build has been run for this file (no Flutter SDK on this machine).
class CashbackScreen extends StatefulWidget {
  const CashbackScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<CashbackScreen> createState() => _CashbackScreenState();
}

class _CashbackScreenState extends State<CashbackScreen> {
  double _withdrawable = 0;
  double _pending = 0;
  List<CashbackTransaction> _txns = [];
  List<CashbackWithdrawal> _withdrawals = [];
  String _filter = 'all';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await widget.api.cashback();
      final txns = await widget.api.cashbackTransactions();
      final wds = await widget.api.cashbackWithdrawals();
      if (!mounted) return;
      setState(() {
        _withdrawable =
            (data['withdrawable_balance'] as num?)?.toDouble() ?? 0;
        _pending = (data['pending_balance'] as num?)?.toDouble() ?? 0;
        _txns = txns.map((e) => CashbackTransaction.fromJson(e)).toList();
        _withdrawals =
            wds.map((e) => CashbackWithdrawal.fromJson(e)).toList();
      });
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  List<CashbackTransaction> get _shown => _filter == 'all'
      ? _txns
      : _txns.where((t) => t.status == _filter).toList();

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('返現')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(16),
              decoration: BoxDecoration(
                color: CgColors.primary,
                borderRadius: BorderRadius.circular(12),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('可提現返現',
                      style: TextStyle(color: Colors.white70)),
                  Text('HK\$${_withdrawable.toStringAsFixed(2)}',
                      style: const TextStyle(
                          color: Colors.white,
                          fontSize: 32,
                          fontWeight: FontWeight.bold)),
                  const SizedBox(height: 4),
                  Text('待確認 HK\$${_pending.toStringAsFixed(2)}',
                      style:
                          const TextStyle(color: Colors.white70, fontSize: 12)),
                  const SizedBox(height: 4),
                  const Text('返現與積分分開記賬，不可互換。',
                      style: TextStyle(color: Colors.white70, fontSize: 12)),
                ],
              ),
            ),
            const SizedBox(height: 12),
            Row(
              children: [
                Expanded(
                  child: ElevatedButton(
                    onPressed: _withdrawable < 50
                        ? null
                        : () async {
                            await Navigator.of(context).push(MaterialPageRoute(
                                builder: (_) => WithdrawScreen(
                                    api: widget.api,
                                    balance: _withdrawable)));
                            _load();
                          },
                    child: const Text('提現'),
                  ),
                ),
                const SizedBox(width: 12),
                Expanded(
                  child: OutlinedButton(
                    onPressed: () async {
                      await Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) =>
                              MissingCashbackScreen(api: widget.api)));
                      _load();
                    },
                    child: const Text('申訴返現'),
                  ),
                ),
              ],
            ),
            if (_withdrawable < 50)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text('提現門檻 HK\$50（轉數快 / 銀行轉賬）。',
                    style: TextStyle(
                        color: CgColors.textSecondary, fontSize: 12)),
              ),
            const SizedBox(height: 16),
            SingleChildScrollView(
              scrollDirection: Axis.horizontal,
              child: Row(
                children: [
                  for (final f in const [
                    ('all', '全部'),
                    ('pending', '待確認'),
                    ('withdrawable', '可提現'),
                    ('void', '已失效'),
                  ])
                    Padding(
                      padding: const EdgeInsets.only(right: 8),
                      child: ChoiceChip(
                        label: Text(f.$2),
                        selected: _filter == f.$1,
                        onSelected: (_) => setState(() => _filter = f.$1),
                      ),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 8),
            if (_shown.isEmpty)
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 24),
                child: Center(child: Text('暫時沒有返現記錄。')),
              ),
            ..._shown.map((t) => Card(
                  margin: const EdgeInsets.only(top: 8),
                  child: ListTile(
                    leading: const Icon(Icons.payments,
                        color: CgColors.primary),
                    title: Text(
                        '${t.shopName} · HK\$${t.amount.toStringAsFixed(2)}'),
                    subtitle: Text(
                        '${t.channelText} · ${(t.rate * 100).toStringAsFixed(1)}% · ${t.statusText}'
                        '${t.status == 'pending' && t.channel == 'dinein' && t.expiresAt != null ? '\n90 天內再消費一次解鎖，否則過期作廢' : ''}'
                        '${t.status == 'pending' && t.channel == 'online' && t.expectedConfirmAt != null ? '\n預計確認：${t.expectedConfirmAt!.toLocal().toString().substring(0, 10)}' : ''}'),
                    isThreeLine: t.status == 'pending',
                  ),
                )),
            const SizedBox(height: 16),
            const Text('提現記錄', style: TextStyle(fontWeight: FontWeight.bold)),
            if (_withdrawals.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text('暫時沒有提現記錄。'),
              ),
            ..._withdrawals.map((w) => Card(
                  margin: const EdgeInsets.only(top: 8),
                  child: ListTile(
                    leading: const Icon(Icons.account_balance,
                        color: CgColors.primary),
                    title: Text(
                        'HK\$${w.amount.toStringAsFixed(2)} · ${w.methodText}'),
                    subtitle: Text('${w.accountName} · ${w.statusText}'),
                  ),
                )),
            const SizedBox(height: 16),
            const Text('演示：記錄一筆消費（開發版）',
                style: TextStyle(fontWeight: FontWeight.bold)),
            const Text(
                '正式版由商戶 POS / 核銷記錄入賬。此處僅供開發演示：線下餐飲固定 5%，90 天內在同一商戶再消費一次才解鎖上一次返現。',
                style:
                    TextStyle(color: CgColors.textSecondary, fontSize: 12)),
            const SizedBox(height: 8),
            _DemoRecord(api: widget.api, onDone: _load),
          ],
        ),
      ),
    );
  }
}

class _DemoRecord extends StatefulWidget {
  const _DemoRecord({required this.api, required this.onDone});

  final ApiClient api;
  final VoidCallback onDone;

  @override
  State<_DemoRecord> createState() => _DemoRecordState();
}

class _DemoRecordState extends State<_DemoRecord> {
  final _amount = TextEditingController(text: '200');
  int? _shopId;
  List<Shop> _shops = [];
  String _channel = 'dinein';

  @override
  void initState() {
    super.initState();
    widget.api.home().then((data) {
      if (!mounted) return;
      final list = (data['popular_merchants'] as List? ?? [])
          .map((e) => Shop.fromJson(e))
          .toList();
      setState(() {
        _shops = list;
        if (list.isNotEmpty) _shopId = list.first.id;
      });
    }).catchError((_) {});
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          children: [
            DropdownButton<int>(
              value: _shopId,
              isExpanded: true,
              hint: const Text('選擇商戶'),
              items: _shops
                  .map((s) => DropdownMenuItem(value: s.id, child: Text(s.name)))
                  .toList(),
              onChanged: (v) => setState(() => _shopId = v),
            ),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _amount,
                    keyboardType: TextInputType.number,
                    decoration: const InputDecoration(labelText: '消費金額 HK\$'),
                  ),
                ),
                const SizedBox(width: 12),
                DropdownButton<String>(
                  value: _channel,
                  items: const [
                    DropdownMenuItem(value: 'dinein', child: Text('線下餐飲')),
                    DropdownMenuItem(value: 'online', child: Text('線上')),
                  ],
                  onChanged: (v) => setState(() => _channel = v ?? 'dinein'),
                ),
              ],
            ),
            const SizedBox(height: 12),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: _shopId == null
                    ? null
                    : () async {
                        try {
                          final amount =
                              double.tryParse(_amount.text) ?? 0;
                          final res = await widget.api.recordSpend(
                              _shopId!, amount, _channel);
                          if (context.mounted) {
                            showCgToast(context,
                                '已記錄返現 HK\$${(res['transaction']['amount'] as num).toStringAsFixed(2)}（待確認）');
                          }
                          widget.onDone();
                        } on ApiException catch (e) {
                          if (context.mounted) showCgToast(context, e.message);
                        }
                      },
                child: const Text('記錄消費'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Withdrawal page: FPS / bank, HK$50 minimum, account name must match
/// the registered name (ShopBack HK rule).
class WithdrawScreen extends StatefulWidget {
  const WithdrawScreen({super.key, required this.api, required this.balance});

  final ApiClient api;
  final double balance;

  @override
  State<WithdrawScreen> createState() => _WithdrawScreenState();
}

class _WithdrawScreenState extends State<WithdrawScreen> {
  final _amount = TextEditingController();
  final _name = TextEditingController();
  final _ref = TextEditingController();
  String _method = 'fps';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('提現返現')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('可提現餘額 HK\$${widget.balance.toStringAsFixed(2)}',
              style:
                  const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          const Text('門檻 HK\$50；每日上限 HK\$1,500；到賬通常 10 天內（v1 為人工打款，提交後狀態為「待打款」）。',
              style: TextStyle(color: CgColors.textSecondary, fontSize: 12)),
          const SizedBox(height: 16),
          TextField(
            controller: _amount,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: '提現金額 HK\$'),
          ),
          const SizedBox(height: 12),
          DropdownButton<String>(
            value: _method,
            isExpanded: true,
            items: const [
              DropdownMenuItem(value: 'fps', child: Text('轉數快 (FPS)')),
              DropdownMenuItem(value: 'bank', child: Text('銀行轉賬')),
            ],
            onChanged: (v) => setState(() => _method = v ?? 'fps'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _name,
            decoration: const InputDecoration(
                labelText: '收款賬戶姓名（須與註冊姓名一致）'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _ref,
            decoration: InputDecoration(
                labelText: _method == 'fps' ? 'FPS ID / 手機號' : '銀行賬號'),
          ),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: () async {
                try {
                  await widget.api.withdrawCashback(
                      double.tryParse(_amount.text) ?? 0,
                      _method,
                      _name.text,
                      _ref.text);
                  if (context.mounted) {
                    showCgToast(context, '提現申請已提交，待打款');
                    Navigator.of(context).pop();
                  }
                } on ApiException catch (e) {
                  if (context.mounted) showCgToast(context, e.message);
                }
              },
              child: const Text('提交提現申請'),
            ),
          ),
        ],
      ),
    );
  }
}

/// Missing Cashback appeal: file within 30 days of the spend, with the
/// order / redemption receipt. Investigation window up to 120 days.
class MissingCashbackScreen extends StatefulWidget {
  const MissingCashbackScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<MissingCashbackScreen> createState() => _MissingCashbackScreenState();
}

class _MissingCashbackScreenState extends State<MissingCashbackScreen> {
  final _orderNo = TextEditingController();
  final _spentAt = TextEditingController();
  final _amount = TextEditingController();
  final _evidence = TextEditingController();
  final _note = TextEditingController();
  int? _shopId;
  List<Shop> _shops = [];

  @override
  void initState() {
    super.initState();
    _spentAt.text =
        DateTime.now().toLocal().toString().substring(0, 10);
    widget.api.home().then((data) {
      if (!mounted) return;
      final list = (data['popular_merchants'] as List? ?? [])
          .map((e) => Shop.fromJson(e))
          .toList();
      setState(() {
        _shops = list;
        if (list.isNotEmpty) _shopId = list.first.id;
      });
    }).catchError((_) {});
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('申訴返現')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text('消費後 30 天內可申訴；調查期最長 120 天。請提供訂單號 / 核銷憑證。',
              style: TextStyle(color: CgColors.textSecondary, fontSize: 12)),
          const SizedBox(height: 16),
          DropdownButton<int>(
            value: _shopId,
            isExpanded: true,
            hint: const Text('選擇商戶'),
            items: _shops
                .map((s) => DropdownMenuItem(value: s.id, child: Text(s.name)))
                .toList(),
            onChanged: (v) => setState(() => _shopId = v),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _orderNo,
            decoration: const InputDecoration(labelText: '訂單號 / 核銷憑證號'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _spentAt,
            decoration:
                const InputDecoration(labelText: '消費日期（YYYY-MM-DD）'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _amount,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: '消費金額 HK\$'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _evidence,
            decoration: const InputDecoration(
                labelText: '訂單確認截圖連結 / 描述'),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _note,
            decoration: const InputDecoration(labelText: '補充說明（可選）'),
          ),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: _shopId == null
                  ? null
                  : () async {
                      try {
                        final spent = DateTime.tryParse(_spentAt.text);
                        if (spent == null) {
                          showCgToast(context, '消費日期格式不正確');
                          return;
                        }
                        await widget.api.submitMissingCashback(
                            _shopId!,
                            _orderNo.text,
                            spent.toIso8601String(),
                            double.tryParse(_amount.text) ?? 0,
                            _evidence.text,
                            _note.text);
                        if (context.mounted) {
                          showCgToast(context, '申訴已提交，調查期最長 120 天');
                          Navigator.of(context).pop();
                        }
                      } on ApiException catch (e) {
                        if (context.mounted) showCgToast(context, e.message);
                      }
                    },
              child: const Text('提交申訴'),
            ),
          ),
        ],
      ),
    );
  }
}
