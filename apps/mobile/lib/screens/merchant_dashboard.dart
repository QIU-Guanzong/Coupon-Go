import 'package:flutter/material.dart';

import '../api_client.dart';
import '../feature_flags.dart';
import '../language.dart';
import '../theme.dart';
import '../widgets/merchant_layout.dart';
import '../widgets/merchant_trend.dart';
import 'merchant_payout.dart';

const _paper = Color(0xFFFAF7F1);
const _ink = Color(0xFF26221B);
const _rule = Color(0xFFE5DED0);
const _yellowPaper = Color(0xFFFFF1CE);

String _money(Object? value) =>
    value is num ? 'HK\$${value.toStringAsFixed(2)}' : '—';
String _count(Object? value) => value is num ? '${value.toInt()}' : '—';
String _rate(Object? value, Object? claims) =>
    value is num && claims is num && claims > 0
        ? '${(value * 100).toStringAsFixed(1)}%'
        : '—';
Map _map(Object? value) => value is Map ? value : const {};
Iterable<Map> _rows(Object? value) =>
    value is List ? value.whereType<Map>() : const [];
String _period(Map period) {
  final from = period['from'];
  final to = period['to'];
  return from is String && to is String ? '$from – $to (UTC)' : '—';
}

String? _unresolved(BuildContext context, Map balance) {
  final transfers = balance['unknown_transfer_count'];
  final reversals = balance['unknown_reversal_count'];
  if (transfers is! num || reversals is! num) {
    return context.tr(
        '待核對流水狀態暫不可用，請先核對。', 'Review the ledger to confirm pending entries.');
  }
  if (transfers == 0 && reversals == 0) return null;
  final amount = balance['unknown_transfer_amount'];
  final amountText = amount is num && transfers > 0
      ? context.tr('（涉及 ${_money(amount)}）', ' (${_money(amount)})')
      : '';
  return context.tr(
    '有 ${_count(transfers)} 筆轉帳$amountText及 ${_count(reversals)} 筆沖回待核對；待確認金額不代表已到帳。',
    '${_count(transfers)} transfers$amountText and ${_count(reversals)} reversals are under review. These amounts have not been confirmed as received.',
  );
}

String _mode(BuildContext context, Object? mode) => switch (mode) {
      'simulated' ||
      'stripe_test' =>
        context.tr('未開通正式收款', 'Live payments are not enabled'),
      'unavailable' => context.tr('未設定收款帳戶', 'Payments account not set up'),
      _ => context.tr('收款狀態待確認', 'Payments status unconfirmed'),
    };

String _errorText(BuildContext context, Object error) => error is ApiException
    ? error.message
    : context.tr('暫時無法讀取資料，請重試', 'Could not load the data. Try again.');

/// Overview of the accounts and shops this merchant may view.
class MerchantDashboardScreen extends StatefulWidget {
  const MerchantDashboardScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<MerchantDashboardScreen> createState() =>
      _MerchantDashboardScreenState();
}

class _MerchantDashboardScreenState extends State<MerchantDashboardScreen> {
  Map? _balance, _dashboard;
  String? _balanceError, _dashboardError;
  int _days = 30;
  int _metricsRequest = 0, _balanceRequest = 0;
  late int _session;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_onSession);
    _refresh();
  }

  @override
  void didUpdateWidget(covariant MerchantDashboardScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.api == widget.api) return;
    oldWidget.api.removeListener(_onSession);
    widget.api.addListener(_onSession);
    _session = widget.api.sessionRevision;
    _refresh();
  }

  @override
  void dispose() {
    widget.api.removeListener(_onSession);
    super.dispose();
  }

  void _onSession() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    _refresh();
  }

  Future<void> _refresh() async {
    await Future.wait([_loadMetrics(), _loadBalance()]);
  }

  bool _metricsLoading = false;

  Future<void> _loadMetrics({int? days}) async {
    final request = ++_metricsRequest;
    setState(() {
      if (days != null && days != _days) _dashboard = null;
      _days = days ?? _days;
      _dashboardError = null;
      _metricsLoading = true;
    });
    try {
      final report = await widget.api.merchantDashboard(_days);
      if (mounted && request == _metricsRequest) {
        setState(() => _dashboard = report);
      }
    } catch (error) {
      if (mounted && request == _metricsRequest) {
        setState(() => _dashboardError = _errorText(context, error));
      }
    } finally {
      if (mounted && request == _metricsRequest) {
        setState(() => _metricsLoading = false);
      }
    }
  }

  Future<void> _loadBalance() async {
    final request = ++_balanceRequest;
    setState(() {
      _balance = null;
      _balanceError = null;
    });
    try {
      final balance = await widget.api.merchantBalance();
      if (mounted && request == _balanceRequest) {
        setState(() => _balance = balance);
      }
    } catch (error) {
      if (mounted && request == _balanceRequest) {
        setState(() => _balanceError = _errorText(context, error));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final report = _dashboard;
    final shops =
        _rows(report?['shops']).where((s) => s['shop_id'] is int).toList();
    final balances = _rows(_balance?['shops']).toList();
    final hasNoShops = report != null && shops.isEmpty && balances.isEmpty;
    return _ReportPage(
      title: context.tr('經營與餘額', 'Business and balance'),
      onRefresh: _refresh,
      children: [
        _ReportHero(
          icon: Icons.query_stats_outlined,
          title: context.tr('經營概覽', 'Business overview'),
          subtitle:
              context.tr('所有可查看的店舖 · 港幣 HKD', 'All accessible shops · HKD'),
        ),
        const SizedBox(height: 20),
        _ReportColumns(
          lead: _ReportPanel(
            title: context.tr('經營數據', 'Operating results'),
            action: DropdownButtonHideUnderline(
              child: DropdownButton<int>(
                key: const ValueKey('merchant-report-date-range'),
                value: _days,
                borderRadius: BorderRadius.circular(12),
                onChanged: (days) {
                  if (days != null && days != _days) _loadMetrics(days: days);
                },
                items: [
                  for (final days in const [7, 30, 90])
                    DropdownMenuItem(
                      value: days,
                      child: Text(context.tr('近 $days 日', 'Last $days days')),
                    ),
                ],
              ),
            ),
            children: [
              if (report == null)
                _ReportState(error: _dashboardError, retry: _loadMetrics)
              else if (shops.isEmpty)
                Text(context.tr('暫無可查看的店舖。', 'No shops to show.'))
              else ...[
                if (_metricsLoading) const LinearProgressIndicator(),
                if (_dashboardError != null)
                  _ReportState(error: _dashboardError, retry: _loadMetrics),
                _OperatingResults(report: report),
              ],
            ],
          ),
          side: _ReportPanel(
            title: context.tr('商戶餘額', 'Merchant balance'),
            children: [
              if (_balance == null)
                _ReportState(error: _balanceError, retry: _loadBalance)
              else if (hasNoShops)
                Text(context.tr(
                    '暫無可查看餘額的店舖。', 'No shops with balances to show.'))
              else
                _BalanceBreakdown(
                  balance: _balance!,
                  onReview: () =>
                      Navigator.of(context).pushNamed('/vendor/settlements'),
                  actionLabel: context.tr('查看結算單', 'View statements'),
                ),
            ],
          ),
        ),
        if (shops.isNotEmpty) ...[
          const SizedBox(height: 16),
          _ReportPanel(
            title: context.tr('各店表現', 'Shop performance'),
            children: [
              for (final shop in shops)
                _ShopLink(
                  name: '${shop['shop_name'] ?? context.tr('店舖', 'Shop')}',
                  totals: _map(shop['totals']),
                  onTap: () =>
                      Navigator.of(context).push(MaterialPageRoute<void>(
                    builder: (_) => MerchantShopDashboardScreen(
                      api: widget.api,
                      shopId: shop['shop_id'] as int,
                      shopName:
                          '${shop['shop_name'] ?? context.tr('店舖', 'Shop')}',
                      days: _days,
                    ),
                  )),
                ),
            ],
          ),
        ],
      ],
    );
  }
}

class MerchantShopDashboardScreen extends StatefulWidget {
  const MerchantShopDashboardScreen({
    super.key,
    required this.api,
    required this.shopId,
    required this.shopName,
    required this.days,
  });
  final ApiClient api;
  final int shopId;
  final String shopName;
  final int days;

  @override
  State<MerchantShopDashboardScreen> createState() =>
      _MerchantShopDashboardScreenState();
}

class _MerchantShopDashboardScreenState
    extends State<MerchantShopDashboardScreen> {
  final _ledgerKey = GlobalKey();
  final _ledgerFocus = FocusNode(skipTraversal: true);
  Map? _dashboard, _entries, _balance;
  String? _dashboardError, _entriesError, _balanceError;
  int _page = 1, _overviewRequest = 0, _ledgerRequest = 0;
  late int _session;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_onSession);
    _refresh();
  }

  @override
  void dispose() {
    widget.api.removeListener(_onSession);
    _ledgerFocus.dispose();
    super.dispose();
  }

  void _onSession() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    _loadEntries(page: 1);
    _loadOverview();
  }

  Future<void> _refresh() async {
    await Future.wait([_loadOverview(), _loadEntries()]);
  }

  Future<void> _loadOverview() async {
    final request = ++_overviewRequest;
    setState(() {
      _dashboard = _balance = null;
      _dashboardError = _balanceError = null;
    });
    Future<void> load(
      Future<Map<String, dynamic>> Function() requestData,
      void Function(Map) applyData,
      void Function(String) applyError,
    ) async {
      try {
        final data = await requestData();
        if (mounted && request == _overviewRequest) {
          setState(() => applyData(data));
        }
      } catch (error) {
        if (mounted && request == _overviewRequest) {
          setState(() => applyError(_errorText(context, error)));
        }
      }
    }

    await Future.wait([
      load(() => widget.api.merchantShopDashboard(widget.shopId, widget.days),
          (data) => _dashboard = data, (error) => _dashboardError = error),
      load(() => widget.api.merchantShopBalance(widget.shopId),
          (data) => _balance = data, (error) => _balanceError = error),
    ]);
  }

  Future<void> _loadEntries({int? page}) async {
    final request = ++_ledgerRequest;
    setState(() {
      _page = page ?? _page;
      _entries = null;
      _entriesError = null;
    });
    try {
      final entries =
          await widget.api.merchantBalanceEntries(widget.shopId, _page);
      if (mounted && request == _ledgerRequest) {
        setState(() => _entries = entries);
      }
    } catch (error) {
      if (mounted && request == _ledgerRequest) {
        setState(() => _entriesError = _errorText(context, error));
      }
    }
  }

  void _openWeekly() {
    Navigator.of(context).push(MaterialPageRoute<void>(
      builder: (_) => _WeeklyReportScreen(
          api: widget.api, shopId: widget.shopId, shopName: widget.shopName),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final dashboard = _dashboard;
    final entries = _rows(_entries?['entries']).toList();
    final coupons = _rows(dashboard?['coupons']).toList();
    final canViewShop = dashboard != null || _balance != null;
    return _ReportPage(
      title: context.tr('店舖報告', 'Shop report'),
      onRefresh: _refresh,
      children: [
        _ReportHero(
          icon: Icons.storefront_outlined,
          title: widget.shopName,
          subtitle: context.tr(
              '單店經營與對帳 · 港幣 HKD', 'Shop reporting and reconciliation · HKD'),
          actions: [
            if (dashboard != null)
              FilledButton.tonalIcon(
                onPressed: _openWeekly,
                icon: const Icon(Icons.date_range_outlined),
                label: Text(context.tr('經營週報', 'Weekly report')),
              ),
            if (canViewShop && !publicTrial)
              FilledButton.tonalIcon(
                onPressed: () =>
                    Navigator.of(context).push(MaterialPageRoute<void>(
                  builder: (_) => MerchantPayoutScreen(
                      api: widget.api,
                      shopId: widget.shopId,
                      shopName: widget.shopName),
                )),
                icon: const Icon(Icons.account_balance_wallet_outlined),
                label: Text(context.tr('提款紀錄', 'Withdrawal history')),
              ),
          ],
        ),
        const SizedBox(height: 20),
        _ReportColumns(
          lead: _ReportPanel(
            title: context.tr('近 ${widget.days} 日', 'Last ${widget.days} days'),
            children: [
              if (dashboard == null)
                _ReportState(error: _dashboardError, retry: _loadOverview)
              else ...[
                _OperatingResults(report: dashboard),
              ],
            ],
          ),
          side: _ReportPanel(
            title: context.tr('店舖餘額', 'Shop balance'),
            children: [
              if (_balance == null)
                _ReportState(error: _balanceError, retry: _loadOverview)
              else
                _BalanceBreakdown(
                  balance: _balance!,
                  onReview: () {
                    final anchor = _ledgerKey.currentContext;
                    if (anchor != null) {
                      _ledgerFocus.requestFocus();
                      Scrollable.ensureVisible(anchor, alignment: .04);
                    }
                  },
                  actionLabel: context.tr('查看轉帳流水', 'View transfer history'),
                ),
            ],
          ),
        ),
        if (dashboard != null) ...[
          const SizedBox(height: 16),
          _ReportPanel(
            title: context.tr('優惠券表現', 'Coupon performance'),
            children: [
              if (coupons.isEmpty)
                Text(context.tr('此期間沒有優惠券資料。', 'No coupons for this period.')),
              for (final coupon in coupons)
                _ReportLine(
                  title:
                      '${coupon['coupon_name'] ?? context.tr('優惠券', 'Coupon')}',
                  detail: context.tr(
                    '領取 ${_count(coupon['claims'])} · 核銷 ${_count(coupon['redemptions'])}',
                    '${_count(coupon['claims'])} claims · ${_count(coupon['redemptions'])} redemptions',
                  ),
                  value: _money(coupon['gross_amount']),
                ),
            ],
          ),
        ],
        const SizedBox(height: 16),
        Focus(
            focusNode: _ledgerFocus,
            child: _ReportPanel(
              key: _ledgerKey,
              title: context.tr('轉帳流水', 'Transfer history'),
              children: [
                Text(context.tr('所有日期的流水 · 金額為扣佣後金額',
                    'All ledger dates · Amounts are after commission')),
                const SizedBox(height: 12),
                if (_entries == null)
                  _ReportState(error: _entriesError, retry: _loadEntries)
                else ...[
                  if (entries.isEmpty)
                    Text(context.tr('暫無流水。', 'No ledger entries.')),
                  for (final entry in entries)
                    _ReportLine(
                      title:
                          '${entry['coupon_name'] ?? context.tr('優惠券', 'Coupon')}',
                      detail: entry['scanned_at'] is String
                          ? '${entry['scanned_at']}'.split('T').first
                          : null,
                      value: _money(entry['net_amount']),
                      status: MerchantStatusPill(
                        label: context.tr(
                            '狀態：${_entryState(context, entry['state'])}',
                            'Status: ${_entryState(context, entry['state'])}'),
                        active: entry['state'] == 'transferred',
                      ),
                    ),
                  if ((_entries?['total_pages'] as num? ?? 1) > 1 || _page > 1)
                    Wrap(
                      crossAxisAlignment: WrapCrossAlignment.center,
                      spacing: 12,
                      children: [
                        TextButton(
                            onPressed: _page > 1
                                ? () => _loadEntries(page: _page - 1)
                                : null,
                            child: Text(context.tr('上一頁', 'Previous'))),
                        Text(context.tr('第 $_page 頁', 'Page $_page')),
                        TextButton(
                          onPressed:
                              _page < (_entries?['total_pages'] as num? ?? 1)
                                  ? () => _loadEntries(page: _page + 1)
                                  : null,
                          child: Text(context.tr('下一頁', 'Next')),
                        ),
                      ],
                    ),
                ],
              ],
            )),
      ],
    );
  }
}

class _WeeklyReportScreen extends StatefulWidget {
  const _WeeklyReportScreen(
      {required this.api, required this.shopId, required this.shopName});
  final ApiClient api;
  final int shopId;
  final String shopName;

  @override
  State<_WeeklyReportScreen> createState() => _WeeklyReportScreenState();
}

class _WeeklyReportScreenState extends State<_WeeklyReportScreen> {
  Map? _report;
  String? _error;
  int _request = 0;
  late int _session;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_onSession);
    _load();
  }

  @override
  void dispose() {
    widget.api.removeListener(_onSession);
    super.dispose();
  }

  void _onSession() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    _load();
  }

  Future<void> _load() async {
    final request = ++_request;
    setState(() {
      _report = null;
      _error = null;
    });
    try {
      final report = await widget.api
          .get('/merchant/shops/${widget.shopId}/weekly-report');
      if (report['shop_id'] != widget.shopId ||
          report['currency'] != 'HKD' ||
          report['timezone'] != 'UTC' ||
          _period(_map(_map(report['current'])['period'])) == '—' ||
          _period(_map(_map(report['previous'])['period'])) == '—') {
        throw const FormatException('Invalid weekly report');
      }
      if (mounted && request == _request) setState(() => _report = report);
    } catch (error) {
      if (mounted && request == _request) {
        setState(() => _error = _errorText(context, error));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final report = _report;
    final current = _map(report?['current']);
    final previous = _map(report?['previous']);
    final currentTotals = _map(current['totals']);
    final previousTotals = _map(previous['totals']);
    final changes = _map(report?['changes']);
    String delta(String key, bool money) {
      final value = _map(changes[key])['delta'];
      if (value is! num) return '—';
      return '${value > 0 ? '+' : ''}${money ? _money(value) : _count(value)}';
    }

    final shift = changes['conversion_percentage_points'];
    return _ReportPage(
      title: context.tr('經營週報', 'Weekly report'),
      onRefresh: _load,
      children: [
        _ReportHero(
          icon: Icons.date_range_outlined,
          title: widget.shopName,
          subtitle: context.tr('最近兩個完整星期 · 週一至週日 (UTC)',
              'Latest two completed weeks · Monday to Sunday (UTC)'),
        ),
        const SizedBox(height: 20),
        _ReportPanel(
          title: context.tr('週度比較', 'Week comparison'),
          children: [
            if (report == null)
              _ReportState(error: _error, retry: _load)
            else ...[
              Text(
                  '${context.tr('最近一週', 'Latest week')}  ${_period(_map(current['period']))}'),
              Text(
                  '${context.tr('再前一週', 'Previous week')}  ${_period(_map(previous['period']))}'),
              const SizedBox(height: 12),
              for (final (key, label, money) in [
                ('claims', context.tr('領券', 'Claims'), false),
                ('redemptions', context.tr('核銷', 'Redemptions'), false),
                ('gross_amount', context.tr('已核銷金額', 'Redeemed amount'), true),
                ('commission', context.tr('佣金', 'Commission'), true),
                ('net_amount', context.tr('扣佣後金額', 'After commission'), true),
              ])
                _WeekLine(
                  label: label,
                  current: money
                      ? _money(currentTotals[key])
                      : _count(currentTotals[key]),
                  previous: money
                      ? _money(previousTotals[key])
                      : _count(previousTotals[key]),
                  delta: delta(key, money),
                ),
              _WeekLine(
                label: context.tr('本期領券核銷率', 'Claim-to-redemption rate'),
                current: _rate(
                    currentTotals['conversion_rate'], currentTotals['claims']),
                previous: _rate(previousTotals['conversion_rate'],
                    previousTotals['claims']),
                delta: shift is num
                    ? context.tr(
                        '${shift > 0 ? '+' : ''}${shift.toStringAsFixed(1)} 個百分點',
                        '${shift > 0 ? '+' : ''}${shift.toStringAsFixed(1)} pp')
                    : '—',
              ),
              const SizedBox(height: 10),
              Text(context.tr('撤銷及已退款的核銷不計入。到帳情況可查閱轉帳流水。',
                  'Reversed and refunded redemptions are excluded. See the transfer ledger for payment status.')),
            ],
          ],
        ),
      ],
    );
  }
}

class _ReportPage extends StatelessWidget {
  const _ReportPage(
      {required this.title, required this.onRefresh, required this.children});
  final String title;
  final Future<void> Function() onRefresh;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) => Scaffold(
        backgroundColor: _paper,
        appBar: AppBar(
          title: Text(title),
          actions: [
            IconButton(
                onPressed: onRefresh,
                icon: const Icon(Icons.refresh),
                tooltip: context.tr('更新資料', 'Refresh data')),
          ],
        ),
        body: RefreshIndicator(
          onRefresh: onRefresh,
          child: SingleChildScrollView(
            physics: const AlwaysScrollableScrollPhysics(),
            padding: const EdgeInsets.all(16),
            child: Center(
                child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 1200),
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: children),
            )),
          ),
        ),
      );
}

class _ReportHero extends StatelessWidget {
  const _ReportHero(
      {required this.icon,
      required this.title,
      required this.subtitle,
      this.actions = const []});
  final IconData icon;
  final String title, subtitle;
  final List<Widget> actions;

  @override
  Widget build(BuildContext context) => DecoratedBox(
        decoration:
            BoxDecoration(color: _ink, borderRadius: BorderRadius.circular(16)),
        child: Padding(
          padding: const EdgeInsets.all(20),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Padding(
                padding: const EdgeInsets.only(top: 5),
                child: Icon(icon, color: CgColors.vendorGold),
              ),
              const SizedBox(width: 12),
              Expanded(
                  child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Semantics(
                    header: true,
                    child: Text(title,
                        style: Theme.of(context)
                            .textTheme
                            .headlineSmall
                            ?.copyWith(
                                color: Colors.white,
                                fontWeight: FontWeight.w800)),
                  ),
                  const SizedBox(height: 4),
                  Text(subtitle,
                      style: TextStyle(
                          color: Colors.white.withValues(alpha: .84))),
                ],
              )),
            ]),
            if (actions.isNotEmpty) ...[
              const SizedBox(height: 12),
              Wrap(spacing: 8, runSpacing: 8, children: actions),
            ],
          ]),
        ),
      );
}

class _ReportColumns extends StatelessWidget {
  const _ReportColumns({required this.lead, required this.side});
  final Widget lead, side;

  @override
  Widget build(BuildContext context) =>
      LayoutBuilder(builder: (context, constraints) {
        if (constraints.maxWidth < 760) {
          return Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [lead, const SizedBox(height: 16), side]);
        }
        return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(flex: 3, child: lead),
          const SizedBox(width: 16),
          Expanded(flex: 2, child: side),
        ]);
      });
}

class _ReportPanel extends StatelessWidget {
  const _ReportPanel(
      {super.key, required this.title, this.action, required this.children});
  final String title;
  final Widget? action;
  final List<Widget> children;

  @override
  Widget build(BuildContext context) {
    final heading = Semantics(
        header: true,
        child: Text(title,
            style: Theme.of(context)
                .textTheme
                .titleLarge
                ?.copyWith(fontWeight: FontWeight.w700)));
    return Container(
      padding: const EdgeInsets.all(16),
      decoration: BoxDecoration(
          color: Colors.white,
          border: Border.all(color: _rule),
          borderRadius: BorderRadius.circular(14)),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        if (action == null)
          heading
        else
          Wrap(
              alignment: WrapAlignment.spaceBetween,
              crossAxisAlignment: WrapCrossAlignment.center,
              spacing: 12,
              children: [heading, action!]),
        const SizedBox(height: 12),
        ...children,
      ]),
    );
  }
}

class _ReportState extends StatelessWidget {
  const _ReportState({required this.error, required this.retry});
  final String? error;
  final VoidCallback retry;

  @override
  Widget build(BuildContext context) => error == null
      ? LinearProgressIndicator(
          semanticsLabel: context.tr('正在載入資料', 'Loading data'))
      : Semantics(
          liveRegion: true,
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(error!),
            TextButton(
                onPressed: retry, child: Text(context.tr('重試', 'Retry'))),
          ]));
}

class _OperatingResults extends StatelessWidget {
  const _OperatingResults({required this.report});
  final Map report;
  @override
  Widget build(BuildContext context) {
    final totals = _map(report['totals']);
    final trend = _rows(report['trend']).toList();
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Text(_period(_map(report['period'])),
          style: Theme.of(context).textTheme.labelMedium),
      const SizedBox(height: 12),
      DecoratedBox(
        decoration: BoxDecoration(
          color: _yellowPaper,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(context.tr('扣佣後金額', 'After commission')),
            const SizedBox(height: 4),
            Text(_money(totals['net_amount']),
                style: Theme.of(context)
                    .textTheme
                    .headlineSmall
                    ?.copyWith(fontWeight: FontWeight.w800, color: _ink)),
          ]),
        ),
      ),
      const SizedBox(height: 12),
      _ReportNumbers(values: [
        (context.tr('領券', 'Claims'), _count(totals['claims'])),
        (context.tr('核銷數', 'Redemptions'), _count(totals['redemptions'])),
        (
          context.tr('本期領券核銷率', 'Claim-to-redemption rate'),
          _rate(totals['conversion_rate'], totals['claims'])
        ),
      ]),
      if (trend.isNotEmpty) MerchantTrend(days: trend),
      const Divider(height: 28),
      MerchantAmountRow(
          label: context.tr('已核銷金額', 'Redeemed amount'),
          value: _money(totals['gross_amount'])),
      MerchantAmountRow(
          label: context.tr('佣金', 'Commission'),
          value: _money(totals['commission'])),
      MerchantAmountRow(
          label: context.tr('已核銷訂單平均金額', 'Average redeemed order'),
          value: (totals['redeemed_orders'] as num? ?? 0) > 0
              ? _money(totals['avg_order_value'])
              : '—'),
    ]);
  }
}

class _BalanceBreakdown extends StatelessWidget {
  const _BalanceBreakdown(
      {required this.balance,
      required this.onReview,
      required this.actionLabel});
  final Map balance;
  final VoidCallback onReview;
  final String actionLabel;

  @override
  Widget build(BuildContext context) {
    final mode = _mode(context, balance['connect_mode']);
    final unresolved = _unresolved(context, balance);
    final complete = balance['balance_complete'];
    final coverage = complete == true
        ? null
        : complete == false
            ? context.tr(
                '有 ${_count(balance['legacy_untracked_redemptions'])} 筆歷史核銷待核對，金額僅包含已接入流水。',
                '${_count(balance['legacy_untracked_redemptions'])} past redemptions require review. The amount covers linked ledger entries only.')
            : context.tr('餘額完整性待確認，請先核對流水。',
                'Check the ledger to confirm the complete balance.');
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      DecoratedBox(
        decoration: BoxDecoration(
            color: _yellowPaper, borderRadius: BorderRadius.circular(10)),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child:
              Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text(complete == true
                ? context.tr('可提取', 'Available')
                : context.tr('已接入流水可提取', 'Available in the ledger')),
            const SizedBox(height: 4),
            MerchantAmountValue(
                value: _money(balance['withdrawable']), emphasized: true),
            const SizedBox(height: 6),
            Text(mode),
          ]),
        ),
      ),
      const SizedBox(height: 10),
      MerchantAmountRow(
          label: context.tr('待轉帳', 'Pending transfer'),
          value: _money(balance['pending_settlement'])),
      MerchantAmountRow(
          label: context.tr('提款處理中', 'Payout in progress'),
          value: _money(balance['reserved'])),
      MerchantAmountRow(
          label: context.tr('已提款', 'Paid out'),
          value: _money(balance['withdrawn'])),
      if (coverage != null || unresolved != null) ...[
        const Divider(height: 24),
        Row(children: [
          const Icon(Icons.info_outline, size: 18, color: CgColors.vendorLink),
          const SizedBox(width: 8),
          Expanded(
              child: Text(context.tr('待核對', 'Needs reconciliation'),
                  style: Theme.of(context).textTheme.titleSmall)),
        ]),
        const SizedBox(height: 6),
        if (coverage != null) Text(coverage),
        if (unresolved != null) Text(unresolved),
      ],
      Align(
          alignment: AlignmentDirectional.centerStart,
          child: TextButton.icon(
              onPressed: onReview,
              icon: const Icon(Icons.receipt_long_outlined),
              label: Text(actionLabel))),
    ]);
  }
}

class _ReportNumbers extends StatelessWidget {
  const _ReportNumbers({required this.values});
  final List<(String, String)> values;

  @override
  Widget build(BuildContext context) =>
      LayoutBuilder(builder: (context, constraints) {
        final columns = constraints.maxWidth >= 300
            ? values.length
            : (constraints.maxWidth >= 240 ? 2 : 1);
        return Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final (label, value) in values)
              SizedBox(
                width: (constraints.maxWidth - (columns - 1) * 8) / columns,
                child: DecoratedBox(
                  decoration: BoxDecoration(
                      color: _paper, borderRadius: BorderRadius.circular(8)),
                  child: Padding(
                    padding: const EdgeInsets.all(10),
                    child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          MerchantAmountValue(value: value),
                          const SizedBox(height: 4),
                          Text(label,
                              style: Theme.of(context).textTheme.bodySmall),
                        ]),
                  ),
                ),
              ),
          ],
        );
      });
}

class _WeekLine extends StatelessWidget {
  const _WeekLine(
      {required this.label,
      required this.current,
      required this.previous,
      required this.delta});
  final String label, current, previous, delta;

  @override
  Widget build(BuildContext context) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Divider(height: 24),
          Text(label, style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 6),
          _ReportNumbers(values: [
            (context.tr('最近一週', 'Latest week'), current),
            (context.tr('再前一週', 'Previous week'), previous),
            (context.tr('變化', 'Change'), delta),
          ]),
        ],
      );
}

class _ShopLink extends StatelessWidget {
  const _ShopLink(
      {required this.name, required this.totals, required this.onTap});
  final String name;
  final Map totals;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) => ListTile(
        contentPadding: EdgeInsets.zero,
        leading: const Icon(Icons.storefront_outlined),
        title: Text(name),
        subtitle: Text(context.tr(
            '核銷 ${_count(totals['redemptions'])} · 扣佣後 ${_money(totals['net_amount'])}',
            '${_count(totals['redemptions'])} redemptions · ${_money(totals['net_amount'])} after commission')),
        trailing: const Icon(Icons.chevron_right),
        onTap: onTap,
      );
}

class _ReportLine extends StatelessWidget {
  const _ReportLine(
      {required this.title, required this.value, this.detail, this.status});
  final String title, value;
  final String? detail;
  final Widget? status;

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 8),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Expanded(
              child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                Text(title, style: Theme.of(context).textTheme.titleSmall),
                if (detail != null) Text(detail!),
                if (status != null) ...[const SizedBox(height: 4), status!],
              ])),
          const SizedBox(width: 12),
          Flexible(child: MerchantAmountValue(value: value)),
        ]),
      );
}

String _entryState(BuildContext context, Object? value) => switch (value) {
      'pending_settlement' ||
      'pending' =>
        context.tr('待轉帳', 'Pending transfer'),
      'transferred' => context.tr('已轉帳', 'Transferred'),
      'transfer_unknown' ||
      'transferring' =>
        context.tr('轉帳待核對', 'Transfer under review'),
      'reversal_pending' ||
      'reversal_unknown' =>
        context.tr('沖回待核對', 'Reversal under review'),
      'reversed' => context.tr('已沖回', 'Reversed'),
      'unavailable' => context.tr('缺少流水，待核對', 'Ledger entry under review'),
      _ => context.tr('狀態待確認', 'Status pending confirmation'),
    };
