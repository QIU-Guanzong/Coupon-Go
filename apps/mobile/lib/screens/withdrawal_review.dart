import 'package:flutter/material.dart';
import '../api_client.dart';
import '../models.dart';

class WithdrawalReviewScreen extends StatefulWidget {
  const WithdrawalReviewScreen({super.key, required this.api});
  final ApiClient api;
  @override
  State<WithdrawalReviewScreen> createState() => _WithdrawalReviewState();
}

class _WithdrawalReviewState extends State<WithdrawalReviewScreen> {
  static const stages = {
    'pending': '待審核',
    'approved': '待付款',
    'failed': '失敗',
    'paid': '已付款'
  };
  String stage = 'pending';
  List<dynamic> rows = [];
  int? next;
  bool busy = false;
  String? error;

  @override
  void initState() {
    super.initState();
    load();
  }

  Future<void> load({bool more = false}) async {
    setState(() {
      busy = true;
      error = null;
    });
    try {
      final result = await widget.api.get('/admin/withdrawals', query: {
        'status': stage,
        if (more && next != null) 'before': '$next',
      });
      if (!mounted) return;
      setState(() {
        rows = [if (more) ...rows, ...result['withdrawals']];
        next = result['next'];
      });
    } catch (e) {
      if (mounted) {
        setState(() => error = e is ApiException ? e.message : '載入失敗');
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> review(int id, String target) async {
    final title =
        {'approved': '批准申請', 'paid': '登記已付款', 'failed': '解除提現預留'}[target]!;
    String detail = '';
    final accepted = await showDialog<bool>(
        context: context,
        builder: (ctx) => StatefulBuilder(
            builder: (ctx, update) => AlertDialog(
                  title: Text('$title · #$id'),
                  content: target == 'approved'
                      ? const Text('核對收款資料後，將此單移至待付款。')
                      : TextField(
                          autofocus: true,
                          maxLength: 200,
                          decoration: InputDecoration(
                              labelText: target == 'paid'
                                  ? '已完成轉賬的付款參考編號'
                                  : '失敗原因（顧客可見）'),
                          onChanged: (v) => update(() => detail = v.trim())),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(ctx, false),
                        child: const Text('取消')),
                    FilledButton(
                        onPressed: target == 'approved' || detail.isNotEmpty
                            ? () => Navigator.pop(ctx, true)
                            : null,
                        child: Text(title)),
                  ],
                )));
    if (accepted != true || !mounted) return;
    setState(() {
      busy = true;
      error = null;
    });
    try {
      await widget.api.post('/admin/withdrawals/$id',
          body: {'status': target, 'detail': detail});
      if (mounted) await load();
    } catch (e) {
      if (mounted) {
        setState(() {
          busy = false;
          error = e is ApiException ? e.message : '提交失敗，請重新整理';
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('提現審核'), actions: [
          IconButton(
              onPressed: busy ? null : load,
              tooltip: '重新整理',
              icon: const Icon(Icons.refresh)),
        ]),
        body: Center(
            child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 720),
                child: ListView(padding: const EdgeInsets.all(16), children: [
                  Wrap(
                      spacing: 8,
                      children: stages.entries
                          .map((e) => ChoiceChip(
                              label: Text(e.value),
                              selected: stage == e.key,
                              onSelected: busy
                                  ? null
                                  : (_) {
                                      setState(() {
                                        stage = e.key;
                                        rows = [];
                                        next = null;
                                      });
                                      load();
                                    }))
                          .toList()),
                  if (busy) const LinearProgressIndicator(),
                  if (error != null)
                    Text(error!,
                        style: TextStyle(
                            color: Theme.of(context).colorScheme.error)),
                  if (!busy && error == null && rows.isEmpty)
                    Padding(
                        padding: const EdgeInsets.symmetric(vertical: 24),
                        child: Text('暫無${stages[stage]}提現')),
                  for (final row in rows)
                    Builder(builder: (context) {
                      final w = CashbackWithdrawal.fromJson(row);
                      return Padding(
                          padding: const EdgeInsets.symmetric(vertical: 12),
                          child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                    '#${w.id}　HK\$${w.amount.toStringAsFixed(2)}',
                                    style: Theme.of(context)
                                        .textTheme
                                        .titleMedium),
                                SelectableText(
                                    '${w.accountName} · ${w.methodText} · ${row['account_ref']}'),
                                if (w.failureReason != null)
                                  Text(w.failureReason!),
                                for (final event in w.events
                                    .where((e) => e['status'] == 'paid'))
                                  Text('付款參考編號 ${event['detail']}'),
                                Wrap(spacing: 8, children: [
                                  if (stage == 'pending')
                                    FilledButton(
                                        onPressed: busy
                                            ? null
                                            : () => review(w.id, 'approved'),
                                        child: const Text('批准申請')),
                                  if (stage == 'approved')
                                    FilledButton(
                                        onPressed: busy
                                            ? null
                                            : () => review(w.id, 'paid'),
                                        child: const Text('登記已付款')),
                                  if (stage == 'pending' ||
                                      stage == 'approved' ||
                                      (stage == 'failed' && !w.holdReleased))
                                    OutlinedButton(
                                        onPressed: busy
                                            ? null
                                            : () => review(w.id, 'failed'),
                                        child: Text(stage == 'failed'
                                            ? '核對舊單退款'
                                            : '記錄失敗')),
                                ]),
                                const Divider(),
                              ]));
                    }),
                  if (next != null)
                    TextButton(
                        onPressed: busy ? null : () => load(more: true),
                        child: const Text('更多申請')),
                ]))),
      );
}
