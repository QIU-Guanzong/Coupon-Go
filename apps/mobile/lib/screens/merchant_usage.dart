import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../language.dart';
import '../theme.dart';
import '../merchant_usage_data.dart';
import 'merchant_dashboard.dart';

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

  void _openShop(ShopUsage shop) => Navigator.of(context).push(
        MaterialPageRoute<void>(
          builder: (_) => MerchantShopDashboardScreen(
            api: widget.api,
            shopId: shop.id,
            shopName: shop.name,
            days: _days,
          ),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final data = _data;
    final legacyBuilder = widget.legacyUsageBuilder;
    final claimsLabel = context.tr('領券次數', 'Coupon claims');
    final redemptionsLabel = context.tr('可對賬核銷', 'Reconciled redemptions');
    return Scaffold(
      backgroundColor: const Color(0xFFFAF7F1),
      appBar: AppBar(
        title: Text(context.tr('使用數據', 'Usage data')),
        actions: [
          IconButton(
            onPressed: _loading ? null : _load,
            icon: const Icon(Icons.refresh),
            tooltip: context.tr('更新資料', 'Refresh data'),
          ),
          if (data != null && data.shops.isNotEmpty)
            IconButton(
              onPressed: () => _showCsv(data),
              icon: const Icon(Icons.download_outlined),
              tooltip: context.tr('匯出門店 CSV', 'Export shop CSV'),
            ),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: SingleChildScrollView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(16),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 1200),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: [
                  DecoratedBox(
                    decoration: BoxDecoration(
                      color: const Color(0xFF26221B),
                      borderRadius: BorderRadius.circular(16),
                    ),
                    child: Padding(
                      padding: const EdgeInsets.all(20),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Icon(Icons.bar_chart_outlined,
                              color: CgColors.vendorGold),
                          const SizedBox(height: 8),
                          Semantics(
                            header: true,
                            child: Text(
                              context.tr('門店優惠券成效', 'Coupon activity by shop'),
                              style: Theme.of(context)
                                  .textTheme
                                  .headlineMedium
                                  ?.copyWith(
                                    color: Colors.white,
                                    fontWeight: FontWeight.w700,
                                  ),
                            ),
                          ),
                          const SizedBox(height: 4),
                          Text(
                            context.tr('領券及可對帳核銷 · UTC 日期',
                                'Claims and reconciled redemptions · UTC dates'),
                            style: TextStyle(
                                color: Colors.white.withValues(alpha: .84)),
                          ),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 16),
                  _UsageSection(
                    children: [
                      Wrap(
                        alignment: WrapAlignment.spaceBetween,
                        crossAxisAlignment: WrapCrossAlignment.center,
                        spacing: 12,
                        runSpacing: 8,
                        children: [
                          Text(context.tr('日期範圍', 'Date range'),
                              style: Theme.of(context).textTheme.titleSmall),
                          Wrap(
                            spacing: CgTheme.space8,
                            runSpacing: CgTheme.space4,
                            children: [
                              for (final days in const [7, 30, 90])
                                ChoiceChip(
                                  key: ValueKey('usage-days-$days'),
                                  label:
                                      Text(context.tr('$days 日', '$days days')),
                                  selected: days == _days,
                                  onSelected: (selected) {
                                    if (selected && days != _days) {
                                      _load(days: days);
                                    }
                                  },
                                ),
                            ],
                          ),
                        ],
                      ),
                      if (_loading) ...[
                        const SizedBox(height: 10),
                        LinearProgressIndicator(
                            semanticsLabel:
                                context.tr('正在載入資料', 'Loading data')),
                      ],
                      if (_failure != null) ...[
                        const SizedBox(height: 10),
                        Semantics(
                          liveRegion: true,
                          child: Text(
                            _failure == _Failure.denied
                                ? context.tr('沒有權限查看此門店數據。',
                                    'You don’t have access to this shop data.')
                                : context.tr('未能讀取這段日期的數據。',
                                    'Could not load data for this period.'),
                          ),
                        ),
                        Align(
                          alignment: AlignmentDirectional.centerStart,
                          child: TextButton(
                            onPressed: _load,
                            child: Text(context.tr('重試', 'Try again')),
                          ),
                        ),
                      ],
                      if (data != null && data.shops.isEmpty) ...[
                        const SizedBox(height: 10),
                        Text(context.tr('沒有可查看數據的門店。', 'No shops to show.')),
                      ],
                      if (data != null && data.shops.isNotEmpty) ...[
                        const SizedBox(height: 10),
                        Text('${data.from} – ${data.to} (UTC)'),
                        const SizedBox(height: 12),
                        DecoratedBox(
                          decoration: BoxDecoration(
                            color: const Color(0xFFFFF1CE),
                            borderRadius: BorderRadius.circular(10),
                          ),
                          child: Padding(
                            padding: const EdgeInsets.all(12),
                            child: _UsageRow(
                              title: context.tr('門店合計', 'Shop totals'),
                              counts: data.totals,
                              claimsLabel: claimsLabel,
                              redemptionsLabel: redemptionsLabel,
                            ),
                          ),
                        ),
                      ],
                    ],
                  ),
                  if (data != null && data.shops.isNotEmpty) ...[
                    const SizedBox(height: 16),
                    _UsageSection(
                      children: [
                        Semantics(
                          header: true,
                          child: Text(
                            context.tr('門店明細', 'Shop breakdown'),
                            style: Theme.of(context)
                                .textTheme
                                .titleLarge
                                ?.copyWith(fontWeight: FontWeight.w700),
                          ),
                        ),
                        const SizedBox(height: 8),
                        for (var i = 0; i < data.shops.length; i++) ...[
                          if (i != 0) const Divider(height: 1),
                          _UsageRow(
                            key: ValueKey('usage-shop-${data.shops[i].id}'),
                            title: data.shops[i].name,
                            counts: data.shops[i].counts,
                            claimsLabel: claimsLabel,
                            redemptionsLabel: redemptionsLabel,
                            onOpen: () => _openShop(data.shops[i]),
                          ),
                        ],
                      ],
                    ),
                  ],
                  if (legacyBuilder != null) ...[
                    const SizedBox(height: CgTheme.space16),
                    Align(
                      alignment: AlignmentDirectional.centerStart,
                      child: TextButton.icon(
                        onPressed: () => Navigator.of(context).push(
                            MaterialPageRoute<void>(builder: legacyBuilder)),
                        icon: const Icon(Icons.history),
                        label: Text(context.tr(
                            '全部時間的優惠券記錄', 'All-time coupon activity')),
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _UsageSection extends StatelessWidget {
  const _UsageSection({required this.children});
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: Colors.white,
          border: Border.all(color: const Color(0xFFE5DED0)),
          borderRadius: BorderRadius.circular(14),
        ),
        child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch, children: children),
      );
}

class _UsageRow extends StatelessWidget {
  const _UsageRow({
    super.key,
    required this.title,
    required this.counts,
    required this.claimsLabel,
    required this.redemptionsLabel,
    this.onOpen,
  });

  final String title;
  final UsageCounts counts;
  final String claimsLabel;
  final String redemptionsLabel;
  final VoidCallback? onOpen;

  @override
  Widget build(BuildContext context) {
    final name = Text(title, style: Theme.of(context).textTheme.titleMedium);
    final claims = Text('$claimsLabel: ${counts.claims}',
        style: const TextStyle(fontFeatures: [FontFeature.tabularFigures()]));
    final redemptions = Text('$redemptionsLabel: ${counts.redemptions}',
        style: const TextStyle(fontFeatures: [FontFeature.tabularFigures()]));
    final content = LayoutBuilder(
      builder: (context, constraints) {
        if (constraints.maxWidth < 740) {
          return Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                name,
                const SizedBox(height: 4),
                Wrap(
                    spacing: 20,
                    runSpacing: 4,
                    children: [claims, redemptions]),
              ]);
        }
        return Row(children: [
          Expanded(flex: 2, child: name),
          const SizedBox(width: 12),
          Expanded(child: claims),
          const SizedBox(width: 12),
          Expanded(child: redemptions),
        ]);
      },
    );
    if (onOpen == null) return content;
    return InkWell(
      onTap: onOpen,
      borderRadius: BorderRadius.circular(8),
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 12),
        child: Row(children: [
          Expanded(child: content),
          const SizedBox(width: 12),
          const Icon(Icons.chevron_right),
        ]),
      ),
    );
  }
}
