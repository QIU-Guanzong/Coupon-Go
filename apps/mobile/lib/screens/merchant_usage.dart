import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../language.dart';
import '../theme.dart';
import '../merchant_usage_data.dart';

/// Uses only the count fields in the existing per-shop settlement dashboard.
class MerchantUsageScreen extends StatefulWidget {
  const MerchantUsageScreen({
    super.key,
    required this.api,
    this.legacyUsageBuilder,
  });

  final ApiClient api;
  final WidgetBuilder? legacyUsageBuilder;

  @override
  State<MerchantUsageScreen> createState() => _MerchantUsageScreenState();
}

enum _Failure { denied, unavailable }

class _MerchantUsageScreenState extends State<MerchantUsageScreen> {
  int _days = 30;
  int _request = 0;
  late int _session;
  MerchantUsageData? _data;
  _Failure? _failure;
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    _load();
  }

  @override
  void didUpdateWidget(covariant MerchantUsageScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.api != widget.api) {
      oldWidget.api.removeListener(_sessionChanged);
      _session = widget.api.sessionRevision;
      widget.api.addListener(_sessionChanged);
      _load();
    }
  }

  void _sessionChanged() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    _load();
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    super.dispose();
  }

  Future<void> _load({int? days}) async {
    final api = widget.api;
    final session = _session;
    final window = days ?? _days;
    final request = ++_request;
    bool isCurrent() =>
        mounted &&
        api == widget.api &&
        session == api.sessionRevision &&
        request == _request;
    setState(() {
      _days = window;
      _data = null;
      _failure = null;
      _loading = true;
    });
    try {
      final json = await api.merchantDashboard(window);
      if (!isCurrent()) return;
      final result = MerchantUsageData.parse(json, window);
      setState(() {
        _data = result;
        _loading = false;
      });
    } catch (error) {
      if (!isCurrent()) return;
      setState(() {
        _failure = error is ApiException &&
                (error.status == 401 || error.status == 403)
            ? _Failure.denied
            : _Failure.unavailable;
        _loading = false;
      });
    }
  }

  Future<void> _showCsv(MerchantUsageData data) async {
    final session = _session;
    final api = widget.api;
    final fields = [
      context.tr('門店編號', 'Shop ID'),
      context.tr('門店名稱', 'Shop name'),
      context.tr('開始日期 (UTC)', 'Start date (UTC)'),
      context.tr('結束日期 (UTC)', 'End date (UTC)'),
      context.tr('領券次數', 'Coupon claims'),
      context.tr('可對賬核銷', 'Reconciled redemptions'),
    ];
    final csv = data.csv(fields);
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => ListenableBuilder(
        listenable: api,
        builder: (context, _) {
          final current = session == api.sessionRevision;
          return AlertDialog(
            title: Text(context.tr('門店數據 CSV', 'Shop usage CSV')),
            content: current
                ? SingleChildScrollView(child: SelectableText(csv))
                : Text(context.tr('帳戶已切換。', 'The account changed.')),
            actions: [
              TextButton(
                onPressed: () => Navigator.pop(dialogContext),
                child: Text(context.tr('關閉', 'Close')),
              ),
              if (current)
                TextButton(
                  onPressed: () async {
                    if (session != api.sessionRevision) return;
                    try {
                      await Clipboard.setData(ClipboardData(text: csv));
                    } catch (_) {
                      if (!context.mounted || session != api.sessionRevision) {
                        return;
                      }
                      ScaffoldMessenger.of(context).showSnackBar(
                        SnackBar(
                          content: Text(
                            context.tr('未能複製 CSV。', 'Could not copy CSV.'),
                          ),
                        ),
                      );
                      return;
                    }
                    if (!dialogContext.mounted ||
                        !mounted ||
                        session != api.sessionRevision) {
                      return;
                    }
                    Navigator.pop(dialogContext);
                    ScaffoldMessenger.of(this.context).showSnackBar(
                      SnackBar(
                        content: Text(
                          this.context.tr('已複製 CSV。', 'CSV copied.'),
                        ),
                      ),
                    );
                  },
                  child: Text(context.tr('複製 CSV', 'Copy CSV')),
                ),
            ],
          );
        },
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final data = _data;
    final legacyBuilder = widget.legacyUsageBuilder;
    final claimsLabel = context.tr('領券次數', 'Coupon claims');
    final redemptionsLabel = context.tr('可對賬核銷', 'Reconciled redemptions');
    return Scaffold(
      appBar: AppBar(
        title: Text(context.tr('使用數據', 'Usage data')),
        actions: [
          if (data != null && data.shops.isNotEmpty)
            IconButton(
              onPressed: () => _showCsv(data),
              icon: const Icon(Icons.download_outlined),
              tooltip: context.tr('匯出門店 CSV', 'Export shop CSV'),
            ),
        ],
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: CgTheme.listMaxWidth),
          child: RefreshIndicator(
            onRefresh: _load,
            child: ListView(
              physics: const AlwaysScrollableScrollPhysics(),
              padding: const EdgeInsets.all(CgTheme.pageInset),
              children: [
                Wrap(
                  spacing: CgTheme.space8,
                  runSpacing: CgTheme.space4,
                  children: [
                    for (final days in const [7, 30, 90])
                      ChoiceChip(
                        key: ValueKey('usage-days-$days'),
                        label: Text(context.tr('$days 日', '$days days')),
                        selected: days == _days,
                        onSelected: (selected) {
                          if (selected && days != _days) _load(days: days);
                        },
                      ),
                  ],
                ),
                const SizedBox(height: CgTheme.space16),
                if (_loading) const LinearProgressIndicator(),
                if (_failure != null)
                  Semantics(
                    liveRegion: true,
                    child: Text(
                      _failure == _Failure.denied
                          ? context.tr(
                              '沒有權限查看此門店數據。',
                              'You don’t have access to this shop data.',
                            )
                          : context.tr(
                              '未能讀取這段日期的數據。',
                              'Could not load data for this period.',
                            ),
                    ),
                  ),
                if (_failure != null)
                  Align(
                    alignment: AlignmentDirectional.centerStart,
                    child: TextButton(
                      onPressed: _load,
                      child: Text(context.tr('重試', 'Try again')),
                    ),
                  ),
                if (data != null && data.shops.isEmpty)
                  Text(context.tr('沒有可查看數據的門店。', 'No shops to show.')),
                if (data != null && data.shops.isNotEmpty) ...[
                  Text('${data.from} – ${data.to} (UTC)'),
                  const SizedBox(height: CgTheme.space8),
                  _UsageCard(
                    title: context.tr('門店合計', 'Shop totals'),
                    counts: data.totals,
                    claimsLabel: claimsLabel,
                    redemptionsLabel: redemptionsLabel,
                  ),
                  for (final shop in data.shops)
                    _UsageCard(
                      key: ValueKey('usage-shop-${shop.id}'),
                      title: shop.name,
                      counts: shop.counts,
                      claimsLabel: claimsLabel,
                      redemptionsLabel: redemptionsLabel,
                    ),
                ],
                if (legacyBuilder != null) ...[
                  const SizedBox(height: CgTheme.space16),
                  TextButton(
                    onPressed: () => Navigator.of(
                      context,
                    ).push(MaterialPageRoute<void>(builder: legacyBuilder)),
                    child: Text(
                      context.tr('全部時間的優惠券記錄', 'All-time coupon activity'),
                    ),
                  ),
                ],
              ],
            ),
          ),
        ),
      ),
    );
  }
}

class _UsageCard extends StatelessWidget {
  const _UsageCard({
    super.key,
    required this.title,
    required this.counts,
    required this.claimsLabel,
    required this.redemptionsLabel,
  });

  final String title;
  final UsageCounts counts;
  final String claimsLabel;
  final String redemptionsLabel;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: CgTheme.space8),
      child: Padding(
        padding: const EdgeInsets.all(CgTheme.space16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: CgTheme.space4),
            Text('$claimsLabel: ${counts.claims}'),
            Text('$redemptionsLabel: ${counts.redemptions}'),
          ],
        ),
      ),
    );
  }
}
