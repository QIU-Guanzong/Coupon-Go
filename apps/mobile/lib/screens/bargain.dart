import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../language.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import 'account.dart';

const _isolatedPublicSession = bool.fromEnvironment('PUBLIC_TRIAL');

/// Bargain 2C screens (line E): list (§4.1) / campaign start (§4.2),
/// my bargain (§4.3) + friend help (§4.4), group-formed result.
/// Amounts are HKD; all prices/statuses come from the server.
///
/// Flow: pick a campaign -> start (stock locked, prepay of the original
/// price) -> share the link -> friends help cut the price -> floor price
/// or helper limit reached -> buy at the cut price -> package coupon
/// lands in Q Wallet (existing redeem loop). Timeout / unformed = full
/// refund + stock released. Terminal copy never suggests a refund is
/// withheld or a fee deducted.
///
/// 未登录可浏览砍价列表与详情（只读）；点「发起砍价 / 砍一刀」时先登录，
/// 登录后回原界面。
Future<bool> _requireLogin(BuildContext context, ApiClient api) async {
  if (api.isLoggedIn) return true;
  final result = await Navigator.of(context).push<bool>(
    MaterialPageRoute(
      builder: (_) => AuthScreen(
        api: api,
        onAuthed: () => Navigator.of(context).pop(true),
      ),
    ),
  );
  return result == true && api.isLoggedIn;
}

/// List (§4.1): 2-col cards, same as the home cards, plus
/// 「已有 N 人在砍」. My sessions stay on top.
class BargainListScreen extends StatefulWidget {
  const BargainListScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<BargainListScreen> createState() => _BargainListScreenState();
}

class _BargainListScreenState extends State<BargainListScreen> {
  List<BargainCampaign> _campaigns = [];
  List<BargainSession> _mine = [];
  bool _loading = true;
  bool _error = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    if (mounted) setState(() => _loading = true);
    try {
      final list = await widget.api.bargainCampaigns();
      var mine = <BargainSession>[];
      if (widget.api.isLoggedIn) {
        try {
          mine = (await widget.api.myBargainSessions())
              .map((e) => BargainSession.fromJson(e as Map<String, dynamic>))
              .toList();
        } on ApiException {
          mine = <BargainSession>[];
        }
      }
      if (!mounted) return;
      setState(() {
        _campaigns = list
            .map((e) => BargainCampaign.fromJson(e as Map<String, dynamic>))
            .toList();
        _mine = mine;
        _loading = false;
        _error = false;
      });
    } on ApiException catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = true;
        });
      }
    }
  }

  void _openCampaign(BargainCampaign c) {
    Navigator.of(context)
        .push(MaterialPageRoute(
            builder: (_) => BargainCampaignScreen(api: widget.api, campaign: c)))
        .then((_) => _load());
  }

  void _openSession(BargainSession s) {
    Navigator.of(context)
        .push(MaterialPageRoute(
            builder: (_) =>
                BargainDetailScreen(api: widget.api, sessionId: s.id)))
        .then((_) => _load());
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('砍價')),
      body: _loading
          ? const _LoadingList()
          : _error
              ? _ErrorView(onRetry: _load)
              : _campaigns.isEmpty && _mine.isEmpty
                  ? _EmptyView(
                      message: '暫時沒有砍價活動。',
                      actionLabel: '重新整理',
                      onAction: _load,
                    )
                  : RefreshIndicator(
                      onRefresh: _load,
                      child: CustomScrollView(
                        slivers: [
                          if (_mine.isNotEmpty)
                            SliverToBoxAdapter(
                              child: Padding(
                                padding:
                                    const EdgeInsets.fromLTRB(16, 12, 16, 0),
                                child: Column(
                                  crossAxisAlignment: CrossAxisAlignment.start,
                                  children: [
                                    const Text('我的砍價',
                                        style: TextStyle(
                                            fontWeight: FontWeight.bold,
                                            fontSize: 16)),
                                    ..._mine.map((s) => Card(
                                          margin:
                                              const EdgeInsets.only(top: 8),
                                          child: ListTile(
                                            leading: const Icon(
                                                Icons.content_cut,
                                                color: CgColors.primary),
                                            title: Text(s.campaign?.name ??
                                                '砍價 #${s.id}'),
                                            subtitle: Text(
                                                '${s.statusText} · 現價 ${formatHkd(s.currentPrice)} · ${s.helperCount}/${s.helperLimit} 人助力'),
                                            trailing: const Icon(
                                                Icons.chevron_right),
                                            onTap: () => _openSession(s),
                                          ),
                                        )),
                                  ],
                                ),
                              ),
                            ),
                          SliverToBoxAdapter(
                            child: Padding(
                              padding: const EdgeInsets.fromLTRB(16, 16, 16, 8),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(context.tr('砍價套餐', 'Bargain offers'),
                                      style: const TextStyle(
                                          fontWeight: FontWeight.bold,
                                          fontSize: 16)),
                                  const SizedBox(height: 4),
                                  Text(_isolatedPublicSession
                                      ? context.tr('數量有限，先到先得。', 'Limited stock, first come first served.')
                                      : context.tr('分享助力砍價，數量有限，先到先得。', 'Share a bargain to lower the price. Stock is limited.'),
                                      style: const TextStyle(color: Colors.grey)),
                                  const SizedBox(height: 4),
                                  Text(
                                      context.tr('發起後鎖定 1 份庫存，不會扣款。', 'Starting reserves one item. You will not be charged.'),
                                      style: const TextStyle(
                                          color: Colors.grey, fontSize: 12)),
                                ],
                              ),
                            ),
                          ),
                          if (_campaigns.isEmpty)
                            const SliverToBoxAdapter(
                              child: Padding(
                                padding: EdgeInsets.all(16),
                                child: Text('暫時沒有砍價活動。',
                                    style: TextStyle(
                                        color: CgColors.textSecondary)),
                              ),
                            )
                          else
                            SliverPadding(
                              padding:
                                  const EdgeInsets.symmetric(horizontal: 12),
                              sliver: SliverGrid(
                                gridDelegate:
                                    const SliverGridDelegateWithFixedCrossAxisCount(
                                  crossAxisCount: 2,
                                  childAspectRatio: 0.62,
                                  crossAxisSpacing: 8,
                                  mainAxisSpacing: 8,
                                ),
                                delegate: SliverChildBuilderDelegate(
                                  (_, i) => _BargainCard(
                                    campaign: _campaigns[i],
                                    onTap: () =>
                                        _openCampaign(_campaigns[i]),
                                  ),
                                  childCount: _campaigns.length,
                                ),
                              ),
                            ),
                          const SliverToBoxAdapter(
                              child: SizedBox(height: 24)),
                        ],
                      ),
                    ),
    );
  }
}

/// 砍价卡片（列表 2 列 / 首页横滑共用样式）：商户图、套餐名、当前价大、
/// 底价小灰、已有 N 人在砍、剩余时间、「去砍价」强调色实心。
class _BargainCard extends StatelessWidget {
  const _BargainCard({required this.campaign, required this.onTap});

  final BargainCampaign campaign;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final timeLeft = campaign.timeLeft;
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AspectRatio(
              aspectRatio: 16 / 9,
              child: CatalogPhoto(url: campaign.image),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 8, 10, 0),
              child: Text(
                campaign.name,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                    fontWeight: FontWeight.w600, fontSize: 14),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 4, 10, 0),
              child: Text(
                campaign.shopName,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(
                    color: CgColors.textSecondary, fontSize: 12),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 4, 10, 0),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(
                    formatHkd(campaign.originalPrice),
                    style: const TextStyle(
                        color: CgColors.primary,
                        fontWeight: FontWeight.bold,
                        fontSize: 16,
                        fontFeatures: [FontFeature.tabularFigures()]),
                  ),
                  const SizedBox(width: 4),
                  Flexible(
                    child: Text(
                      '底價 ${formatHkd(campaign.floorPrice)}',
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          color: CgColors.textSecondary, fontSize: 11),
                    ),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 2, 10, 0),
              child: Text(
                '已有 ${campaign.helperCount} 人在砍'
                '${timeLeft == null ? '' : ' · 剩 ${formatCountdown(timeLeft)}'}',
                style: const TextStyle(
                    color: CgColors.textSecondary, fontSize: 12),
              ),
            ),
            const Spacer(),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 0, 10, 10),
              child: SizedBox(
                width: double.infinity,
                height: 44,
                child: ElevatedButton(
                  style: ElevatedButton.styleFrom(
                    backgroundColor: CgColors.orange,
                    foregroundColor: Colors.white,
                    padding: EdgeInsets.zero,
                  ),
                  onPressed: campaign.isSoldOut ? null : onTap,
                  child: Text(campaign.isSoldOut ? '已售罄' : '去砍價'),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// Campaign detail before starting (§4.2): big image, original price,
/// floor price (prominent), 3-step rules, stock note, prepay note,
/// 「立即发起砍价」.
class BargainCampaignScreen extends StatefulWidget {
  const BargainCampaignScreen(
      {super.key, required this.api, required this.campaign});

  final ApiClient api;
  final BargainCampaign campaign;

  @override
  State<BargainCampaignScreen> createState() => _BargainCampaignScreenState();
}

class _BargainCampaignScreenState extends State<BargainCampaignScreen> {
  bool _busy = false;

  Future<void> _start() async {
    final c = widget.campaign;
    if (_busy || c.isSoldOut) return;
    final ok = await _requireLogin(context, widget.api);
    if (!ok || !mounted) return;
    setState(() => _busy = true);
    try {
      final data = await widget.api.startBargain(c.id);
      if (!mounted) return;
      final session =
          BargainSession.fromJson(data['session'] as Map<String, dynamic>);
      showCgToast(context, '砍價已發起，分享給好友一起砍到底價');
      await Navigator.of(context).pushReplacement(MaterialPageRoute(
          builder: (_) =>
              BargainDetailScreen(api: widget.api, sessionId: session.id)));
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _busy = false);
        showCgToast(context, e.message);
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.campaign;
    final timeLeft = c.timeLeft;
    return Scaffold(
      appBar: AppBar(title: const Text('砍價')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: SizedBox(
              height: 220,
              width: double.infinity,
              child: CatalogPhoto(url: c.image),
            ),
          ),
          const SizedBox(height: 16),
          Text(c.name,
              style:
                  const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          Text('${c.shopName} · ${c.personCount} 人餐',
              style: const TextStyle(color: CgColors.textSecondary)),
          const SizedBox(height: 16),
          Row(
            crossAxisAlignment: CrossAxisAlignment.end,
            children: [
              const Text('底價 ',
                  style: TextStyle(
                      color: CgColors.textSecondary, fontSize: 16)),
              Text(formatHkd(c.floorPrice),
                  style: const TextStyle(
                      color: CgColors.primary,
                      fontWeight: FontWeight.bold,
                      fontSize: 32,
                      fontFeatures: [FontFeature.tabularFigures()])),
              const SizedBox(width: 10),
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text('原價 ${formatHkd(c.originalPrice)}',
                    style: const TextStyle(
                        decoration: TextDecoration.lineThrough,
                        color: CgColors.textSecondary)),
              ),
            ],
          ),
          if (timeLeft != null)
            Padding(
              padding: const EdgeInsets.only(top: 4),
              child: Text('剩餘時間 ${formatCountdown(timeLeft)}',
                  style: const TextStyle(color: CgColors.textSecondary)),
            ),
          const SizedBox(height: 16),
          const Text('砍價規則', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          const _RuleStep(
              icon: Icons.payments,
              text: '付款後發起砍價，先付原價鎖定庫存'),
          const _RuleStep(
              icon: Icons.share, text: '分享給好友助力，每人幫你砍一刀'),
          const _RuleStep(
              icon: Icons.replay,
              text: '價格降到底價或時間到，差額自動退回'),
          const SizedBox(height: 16),
          Text(
              '數量有限，先到先得，剩餘庫存 ${c.stockLeft} 份',
              style: const TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          Text('已有 ${c.helperCount} 人在砍 · ${c.helperLimit} 人成團',
              style: const TextStyle(color: CgColors.textSecondary)),
          const SizedBox(height: 24),
          Text(context.tr('預付金額為 ${formatHkd(c.originalPrice)}，差額按砍價結果處理。',
              'The recorded prepayment is ${formatHkd(c.originalPrice)}. Any price difference depends on the bargain result.'),
              textAlign: TextAlign.center,
              style: const TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          SizedBox(
            width: double.infinity,
            height: 48,
            child: ElevatedButton(
              style: ElevatedButton.styleFrom(
                backgroundColor: CgColors.orange,
                foregroundColor: Colors.white,
              ),
              onPressed: (_busy || c.isSoldOut) ? null : _start,
              child: Text(_busy
                  ? context.tr('處理中…', 'Processing…')
                  : c.isSoldOut
                      ? context.tr('已售罄', 'Sold out')
                      : context.tr('立即發起砍價', 'Start bargain')),
            ),
          ),
          const SizedBox(height: 8),
          Text(context.tr('此操作只更新訂單記錄，不會扣款。', 'This only updates the order record and will not charge you.'),
              textAlign: TextAlign.center,
              style: const TextStyle(color: CgColors.textSecondary, fontSize: 12)),
        ],
      ),
    );
  }
}

class _RuleStep extends StatelessWidget {
  const _RuleStep({required this.icon, required this.text});

  final IconData icon;
  final String text;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(top: 6),
      child: Row(
        children: [
          Icon(icon, color: CgColors.primary, size: 20),
          const SizedBox(width: 10),
          Expanded(child: Text(text)),
        ],
      ),
    );
  }
}

/// Detail = my bargain (§4.3) + friend help page (§4.4) in one.
/// [sessionId] comes from a fresh start or from the shared link.
class BargainDetailScreen extends StatefulWidget {
  const BargainDetailScreen(
      {super.key, required this.api, required this.sessionId});

  final ApiClient api;
  final int sessionId;

  @override
  State<BargainDetailScreen> createState() => _BargainDetailScreenState();
}

class _BargainDetailScreenState extends State<BargainDetailScreen> {
  BargainSession? _session;
  bool _loading = true;
  bool _error = false;
  bool _busy = false;
  Timer? _tick;
  int _secondsLeft = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _tick?.cancel();
    super.dispose();
  }

  void _startTick() {
    _tick?.cancel();
    if (_secondsLeft <= 0) return;
    _tick = Timer.periodic(const Duration(seconds: 1), (_) {
      if (!mounted) return;
      if (_secondsLeft <= 0) {
        _tick?.cancel();
        return;
      }
      setState(() => _secondsLeft--);
    });
  }

  Future<void> _load() async {
    if (mounted) setState(() => _loading = true);
    try {
      final data = await widget.api.bargainSession(widget.sessionId);
      if (!mounted) return;
      final session = BargainSession.fromJson(data);
      setState(() {
        _session = session;
        _loading = false;
        _error = false;
        _secondsLeft = session.secondsLeft > 0
            ? session.secondsLeft
            : (session.expiresAt == null
                ? 0
                : session.expiresAt!
                    .difference(DateTime.now())
                    .inSeconds
                    .clamp(0, 1 << 31));
      });
      _startTick();
    } on ApiException catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = true;
        });
      }
    }
  }

  Future<void> _help() async {
    final s = _session;
    if (s == null || _busy) return;
    final ok = await _requireLogin(context, widget.api);
    if (!ok || !mounted) return;
    if (s.alreadyHelped) {
      showCgToast(context, '你已經幫 TA 砍過啦');
      return;
    }
    setState(() => _busy = true);
    try {
      final deviceId = await widget.api.deviceId();
      final data = await widget.api.helpBargain(s.id, deviceId);
      if (!mounted) return;
      final updated =
          BargainSession.fromJson(data['session'] as Map<String, dynamic>);
      final cut = (data['cut_amount'] as num?)?.toDouble() ?? 0.0;
      setState(() => _session = updated);
      await _showHelpSuccess(cut, updated);
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// 助力成功反馈：缩放动效 300ms（≤ 800ms），显示砍了多少，
  /// 下方「我也要发起砍价」。
  Future<void> _showHelpSuccess(double cut, BargainSession updated) {
    return showDialog<void>(
      context: context,
      barrierDismissible: true,
      builder: (d) => TweenAnimationBuilder<double>(
        tween: Tween(begin: 0.8, end: 1.0),
        duration: const Duration(milliseconds: 300),
        curve: Curves.easeOutBack,
        builder: (ctx, scale, child) =>
            Transform.scale(scale: scale, child: child),
        child: AlertDialog(
          title: const Text('砍價成功'),
          content: Text('你幫 TA 砍了 ${formatHkd(cut)}！'
              '${updated.status == 'success' ? '已砍到底價。' : ''}'),
          actions: [
            TextButton(
              onPressed: () => Navigator.of(d).pop(),
              child: const Text('知道了'),
            ),
            ElevatedButton(
              onPressed: () {
                Navigator.of(d).pop();
                final campaign = updated.campaign;
                if (campaign != null) {
                  Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => BargainCampaignScreen(
                          api: widget.api, campaign: campaign)));
                } else {
                  Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => BargainListScreen(api: widget.api)));
                }
              },
              child: const Text('我也要發起砍價'),
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _purchase() async {
    final s = _session;
    if (s == null || _busy) return;
    // 现在购买：以当前价结算，差额退回，退款金额在确认页明示。
    final refund =
        (s.paidAmount - s.currentPrice).clamp(0.0, 1 << 30).toDouble();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(context.tr('現在購買', 'Buy now')),
        content: Text(context.tr(
            '按現價 ${formatHkd(s.currentPrice)} 確認。差價 ${formatHkd(refund)} 會反映在訂單記錄，不會扣款或返還款項。',
            'Confirm at the current price of ${formatHkd(s.currentPrice)}. The ${formatHkd(refund)} difference will be recorded without a charge or money transfer.',
        )),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(ctx).pop(false),
            child: const Text('取消'),
          ),
          ElevatedButton(
            onPressed: () => Navigator.of(ctx).pop(true),
            child: Text(context.tr('確認購買', 'Confirm purchase')),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      final data = await widget.api.purchaseBargain(s.id);
      if (!mounted) return;
      final updated =
          BargainSession.fromJson(data['session'] as Map<String, dynamic>);
      await Navigator.of(context).push(MaterialPageRoute(
          builder: (_) => BargainResultScreen(
              api: widget.api, session: updated, purchase: data)));
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _share() {
    final s = _session;
    if (s == null) return;
    Clipboard.setData(ClipboardData(text:
        '幫我砍一刀！${s.campaign?.name ?? ''} 原價 ${formatHkd(s.originalPrice)}，'
        '現價 ${formatHkd(s.currentPrice)}，砍到底價 ${formatHkd(s.floorPrice)} 就能拿套餐：${s.shareLink}'));
    showDialog<void>(
      context: context,
      builder: (d) => AlertDialog(
        title: const Text('分享給好友助力'),
        content: SelectableText(
            '連結已複製，發給好友打開 App 即可助力：\n${s.shareLink}'),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(d).pop(),
            child: const Text('知道了'),
          ),
        ],
      ),
    );
  }

  void _openOtherBargains() {
    Navigator.of(context).pushReplacement(MaterialPageRoute(
        builder: (_) => BargainListScreen(api: widget.api)));
  }

  @override
  Widget build(BuildContext context) {
    final s = _session;
    if (_loading) {
      return Scaffold(
        appBar: AppBar(title: const Text('砍價')),
        body: const _LoadingList(),
      );
    }
    if (_error || s == null) {
      return Scaffold(
        appBar: AppBar(title: const Text('砍價')),
        body: _ErrorView(onRetry: _load),
      );
    }
    final isOwner = s.isOwner;
    final ended = s.status == 'failed' || s.status == 'expired';
    final success = s.status == 'success';
    final active = s.status == 'active';
    return Scaffold(
      appBar: AppBar(title: const Text('砍價')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            // 发起人（好友助力页可见）
            if (s.initiatorName.isNotEmpty)
              Row(
                children: [
                  _Avatar(
                      name: s.initiatorName, avatar: s.initiatorAvatar),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Text(context.tr('${s.initiatorName} 邀請你幫忙砍價',
                        '${s.initiatorName} invited you to help lower the price'),
                        style: const TextStyle(fontWeight: FontWeight.w600)),
                  ),
                ],
              ),
            if (s.initiatorName.isNotEmpty) const SizedBox(height: 12),
            Text(s.campaign?.name ?? '砍價',
                style: const TextStyle(
                    fontWeight: FontWeight.bold, fontSize: 20)),
            const SizedBox(height: 4),
            Text(
                '${s.campaign?.shopName ?? ''} · ${s.campaign?.personCount ?? 2} 人餐'),
            const SizedBox(height: 16),
            // 价格 + 进度（原价 → 底价；已砍多少、还差多少取接口字段）
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                Text(formatHkd(s.currentPrice),
                    style: const TextStyle(
                        color: CgColors.primary,
                        fontWeight: FontWeight.bold,
                        fontSize: 32,
                        fontFeatures: [FontFeature.tabularFigures()])),
                const SizedBox(width: 8),
                Padding(
                  padding: const EdgeInsets.only(bottom: 6),
                  child: Text(
                    '原價 ${formatHkd(s.originalPrice)} · 底價 ${formatHkd(s.floorPrice)}',
                    style: const TextStyle(color: Colors.grey),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 8),
            ClipRRect(
              borderRadius: BorderRadius.circular(4),
              child: LinearProgressIndicator(
                  value: s.cutProgress,
                  minHeight: 8,
                  backgroundColor: Colors.grey.shade300,
                  color: CgColors.primary),
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Text('已砍 ${formatHkd(s.cutTotal)}',
                    style: const TextStyle(fontWeight: FontWeight.bold)),
                const SizedBox(width: 12),
                Text('還差 ${formatHkd(s.remainingToFloor)} 到底價',
                    style: const TextStyle(color: CgColors.textSecondary)),
                const Spacer(),
                Text('${s.helperCount}/${s.helperLimit} 人助力'),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Chip(label: Text(s.statusText)),
                const SizedBox(width: 8),
                if (active && _secondsLeft > 0)
                  Text('倒計時 ${formatCountdown(Duration(seconds: _secondsLeft))}',
                      style: const TextStyle(color: Colors.grey)),
                if (s.stockLocked && active)
                  const Padding(
                    padding: EdgeInsets.only(left: 8),
                    child: Text('已為你鎖定 1 份庫存',
                        style: TextStyle(color: Colors.grey)),
                  ),
              ],
            ),
            // 终态
            if (success) ...[
              const SizedBox(height: 16),
              _TerminalCard(
                icon: Icons.check_circle,
                title: '已購買，套餐券在錢包',
                lines: [
                  if (s.refunds.isNotEmpty)
                    ...s.refunds.map((r) => '${r.reasonText} ${formatHkd(r.amount)} · ${r.statusText}'),
                  context.tr('退款只更新訂單記錄，不會退回款項。', 'A refund only updates the order record; no money is returned.'),
                ],
              ),
            ],
            if (ended) ...[
              const SizedBox(height: 16),
              _TerminalCard(
                icon: Icons.info_outline,
                title: '砍價已結束',
                lines: [
                  ...s.refunds.map((r) => '${r.reasonText} ${formatHkd(r.amount)} · ${r.statusText}'),
                  context.tr('退款只更新訂單記錄，不會退回款項。', 'A refund only updates the order record; no money is returned.'),
                ],
              ),
              const SizedBox(height: 16),
              SizedBox(
                width: double.infinity,
                height: 48,
                child: OutlinedButton(
                  onPressed: _openOtherBargains,
                  child: const Text('去看看其他砍價'),
                ),
              ),
            ],
            if (active && !_isolatedPublicSession) ...[
              const SizedBox(height: 16),
              // 发起人：分享为主；好友：帮忙砍一刀为主；归属未知时两者都显示。
              if (isOwner != false)
                SizedBox(
                  width: double.infinity,
                  height: 48,
                  child: ElevatedButton.icon(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: CgColors.orange,
                      foregroundColor: Colors.white,
                    ),
                    onPressed: _share,
                    icon: const Icon(Icons.share),
                    label: const Text('分享給好友'),
                  ),
                ),
              if (isOwner != true) ...[
                if (isOwner != false) const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  height: 48,
                  child: ElevatedButton.icon(
                    onPressed: (s.alreadyHelped || _busy) ? null : _help,
                    icon: const Icon(Icons.content_cut),
                    label: Text(
                        s.alreadyHelped ? '你已經幫 TA 砍過啦' : '幫 TA 砍一刀'),
                  ),
                ),
                if (s.alreadyHelped)
                  const Padding(
                    padding: EdgeInsets.only(top: 8),
                    child: Text('你已經幫 TA 砍過啦',
                        textAlign: TextAlign.center,
                        style: TextStyle(color: CgColors.textSecondary)),
                  ),
              ],
              if (s.canPurchase) ...[
                const SizedBox(height: 12),
                SizedBox(
                  width: double.infinity,
                  height: 48,
                  child: OutlinedButton(
                    onPressed: _busy ? null : _purchase,
                    child: Text(
                        context.tr('現在購買（以 ${formatHkd(s.currentPrice)} 結算）',
                            'Buy now for ${formatHkd(s.currentPrice)}')),
                  ),
                ),
              ],
            ],
            const SizedBox(height: 16),
            Text(context.tr('助力記錄', 'Bargain help history'),
                style: const TextStyle(fontWeight: FontWeight.bold)),
            if (s.helps.isEmpty)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(_isolatedPublicSession
                    ? context.tr('暫無助力記錄。', 'No help activity yet.')
                    : context.tr('分享給好友，每人幫你砍一刀。', 'Share with friends so each one can help lower the price.')),
              ),
            ...s.helps.map((hp) => ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: _Avatar(name: hp.helperName, avatar: hp.avatar),
                  title: Text(hp.helperName),
                  subtitle: Text(
                      '${hp.createdAt == null ? '' : formatDateTime(hp.createdAt)}${hp.isNewUser ? ' · 新用戶助力' : ''}'),
                  trailing: Text(
                      '−${formatHkd(hp.cutAmount)}',
                      style: const TextStyle(
                          color: CgColors.primary,
                          fontWeight: FontWeight.bold,
                          fontFeatures: [FontFeature.tabularFigures()])),
                )),
            const SizedBox(height: 8),
            const Text(
                '每人每單限助一次；新用戶（註冊 7 天內）助力砍得更多。未成團 / 超時全額原路退款。',
                style: TextStyle(color: Colors.grey, fontSize: 12)),
          ],
        ),
      ),
    );
  }
}

/// 终态卡片：成功 / 已结束 + 退款明细。
class _TerminalCard extends StatelessWidget {
  const _TerminalCard(
      {required this.icon, required this.title, required this.lines});

  final IconData icon;
  final String title;
  final List<String> lines;

  @override
  Widget build(BuildContext context) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(icon, color: CgColors.primary, size: 28),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(title,
                      style: const TextStyle(
                          fontWeight: FontWeight.bold, fontSize: 16)),
                ),
              ],
            ),
            ...lines.map((l) => Padding(
                  padding: const EdgeInsets.only(top: 6),
                  child: Text(l,
                      style: const TextStyle(color: CgColors.textSecondary)),
                )),
          ],
        ),
      ),
    );
  }
}

/// 头像：有图用图，匿名用首字圆圈。
class _Avatar extends StatelessWidget {
  const _Avatar({required this.name, required this.avatar});

  final String name;
  final String avatar;

  @override
  Widget build(BuildContext context) {
    final initial = name.trim().isEmpty ? '匿' : name.characters.first;
    if (avatar.startsWith('http://') || avatar.startsWith('https://')) {
      return CircleAvatar(
        backgroundImage: NetworkImage(avatar),
        onBackgroundImageError: (_, __) {},
        child: Text(initial),
      );
    }
    return CircleAvatar(
      backgroundColor: CgColors.primary,
      foregroundColor: Colors.white,
      child: Text(initial),
    );
  }
}



/// Group-formed result page: package coupon issued + price-cut refund.
class BargainResultScreen extends StatelessWidget {
  const BargainResultScreen(
      {super.key, required this.api, required this.session, required this.purchase});

  final ApiClient api;
  final BargainSession session;
  final Map<String, dynamic> purchase;

  @override
  Widget build(BuildContext context) {
    final refund = purchase['refund'] as Map<String, dynamic>?;
    final refundAmount = refund == null
        ? 0.0
        : (refund['amount'] as num?)?.toDouble() ?? 0.0;
    return Scaffold(
      appBar: AppBar(title: const Text('砍價成功')),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Icon(Icons.check_circle,
                color: CgColors.primary, size: 64),
            const SizedBox(height: 12),
            Text('成團！以 ${formatHkd(session.currentPrice)} 拿下套餐',
                style: const TextStyle(
                    fontWeight: FontWeight.bold, fontSize: 20)),
            const SizedBox(height: 8),
            Text(session.campaign?.name ?? ''),
            const SizedBox(height: 16),
            const Card(
              child: ListTile(
                leading:
                    Icon(Icons.card_giftcard, color: CgColors.primary),
                title: Text('套餐券已存入 Q Wallet'),
                subtitle: Text('到店出示 QR 碼即可核銷（與一般優惠券相同流程）。'),
              ),
            ),
            if (refundAmount > 0)
              Card(
                child: ListTile(
                  leading:
                      const Icon(Icons.replay, color: CgColors.primary),
                  title: Text('差價 ${formatHkd(refundAmount)} · ${BargainRefund.fromJson(refund!).statusText}'),
                  subtitle: Text(context.tr('退款只更新訂單記錄，不會退回款項。', 'A refund only updates the order record; no money is returned.')),
                ),
              ),
            const Spacer(),
            SizedBox(
              width: double.infinity,
              height: 56,
              child: ElevatedButton(
                onPressed: () =>
                    Navigator.of(context).popUntil((r) => r.isFirst),
                child: const Text('返回錢包查看套餐券'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 加载态：骨架屏，不用转圈。
class _LoadingList extends StatelessWidget {
  const _LoadingList();

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(12),
      children: const [
        SkeletonBox(height: 72),
        SizedBox(height: 12),
        SkeletonBox(height: 72),
        SizedBox(height: 12),
        SkeletonBox(height: 72),
      ],
    );
  }
}

/// 空态：一句话 + 一个动作按钮。
class _EmptyView extends StatelessWidget {
  const _EmptyView(
      {required this.message, required this.actionLabel, required this.onAction});

  final String message;
  final String actionLabel;
  final VoidCallback onAction;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text(message, textAlign: TextAlign.center),
            const SizedBox(height: 16),
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                  minimumSize: const Size(160, 48)),
              onPressed: onAction,
              child: Text(actionLabel),
            ),
          ],
        ),
      ),
    );
  }
}

/// 错误态：一句话 + 重试。
class _ErrorView extends StatelessWidget {
  const _ErrorView({required this.onRetry});

  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.wifi_off, size: 48, color: CgColors.textSecondary),
            const SizedBox(height: 12),
            const Text('網絡不穩定，請重試', textAlign: TextAlign.center),
            const SizedBox(height: 16),
            ElevatedButton(
              style: ElevatedButton.styleFrom(
                  minimumSize: const Size(160, 48)),
              onPressed: onRetry,
              child: const Text('重試'),
            ),
          ],
        ),
      ),
    );
  }
}
