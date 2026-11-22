import 'package:flutter/material.dart';

import '../api_client.dart';
import '../theme.dart';

String _money(Object? value) =>
    value is num ? 'HK\$${value.toStringAsFixed(2)}' : '—';
String _count(Object? value) => value is num ? '${value.toInt()}' : '—';
String _rate(Object? value, Object? claims) =>
    value is num && claims is num && claims > 0
        ? '${(value * 100).toStringAsFixed(1)}%'
        : '—';

String? _unresolved(Map? balance) {
  final transfers = balance?['unknown_transfer_count'];
  final reversals = balance?['unknown_reversal_count'];
  if (transfers is! num || reversals is! num) return '待核對流水狀態暫不可用，請先核對。';
  if (transfers == 0 && reversals == 0) return null;
  final amount = balance?['unknown_transfer_amount'];
  final amountText =
      amount is num && transfers > 0 ? '（涉及 ${_money(amount)}）' : '';
  return '有 ${_count(transfers)} 筆轉帳$amountText及 ${_count(reversals)} 筆沖回待核對；待確認金額不代表已到帳。';
}

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
        _error = e is ApiException ? e.message : '暫時無法讀取資料，請重試';
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
      appBar: AppBar(
          backgroundColor: CgColors.vendorGold, title: const Text('經營與餘額')),
      body: Center(
          child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 800),
              child: RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(padding: const EdgeInsets.all(20), children: [
                    const Text('商戶餘額',
                        style: TextStyle(
                            fontSize: 24, fontWeight: FontWeight.bold)),
                    const SizedBox(height: 6),
                    const Text('收款狀態以 Connect 流水為準；結算單供對帳。'),
                    if (_loading) const LinearProgressIndicator(),
                    if (_error != null) ...[
                      const SizedBox(height: 12),
                      Semantics(
                          liveRegion: true,
                          child: Text(_error!,
                              style: TextStyle(
                                  color: Theme.of(context).colorScheme.error))),
                      Align(
                          alignment: Alignment.centerLeft,
                          child: TextButton(
                              onPressed: _load, child: const Text('重試'))),
                    ],
                    if (hasData && !_loading && _error == null) ...[
                      const SizedBox(height: 14),
                      if (balance['balance_complete'] != true) ...[
                        Text(
                            balance['balance_complete'] == false
                                ? '有 ${_count(balance['legacy_untracked_redemptions'])} 筆歷史核銷待核對，金額僅包含已接入流水。'
                                : '餘額完整性待確認，請先核對流水。',
                            style:
                                const TextStyle(fontWeight: FontWeight.w600)),
                        const SizedBox(height: 12),
                      ],
                      if (_unresolved(balance) != null) ...[
                        Text(_unresolved(balance)!),
                        const SizedBox(height: 12),
                      ],
                      _AmountRow(
                          label: balance['balance_complete'] == true
                              ? '可提取'
                              : '已接入流水可提取',
                          value: _money(balance['withdrawable']),
                          prominent: true),
                      _AmountRow(
                          label: '待轉帳',
                          value: _money(balance['pending_settlement'])),
                      _AmountRow(
                          label: '提款處理中', value: _money(balance['reserved'])),
                      _AmountRow(
                          label: '已提款', value: _money(balance['withdrawn'])),
                      const SizedBox(height: 8),
                      Align(
                          alignment: Alignment.centerLeft,
                          child: TextButton.icon(
                              onPressed: () => Navigator.of(context)
                                  .pushNamed('/vendor/settlements'),
                              icon: const Icon(Icons.receipt_long_outlined),
                              label: const Text('查看結算單'))),
                      const Divider(height: 36),
                      Row(children: [
                        const Expanded(
                            child: Text('經營概覽',
                                style: TextStyle(
                                    fontSize: 22,
                                    fontWeight: FontWeight.bold))),
                        DropdownButton<int>(
                            value: _days,
                            items: const [
                              DropdownMenuItem(value: 7, child: Text('近 7 日')),
                              DropdownMenuItem(
                                  value: 30, child: Text('近 30 日')),
                              DropdownMenuItem(
                                  value: 90, child: Text('近 90 日')),
                            ],
                            onChanged: (v) {
                              if (v != null && v != _days) {
                                setState(() {
                                  _days = v;
                                });
                                _load();
                              }
                            }),
                      ]),
                      if (_dashboard!['period'] is Map)
                        Text(
                            '${(_dashboard!['period'] as Map)['from']} 至 ${(_dashboard!['period'] as Map)['to']}'),
                      const SizedBox(height: 12),
                      _AmountRow(
                          label: '核銷數', value: _count(totals['redemptions'])),
                      _AmountRow(
                          label: '已核銷金額',
                          value: _money(totals['gross_amount'])),
                      _AmountRow(
                          label: '佣金', value: _money(totals['commission'])),
                      _AmountRow(
                          label: '扣佣後金額', value: _money(totals['net_amount'])),
                      _AmountRow(
                          label: '已核銷訂單平均金額',
                          value: totals['redeemed_orders'] is num &&
                                  (totals['redeemed_orders'] as num) > 0
                              ? _money(totals['avg_order_value'])
                              : '—'),
                      _AmountRow(
                          label: '本期領券核銷率',
                          value: _rate(
                              totals['conversion_rate'], totals['claims'])),
                      const SizedBox(height: 20),
                      const Text('店舖',
                          style: TextStyle(
                              fontSize: 19, fontWeight: FontWeight.w600)),
                      if (shops.isEmpty)
                        const Padding(
                            padding: EdgeInsets.symmetric(vertical: 14),
                            child: Text('暫無可查看的店舖。')),
                      for (final raw in shops)
                        if (raw is Map && raw['shop_id'] is int)
                          ListTile(
                              contentPadding: EdgeInsets.zero,
                              title: Text('${raw['shop_name'] ?? '店舖'}'),
                              subtitle: const Text('查看核銷、券及流水'),
                              trailing: const Icon(Icons.chevron_right),
                              onTap: () => Navigator.of(context).push(
                                  MaterialPageRoute(
                                      builder: (_) =>
                                          MerchantShopDashboardScreen(
                                              api: widget.api,
                                              shopId: raw['shop_id'] as int,
                                              shopName:
                                                  '${raw['shop_name'] ?? '店舖'}',
                                              days: _days)))),
                    ],
                  ])))),
    );
  }
}

class _AmountRow extends StatelessWidget {
  const _AmountRow(
      {required this.label, required this.value, this.prominent = false});
  final String label;
  final String value;
  final bool prominent;
  @override
  Widget build(BuildContext context) => Padding(
      padding: const EdgeInsets.symmetric(vertical: 8),
      child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Expanded(child: Text(label)),
        const SizedBox(width: 12),
        Text(value,
            style: TextStyle(
                fontWeight: prominent ? FontWeight.bold : FontWeight.w600,
                fontSize: prominent ? 22 : 16)),
      ]));
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
          _error = e is ApiException ? e.message : '暫時無法讀取資料，請重試';
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
        appBar: AppBar(
            title: Text(widget.shopName), backgroundColor: CgColors.vendorGold),
        body: Center(
            child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 800),
                child: ListView(padding: const EdgeInsets.all(20), children: [
                  if (_loading) const LinearProgressIndicator(),
                  if (_error != null) ...[
                    Text(_error!),
                    TextButton(onPressed: _load, child: const Text('重試'))
                  ],
                  if (!_loading &&
                      _error == null &&
                      _dashboard != null &&
                      _entries != null) ...[
                    const Text('店舖餘額',
                        style: TextStyle(
                            fontSize: 22, fontWeight: FontWeight.bold)),
                    if (_balance?['balance_complete'] != true)
                      Text(_balance?['balance_complete'] == false
                          ? '有 ${_count(_balance?['legacy_untracked_redemptions'])} 筆歷史核銷待核對，金額僅包含已接入流水。'
                          : '餘額完整性待確認，請先核對流水。'),
                    if (_unresolved(_balance) != null)
                      Text(_unresolved(_balance)!),
                    _AmountRow(
                        label: _balance?['balance_complete'] == true
                            ? '可提取'
                            : '已接入流水可提取',
                        value: _money(_balance?['withdrawable'])),
                    _AmountRow(
                        label: '待轉帳',
                        value: _money(_balance?['pending_settlement'])),
                    _AmountRow(
                        label: '提款處理中', value: _money(_balance?['reserved'])),
                    _AmountRow(
                        label: '已提款', value: _money(_balance?['withdrawn'])),
                    const Divider(height: 32),
                    Text('近 ${widget.days} 日',
                        style: const TextStyle(
                            fontSize: 22, fontWeight: FontWeight.bold)),
                    _AmountRow(
                        label: '核銷數', value: _count(totals['redemptions'])),
                    _AmountRow(
                        label: '已核銷金額', value: _money(totals['gross_amount'])),
                    _AmountRow(
                        label: '本期領券核銷率',
                        value:
                            _rate(totals['conversion_rate'], totals['claims'])),
                    const Divider(height: 32),
                    ExpansionTile(
                        title: const Text('每日核銷'),
                        tilePadding: EdgeInsets.zero,
                        children: [
                          if (trend.isEmpty)
                            const Padding(
                                padding: EdgeInsets.symmetric(vertical: 12),
                                child: Text('此期間沒有趨勢資料。')),
                          for (final day in trend)
                            if (day is Map)
                              ListTile(
                                  contentPadding: EdgeInsets.zero,
                                  title: Text('${day['date'] ?? '日期待確認'}'),
                                  subtitle:
                                      Text('核銷 ${_count(day['redemptions'])}'),
                                  trailing: Text(_money(day['gross_amount']))),
                        ]),
                    const Divider(height: 32),
                    const Text('優惠券表現',
                        style: TextStyle(
                            fontSize: 19, fontWeight: FontWeight.w600)),
                    if (coupons.isEmpty)
                      const Padding(
                          padding: EdgeInsets.symmetric(vertical: 12),
                          child: Text('此期間沒有優惠券資料。')),
                    for (final c in coupons)
                      if (c is Map)
                        ListTile(
                            contentPadding: EdgeInsets.zero,
                            title: Text('${c['coupon_name'] ?? '優惠券'}'),
                            subtitle: Text(
                                '領取 ${_count(c['claims'])} · 核銷 ${_count(c['redemptions'])}'),
                            trailing: Text(_money(c['gross_amount']))),
                    const Divider(height: 32),
                    const Text('轉帳流水',
                        style: TextStyle(
                            fontSize: 19, fontWeight: FontWeight.w600)),
                    if (entries.isEmpty)
                      const Padding(
                          padding: EdgeInsets.symmetric(vertical: 12),
                          child: Text('暫無流水。')),
                    for (final entry in entries)
                      if (entry is Map)
                        ListTile(
                            contentPadding: EdgeInsets.zero,
                            title: Text('${entry['coupon_name'] ?? '優惠券'}'),
                            subtitle: Text('狀態：${_entryState(entry['state'])}'),
                            trailing: Text(_money(entry['net_amount']))),
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
                              child: const Text('上一頁')),
                          Text('第 $_page 頁'),
                          TextButton(
                              onPressed: _page <
                                      (_entries!['total_pages'] as num? ?? 1)
                                  ? () {
                                      setState(() {
                                        _page++;
                                      });
                                      _load();
                                    }
                                  : null,
                              child: const Text('下一頁')),
                        ]),
                  ]
                ]))));
  }
}

String _entryState(Object? value) => switch (value) {
      'pending_settlement' || 'pending' => '待轉帳',
      'transferred' => '已轉帳',
      'transfer_unknown' || 'transferring' => '轉帳待核對',
      'reversal_pending' || 'reversal_unknown' => '沖回待核對',
      'reversed' => '已沖回',
      'unavailable' => '缺少流水，待核對',
      _ => '狀態待確認',
    };
