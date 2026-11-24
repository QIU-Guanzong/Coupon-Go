import 'dart:async';

import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../widgets/widgets.dart';

class MerchantAccountOverview extends StatefulWidget {
  const MerchantAccountOverview({super.key, required this.api});

  final ApiClient api;

  @override
  State<MerchantAccountOverview> createState() => _MerchantAccountOverviewState();
}

enum _Read { loading, signedOut, denied, unavailable, ready }

class _Shop {
  const _Shop(this.id, this.name, this.canViewSettlement);

  final int id;
  final String name;
  final bool canViewSettlement;

  static List<_Shop> parse(Object? data) {
    if (data is! List) throw const FormatException('Invalid merchant shops');
    final shops = <_Shop>[];
    final ids = <int>{};
    for (final row in data) {
      if (row is! Map || row['id'] is! int || row['name'] is! String) {
        throw const FormatException('Invalid merchant shop');
      }
      final id = row['id'] as int;
      if (id <= 0 || !ids.add(id)) {
        throw const FormatException('Invalid shop identity');
      }
      shops.add(_Shop(id, row['name'] as String,
          row['can_view_settlement'] == true));
    }
    return shops;
  }
}

class _Entry {
  const _Entry(this.kind, this.status, this.createdAt, this.netAmount);

  final Object? kind;
  final Object? status;
  final Object? createdAt;
  final Object? netAmount;
}

// This panel reads the account and its ledger. Setting up an account and
// requesting a payout are deliberately handled outside this screen.
class _MerchantAccountOverviewState extends State<MerchantAccountOverview> {
  int _request = 0;
  late int _session;
  _Read _account = _Read.loading;
  _Read _settlement = _Read.loading;
  Map<String, dynamic>? _profile;
  List<_Shop> _shops = const [];
  _Shop? _shop;
  bool _hasAccount = false;
  String? _setupStatus;
  Map<String, dynamic>? _balance;
  List<_Entry> _ledger = const [];

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    unawaited(_loadAccount());
  }

  @override
  void didUpdateWidget(covariant MerchantAccountOverview oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.api == widget.api) return;
    oldWidget.api.removeListener(_sessionChanged);
    widget.api.addListener(_sessionChanged);
    _session = widget.api.sessionRevision;
    unawaited(_loadAccount());
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    super.dispose();
  }

  void _sessionChanged() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    unawaited(_loadAccount());
  }

  bool _current(ApiClient api, int request) =>
      mounted && widget.api == api && request == _request &&
      _session == api.sessionRevision && api.isLoggedIn;

  void _clearSettlement() {
    _hasAccount = false;
    _setupStatus = null;
    _balance = null;
    _ledger = const [];
  }

  void _resetAccount(_Read state) {
    _account = state;
    _profile = null;
    _shops = const [];
    _shop = null;
    _clearSettlement();
  }

  Future<void> _loadAccount() async {
    final api = widget.api;
    final request = ++_request;
    setState(() => _resetAccount(api.isLoggedIn ? _Read.loading : _Read.signedOut));
    if (!api.isLoggedIn) return;
    try {
      final profile = await api.get('/me');
      if (!_current(api, request)) return;
      final payload = await api.get('/merchant/shops');
      if (!_current(api, request)) return;
      final shops = _Shop.parse(payload['shops']);
      final first = shops.isEmpty ? null : shops.firstWhere(
        (shop) => shop.canViewSettlement,
        orElse: () => shops.first,
      );
      setState(() {
        _profile = profile;
        _shops = shops;
        _account = _Read.ready;
        _settlement = _Read.loading;
      });
      if (first != null) await _loadShop(first);
    } catch (error) {
      if (!_current(api, request)) return;
      setState(() => _resetAccount(_readError(error)));
    }
  }

  Future<void> _loadShop(_Shop shop) async {
    final api = widget.api;
    final request = ++_request;
    if (!api.isLoggedIn) return;
    setState(() {
      _shop = shop;
      _clearSettlement();
      _settlement = shop.canViewSettlement ? _Read.loading : _Read.denied;
    });
    if (!shop.canViewSettlement) return;

    final query = {'shop_id': '${shop.id}'};
    try {
      final status = await api.get('/merchant/connect/status', query: query);
      if (!_current(api, request)) return;
      if (status['shop_id'] != shop.id ||
          !status.containsKey('stripe_account_id')) {
        throw const FormatException('Invalid merchant account status');
      }
      final id = status['stripe_account_id'];
      if (id == null) {
        setState(() => _settlement = _Read.ready);
        return;
      }
      if (id is! String || id.isEmpty) {
        throw const FormatException('Invalid merchant account');
      }
      final setupStatus = status['onboarding_status'];
      if (setupStatus is! String || setupStatus.isEmpty) {
        throw const FormatException('Invalid onboarding status');
      }

      final balance = await api.merchantConnectBalance(shop.id);
      if (!_current(api, request)) return;
      if (balance['shop_id'] != shop.id) {
        throw const FormatException('Mismatched merchant balance scope');
      }
      final ledger = await api.get('/merchant/connect/ledger', query: query);
      if (!_current(api, request)) return;
      final entries = ledger['entries'];
      if (entries is! List || entries.any((entry) => entry is! Map)) {
        throw const FormatException('Invalid merchant ledger');
      }
      setState(() {
        _hasAccount = true;
        _setupStatus = setupStatus;
        _balance = balance;
        _ledger = entries
            .map((entry) => entry as Map)
            .map((entry) => _Entry(entry['kind'], entry['status'],
                entry['created_at'], entry['net_amount']))
            .toList(growable: false);
        _settlement = _Read.ready;
      });
    } catch (error) {
      if (!_current(api, request)) return;
      setState(() {
        final state = _readError(error);
        if (state == _Read.signedOut) {
          _resetAccount(state);
        } else {
          _clearSettlement();
          _settlement = state;
        }
      });
    }
  }

  _Read _readError(Object error) => switch (error) {
    ApiException(status: 401) => _Read.signedOut,
    ApiException(status: 403) => _Read.denied,
    _ => _Read.unavailable,
  };

  String _money(Object? amount, Object? currency) =>
      currency == 'HKD' && amount is num && amount.isFinite
          ? 'HK\$ ${amount.toStringAsFixed(2)}'
          : '—';

  String _ledgerTitle(BuildContext context, Object? kind) => switch (kind) {
    'transfer' => context.tr('結算轉帳', 'Settlement transfer'),
    'payout' => context.tr('提款紀錄', 'Payout record'),
    _ => context.tr('交易紀錄', 'Transaction'),
  };

  String _ledgerStatus(BuildContext context, Object? status) => switch (status) {
    'transferred' => context.tr('已轉帳', 'Transferred'),
    'paid_out' => context.tr('已記帳', 'Recorded'),
    'pending' || 'transferring' || 'processing' || 'submitted' =>
      context.tr('處理中', 'Processing'),
    'failed' => context.tr('未完成', 'Not completed'),
    'reversed' => context.tr('已沖回', 'Reversed'),
    _ => context.tr('待核對', 'Pending verification'),
  };

  Widget _readMessage(BuildContext context, _Read state, VoidCallback retry) {
    if (state == _Read.loading) {
      return const Padding(padding: EdgeInsets.all(16),
          child: SkeletonBox(height: 64));
    }
    final message = switch (state) {
      _Read.signedOut => context.tr('請登入查看商戶帳戶', 'Sign in to view your merchant account'),
      _Read.denied => context.tr('沒有權限查看此資料', 'You cannot view these details'),
      _ => context.tr('未能載入帳戶資料', 'Could not load account details'),
    };
    return ListTile(
      title: Text(message),
      trailing: state == _Read.unavailable
          ? TextButton(onPressed: retry, child: Text(context.tr('重試', 'Retry')))
          : null,
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_account != _Read.ready) {
      return _readMessage(context, _account, _loadAccount);
    }
    final name = _profile?['name'];
    final email = _profile?['email'];
    final displayName = name is String ? name.trim() : '';
    final displayEmail = email is String ? email.trim() : '';
    final balance = _balance;
    final status = !_hasAccount
        ? context.tr('未設定收款帳戶', 'Receiving account not set up')
        : _setupStatus == 'active' && balance?['account_ready'] == true
            ? context.tr('收款帳戶已設定', 'Receiving account set up')
            : context.tr('收款帳戶未就緒', 'Receiving account not ready');
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        ListTile(
          leading: const Icon(Icons.storefront),
          title: Text(displayName.isNotEmpty ? displayName : displayEmail.isNotEmpty
              ? displayEmail : context.tr('未有帳戶資料', 'No account details')),
          subtitle: displayName.isNotEmpty && displayEmail.isNotEmpty
              ? Text(displayEmail) : null,
        ),
        if (_shops.length > 1)
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: DropdownButtonFormField<int>(
              initialValue: _shop?.id,
              isExpanded: true,
              decoration: InputDecoration(labelText: context.tr('店舖', 'Shop')),
              items: _shops.map((shop) => DropdownMenuItem(
                value: shop.id,
                child: Text(shop.name, overflow: TextOverflow.ellipsis),
              )).toList(),
              onChanged: (id) {
                if (id == null || id == _shop?.id) return;
                unawaited(_loadShop(_shops.firstWhere((shop) => shop.id == id)));
              },
            ),
          )
        else if (_shop != null)
          ListTile(title: Text(_shop!.name)),
        if (_shop == null)
          ListTile(title: Text(context.tr('未有店舖', 'No shops')))
        else if (_settlement != _Read.ready)
          _readMessage(context, _settlement, () => _loadShop(_shop!))
        else
          ListTile(title: Text(status)),
        if (balance != null) ...[
          ListTile(title: Text(context.tr('帳面餘額', 'Ledger balance'))),
          for (final item in [
            ('pending', context.tr('待結算', 'Pending settlement')),
            ('available', context.tr('帳面可提取', 'Ledger available')),
            ('processing', context.tr('提款處理中', 'Payout in progress')),
            ('paid_out', context.tr('已提取', 'Paid out')),
          ])
            ListTile(title: Text(item.$2),
                trailing: Text(_money(balance[item.$1], balance['currency']))),
          ListTile(title: Text(context.tr('最近流水', 'Recent ledger entries'))),
          if (_ledger.isEmpty)
            ListTile(title: Text(context.tr('未有流水', 'No ledger entries'))),
          for (final entry in _ledger)
            ListTile(
              title: Text(_ledgerTitle(context, entry.kind)),
              subtitle: Text([
                _ledgerStatus(context, entry.status),
                if (_date(entry.createdAt) != null) _date(entry.createdAt)!,
              ].join(' · ')),
              trailing: Text(_money(entry.netAmount, balance['currency'])),
            ),
        ],
      ],
    );
  }

  String? _date(Object? raw) {
    if (raw is! String) return null;
    final date = DateTime.tryParse(raw);
    return date?.toIso8601String().split('T').first;
  }
}
