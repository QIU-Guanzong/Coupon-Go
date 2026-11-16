import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';

/// Bargain 2C screens (line E): list / start, share + help progress,
/// group-formed result. Amounts are HKD.
///
/// Flow: pick a limited package -> start (stock locked, simulated
/// prepay of the original price) -> share the link -> friends help cut
/// the price -> floor price or helper limit reached -> buy at the cut
/// price -> package coupon lands in Q Wallet (existing redeem loop).
/// Timeout / unformed = full refund + stock released (see the refund
/// card on the detail page). No Dutch-auction time decay, no share
/// commission, no 30% fee, no "一經售出不退不換".
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

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
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
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _loading = false);
        showCgToast(context, e.message);
      }
    }
  }

  Future<void> _start(BargainCampaign c) async {
    if (!widget.api.isLoggedIn) {
      showCgToast(context, '請先登錄再發起砍價');
      return;
    }
    try {
      final data = await widget.api.startBargain(c.id);
      if (!mounted) return;
      final session =
          BargainSession.fromJson(data['session'] as Map<String, dynamic>);
      showCgToast(context, '砍價已發起，分享給好友一起砍到底價');
      await Navigator.of(context).push(MaterialPageRoute(
          builder: (_) =>
              BargainDetailScreen(api: widget.api, sessionId: session.id)));
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('砍價')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(12),
                children: [
                  const Text('分享助力砍價，越砍越便宜，數量有限，先到先得。',
                      style: TextStyle(color: Colors.grey)),
                  const SizedBox(height: 4),
                  const Text(
                      '發起即鎖定 1 份庫存；未成團 / 超時全額原路退款（1–3 個工作日）；成團後退回砍價差價。',
                      style: TextStyle(color: Colors.grey, fontSize: 12)),
                  const SizedBox(height: 12),
                  if (_mine.isNotEmpty) ...[
                    const Text('我的砍價',
                        style: TextStyle(fontWeight: FontWeight.bold)),
                    ..._mine.map((s) => Card(
                          margin: const EdgeInsets.only(top: 8),
                          child: ListTile(
                            leading: const Icon(Icons.content_cut,
                                color: CgColors.primary),
                            title: Text(
                                s.campaign?.name ?? '砍價 #${s.id}'),
                            subtitle: Text(
                                '${s.statusText} · 現價 \$${s.currentPrice.toStringAsFixed(0)} · ${s.helperCount}/${s.helperLimit} 人助力'),
                            trailing: const Icon(Icons.chevron_right),
                            onTap: () => Navigator.of(context)
                                .push(MaterialPageRoute(
                                    builder: (_) => BargainDetailScreen(
                                        api: widget.api, sessionId: s.id)))
                                .then((_) => _load()),
                          ),
                        )),
                    const SizedBox(height: 16),
                  ],
                  const Text('砍價套餐',
                      style: TextStyle(fontWeight: FontWeight.bold)),
                  if (_campaigns.isEmpty)
                    const Padding(
                      padding: EdgeInsets.only(top: 8),
                      child: Text('暫時沒有砍價活動。'),
                    ),
                  ..._campaigns.map((c) => Card(
                        margin: const EdgeInsets.only(top: 8),
                        child: Padding(
                          padding: const EdgeInsets.all(12),
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(c.name,
                                  style: const TextStyle(
                                      fontWeight: FontWeight.bold,
                                      fontSize: 16)),
                              const SizedBox(height: 4),
                              Text(
                                  '${c.shopName} · ${c.personCount} 人餐${c.items.isNotEmpty ? ' · ${c.items}' : ''}'),
                              const SizedBox(height: 8),
                              Row(
                                children: [
                                  Text(
                                    '底價 \$${c.floorPrice.toStringAsFixed(0)}',
                                    style: const TextStyle(
                                        color: CgColors.primary,
                                        fontWeight: FontWeight.bold,
                                        fontSize: 18),
                                  ),
                                  const SizedBox(width: 8),
                                  Text(
                                    '\$${c.originalPrice.toStringAsFixed(0)}',
                                    style: const TextStyle(
                                        decoration:
                                            TextDecoration.lineThrough,
                                        color: Colors.grey),
                                  ),
                                  const Spacer(),
                                  Text(c.isSoldOut
                                      ? '已售罄'
                                      : '剩 ${c.stockLeft}/${c.stockTotal} 份'),
                                ],
                              ),
                              const SizedBox(height: 8),
                              SizedBox(
                                width: double.infinity,
                                height: 44,
                                child: ElevatedButton(
                                  onPressed: c.isSoldOut
                                      ? null
                                      : () => _start(c),
                                  child: Text(c.isSoldOut
                                      ? '已售罄'
                                      : '發起砍價（${c.helperLimit} 人成團）'),
                                ),
                              ),
                            ],
                          ),
                        ),
                      )),
                ],
              ),
            ),
    );
  }
}

/// Detail = start page + share page + help progress in one.
/// [sessionId] comes from a fresh start or from the shared link
/// (coupon-go://bargain?session=<id>).
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
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await widget.api.bargainSession(widget.sessionId);
      if (!mounted) return;
      setState(() {
        _session = BargainSession.fromJson(data);
        _loading = false;
      });
    } on ApiException catch (e) {
      if (mounted) {
        setState(() => _loading = false);
        showCgToast(context, e.message);
      }
    }
  }

  Future<void> _help() async {
    final s = _session;
    if (s == null || _busy) return;
    if (!widget.api.isLoggedIn) {
      showCgToast(context, '請先登錄再助力');
      return;
    }
    setState(() => _busy = true);
    try {
      final deviceId = await widget.api.deviceId();
      final data = await widget.api.helpBargain(s.id, deviceId);
      if (!mounted) return;
      final updated =
          BargainSession.fromJson(data['session'] as Map<String, dynamic>);
      setState(() => _session = updated);
      showCgToast(context,
          '已砍 \$${(data['cut_amount'] as num).toStringAsFixed(2)}${updated.status == 'success' ? '，砍價成功！' : ''}');
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _purchase() async {
    final s = _session;
    if (s == null || _busy) return;
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
        '幫我砍一刀！${s.campaign?.name ?? ''} 原價 \$${s.originalPrice.toStringAsFixed(0)}，'
        '現價 \$${s.currentPrice.toStringAsFixed(0)}，砍到底價 \$${s.floorPrice.toStringAsFixed(0)} 就能拿套餐：${s.shareLink}'));
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

  @override
  Widget build(BuildContext context) {
    final s = _session;
    return Scaffold(
      appBar: AppBar(title: const Text('砍價')),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : s == null
              ? const Center(child: Text('找不到此砍價。'))
              : RefreshIndicator(
                  onRefresh: _load,
                  child: ListView(
                    padding: const EdgeInsets.all(16),
                    children: [
                      Text(s.campaign?.name ?? '砍價',
                          style: const TextStyle(
                              fontWeight: FontWeight.bold, fontSize: 20)),
                      const SizedBox(height: 4),
                      Text(
                          '${s.campaign?.shopName ?? ''} · ${s.campaign?.personCount ?? 2} 人餐'),
                      const SizedBox(height: 16),
                      Row(
                        crossAxisAlignment: CrossAxisAlignment.end,
                        children: [
                          Text('\$${s.currentPrice.toStringAsFixed(2)}',
                              style: const TextStyle(
                                  color: CgColors.primary,
                                  fontWeight: FontWeight.bold,
                                  fontSize: 32)),
                          const SizedBox(width: 8),
                          Text(
                            '原價 \$${s.originalPrice.toStringAsFixed(0)} · 底價 \$${s.floorPrice.toStringAsFixed(0)}',
                            style: const TextStyle(color: Colors.grey),
                          ),
                        ],
                      ),
                      const SizedBox(height: 8),
                      LinearProgressIndicator(
                          value: s.cutProgress,
                          minHeight: 8,
                          backgroundColor: Colors.grey.shade300,
                          color: CgColors.primary),
                      const SizedBox(height: 8),
                      Row(
                        children: [
                          Chip(label: Text(s.statusText)),
                          const SizedBox(width: 8),
                          Text('${s.helperCount}/${s.helperLimit} 人助力'),
                          const Spacer(),
                          if (s.status == 'active')
                            Text(s.countdownText,
                                style: const TextStyle(color: Colors.grey)),
                        ],
                      ),
                      if (s.stockLocked && s.status == 'active')
                        const Padding(
                          padding: EdgeInsets.only(top: 4),
                          child: Text('已為你鎖定 1 份庫存',
                              style: TextStyle(color: Colors.grey)),
                        ),
                      const SizedBox(height: 16),
                      Row(
                        children: [
                          Expanded(
                            child: SizedBox(
                              height: 48,
                              child: ElevatedButton.icon(
                                onPressed:
                                    s.status == 'active' ? _share : null,
                                icon: const Icon(Icons.share),
                                label: const Text('分享給好友助力'),
                              ),
                            ),
                          ),
                          const SizedBox(width: 12),
                          Expanded(
                            child: SizedBox(
                              height: 48,
                              child: OutlinedButton(
                                onPressed: s.status == 'active' && !_busy
                                    ? _help
                                    : null,
                                child: const Text('幫好友砍一刀'),
                              ),
                            ),
                          ),
                        ],
                      ),
                      if (s.canPurchase) ...[
                        const SizedBox(height: 12),
                        SizedBox(
                          width: double.infinity,
                          height: 56,
                          child: ElevatedButton(
                            onPressed: _busy ? null : _purchase,
                            child: Text(
                                '成團！以 \$${s.currentPrice.toStringAsFixed(2)} 下單拿套餐'),
                          ),
                        ),
                      ],
                      if (s.refunds.isNotEmpty) ...[
                        const SizedBox(height: 16),
                        const Text('退款',
                            style:
                                TextStyle(fontWeight: FontWeight.bold)),
                        ...s.refunds.map((r) => Card(
                              margin: const EdgeInsets.only(top: 8),
                              child: ListTile(
                                leading: const Icon(Icons.replay,
                                    color: CgColors.primary),
                                title: Text(
                                    '${r.reasonText} · \$${r.amount.toStringAsFixed(2)}'),
                                subtitle: Text(r.note.isNotEmpty
                                    ? r.note
                                    : '原路返回，1–3 個工作日'),
                              ),
                            )),
                      ],
                      const SizedBox(height: 16),
                      const Text('助力記錄',
                          style: TextStyle(fontWeight: FontWeight.bold)),
                      if (s.helps.isEmpty)
                        const Padding(
                          padding: EdgeInsets.only(top: 8),
                          child: Text('還沒有好友助力，快分享出去吧。'),
                        ),
                      ...s.helps.map((hp) => ListTile(
                            leading: const Icon(Icons.person,
                                color: CgColors.primary),
                            title: Text(hp.helperName),
                            subtitle: hp.isNewUser
                                ? const Text('新用戶助力（加成）')
                                : null,
                            trailing: Text(
                                '−\$${hp.cutAmount.toStringAsFixed(2)}',
                                style: const TextStyle(
                                    color: CgColors.primary,
                                    fontWeight: FontWeight.bold)),
                          )),
                      const SizedBox(height: 8),
                      const Text(
                          '每人每單限助一次；新用戶（註冊 7 天內）助力砍得更多。未成團 / 超時全額原路退款，不扣手續費。',
                          style:
                              TextStyle(color: Colors.grey, fontSize: 12)),
                    ],
                  ),
                ),
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
            Text('成團！以 \$${session.currentPrice.toStringAsFixed(2)} 拿下套餐',
                style: const TextStyle(
                    fontWeight: FontWeight.bold, fontSize: 20)),
            const SizedBox(height: 8),
            Text(session.campaign?.name ?? ''),
            const SizedBox(height: 16),
            Card(
              child: ListTile(
                leading:
                    const Icon(Icons.card_giftcard, color: CgColors.primary),
                title: const Text('套餐券已存入 Q Wallet'),
                subtitle: const Text('到店出示 QR 碼即可核銷（與一般優惠券相同流程）。'),
              ),
            ),
            if (refundAmount > 0)
              Card(
                child: ListTile(
                  leading:
                      const Icon(Icons.replay, color: CgColors.primary),
                  title: Text('已退差價 \$${refundAmount.toStringAsFixed(2)}'),
                  subtitle: const Text('原路返回，1–3 個工作日。'),
                ),
              ),
            const Spacer(),
            SizedBox(
              width: double.infinity,
              height: 56,
              child: ElevatedButton(
                onPressed: () =>
                    Navigator.of(context).popUntil((r) => r.isFirst),
                child: const Text('返回（到 Q Wallet 查看套餐券）'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}
