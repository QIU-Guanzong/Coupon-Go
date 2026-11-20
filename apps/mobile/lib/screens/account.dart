import 'dart:async';

import 'package:flutter/material.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../api_client.dart';
import '../language.dart';
export 'qbot.dart' show QBotScreen;
export 'guest_auth.dart' show AuthScreen;
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import '../widgets/wallet_updates.dart';
import 'cashback.dart';
import 'reviews_admin.dart';

/// Q Wallet: points balance, three status tabs (1 unused / 2 used / 3 expired),
/// points-exchange zone (9-point coupon), records.
class WalletScreen extends StatefulWidget {
  const WalletScreen({super.key, required this.api, this.active = true, this.guest = false, required this.onShowQr});

  final ApiClient api;
  final bool active;
  final bool guest;
  final Future<void> Function(WalletItem) onShowQr;

  @override
  State<WalletScreen> createState() => _WalletScreenState();
}

class _WalletScreenState extends State<WalletScreen>
    with SingleTickerProviderStateMixin, WalletUpdates<WalletScreen> {
  late final TabController _tab = TabController(length: 3, vsync: this);
  int? _balance;
  double? _cashbackWithdrawable;
  double? _cashbackPending;
  List<WalletItem> _items = [];
  bool _loading = false;
  int _request = 0, _page = 1, _pages = 1;
  bool _moreBusy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
    _tab.addListener(() {
      if (_tab.indexIsChanging) _load();
    });
  }

  @override
  void didUpdateWidget(WalletScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if ((widget.active && !oldWidget.active) || widget.guest != oldWidget.guest) _load();
  }

  @override
  void dispose() {
    _tab.dispose();
    super.dispose();
  }

  @override
  bool get walletPageActive => widget.active && !widget.guest;

  @override
  Future<void> syncWallet() async {
    if (_loading || _moreBusy) return;
    await refreshOnReceiptChange(widget.api, () async {
      await _load(quiet: true);
      return mounted && _error == null;
    });
  }

  Future<void> _load({bool quiet = false}) async {
    final request = ++_request;
    if (widget.guest) {
      setState(() {
        _items = [];
        _balance = null;
        _cashbackPending = _cashbackWithdrawable = null;
        _loading = false;
        _error = null;
      });
      return;
    }
    final status = _tab.index + 1;
    setState(() {
      if (!quiet) _loading = true;
      _error = null;
      _moreBusy = false;
    });
    try {
      unawaited(_loadBalances(request));
      final keepPages = quiet ? _page : 1;
      final wallet = await widget.api.wallet(status: status);
      final pages = wallet['total_pages'] as int? ?? 1;
      final last = keepPages.clamp(1, pages);
      final items = List<dynamic>.of(wallet['items'] as List);
      for (var page = 2; page <= last; page++) {
        if (!mounted || request != _request) return;
        final more = await widget.api.wallet(status: status, page: page);
        items.addAll(more['items'] as List);
      }
      if (!mounted || request != _request) return;
      setState(() {
        _page = last;
        _pages = pages;
        final seen = <int>{};
        _items = items
            .map((e) => WalletItem.fromJson(e))
            .where((item) => seen.add(item.id)).toList();
        _loading = false;
      });
    } catch (_) {
      if (mounted && request == _request) {
        setState(() {
          _error = '网络不稳定，请重试';
          _loading = false;
        });
      }
    }
  }

  Future<void> _loadBalances(int request) async {
    await Future.wait([
      widget.api.points().then((data) {
        if (mounted && request == _request) setState(() => _balance = data['balance'] as int?);
      }).catchError((Object _) {
        if (mounted && request == _request) setState(() => _balance = null);
      }),
      widget.api.cashback().then((data) {
        if (!mounted || request != _request) return;
        setState(() {
          _cashbackPending = (data['pending_balance'] as num?)?.toDouble();
          _cashbackWithdrawable = (data['withdrawable_balance'] as num?)?.toDouble();
        });
      }).catchError((Object _) {
        if (mounted && request == _request) {
          setState(() {
            _cashbackPending = null;
            _cashbackWithdrawable = null;
          });
        }
      }),
    ]);
  }

  Future<void> _more() async {
    if (_moreBusy) return;
    final request = _request;
    setState(() => _moreBusy = true);
    try {
      final wallet = await widget.api.wallet(status: _tab.index+1,page:_page+1);
      if (!mounted || request != _request) return;
      final known = _items.map((item) => item.id).toSet();
      setState(() {
        _items.addAll((wallet['items'] as List).map((e)=>WalletItem.fromJson(e)).where((item)=>known.add(item.id)));
        _page++; _pages=wallet['total_pages'] as int;
      });
    } catch (_) {
      if (mounted && request == _request) showCgToast(context,'未能載入下一頁，請重試');
    } finally {
      if (mounted && request == _request) setState(()=>_moreBusy=false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 760),
      child: Column(
      children: [
        Container(
          width: double.infinity,
          color: CgColors.primary,
          padding: EdgeInsets.fromLTRB(
              16, 12 + MediaQuery.paddingOf(context).top, 16, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Q Wallet 積分',
                  style: TextStyle(color: Colors.white70)),
              Text("${_balance ?? '—'} 分",
                  style: const TextStyle(
                      color: Colors.white,
                      fontSize: 36,
                      fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              const Text('領券 +10　核銷 +20　評價 +15',
                  style: TextStyle(color: Colors.white70, fontSize: 12)),
              const SizedBox(height: 12),
              // Line D: cashback balance sits next to points, separate ledger.
              InkWell(
                onTap: widget.guest ? null : () async {
                  await Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => CashbackScreen(api: widget.api)));
                  _load();
                },
                child: Row(
                  children: [
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('返現（可提現）',
                              style: TextStyle(color: Colors.white70)),
                          Text(_cashbackWithdrawable == null ? '—' : formatHkd(_cashbackWithdrawable!),
                              style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 22,
                                  fontWeight: FontWeight.bold,
                                  fontFeatures: [
                                    FontFeature.tabularFigures()
                                  ])),
                          Text(
                              '待確認 ${_cashbackPending == null ? '—' : formatHkd(_cashbackPending!)}',
                              style: const TextStyle(
                                  color: Colors.white70,
                                  fontSize: 12,
                                  fontFeatures: [
                                    FontFeature.tabularFigures()
                                  ])),
                        ],
                      ),
                    ),
                    const Icon(Icons.chevron_right, color: Colors.white70),
                  ],
                ),
              ),            ],
          ),
        ),
        TabBar(
          controller: _tab,
          labelColor: CgColors.primary,
          tabs: const [
            Tab(text: '未使用'),
            Tab(text: '已使用'),
            Tab(text: '已失效'),
          ],
        ),
        Expanded(
          child: _loading
              ? ListView(
                  padding: const EdgeInsets.all(12),
                  children: const [
                    SkeletonBox(height: 84),
                    SizedBox(height: 8),
                    SkeletonBox(height: 84),
                    SizedBox(height: 8),
                    SkeletonBox(height: 84),
                  ],
                )
              : _error != null
                  ? Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          const Text('网络不稳定，请重试'),
                          const SizedBox(height: 12),
                          ElevatedButton(
                              onPressed: _load, child: const Text('重試')),
                        ],
                      ),
                    )
                  : RefreshIndicator(
                      onRefresh: _load,
                      child: _items.isEmpty
                          ? ListView(
                              children: [
                                const SizedBox(height: 60),
                                Center(
                                  child: Column(
                                    children: [
                                      Image.asset(
                                        'assets/images/ic_notification_cat.png',
                                        height: 120,
                                        errorBuilder: (_, __, ___) =>
                                            const Icon(
                                                Icons.inbox_outlined,
                                                size: 64,
                                                color:
                                                    CgColors.textSecondary),
                                      ),
                                      const SizedBox(height: 12),
                                      Text(widget.guest ? context.tr('登入後可在這裏查看已領取的優惠券。', 'Sign in to view your coupons.') : context.tr('暫時沒有優惠券，去首頁看看吧！', 'No coupons yet.')),
                                      const SizedBox(height: 12),
                                      ElevatedButton(
                                        onPressed: () => Navigator.of(context)
                                            .pushNamed('/packages'),
                                        child: const Text('去看看套餐'),
                                      ),
                                    ],
                                  ),
                                ),
                              ],
                            )
                          : ListView.builder(
                              itemCount: _items.length + (_page < _pages ? 1 : 0),
                              itemBuilder: (_, i) {
                                if (i == _items.length) return TextButton(onPressed:_moreBusy ? null : _more, child:const Text("載入更多"));
                                final item = _items[i];
                                return Stack(
                                  children: [
                                    Card(
                                      margin: const EdgeInsets.fromLTRB(
                                          12, 8, 12, 0),
                                      child: ListTile(
                                        leading: const Icon(Icons.local_offer,
                                            color: CgColors.primary),
                                        title: Text(item.couponName),
                                        subtitle: Text(
                                            '${item.shopName} · ${item.statusText}'
                                            '${item.isPackage ? ' · 套餐券 ×${item.quantity}' : ''}'),
                                        trailing: item.status == 1
                                            ? Row(
                                                mainAxisSize: MainAxisSize.min,
                                                children: [
                                                  if (item.canRefund)
                                                    TextButton(
                                                      onPressed: () async {
                                                        final confirmed = await showDialog<bool>(
                                                          context: context,
                                                          builder: (ctx) => AlertDialog(
                                                            title: const Text('申請退款'),
                                                            content: Text('${item.couponName} · ${item.quantity} 份\n確定退回整張套餐券嗎？'),
                                                            actions: [
                                                              TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('取消')),
                                                              ElevatedButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('確認退款')),
                                                            ],
                                                          ),
                                                        );
                                                        if (confirmed != true || !mounted) return;
                                                        try {
                                                          final result = await widget.api
                                                              .refundOrder(item
                                                                  .orderId!);
                                                          if (context
                                                              .mounted) {
                                                            showCgToast(
                                                                context,
                                                                result['message'] as String? ?? '退款申請已提交');
                                                          }
                                                          _load();
                                                        } on ApiException catch (e) {
                                                          if (context
                                                              .mounted) {
                                                            showCgToast(context,
                                                                e.message);
                                                          }
                                                        }
                                                      },
                                                      child: const Text('退款'),
                                                    ),
                                                  ElevatedButton(
                                                    onPressed: () async { await widget.onShowQr(item); if (mounted) await _load(); },
                                                    style: ElevatedButton
                                                        .styleFrom(
                                                      minimumSize:
                                                          const Size(72, 40),
                                                    ),
                                                    child: const Text('出示'),
                                                  ),
                                                ],
                                              )
                                            : item.status == 2 &&
                                                    !item.reviewed
                                                ? TextButton(
                                                    onPressed: () async {
                                                      final ok = await Navigator.of(
                                                              context)
                                                          .push<bool>(
                                                              MaterialPageRoute(
                                                                  builder: (_) =>
                                                                      ReviewFormScreen(
                                                                          api: widget
                                                                              .api,
                                                                          item:
                                                                              item)));
                                                      if (ok == true) _load();
                                                    },
                                                    child:
                                                        const Text('去評價'),
                                                  )
                                                : null,
                                      ),
                                    ),
                                    // UI_SPEC §8: package voucher corner badge.
                                    if (item.isPackage)
                                      Positioned(
                                        left: 12,
                                        top: 8,
                                        child: Container(
                                          padding: const EdgeInsets.symmetric(
                                              horizontal: 6, vertical: 2),
                                          decoration: const BoxDecoration(
                                            color: CgColors.primary,
                                            borderRadius: BorderRadius.only(
                                              topLeft: Radius.circular(12),
                                              bottomRight:
                                                  Radius.circular(8),
                                            ),
                                          ),
                                          child: const Text('套餐',
                                              style: TextStyle(
                                                  color: Colors.white,
                                                  fontSize: 11,
                                                  fontWeight:
                                                      FontWeight.bold)),
                                        ),
                                      ),
                                  ],
                                );
                              },
                            ),
                    ),
        ),
      ],
    )));
  }
}

/// QR show page: red screen, white card, QR + big code + 15:00 countdown.
class QrShowScreen extends StatefulWidget {
  const QrShowScreen({super.key, required this.api, required this.item});

  final ApiClient api;
  final WalletItem item;

  @override
  State<QrShowScreen> createState() => _QrShowScreenState();
}

class _QrShowScreenState extends State<QrShowScreen> {
  String? _code;
  String? _payload;
  int _secondsLeft = 900;
  int _totalSeconds = 900;
  Timer? _timer;
  Timer? _pollTimer;
  bool _justRefreshed = false;
  bool _redeemed = false;
  bool _generating = false, _polling = false, _expired = false;
  String? _qrError;

  @override
  void initState() {
    super.initState();
    _generate();
    // UI_SPEC §8: poll until the voucher is redeemed, then close this
    // page and open the redemption-success page.
    _pollTimer = Timer.periodic(const Duration(seconds: 5), (_) async {
      if (_redeemed || _polling || !mounted) return;
      _polling = true;
      try {
        final result = await widget.api.get('/wallet/items/${widget.item.id}/status');
        if (!mounted) return;
        final reversedBeforePoll = result['revoked'] == true &&
            result['has_code'] == false && _code != null && !_generating;
        if ((result['status'] == 2 || reversedBeforePoll) &&
            ModalRoute.of(context)?.isCurrent == true) {
          _redeemed = true;
          _timer?.cancel();
          _pollTimer?.cancel();
          Navigator.of(context).pushReplacement(MaterialPageRoute(
              builder: (_) => RedemptionSuccessScreen(
                api: widget.api, item: widget.item,
                redemptionId: result['redemption_id'] as int?,
                initiallyRevoked: reversedBeforePoll,
                initialStatus: result['status'] as int,
              )));
        } else if (result['status'] == 3) {
          _timer?.cancel();
          _pollTimer?.cancel();
          setState(() { _expired = true; _payload = null; _code = null; _secondsLeft = 0; _qrError = '此優惠券已到期'; });
        }
      } catch (_) {
        // Retry the authoritative status on the next tick.
      } finally {
        _polling = false;
      }
    });
  }

  @override
  void dispose() {
    _timer?.cancel();
    _pollTimer?.cancel();
    // Brightness is restored here. NOTE: there is no brightness plugin
    // wired in (no screen_brightness in the pub cache and no native
    // platform code); the page uses a full-white card on a bright
    // background instead. See the batch report — this needs the plugin
    // + a real-device check before it can be claimed as done.
    super.dispose();
  }

  Future<void> _generate({bool auto = false}) async {
    if (_generating || _redeemed || _expired) return;
    _timer?.cancel();
    setState(() { _generating = true; _qrError = null; _payload = null; _code = null; _secondsLeft = 0; });
    try {
      final data = await widget.api.generateQr(widget.item.id);
      if (!mounted || _expired || _redeemed) return;
      setState(() {
        _code = data['code'] as String?;
        _payload = data['qr_payload'] as String?;
        _secondsLeft = data['expires_in_seconds'] as int? ?? 900;
        _totalSeconds = _secondsLeft;
        if (auto) _justRefreshed = true;
      });
      if (auto) {
        Future.delayed(const Duration(seconds: 2), () {
          if (mounted) setState(() => _justRefreshed = false);
        });
      }
      _timer?.cancel();
      final deadline = DateTime.now().add(Duration(seconds:_secondsLeft));
      _timer = Timer.periodic(const Duration(seconds: 1), (t) {
        final remaining = (deadline.difference(DateTime.now()).inMilliseconds / 1000).ceil();
        if (remaining <= 0) {
          t.cancel();
          _generate(auto: true); // expired: rotate a fresh code
          return;
        }
        if (mounted) setState(() => _secondsLeft = remaining);
      });
    } catch (e) {
      if (mounted) setState(() => _qrError = e is ApiException ? e.message : '無法取得兌換碼，請重試');
    } finally {
      if (mounted) setState(() => _generating = false);
    }
  }

  String get _countdown => formatCountdown(
      Duration(seconds: _secondsLeft));

  String get _validToText {
    final v = widget.item.validTo;
    if (v == null) return '有效期：—';
    return '有效期至 ${v.year}年${v.month}月${v.day}日';
  }

  @override
  Widget build(BuildContext context) {
    final progress =
        _totalSeconds == 0 ? 0.0 : _secondsLeft / _totalSeconds;
    return Scaffold(
      backgroundColor: CgColors.primary,
      appBar: AppBar(title: const Text('出示優惠券')),
      body: Center(
        child: SingleChildScrollView(
          child: Card(
            margin: const EdgeInsets.all(24),
            child: Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  if (_justRefreshed)
                    Container(
                      margin: const EdgeInsets.only(bottom: 12),
                      padding: const EdgeInsets.symmetric(
                          horizontal: 12, vertical: 6),
                      decoration: BoxDecoration(
                        color: const Color(0xFFE8F5E9),
                        borderRadius: BorderRadius.circular(20),
                      ),
                      child: const Text('二維碼已更新',
                          style: TextStyle(color: CgColors.success)),
                    ),
                  Text(widget.item.couponName,
                      textAlign: TextAlign.center,
                      style: const TextStyle(
                          fontSize: 18, fontWeight: FontWeight.bold)),
                  const SizedBox(height: 4),
                  Text(widget.item.shopName,
                      textAlign: TextAlign.center,
                      style:
                          const TextStyle(color: CgColors.textSecondary)),
                  Text(_validToText,
                      textAlign: TextAlign.center,
                      style:
                          const TextStyle(color: CgColors.textSecondary)),
                  const SizedBox(height: 16),
                  if (_payload != null)
                    QrImageView(data: _payload!, size: 220,backgroundColor: Colors.white)
                  else if (_qrError != null)
                    Text(_qrError!,textAlign:TextAlign.center)
                  else
                    const SkeletonBox(height: 220, width: 220),
                  const SizedBox(height: 12),
                  Text(_code ?? '------',
                      style: const TextStyle(
                          fontSize: 32,
                          fontWeight: FontWeight.bold,
                          letterSpacing: 6,
                          fontFeatures: [FontFeature.tabularFigures()])),
                  const SizedBox(height: 12),
                  // Countdown ring around the remaining time.
                  SizedBox(
                    width: 96,
                    height: 96,
                    child: Stack(
                      alignment: Alignment.center,
                      children: [
                        SizedBox(
                          width: 96,
                          height: 96,
                          child: CircularProgressIndicator(
                            value: progress,
                            strokeWidth: 6,
                            backgroundColor: const Color(0xFFE8E8E8),
                            color: CgColors.primary,
                          ),
                        ),
                        Text(_countdown,
                            style: const TextStyle(
                                color: CgColors.primary,
                                fontSize: 20,
                                fontWeight: FontWeight.bold,
                                fontFeatures: [
                                  FontFeature.tabularFigures()
                                ])),
                      ],
                    ),
                  ),
                  const SizedBox(height: 12),
                  const Text('使用前出示 QR 碼\n請讓店員掃描兌換碼以核銷優惠券',
                      textAlign: TextAlign.center,
                      style: TextStyle(color: CgColors.textSecondary)),
                  const SizedBox(height: 12),
                  Container(
                    padding: const EdgeInsets.all(10),
                    decoration: BoxDecoration(
                      color: const Color(0xFFFFF3CD),
                      borderRadius: BorderRadius.circular(8),
                    ),
                    child: const Text(
                        '请勿转发二维码\n每次出示都會產生新的兌換碼，舊碼可能已失效。',
                        textAlign: TextAlign.center,
                        style: TextStyle(fontSize: 13)),
                  ),
                  const SizedBox(height: 8),
                  TextButton(
                    onPressed: _generating || _expired ? null : () => _generate(),
                    child: const Text('重新產生新碼'),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

/// Redemption success (UI_SPEC §8): the QR page closes itself on
/// redemption and lands here — voucher name, shop, cashback note,
/// and the "去評價" entry (only for a not-yet-reviewed voucher).
class RedemptionSuccessScreen extends StatefulWidget {
  const RedemptionSuccessScreen({super.key, required this.api, required this.item,
    this.redemptionId, this.initiallyRevoked = false, this.initialStatus = 2});
  final ApiClient api;
  final WalletItem item;
  final int? redemptionId;
  final bool initiallyRevoked;
  final int initialStatus;
  @override
  State<RedemptionSuccessScreen> createState() => _ReceiptState();
}

class _ReceiptState extends State<RedemptionSuccessScreen> with WalletUpdates<RedemptionSuccessScreen> {
  late bool _revoked = widget.initiallyRevoked;
  late int _status = widget.initialStatus;
  late bool _reviewed = widget.item.reviewed;

  @override
  Future<void> syncWallet() async {
    final data = await widget.api.get('/wallet/items/${widget.item.id}/status',
        query: {if (widget.redemptionId != null) 'redemption_id': '${widget.redemptionId}'});
    if (!mounted) return;
    setState(() {
      _revoked = data['revoked'] == true;
      _status = data['status'] as int;
      _reviewed = data['reviewed'] == true;
    });
  }

  @override
  Widget build(BuildContext context) {
    final title = _revoked ? '商家已撤銷核銷' : '核銷成功';
    return Scaffold(
      appBar: AppBar(title: const Text('核銷結果')),
      body: Center(child: SingleChildScrollView(padding: const EdgeInsets.all(24),
        child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 440),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Icon(_revoked ? Icons.undo : Icons.check_circle, size: 64,
                color: _revoked ? CgColors.textSecondary : CgColors.success),
            const SizedBox(height: 16),
            Semantics(liveRegion: true, child: Text(title, style: Theme.of(context).textTheme.headlineSmall)),
            const SizedBox(height: 12),
            Text(widget.item.couponName, textAlign: TextAlign.center,
                style: const TextStyle(fontSize: 18, fontWeight: FontWeight.w600)),
            Text(widget.item.shopName, style: const TextStyle(color: CgColors.textSecondary)),
            if (_revoked) Padding(padding: const EdgeInsets.only(top: 12),
              child: Text(switch (_status) {
                1 => '優惠券已退回錢包，可重新出示。',
                3 => '優惠券已失效，可在錢包查看最新狀態。',
                _ => '這張券其後已再次使用。'
              }, textAlign: TextAlign.center)),
            const SizedBox(height: 24),
            if (!_revoked && !_reviewed)
              ElevatedButton(onPressed: () async {
                final done = await Navigator.of(context).push(MaterialPageRoute(builder: (_) =>
                    ReviewFormScreen(api: widget.api, item: widget.item, redemptionId: widget.redemptionId)));
                if (mounted && done == true) setState(() => _reviewed = true);
              }, child: const Text('去評價')),
            OutlinedButton(onPressed: () => Navigator.of(context).pop(), child: const Text('返回錢包')),
          ]),
        ),
      )),
    );
  }
}

/// Notifications: unread badge, mark-all-read, clear.
class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key, required this.api, this.guest = false});

  final ApiClient api;
  final bool guest;

  @override
  State<NotificationsScreen> createState() => _NotificationsScreenState();
}

class _NotificationsScreenState extends State<NotificationsScreen> {
  List<AppNotification> _items = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (widget.guest) return;
    try {
      final data = await widget.api.notifications();
      if (!mounted) return;
      setState(() => _items = (data['notifications'] as List)
          .map((e) => AppNotification.fromJson(e))
          .toList());
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('通知'),
        actions: [
          if (!widget.guest) TextButton(
            onPressed: () async {
              await widget.api.markAllNotificationsRead();
              _load();
            },
            child: const Text('全部標示已讀',
                style: TextStyle(color: Colors.white)),
          ),
        ],
      ),
      body: _items.isEmpty
          ? Center(
              child: Column(
                mainAxisAlignment: MainAxisAlignment.center,
                children: [
                  Image.asset('assets/images/ic_notification_cat.png',
                      height: 120,
                      errorBuilder: (_, __, ___) => const Icon(
                          Icons.notifications_none,
                          size: 64,
                          color: CgColors.textSecondary)),
                  const SizedBox(height: 12),
                  Text(widget.guest ? context.tr('登入後可查看你的通知。', 'Sign in to view your notifications.') : context.tr('沒有新通知。', 'No new notifications.')),
                ],
              ),
            )
          : ListView.builder(
              itemCount: _items.length,
              itemBuilder: (_, i) {
                final n = _items[i];
                return Card(
                  margin: const EdgeInsets.fromLTRB(12, 8, 12, 0),
                  child: ListTile(
                    title: Text(n.title,
                        style: TextStyle(
                            fontWeight: n.isRead
                                ? FontWeight.normal
                                : FontWeight.bold)),
                    subtitle: Text(n.body),
                    trailing: n.isRead
                        ? null
                        : const Icon(Icons.circle,
                            size: 10, color: CgColors.primary),
                    onTap: () async {
                      if (!n.isRead) {
                        await widget.api.markNotificationRead(n.id);
                        _load();
                      }
                    },
                  ),
                );
              },
            ),
    );
  }
}

/// Settings: profile, language, notification toggles, logout / delete.
class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key, required this.api, required this.onLogout});

  final ApiClient api;
  final VoidCallback onLogout;

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {


  @override
  Widget build(BuildContext context) {
    final language = LanguageScope.of(context);
    final t = context.tr;
    final draft = language.draft;
    return Scaffold(
      appBar: AppBar(title: Text(t('設置','Settings'))),
      body: Stack(children: [ListView(
        children: [
          ListTile(title: Text(t('語言','Languages')),
            subtitle: Text((draft ?? language.english) ? 'English' : '繁體中文'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => setState(() => language.draft = language.english)),
          ListTile(
            title: const Text('新手指南 / 常見問題'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => Navigator.of(context).pushNamed('/guide'),
          ),
          ListTile(
            title: const Text('關於 Coupon GO!'),
            subtitle: const Text('版本 v0.1.0'),
            onTap: () => showAboutDialog(context: context,
                applicationName: 'Coupon GO!', applicationVersion: 'v0.1.0'),
          ),
          if (widget.api.isLoggedIn) ...[
          const Divider(),
          ListTile(
            title: const Text('登出', style: TextStyle(color: CgColors.primary)),
            onTap: () async {
              await widget.api.clearToken();
              widget.onLogout();
            },
          ),
          ListTile(
            title: const Text('刪除帳戶', style: TextStyle(color: Colors.red)),
            onTap: () async {
              final ok = await showDialog<bool>(
                context: context,
                builder: (c) => AlertDialog(
                  title: const Text('刪除帳戶'),
                  content: const Text('請填寫刪除原因。刪除後帳戶將停用。'),
                  actions: [
                    TextButton(
                        onPressed: () => Navigator.pop(c, false),
                        child: const Text('取消')),
                    TextButton(
                        onPressed: () => Navigator.pop(c, true),
                        child: const Text('確認刪除')),
                  ],
                ),
              );
              if (ok == true) {
                try {
                  await widget.api.post('/delete-account', body: {'reason': '用戶申請'});
                  await widget.api.clearToken();
                  widget.onLogout();
                } on ApiException catch (e) {
                  if (context.mounted) showCgToast(context, e.message);
                }
              }
            },
          ),
          ],
        ],
      ),
      if (draft != null) ...[
        ModalBarrier(color: Colors.black38, dismissible: true,
          onDismiss: () => setState(() => language.draft = null)),
        Align(alignment: Alignment.bottomCenter, child: Material(
          borderRadius: const BorderRadius.vertical(top: Radius.circular(18)),
          child: SafeArea(child: Column(mainAxisSize: MainAxisSize.min, children: [
            for (final english in [true,false]) ListTile(
              title: Text(english ? 'English' : '繁體中文'),
              trailing: Icon(draft == english ? Icons.radio_button_checked : Icons.radio_button_off),
              onTap: () => setState(() => language.draft = english)),
            Padding(padding: const EdgeInsets.all(12), child: FilledButton(
              onPressed: () async { await language.commit(draft); if (mounted) setState(() {}); },
              child: Text(t('提交','Submit')))),
          ])),
        )),
      ],
      ]),
    );
  }
}

/// Guide + FAQ (local, behaviour-based).
class GuideScreen extends StatelessWidget {
  const GuideScreen({super.key});

  static const _faqs = [
    ('如何領取優惠券？', '在首頁或優惠券詳情頁點「立即領取」，券會存入 Q Wallet，領券 +10 分。'),
    ('如何使用優惠券？', '在 Q Wallet 點「出示」，把 QR 碼給店員掃描，核銷成功 +20 分。'),
    ('積分有什麼用？', '領券 +10、核銷 +20、評價 +15；9 分可以兌換 9 分分券。'),
    ('QR 碼有效期多久？', '出示後 15:00 內有效，過期請重新出示。'),
    ('商戶如何發券？', '切換到企業版，填表 → 預覽 → 提交審核，通過後上架。'),
    ('忘記密碼怎麼辦？', '登錄頁點「忘記密碼」，用電郵收 6 位驗證碼重設。'),
  ];

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('新手指南 / 常見問題')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text('消費三步', style: TextStyle(fontWeight: FontWeight.bold)),
          const ListTile(leading: Icon(Icons.search), title: Text('1. 搜尋或瀏覽優惠券')),
          const ListTile(leading: Icon(Icons.local_offer), title: Text('2. 領取存入 Q Wallet')),
          const ListTile(leading: Icon(Icons.qr_code), title: Text('3. 到店出示 QR 碼核銷')),
          const SizedBox(height: 8),
          const Text('常見問題', style: TextStyle(fontWeight: FontWeight.bold)),
          ..._faqs.map((f) => ExpansionTile(
                title: Text(f.$1),
                children: [ListTile(title: Text(f.$2))],
              )),
        ],
      ),
    );
  }
}
