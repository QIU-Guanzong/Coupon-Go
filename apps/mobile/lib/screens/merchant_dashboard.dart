import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../theme.dart';
import '../widgets/merchant_layout.dart';
import 'merchant_payout.dart';

String _money(Object? value) =>
    value is num ? 'HK\$${value.toStringAsFixed(2)}' : '—';
String _count(Object? value) => value is num ? '${value.toInt()}' : '—';
String _rate(Object? value, Object? claims) =>
    value is num && claims is num && claims > 0
        ? '${(value * 100).toStringAsFixed(1)}%'
        : '—';

String? _unresolved(BuildContext context, Map? balance) {
  final transfers = balance?['unknown_transfer_count'];
  final reversals = balance?['unknown_reversal_count'];
  if (transfers is! num || reversals is! num) {
    return context.tr(
        '待核對流水狀態暫不可用，請先核對。', 'Review the ledger to confirm pending entries.');
  }
  if (transfers == 0 && reversals == 0) return null;
  final amount = balance?['unknown_transfer_amount'];
  final amountText = amount is num && transfers > 0
      ? context.tr('（涉及 ${_money(amount)}）', ' (${_money(amount)})')
      : '';
  return context.tr(
    '有 ${_count(transfers)} 筆轉帳$amountText及 ${_count(reversals)} 筆沖回待核對；待確認金額不代表已到帳。',
    '${_count(transfers)} transfers$amountText and ${_count(reversals)} reversals are under review. These amounts have not been confirmed as received.',
  );
}

const _trialMode = bool.fromEnvironment('PUBLIC_TRIAL');

String _mode(BuildContext context, Object? mode) => switch (mode) {
      'simulated' ||
      'stripe_test' =>
        context.tr('未開通正式收款', 'Live payments are not enabled'),
      'unavailable' => context.tr('未設定收款帳戶', 'Payments account not set up'),
      _ => context.tr('收款狀態待確認', 'Payments status unconfirmed'),
    };

class MerchantDashboardScreen extends StatefulWidget {
  const MerchantDashboardScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<MerchantDashboardScreen> createState() =>
      _MerchantDashboardScreenState();
}

class _MerchantDashboardScreenState extends State<MerchantDashboardScreen> {
  Map<String, dynamic>? _balance;
  Map<String, dynamic>? _dashboard;
  String? _error;
  bool _loading = true;
  int _days = 30;
  int _generation = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    final generation = ++_generation;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final results = await Future.wait(
          [widget.api.merchantBalance(), widget.api.merchantDashboard(_days)]);
      if (!mounted || generation != _generation) return;
      setState(() {
        _balance = results[0];
        _dashboard = results[1];
        _loading = false;
      });
    } catch (e) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _error = e is ApiException
            ? e.message
            : context.tr('暫時無法讀取資料，請重試', 'Could not load the data. Try again.');
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final balance = _balance;
    final totals = _dashboard?['totals'] is Map
        ? Map<String, dynamic>.from(_dashboard!['totals'] as Map)
        : <String, dynamic>{};
    final shops = _dashboard?['shops'] is List
        ? _dashboard!['shops'] as List
        : <dynamic>[];
    final hasData = balance != null && _dashboard != null;
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('經營與餘額', 'Business and balance'))),
      body: Center(
          child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: CgTheme.listMaxWidth),
              child: RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                      padding: const EdgeInsets.all(CgTheme.pageInset),
                      children: [
                        Text(context.tr('商戶餘額', 'Merchant balance'),
                            style: Theme.of(context).textTheme.titleLarge),
                        const SizedBox(height: CgTheme.space8),
                        Text(context.tr('收款狀態以 Connect 流水為準；結算單供對帳。',
                            'Refer to the Connect ledger for payment status and to statements for reconciliation.')),
                        if (_loading) const LinearProgressIndicator(),
                        if (_error != null) ...[
                          const SizedBox(height: CgTheme.space12),
                          Semantics(
                              liveRegion: true,
                              child: Text(_error!,
                                  style: TextStyle(
                                      color: Theme.of(context)
                                          .colorScheme
                                          .error))),
                          Align(
                              alignment: Alignment.centerLeft,
                              child: TextButton(
                                  onPressed: _load,
                                  child: Text(context.tr('重試', 'Retry')))),
                        ],
                        if (hasData && !_loading && _error == null) ...[
                          const SizedBox(height: CgTheme.space12),
                          Text(_mode(context, balance['connect_mode'])),
                          Text(context.tr('環境標示不代表歷史流水來源或款項已到帳。',
                              'The current configuration does not determine payment status or the origin of earlier entries.')),
                          const SizedBox(height: CgTheme.space12),
                          if (balance['balance_complete'] != true) ...[
                            Text(
                                balance['balance_complete'] == false
                                    ? context.tr(
                                        '有 ${_count(balance['legacy_untracked_redemptions'])} 筆歷史核銷待核對，金額僅包含已接入流水。',
                                        '${_count(balance['legacy_untracked_redemptions'])} past redemptions require review. The amount covers linked ledger entries only.')
                                    : context.tr('餘額完整性待確認，請先核對流水。',
                                        'Check the ledger to confirm the complete balance.'),
                                style: const TextStyle(
                                    fontWeight: FontWeight.w600)),
                            const SizedBox(height: CgTheme.space12),
                          ],
                          if (_unresolved(context, balance) != null) ...[
                            Text(_unresolved(context, balance)!),
                            const SizedBox(height: CgTheme.space12),
                          ],
                          MerchantAmountRow(
                              label: balance['balance_complete'] == true
                                  ? context.tr('可提取', 'Available')
                                  : context.tr(
                                      '已接入流水可提取', 'Available in the ledger'),
                              value: _money(balance['withdrawable']),
                              emphasized: true),
                          MerchantAmountRow(
                              label: context.tr('待轉帳', 'Pending transfer'),
                              value: _money(balance['pending_settlement'])),
                          MerchantAmountRow(
                              label: context.tr('提款處理中', 'Payout in progress'),
                              value: _money(balance['reserved'])),
                          MerchantAmountRow(
                              label: context.tr('已提款', 'Paid out'),
                              value: _money(balance['withdrawn'])),
                          const SizedBox(height: CgTheme.space8),
                          Align(
                              alignment: Alignment.centerLeft,
                              child: TextButton.icon(
                                  onPressed: () => Navigator.of(context)
                                      .pushNamed('/vendor/settlements'),
                                  icon: const Icon(Icons.receipt_long_outlined),
                                  label: Text(
                                      context.tr('查看結算單', 'View statements')))),
                          const Divider(height: CgTheme.dividerHeight),
                          Text(context.tr('經營概覽', 'Business overview'),
                              style: Theme.of(context).textTheme.titleMedium),
                          const SizedBox(height: CgTheme.space12),
                          Align(
                            alignment: AlignmentDirectional.centerStart,
                            child: ConstrainedBox(
                              constraints: const BoxConstraints(maxWidth: 240),
                              child: DropdownButtonFormField<int>(
                                  initialValue: _days,
                                  isExpanded: true,
                                  decoration: InputDecoration(
                                    labelText: context.tr('日期範圍', 'Date range'),
                                  ),
                                  items: [
                                    for (final days in const [7, 30, 90])
                                      DropdownMenuItem(
                                        value: days,
                                        child: Text(
                                            context.tr(
                                                '近 $days 日', 'Last $days days'),
                                            maxLines: 1,
                                            overflow: TextOverflow.ellipsis),
                                      ),
                                  ],
                                  onChanged: (v) {
                                    if (v != null && v != _days) {
                                      setState(() {
                                        _days = v;
                                      });
                                      _load();
                                    }
                                  }),
                            ),
                          ),
                          const SizedBox(height: CgTheme.space8),
                          if (_dashboard!['period'] is Map)
                            Text(
                                '${(_dashboard!['period'] as Map)['from']} – ${(_dashboard!['period'] as Map)['to']}'),
                          const SizedBox(height: CgTheme.space12),
                          MerchantAmountRow(
                              label: context.tr('核銷數', 'Redemptions'),
                              value: _count(totals['redemptions'])),
                          MerchantAmountRow(
                              label: context.tr('已核銷金額', 'Redeemed amount'),
                              value: _money(totals['gross_amount'])),
                          MerchantAmountRow(
                              label: context.tr('佣金', 'Commission'),
                              value: _money(totals['commission'])),
                          MerchantAmountRow(
                              label: context.tr(
                                  '扣佣後金額', 'Amount after commission'),
                              value: _money(totals['net_amount'])),
                          MerchantAmountRow(
                              label: context.tr(
                                  '已核銷訂單平均金額', 'Average redeemed order'),
                              value: totals['redeemed_orders'] is num &&
                                      (totals['redeemed_orders'] as num) > 0
                                  ? _money(totals['avg_order_value'])
                                  : '—'),
                          MerchantAmountRow(
                              label: context.tr(
                                  '本期領券核銷率', 'Claim-to-redemption rate'),
                              value: _rate(
                                  totals['conversion_rate'], totals['claims'])),
                          const SizedBox(height: CgTheme.space20),
                          Text(context.tr('店舖', 'Shops'),
                              style: Theme.of(context).textTheme.titleMedium),
                          if (shops.isEmpty)
                            Padding(
                                padding: const EdgeInsets.symmetric(
                                    vertical: CgTheme.space12),
                                child: Text(context.tr(
                                    '暫無可查看的店舖。', 'No shops to show.'))),
                          for (final raw in shops)
                            if (raw is Map && raw['shop_id'] is int)
                              ListTile(
                                  contentPadding: EdgeInsets.zero,
                                  title: Text(
                                      '${raw['shop_name'] ?? context.tr('店舖', 'Shop')}'),
                                  subtitle: Text(context.tr('查看核銷、券及流水',
                                      'Redemptions, coupons and transfers')),
                                  trailing: const Icon(Icons.chevron_right),
                                  onTap: () => Navigator.of(
                                          context)
                                      .push(MaterialPageRoute(
                                          builder: (_) =>
                                              MerchantShopDashboardScreen(
                                                  api: widget.api,
                                                  shopId: raw['shop_id'] as int,
                                                  shopName:
                                                      '${raw['shop_name'] ?? context.tr('店舖', 'Shop')}',
                                                  days: _days)))),
                        ],
                      ])))),
    );
  }
}

class MerchantShopDashboardScreen extends StatefulWidget {
  const MerchantShopDashboardScreen(
      {super.key,
      required this.api,
      required this.shopId,
      required this.shopName,
      required this.days});
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
  Map<String, dynamic>? _dashboard;
  Map<String, dynamic>? _entries;
  Map<String, dynamic>? _balance;
  String? _error;
  bool _loading = true;
  int _page = 1;
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await Future.wait([
        widget.api.merchantShopDashboard(widget.shopId, widget.days),
        widget.api.merchantBalanceEntries(widget.shopId, _page),
        widget.api.merchantShopBalance(widget.shopId),
      ]);
      if (mounted) {
        setState(() {
          _dashboard = result[0];
          _entries = result[1];
          _balance = result[2];
          _loading = false;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = e is ApiException
              ? e.message
              : context.tr(
                  '暫時無法讀取資料，請重試', 'Could not load the data. Try again.');
          _loading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final totals = _dashboard?['totals'] is Map
        ? Map<String, dynamic>.from(_dashboard!['totals'] as Map)
        : <String, dynamic>{};
    final coupons = _dashboard?['coupons'] is List
        ? _dashboard!['coupons'] as List
        : <dynamic>[];
    final trend = _dashboard?['trend'] is List
        ? _dashboard!['trend'] as List
        : <dynamic>[];
    final entries = _entries?['entries'] is List
        ? _entries!['entries'] as List
        : <dynamic>[];
    return Scaffold(
        appBar: AppBar(title: Text(widget.shopName)),
        body: Center(
            child: ConstrainedBox(
                constraints:
                    const BoxConstraints(maxWidth: CgTheme.listMaxWidth),
                child: ListView(
                    padding: const EdgeInsets.all(CgTheme.pageInset),
                    children: [
                      if (_loading) const LinearProgressIndicator(),
                      if (_error != null) ...[
                        Text(_error!),
                        TextButton(
                            onPressed: _load,
                            child: Text(context.tr('重試', 'Retry')))
                      ],
                      if (!_loading &&
                          _error == null &&
                          _dashboard != null &&
                          _entries != null) ...[
                        if (!_trialMode) ...[
                          Align(
                              alignment: Alignment.centerLeft,
                              child: OutlinedButton.icon(
                                  onPressed: () => Navigator.of(context).push(
                                      MaterialPageRoute(
                                          builder: (_) => MerchantPayoutScreen(
                                              api: widget.api,
                                              shopId: widget.shopId,
                                              shopName: widget.shopName))),
                                  icon: const Icon(
                                      Icons.account_balance_wallet_outlined),
                                  label: Text(context.tr(
                                      '提款紀錄', 'Withdrawal history')))),
                          const SizedBox(height: CgTheme.space16),
                        ],
                        Text(context.tr('店舖餘額', 'Shop balance'),
                            style: Theme.of(context).textTheme.titleMedium),
                        Text(_mode(context, _balance?['connect_mode'])),
                        if (_balance?['balance_complete'] != true)
                          Text(_balance?['balance_complete'] == false
                              ? context.tr(
                                  '有 ${_count(_balance?['legacy_untracked_redemptions'])} 筆歷史核銷待核對，金額僅包含已接入流水。',
                                  '${_count(_balance?['legacy_untracked_redemptions'])} past redemptions require review. The amount covers linked ledger entries only.')
                              : context.tr('餘額完整性待確認，請先核對流水。',
                                  'Check the ledger to confirm the complete balance.')),
                        if (_unresolved(context, _balance) != null)
                          Text(_unresolved(context, _balance)!),
                        MerchantAmountRow(
                            label: _balance?['balance_complete'] == true
                                ? context.tr('可提取', 'Available')
                                : context.tr(
                                    '已接入流水可提取', 'Available in the ledger'),
                            value: _money(_balance?['withdrawable'])),
                        MerchantAmountRow(
                            label: context.tr('待轉帳', 'Pending transfer'),
                            value: _money(_balance?['pending_settlement'])),
                        MerchantAmountRow(
                            label: context.tr('提款處理中', 'Payout in progress'),
                            value: _money(_balance?['reserved'])),
                        MerchantAmountRow(
                            label: context.tr('已提款', 'Paid out'),
                            value: _money(_balance?['withdrawn'])),
                        const Divider(height: CgTheme.dividerHeight),
                        Text(
                            context.tr('近 ${widget.days} 日',
                                'Last ${widget.days} days'),
                            style: Theme.of(context).textTheme.titleMedium),
                        MerchantAmountRow(
                            label: context.tr('核銷數', 'Redemptions'),
                            value: _count(totals['redemptions'])),
                        MerchantAmountRow(
                            label: context.tr('已核銷金額', 'Redeemed amount'),
                            value: _money(totals['gross_amount'])),
                        MerchantAmountRow(
                            label: context.tr(
                                '本期領券核銷率', 'Claim-to-redemption rate'),
                            value: _rate(
                                totals['conversion_rate'], totals['claims'])),
                        const Divider(height: CgTheme.dividerHeight),
                        ExpansionTile(
                            title:
                                Text(context.tr('每日核銷', 'Daily redemptions')),
                            tilePadding: EdgeInsets.zero,
                            children: [
                              if (trend.isEmpty)
                                Padding(
                                    padding: const EdgeInsets.symmetric(
                                        vertical: CgTheme.space12),
                                    child: Text(context.tr('此期間沒有趨勢資料。',
                                        'No trends for this period.'))),
                              for (final day in trend)
                                if (day is Map)
                                  ListTile(
                                      contentPadding: EdgeInsets.zero,
                                      title: Text(
                                          '${day['date'] ?? context.tr('日期待確認', 'Date pending confirmation')}'),
                                      subtitle: Text(context.tr(
                                          '核銷 ${_count(day['redemptions'])}',
                                          '${_count(day['redemptions'])} redemptions')),
                                      trailing: MerchantAmountValue(
                                          value: _money(day['gross_amount']))),
                            ]),
                        const Divider(height: CgTheme.dividerHeight),
                        Text(context.tr('優惠券表現', 'Coupon performance'),
                            style: Theme.of(context).textTheme.titleMedium),
                        if (coupons.isEmpty)
                          Padding(
                              padding: const EdgeInsets.symmetric(
                                  vertical: CgTheme.space12),
                              child: Text(context.tr('此期間沒有優惠券資料。',
                                  'No coupons for this period.'))),
                        for (final c in coupons)
                          if (c is Map)
                            ListTile(
                                contentPadding: EdgeInsets.zero,
                                title: Text(
                                    '${c['coupon_name'] ?? context.tr('優惠券', 'Coupon')}'),
                                subtitle: Text(context.tr(
                                    '領取 ${_count(c['claims'])} · 核銷 ${_count(c['redemptions'])}',
                                    '${_count(c['claims'])} claims · ${_count(c['redemptions'])} redemptions')),
                                trailing: MerchantAmountValue(
                                    value: _money(c['gross_amount']))),
                        const Divider(height: CgTheme.dividerHeight),
                        Text(context.tr('轉帳流水', 'Transfer history'),
                            style: Theme.of(context).textTheme.titleMedium),
                        if (entries.isEmpty)
                          Padding(
                              padding: const EdgeInsets.symmetric(
                                  vertical: CgTheme.space12),
                              child: Text(
                                  context.tr('暫無流水。', 'No ledger entries.'))),
                        for (final entry in entries)
                          if (entry is Map)
                            ListTile(
                                contentPadding: EdgeInsets.zero,
                                title: Text(
                                    '${entry['coupon_name'] ?? context.tr('優惠券', 'Coupon')}'),
                                subtitle: Align(
                                  alignment: AlignmentDirectional.centerStart,
                                  child: MerchantStatusPill(
                                    label: context.tr(
                                        '狀態：${_entryState(context, entry['state'])}',
                                        'Status: ${_entryState(context, entry['state'])}'),
                                    active: entry['state'] == 'transferred',
                                  ),
                                ),
                                trailing: MerchantAmountValue(
                                    value: _money(entry['net_amount']))),
                        Row(
                            mainAxisAlignment: MainAxisAlignment.spaceBetween,
                            children: [
                              TextButton(
                                  onPressed: _page > 1
                                      ? () {
                                          setState(() {
                                            _page--;
                                          });
                                          _load();
                                        }
                                      : null,
                                  child: Text(context.tr('上一頁', 'Previous'))),
                              Text(context.tr('第 $_page 頁', 'Page $_page')),
                              TextButton(
                                  onPressed: _page <
                                          (_entries!['total_pages'] as num? ??
                                              1)
                                      ? () {
                                          setState(() {
                                            _page++;
                                          });
                                          _load();
                                        }
                                      : null,
                                  child: Text(context.tr('下一頁', 'Next'))),
                            ]),
                      ]
                    ]))));
  }
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
