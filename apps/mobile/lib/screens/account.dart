import 'dart:async';

import 'package:flutter/material.dart';
import 'package:qr_flutter/qr_flutter.dart';

import '../api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import 'cashback.dart';
import 'reviews_admin.dart';

/// Q Wallet: points balance, three status tabs (1 unused / 2 used / 3 expired),
/// points-exchange zone (9-point coupon), records.
class WalletScreen extends StatefulWidget {
  const WalletScreen({super.key, required this.api, required this.onShowQr});

  final ApiClient api;
  final void Function(WalletItem) onShowQr;

  @override
  State<WalletScreen> createState() => _WalletScreenState();
}

class _WalletScreenState extends State<WalletScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tab = TabController(length: 3, vsync: this);
  int _balance = 0;
  double _cashbackWithdrawable = 0;
  double _cashbackPending = 0;
  List<WalletItem> _items = [];
  bool _loading = false;
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
  void dispose() {
    _tab.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final points = await widget.api.points();
      final wallet = await widget.api.wallet(status: _tab.index + 1);
      final cashback = await widget.api.cashback();
      if (!mounted) return;
      setState(() {
        _balance = points['balance'] as int? ?? 0;
        _cashbackWithdrawable =
            (cashback['withdrawable_balance'] as num?)?.toDouble() ?? 0;
        _cashbackPending =
            (cashback['pending_balance'] as num?)?.toDouble() ?? 0;
        _items = (wallet['items'] as List)
            .map((e) => WalletItem.fromJson(e))
            .toList();
        _loading = false;
      });
    } on ApiException catch (_) {
      if (mounted) {
        setState(() {
          _error = '网络不稳定，请重试';
          _loading = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        Container(
          width: double.infinity,
          color: CgColors.primary,
          padding: const EdgeInsets.fromLTRB(16, 12, 16, 16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Text('Q Wallet 積分',
                  style: TextStyle(color: Colors.white70)),
              Text('$_balance 分',
                  style: const TextStyle(
                      color: Colors.white,
                      fontSize: 36,
                      fontWeight: FontWeight.bold)),
              const SizedBox(height: 8),
              const Text('領券 +10　核銷 +20　評價 +15',
                  style: TextStyle(color: Colors.white70, fontSize: 12)),
              const Text('領積分的評價不計入商户星級（防誘導評價）',
                  style: TextStyle(color: Colors.white70, fontSize: 12)),
              const SizedBox(height: 12),
              // Line D: cashback balance sits next to points, separate ledger.
              InkWell(
                onTap: () async {
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
                          Text(formatHkd(_cashbackWithdrawable),
                              style: const TextStyle(
                                  color: Colors.white,
                                  fontSize: 22,
                                  fontWeight: FontWeight.bold,
                                  fontFeatures: [
                                    FontFeature.tabularFigures()
                                  ])),
                          Text(
                              '待確認 ${formatHkd(_cashbackPending)}',
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
                                      const Text('暫時沒有優惠券，去首頁看看吧！'),
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
                              itemCount: _items.length,
                              itemBuilder: (_, i) {
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
                                            '${item.isPackage ? ' · 套餐券' : ''}'),
                                        trailing: item.status == 1
                                            ? Row(
                                                mainAxisSize: MainAxisSize.min,
                                                children: [
                                                  if (item.canRefund)
                                                    TextButton(
                                                      onPressed: () async {
                                                        try {
                                                          await widget.api
                                                              .refundOrder(item
                                                                  .orderId!);
                                                          if (context
                                                              .mounted) {
                                                            showCgToast(
                                                                context,
                                                                '退款成功，套餐券已作廢');
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
                                                    onPressed: () => widget
                                                        .onShowQr(item),
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
    );
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

  @override
  void initState() {
    super.initState();
    _generate();
    // UI_SPEC §8: poll until the voucher is redeemed, then close this
    // page and open the redemption-success page.
    _pollTimer = Timer.periodic(const Duration(seconds: 5), (_) async {
      if (_redeemed || !mounted) return;
      try {
        final wallet = await widget.api.wallet(status: 2);
        final items = (wallet['items'] as List?) ?? [];
        final hit = items.any((e) =>
            (e as Map<String, dynamic>)['id'] == widget.item.id);
        if (hit && mounted) {
          _redeemed = true;
          _timer?.cancel();
          _pollTimer?.cancel();
          Navigator.of(context).pushReplacement(MaterialPageRoute(
              builder: (_) => RedemptionSuccessScreen(
                  api: widget.api, item: widget.item)));
        }
      } catch (_) {
        // Poll failure is not an error state; the next tick retries.
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
    try {
      final data = await widget.api.generateQr(widget.item.id);
      if (!mounted) return;
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
      _timer = Timer.periodic(const Duration(seconds: 1), (t) {
        if (_secondsLeft <= 1) {
          t.cancel();
          _generate(auto: true); // expired: rotate a fresh code
          return;
        }
        if (mounted) setState(() => _secondsLeft--);
      });
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
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
                    Stack(
                      alignment: Alignment.center,
                      children: [
                        QrImageView(data: _payload!, size: 220),
                        Image.asset('assets/images/ic_qr_cat.png',
                            width: 48,
                            errorBuilder: (_, __, ___) =>
                                const SizedBox()),
                      ],
                    )
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
                  const Text('使用前出示 QR 碼\n請將 QR 碼呈現給店員掃描以進行支付',
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
                    onPressed: () => _generate(),
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
class RedemptionSuccessScreen extends StatelessWidget {
  const RedemptionSuccessScreen(
      {super.key, required this.api, required this.item});

  final ApiClient api;
  final WalletItem item;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('核銷成功')),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: Column(
            mainAxisAlignment: MainAxisAlignment.center,
            children: [
              const Icon(Icons.check_circle,
                  size: 72, color: CgColors.success),
              const SizedBox(height: 16),
              const Text('核銷成功',
                  style:
                      TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
              const SizedBox(height: 12),
              Text(item.couponName,
                  textAlign: TextAlign.center,
                  style: const TextStyle(fontSize: 18)),
              Text(item.shopName,
                  style: const TextStyle(color: CgColors.textSecondary)),
              const SizedBox(height: 16),
              // The per-redemption cashback amount is not returned by
              // any endpoint yet; show the pending state, not a number.
              const Text('本次消費的返現待確認，可在返現頁查看。',
                  textAlign: TextAlign.center,
                  style: TextStyle(color: CgColors.textSecondary)),
              const SizedBox(height: 24),
              if (!item.reviewed)
                SizedBox(
                  width: double.infinity,
                  child: ElevatedButton(
                    onPressed: () {
                      Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) =>
                              ReviewFormScreen(api: api, item: item)));
                    },
                    child: const Text('去評價'),
                  ),
                ),
              const SizedBox(height: 8),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text('返回錢包'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// Notifications: unread badge, mark-all-read, clear.
class NotificationsScreen extends StatefulWidget {
  const NotificationsScreen({super.key, required this.api});

  final ApiClient api;

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
          TextButton(
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
                  const Text('沒有新通知。'),
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
  bool _limitedNotify = true;
  String _lang = '繁體中文';

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('設置')),
      body: ListView(
        children: [
          SwitchListTile(
            title: const Text('限時搶購通知'),
            subtitle: Text(_limitedNotify ? '已開啟' : '已關閉'),
            value: _limitedNotify,
            onChanged: (v) async {
              try {
                await widget.api.post('/limited-time-notification',
                    query: {'enabled': '$v'});
                setState(() => _limitedNotify = v);
              } on ApiException catch (e) {
                if (context.mounted) showCgToast(context, e.message);
              }
            },
          ),
          ListTile(
            title: const Text('語言'),
            subtitle: Text(_lang),
            trailing: const Icon(Icons.chevron_right),
            onTap: () async {
              final picked = await showDialog<String>(
                context: context,
                builder: (c) => SimpleDialog(
                  title: const Text('選擇語言'),
                  children: ['繁體中文', 'English'].map((l) {
                    return SimpleDialogOption(
                      onPressed: () => Navigator.pop(c, l),
                      child: Text(l),
                    );
                  }).toList(),
                ),
              );
              if (picked != null) setState(() => _lang = picked);
            },
          ),
          ListTile(
            title: const Text('新手指南 / 常見問題'),
            trailing: const Icon(Icons.chevron_right),
            onTap: () => Navigator.of(context).pushNamed('/guide'),
          ),
          ListTile(
            title: const Text('關於 Coupon GO!'),
            subtitle: const Text('版本 v0.1.0'),
            onTap: () {},
          ),
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
      ),
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

/// Q仔 — local demo assistant (no backend model configured online;
/// answers from local rules, same as the MVP).
class QBotScreen extends StatefulWidget {
  const QBotScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<QBotScreen> createState() => _QBotScreenState();
}

class _QBotScreenState extends State<QBotScreen> {
  final _ctrl = TextEditingController();
  final _messages = <Map<String, String>>[
    {'role': 'bot', 'text': '你好，我係 Q仔！想搵咩優惠券？可以問我限時搶購、積分或者點用券。'},
  ];

  Future<void> _ask(String q) async {
    setState(() {
      _messages.add({'role': 'user', 'text': q});
      _ctrl.clear();
    });
    String answer;
    if (q.contains('限時')) {
      answer = '限時搶購有 13 張券，去「限時」頁睇下啦！';
    } else if (q.contains('積分')) {
      try {
        final p = await widget.api.points();
        answer = '你而家有 ${p['balance']} 分。領券 +10、核銷 +20、評價 +15，9 分可以換 9 分分券。';
      } on ApiException catch (_) {
        answer = '領券 +10、核銷 +20、評價 +15；9 分可以兌換 9 分分券。登錄後可以睇到你嘅積分。';
      }
    } else if (q.contains('點用') || q.contains('QR')) {
      answer = '去 Q Wallet 點「出示」，把 QR 碼畀店員掃，15 分鐘內有效。';
    } else {
      answer = '我可以幫你搵優惠券、睇積分、講解點用券。試下問「限時搶購有咩」或者「我有幾多分」。';
    }
    setState(() => _messages.add({'role': 'bot', 'text': answer}));
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Q仔'),
        actions: [
          IconButton(
            icon: const Icon(Icons.delete_outline),
            onPressed: () => setState(() {
              _messages.removeRange(1, _messages.length);
              _messages.add({'role': 'bot', 'text': '對話已清除，有咩可以幫你？'});
            }),
          ),
        ],
      ),
      body: Column(
        children: [
          Expanded(
            child: ListView.builder(
              padding: const EdgeInsets.all(12),
              itemCount: _messages.length,
              itemBuilder: (_, i) {
                final m = _messages[i];
                final isBot = m['role'] == 'bot';
                return Align(
                  alignment: isBot ? Alignment.centerLeft : Alignment.centerRight,
                  child: Container(
                    margin: const EdgeInsets.symmetric(vertical: 4),
                    padding: const EdgeInsets.all(12),
                    constraints: BoxConstraints(
                        maxWidth: MediaQuery.of(context).size.width * 0.75),
                    decoration: BoxDecoration(
                      color: isBot ? Colors.white : CgColors.primary,
                      borderRadius: BorderRadius.circular(12),
                    ),
                    child: Text(m['text']!,
                        style: TextStyle(
                            color: isBot ? CgColors.textPrimary : Colors.white)),
                  ),
                );
              },
            ),
          ),
          Padding(
            padding: const EdgeInsets.all(12),
            child: Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: _ctrl,
                    decoration: const InputDecoration(hintText: '問 Q仔…'),
                    onSubmitted: _ask,
                  ),
                ),
                const SizedBox(width: 8),
                IconButton(
                  icon: const Icon(Icons.send, color: CgColors.primary),
                  onPressed: () {
                    if (_ctrl.text.isNotEmpty) _ask(_ctrl.text);
                  },
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}

/// Auth: email login / register / 6-digit OTP.
class AuthScreen extends StatefulWidget {
  const AuthScreen({super.key, required this.api, required this.onAuthed});

  final ApiClient api;
  final VoidCallback onAuthed;

  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  final _email = TextEditingController();
  final _password = TextEditingController();
  final _name = TextEditingController();
  final _otp = TextEditingController();
  bool _isRegister = false;
  bool _awaitingOtp = false;
  bool _isVendor = false;

  Future<void> _submit() async {
    try {
      if (_awaitingOtp) {
        final data = await widget.api.verifyOtp(
            _email.text, _otp.text, 'register');
        await widget.api.saveToken(data['access_token'] as String);
        widget.onAuthed();
        return;
      }
      if (_isRegister) {
        await widget.api.register(_email.text, _password.text, _name.text,
            _isVendor ? 'vendor' : 'customer');
        setState(() => _awaitingOtp = true);
        if (mounted) showCgToast(context, '請輸入 6 位數驗證碼。');
      } else {
        await widget.api.login(_email.text, _password.text);
        widget.onAuthed();
      }
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: CgColors.primary,
      appBar: AppBar(title: Text(_isRegister ? '註冊' : '登錄')),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(24),
        child: Column(
          children: [
            Image.asset('assets/images/img_logo.png', height: 120,
                errorBuilder: (_, __, ___) => const SizedBox(height: 120)),
            const SizedBox(height: 8),
            const Text('請註冊或登入。',
                style: TextStyle(color: Colors.white, fontSize: 18)),
            const SizedBox(height: 24),
            if (_awaitingOtp)
              TextField(
                controller: _otp,
                keyboardType: TextInputType.number,
                maxLength: 6,
                decoration: const InputDecoration(hintText: '請輸入 6 位數驗證碼。'),
              )
            else ...[
              if (_isRegister)
                TextField(
                  controller: _name,
                  decoration: const InputDecoration(hintText: '姓名'),
                ),
              const SizedBox(height: 12),
              TextField(
                controller: _email,
                keyboardType: TextInputType.emailAddress,
                decoration: const InputDecoration(hintText: '電郵'),
              ),
              const SizedBox(height: 12),
              TextField(
                controller: _password,
                obscureText: true,
                decoration: const InputDecoration(hintText: '密碼（至少 6 個字符，含特殊字符）'),
              ),
              if (_isRegister) ...[
                const SizedBox(height: 12),
                SwitchListTile(
                  title: const Text('註冊為商戶', style: TextStyle(color: Colors.white)),
                  value: _isVendor,
                  onChanged: (v) => setState(() => _isVendor = v),
                ),
              ],
            ],
            const SizedBox(height: 16),
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                style: ElevatedButton.styleFrom(backgroundColor: Colors.white),
                onPressed: _submit,
                child: Text(
                  _awaitingOtp ? '驗證' : _isRegister ? '註冊' : '使用電子郵件帳戶繼續',
                  style: const TextStyle(color: CgColors.primary),
                ),
              ),
            ),
            TextButton(
              onPressed: () => setState(() {
                _isRegister = !_isRegister;
                _awaitingOtp = false;
              }),
              child: Text(
                _isRegister ? '已有帳戶？去登錄' : '未有帳戶？去註冊',
                style: const TextStyle(color: Colors.white),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
