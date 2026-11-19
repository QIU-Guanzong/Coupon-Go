import 'dart:convert';
import 'dart:math';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import '../widgets/wallet_updates.dart';

/// UI_SPEC §5 — 返现：余额、明细、提现、提现记录、漏记申诉。
///
/// 后端缺口已补（Gap 3 / 4 / 6）：
/// * GET /cashback 返回提现门槛 / 每日上限（min_withdraw_hkd /
///   daily_withdraw_limit_hkd）；下方具名常量只作接口缺失时的兜底。
/// * 提现失败有原因字段：失败行显示服务端的 failure_reason；成功路径不动。
/// * GET /cashback/missing 返回本人申诉列表；本机 SharedPreferences
///   只作兜底（接口失败或无数据时不丢已存的本地记录）。
/// * 注册姓名无接口可取（登录接口只返回 token / user_id / role /
///   email）：提现只校验姓名必填；注册姓名可传入时才比对一致性。
/// 用户端不调用已删除的记返现接口（FIX-2）；返现只由核销产生。
/// 核销成功页不编返现金额：/new-scan-coupon 不返回返现金额字段，
/// account.dart 的核销成功页已只写「本次消费的返现待确认」，保持不变。
class CashbackLimits {
  /// 兜底默认值（与后端 config 一致）；接口返回字段时以接口值为准。
  static const double minWithdrawHkd = 50;
  static const double dailyWithdrawLimitHkd = 1500;
  static const int dineinUnlockDays = 90;
  static const int missingWindowDays = 30;
  static const int maxEvidenceImages = 3;
}

String _dateYmd(DateTime d) => '${d.year}年${d.month}月${d.day}日';

String _monthDay(DateTime d) => '${d.month}月${d.day}日';

/// 线下 pending 剩余天数（向上取整；当天到期为 0）。
int _daysLeft(DateTime expiresAt) {
  final hours = expiresAt.difference(DateTime.now()).inHours;
  if (hours <= 0) return 0;
  return (hours / 24).ceil();
}

Color _positiveColor(BuildContext context) =>
    Theme.of(context).brightness == Brightness.dark
        ? CgColors.moneyPositiveDark
        : CgColors.moneyPositive;

Color _pendingColor(BuildContext context) =>
    Theme.of(context).brightness == Brightness.dark
        ? CgColors.moneyPendingDark
        : CgColors.moneyPending;

Color _voidColor(BuildContext context) =>
    Theme.of(context).brightness == Brightness.dark
        ? CgColors.moneyVoidDark
        : CgColors.moneyVoid;

/// 状态胶囊（UI_SPEC §5.1 / §5.2 的状态文案）。
class _StatusCapsule extends StatelessWidget {
  const _StatusCapsule({required this.label, required this.color});

  final String label;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Semantics(
      label: '状态：$label',
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
        decoration: BoxDecoration(
          borderRadius: BorderRadius.circular(999),
          border: Border.all(color: color),
        ),
        child: Text(label,
            style: TextStyle(
                color: color, fontSize: 12, fontWeight: FontWeight.w600)),
      ),
    );
  }
}

/// 提现状态胶囊颜色：处理中取待确认色，已到账取可提现色，失败取错误色。
Color _withdrawalColor(BuildContext context, String status) {
  if (status == 'paid') return _positiveColor(context);
  if (status == 'failed') return Theme.of(context).colorScheme.error;
  return _pendingColor(context);
}

/// void_reason 映射（UI_SPEC §5.1）；未知值原样显示。
String? _voidReasonText(String? reason) {
  switch (reason) {
    case null:
    case '':
      return null;
    case 'expired_90d':
      return '90 天内未再次消费';
    case 'cancelled':
      return '订单已取消';
    case 'revoked':
      return '商家已撤銷核銷';
    case 'returned':
      return '订单已退货';
    default:
      return reason;
  }
}

/// 列表四态共用的错误视图：一句话 + 重试（401 时多一个去登录）。
class _ErrorView extends StatelessWidget {
  const _ErrorView({required this.onRetry, this.needsLogin = false});

  final VoidCallback onRetry;
  final bool needsLogin;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 48, horizontal: 24),
      child: Column(
        children: [
          Text(needsLogin ? '请先登录查看返现' : '网络不稳定，请重试',
              textAlign: TextAlign.center,
              style: const TextStyle(color: CgColors.textSecondary)),
          const SizedBox(height: 16),
          if (needsLogin)
            SizedBox(
              width: double.infinity,
              child: ElevatedButton(
                onPressed: () => Navigator.of(context).pushNamed('/login'),
                child: const Text('去登录'),
              ),
            ),
          if (needsLogin) const SizedBox(height: 8),
          SizedBox(
            width: double.infinity,
            child: OutlinedButton(
              onPressed: onRetry,
              child: const Text('重试'),
            ),
          ),
        ],
      ),
    );
  }
}

/// 列表四态共用的空状态：一句话 + 一个动作按钮。
class _EmptyView extends StatelessWidget {
  const _EmptyView(
      {required this.message,
      required this.actionLabel,
      required this.onAction});

  final String message;
  final String actionLabel;
  final VoidCallback onAction;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 48, horizontal: 24),
      child: Column(
        children: [
          Text(message,
              textAlign: TextAlign.center,
              style: const TextStyle(color: CgColors.textSecondary)),
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: OutlinedButton(
              onPressed: onAction,
              child: Text(actionLabel),
            ),
          ),
        ],
      ),
    );
  }
}

/// 加载骨架屏（UI_SPEC §0：加载中用骨架屏，不用转圈）。
class _ListSkeleton extends StatelessWidget {
  const _ListSkeleton();

  @override
  Widget build(BuildContext context) {
    return const Column(
      children: [
        SizedBox(height: 16),
        SkeletonBox(height: 96),
        SizedBox(height: 12),
        SkeletonBox(height: 72),
        SizedBox(height: 8),
        SkeletonBox(height: 72),
        SizedBox(height: 8),
        SkeletonBox(height: 72),
      ],
    );
  }
}

/// 返现首页（UI_SPEC §5.1）。
class CashbackScreen extends StatefulWidget {
  const CashbackScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<CashbackScreen> createState() => _CashbackScreenState();
}

class _CashbackScreenState extends State<CashbackScreen> with WalletUpdates<CashbackScreen> {
  int _generation = 0;
  bool _loading = true;
  bool _needsLogin = false;
  String? _error;
  double _withdrawable = 0;
  double _held = 0, _paid = 0, _unreconciled = 0;
  double _pending = 0;
  double _totalEarned = 0;
  double _adjustment = 0;
  // Gap 4: 取自 GET /cashback 的 min_withdraw_hkd /
  // daily_withdraw_limit_hkd；接口缺失时用 CashbackLimits 兜底值。
  double _minWithdraw = CashbackLimits.minWithdrawHkd;
  double _dailyLimit = CashbackLimits.dailyWithdrawLimitHkd;
  List<CashbackTransaction> _txns = [];
  String _filter = 'all';
  bool _ruleDismissed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  Future<void> syncWallet() async {
    if (_loading) return;
    await refreshOnReceiptChange(widget.api, () async {
      await _load(quiet: true);
      return mounted && _error == null;
    });
  }

  Future<void> _load({bool quiet = false}) async {
    final generation = ++_generation;
    setState(() {
      if (!quiet) _loading = true;
      _error = null;
      _needsLogin = false;
    });
    try {
      final data = await widget.api.cashback();
      final txns = data['transactions'] as List;
      if (!mounted || generation != _generation) return;
      final parsed = txns.map((e) => CashbackTransaction.fromJson(e)).toList();
      parsed.sort((a, b) {
        final ca = a.createdAt;
        final cb = b.createdAt;
        if (ca != null && cb != null) return cb.compareTo(ca);
        return b.id.compareTo(a.id);
      });
      setState(() {
        _withdrawable = (data['withdrawable_balance'] as num?)?.toDouble() ?? 0;
        _unreconciled = (data['withdrawal_unreconciled'] as num?)?.toDouble() ?? 0;
        _held = (data['withdrawal_held'] as num?)?.toDouble() ?? 0;
        _paid = (data['withdrawal_paid'] as num?)?.toDouble() ?? 0;
        _pending = (data['pending_balance'] as num?)?.toDouble() ?? 0;
        _adjustment = (data['adjustment_total'] as num?)?.toDouble() ?? 0;
        _totalEarned = (data['total_earned'] as num?)?.toDouble() ?? 0;
        _minWithdraw = (data['min_withdraw_hkd'] as num?)?.toDouble() ??
            CashbackLimits.minWithdrawHkd;
        _dailyLimit = (data['daily_withdraw_limit_hkd'] as num?)?.toDouble() ??
            CashbackLimits.dailyWithdrawLimitHkd;
        _txns = parsed;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _loading = false;
        _error = e.message;
        _needsLogin = e.status == 401;
      });
    } catch (_) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _loading = false;
        _error = 'network';
      });
    }
  }

  List<CashbackTransaction> get _shown {
    switch (_filter) {
      case 'pending':
        return _txns
            .where((t) => t.status == 'pending' || t.status == 'tracked')
            .toList();
      case 'withdrawable':
        return _txns
            .where((t) => t.status == 'confirmed' || t.status == 'withdrawable')
            .toList();
      case 'withdrawn':
        return _txns.where((t) => t.status == 'withdrawn').toList();
      case 'void':
        return _txns.where((t) => t.status == 'void').toList();
      default:
        return _txns;
    }
  }

  bool get _hasDineinPending =>
      _txns.any((t) => t.status == 'pending' && t.channel == 'dinein');

  bool get _canWithdraw => _withdrawable >= _minWithdraw;

  Future<void> _openWithdraw() async {
    await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => WithdrawScreen(
            api: widget.api,
            balance: _withdrawable,
            minWithdrawHkd: _minWithdraw,
            dailyWithdrawLimitHkd: _dailyLimit)));
    _load();
  }

  Future<void> _openMissing() async {
    await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => MissingCashbackScreen(api: widget.api)));
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('返现'),
        actions: [
          TextButton(
            onPressed: _openMissing,
            style: TextButton.styleFrom(
              foregroundColor: Colors.white,
              minimumSize: const Size(44, 44),
            ),
            child: const Text('没收到返现？'),
          ),
        ],
      ),
      body: Center(child: ConstrainedBox(constraints: const BoxConstraints(maxWidth: 760),
        child: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_loading)
              const _ListSkeleton()
            else if (_error != null || _needsLogin)
              _ErrorView(onRetry: _load, needsLogin: _needsLogin)
            else ...[
              _buildBalanceCard(context),
              if (_adjustment < 0)
                Padding(padding: const EdgeInsets.only(top: 8),
                  child: Text('核銷已撤銷，已撥作提現的 ${formatHkd(-_adjustment)} 已從可提現餘額扣回。')),
              const SizedBox(height: 8),
              Text('提現處理中 ${formatHkd(_held)}　已付款 ${formatHkd(_paid)}'),
              if (_unreconciled > 0) Text('舊提現待核對 ${formatHkd(_unreconciled)}'),
              Text('累计已赚 ${formatHkd(_totalEarned)}',
                  style: const TextStyle(
                      color: CgColors.textSecondary, fontSize: 12)),
              const SizedBox(height: 12),
              SizedBox(
                width: double.infinity,
                child: ElevatedButton(
                  onPressed: _canWithdraw ? _openWithdraw : null,
                  child: const Text('提现'),
                ),
              ),
              const SizedBox(height: 8),
              Text(
                  '满 HK\$${_minWithdraw.toStringAsFixed(0)} 可提现',
                  textAlign: TextAlign.center,
                  style: const TextStyle(
                      color: CgColors.textSecondary, fontSize: 12)),
              const SizedBox(height: 4),
              Align(
                alignment: Alignment.centerRight,
                child: TextButton(
                  onPressed: () {
                    Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) =>
                            WithdrawalHistoryScreen(api: widget.api)));
                  },
                  style: TextButton.styleFrom(minimumSize: const Size(44, 44)),
                  child: const Text('查看提现记录'),
                ),
              ),
              if (_hasDineinPending && !_ruleDismissed)
                _buildRuleBanner(context),
              const SizedBox(height: 8),
              _buildFilters(),
              const SizedBox(height: 8),
              if (_shown.isEmpty)
                _EmptyView(
                  message: _filter == 'all' ? '暂时没有返现记录' : '这一分类下暂时没有返现记录',
                  actionLabel: '浏览附近商户',
                  onAction: () => Navigator.of(context).pushNamed('/nearby'),
                )
              else
                ..._shown.map((t) => _TxnCard(txn: t)),
            ],
          ],
        ),
      ))),
    );
  }

  Widget _buildBalanceCard(BuildContext context) {
    TextStyle amountStyle(Color color) => TextStyle(
          color: color,
          fontSize: 28,
          fontWeight: FontWeight.bold,
          fontFeatures: const [FontFeature.tabularFigures()],
        );
    return Card(
      child: Padding(
        padding: const EdgeInsets.symmetric(vertical: 20, horizontal: 16),
        child: Row(
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('可提现',
                      style: TextStyle(color: CgColors.textSecondary)),
                  const SizedBox(height: 4),
                  Text(formatHkd(_withdrawable),
                      style: amountStyle(_positiveColor(context))),
                ],
              ),
            ),
            const SizedBox(
              height: 48,
              child: VerticalDivider(width: 32),
            ),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text('待确认',
                      style: TextStyle(color: CgColors.textSecondary)),
                  const SizedBox(height: 4),
                  Text(formatHkd(_pending),
                      style: amountStyle(_pendingColor(context))),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildRuleBanner(BuildContext context) {
    return Container(
      margin: const EdgeInsets.only(top: 8),
      padding: const EdgeInsets.only(left: 12, top: 4, bottom: 4, right: 4),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF7E0),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: CgColors.amber),
      ),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          const Expanded(
            child: Padding(
              padding: EdgeInsets.only(top: 10, bottom: 10),
              child: Text('90 天内在同一商户再次消费，上一笔返现即到账',
                  style: TextStyle(fontSize: 13)),
            ),
          ),
          Semantics(
            label: '关闭提示',
            button: true,
            child: IconButton(
              icon: const Icon(Icons.close, size: 20),
              onPressed: () => setState(() => _ruleDismissed = true),
            ),
          ),
        ],
      ),
    );
  }

  Widget _buildFilters() {
    const filters = [
      ('all', '全部'),
      ('pending', '待确认'),
      ('withdrawable', '可提现'),
      ('withdrawn', '已撥作提現'),
      ('void', '已失效'),
    ];
    return SingleChildScrollView(
      scrollDirection: Axis.horizontal,
      child: Row(
        children: [
          for (final f in filters)
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: ChoiceChip(
                label: Text(f.$2),
                selected: _filter == f.$1,
                onSelected: (_) => setState(() => _filter = f.$1),
              ),
            ),
        ],
      ),
    );
  }
}

/// 返现明细行（UI_SPEC §5.1）：商户名、消费日期、返现金额、状态胶囊。
class _TxnCard extends StatelessWidget {
  const _TxnCard({required this.txn});

  final CashbackTransaction txn;

  String get _capsuleLabel {
    switch (txn.status) {
      case 'pending':
      case 'tracked':
        return txn.channel == 'dinein' ? '再来一次即可到账' : '待确认';
      case 'confirmed':
      case 'withdrawable':
        return '可提现';
      case 'withdrawn':
        return '已撥作提現';
      case 'void':
        return '已失效';
      default:
        return txn.statusText.isEmpty ? '待确认' : txn.statusText;
    }
  }

  Color _capsuleColor(BuildContext context) {
    switch (txn.status) {
      case 'confirmed':
      case 'withdrawable':
        return _positiveColor(context);
      case 'void':
        return _voidColor(context);
      case 'withdrawn':
        return CgColors.textSecondary;
      default:
        return _pendingColor(context);
    }
  }

  /// 状态补充行：线下 pending 倒计时 / 线上 pending 预计到账日 / 失效原因。
  String? get _detailLine {
    if ((txn.status == 'pending' || txn.status == 'tracked')) {
      if (txn.channel == 'dinein') {
        final exp = txn.expiresAt;
        if (exp == null) return '90 天内再次消费即可到账';
        final days = _daysLeft(exp);
        return days <= 0 ? '今天到期' : '还剩 $days 天';
      }
      final exp = txn.expectedConfirmAt;
      if (exp != null) return '预计 ${_monthDay(exp)} 到账';
      return null;
    }
    if (txn.status == 'void') return _voidReasonText(txn.voidReason);
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final detail = _detailLine;
    final date = txn.createdAt;
    return Card(
      margin: const EdgeInsets.only(top: 8),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(txn.shopName.isEmpty ? '商户' : txn.shopName,
                      style: const TextStyle(fontWeight: FontWeight.w600)),
                ),
                Text(formatHkd(txn.amount),
                    style: TextStyle(
                      fontWeight: FontWeight.bold,
                      fontFeatures: const [FontFeature.tabularFigures()],
                      color: txn.status == 'void' ? _voidColor(context) : null,
                    )),
              ],
            ),
            const SizedBox(height: 8),
            Row(
              children: [
                Expanded(
                  child: Text(
                    date == null
                        ? txn.channelText
                        : '${_dateYmd(date)} · ${txn.channelText}',
                    style: const TextStyle(
                        color: CgColors.textSecondary, fontSize: 12),
                  ),
                ),
                _StatusCapsule(
                    label: _capsuleLabel, color: _capsuleColor(context)),
              ],
            ),
            if (detail != null) ...[
              const SizedBox(height: 6),
              Text(detail,
                  style: const TextStyle(
                      color: CgColors.textSecondary, fontSize: 12)),
            ],
          ],
        ),
      ),
    );
  }
}

/// 提现页（UI_SPEC §5.2）：默认全部可提现、门槛与每日上限、FPS / 银行转账、
/// 收款姓名与注册姓名一致（内联报错，不弹窗）。提交后跳提现记录页。
class WithdrawScreen extends StatefulWidget {
  const WithdrawScreen(
      {super.key,
      required this.api,
      this.balance,
      this.registeredName,
      this.minWithdrawHkd = CashbackLimits.minWithdrawHkd,
      this.dailyWithdrawLimitHkd = CashbackLimits.dailyWithdrawLimitHkd});

  final ApiClient api;

  /// 已知的可提现余额；不传时页面自行拉取 GET /cashback。
  final double? balance;

  /// 注册姓名。无接口可取时为 null，此时只校验姓名必填（见文件头注释）。
  final String? registeredName;

  /// Gap 4：提现门槛 / 每日上限，取自 GET /cashback；未传时用兜底常量。
  final double minWithdrawHkd;
  final double dailyWithdrawLimitHkd;

  @override
  State<WithdrawScreen> createState() => _WithdrawScreenState();
}

class _WithdrawScreenState extends State<WithdrawScreen> {
  final _amount = TextEditingController();
  final _name = TextEditingController();
  final _ref = TextEditingController();
  String _method = 'fps';
  String? _requestKey, _requestBody;
  bool _loadingBalance = false;
  double? _balance;
  // Gap 4：页面内的门槛 / 上限以构造参数为准；自行拉取余额时如接口
  // 返回了字段则更新（接口缺失时保持构造参数的兜底值）。
  late double _minWithdraw;
  late double _dailyLimit;
  bool _submitting = false;
  bool _touchedAmount = false;
  bool _touchedName = false;
  bool _submitAttempted = false;
  String? _formError;
  bool _needsLogin = false;

  @override
  void initState() {
    super.initState();
    _minWithdraw = widget.minWithdrawHkd;
    _dailyLimit = widget.dailyWithdrawLimitHkd;
    _balance = widget.balance;
    if (_balance != null) {
      _amount.text = _balance!.toStringAsFixed(2);
    } else {
      _fetchBalance();
    }
    _ref.addListener(() => setState(() {}));
    _amount.addListener(() {
      _touchedAmount = true;
      setState(() {});
    });
    _name.addListener(() {
      _touchedName = true;
      setState(() {});
    });
  }

  @override
  void dispose() {
    _amount.dispose();
    _name.dispose();
    _ref.dispose();
    super.dispose();
  }

  Future<void> _fetchBalance() async {
    setState(() => _loadingBalance = true);
    try {
      final data = await widget.api.cashback();
      if (!mounted) return;
      final balance = (data['withdrawable_balance'] as num?)?.toDouble() ?? 0;
      setState(() {
        _balance = balance;
        _amount.text = balance.toStringAsFixed(2);
        _minWithdraw = (data['min_withdraw_hkd'] as num?)?.toDouble() ??
            widget.minWithdrawHkd;
        _dailyLimit =
            (data['daily_withdraw_limit_hkd'] as num?)?.toDouble() ??
                widget.dailyWithdrawLimitHkd;
        _loadingBalance = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _loadingBalance = false;
        _formError = e.message;
        _needsLogin = e.status == 401;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loadingBalance = false;
        _formError = '网络不稳定，请重试';
      });
    }
  }

  String? _amountError() {
    final value = double.tryParse(_amount.text.trim());
    if (value == null || value <= 0) return '请输入提现金额';
    if (value < _minWithdraw) {
      return '未满 HK\$${_minWithdraw.toStringAsFixed(0)} 提现门槛';
    }
    if (_balance != null && value > _balance!) return '超出可提现余额';
    if (value > _dailyLimit) {
      return '超出每日上限 HK\$${_dailyLimit.toStringAsFixed(0)}';
    }
    return null;
  }

  String? _nameError() {
    final name = _name.text.trim();
    if (name.isEmpty) return '请填写收款姓名';
    final registered = widget.registeredName?.trim();
    if (registered != null && registered.isNotEmpty && name != registered) {
      return '收款姓名须与注册姓名一致';
    }
    return null;
  }

  bool get _canSubmit =>
      !_submitting &&
      !_loadingBalance &&
      _balance != null &&
      _amountError() == null &&
      _nameError() == null && _ref.text.trim().isNotEmpty;

  bool get _showAmountError => _submitAttempted || _touchedAmount;
  bool get _showNameError => _submitAttempted || _touchedName;

  Future<void> _submit() async {
    setState(() => _submitAttempted = true);
    if (!_canSubmit) return;
    setState(() {
      _submitting = true;
      _formError = null;
      _needsLogin = false;
    });
    try {
      final body = jsonEncode([_method, _name.text.trim(), _ref.text.trim(), _amount.text.trim()]);
      if (_requestBody != body) {
        final random = Random.secure();
        _requestKey = base64UrlEncode(List.generate(18, (_) => random.nextInt(256)));
        _requestBody = body;
      }
      await widget.api.withdrawCashback(double.parse(_amount.text.trim()),
          _method, _name.text.trim(), _ref.text.trim(), requestKey: _requestKey);
      if (!mounted) return;
      Navigator.of(context).pushReplacement(MaterialPageRoute(
          builder: (_) => WithdrawalHistoryScreen(api: widget.api)));
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _formError = e.message;
        _needsLogin = e.status == 401;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _formError = '网络不稳定，请重试';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final amountError = _showAmountError ? _amountError() : null;
    final nameError = _showNameError ? _nameError() : null;
    return Scaffold(
      appBar: AppBar(title: const Text('提现返现')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_loadingBalance)
            const SkeletonBox(height: 24)
          else
            Text('可提现余额 ${formatHkd(_balance ?? 0)}',
                style:
                    const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
          const SizedBox(height: 4),
          Text(
              '满 HK\$${_minWithdraw.toStringAsFixed(0)} 可提现；每日上限 HK\$${_dailyLimit.toStringAsFixed(0)}',
              style:
                  const TextStyle(color: CgColors.textSecondary, fontSize: 12)),
          const SizedBox(height: 16),
          TextField(
            controller: _amount,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: InputDecoration(
              labelText: '提现金额 HK\$',
              errorText: amountError,
              suffixIcon: TextButton(
                onPressed: _balance == null
                    ? null
                    : () {
                        _amount.text = _balance!.toStringAsFixed(2);
                      },
                style: TextButton.styleFrom(minimumSize: const Size(44, 44)),
                child: const Text('全部提现'),
              ),
            ),
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              ChoiceChip(
                label: const Text('转数快 (FPS)'),
                selected: _method == 'fps',
                onSelected: (_) => setState(() => _method = 'fps'),
              ),
              const SizedBox(width: 8),
              ChoiceChip(
                label: const Text('银行转账'),
                selected: _method == 'bank',
                onSelected: (_) => setState(() => _method = 'bank'),
              ),
            ],
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _name,
            decoration: InputDecoration(
              labelText: '收款账户姓名',
              errorText: nameError,
            ),
          ),
          const Padding(
            padding: EdgeInsets.only(top: 6),
            child: Text('收款姓名须与注册姓名一致',
                style: TextStyle(color: CgColors.textSecondary, fontSize: 12)),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _ref,
            decoration: InputDecoration(
                labelText: _method == 'fps' ? 'FPS ID / 手机号' : '银行账号'),
          ),
          const SizedBox(height: 8),
          const Text('提交后请在提现记录查看处理进度。',
              style: TextStyle(color: CgColors.textSecondary, fontSize: 12)),
          if (_formError != null) ...[
            const SizedBox(height: 12),
            Text(_formError!,
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          if (_needsLogin) ...[
            const SizedBox(height: 8),
            SizedBox(
              width: double.infinity,
              child: OutlinedButton(
                onPressed: () => Navigator.of(context).pushNamed('/login'),
                child: const Text('去登录'),
              ),
            ),
          ],
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: _canSubmit ? _submit : null,
              child: _submitting ? const Text('提交中…') : const Text('提交提现申请'),
            ),
          ),
        ],
      ),
    );
  }
}

/// 提现记录页（UI_SPEC §5.2）：处理中 / 已到账 / 失败；失败显示服务端
/// 的 failure_reason（Gap 6），无原因时才显示通用文案；重试即重新提交一笔提现。
class WithdrawalHistoryScreen extends StatefulWidget {
  const WithdrawalHistoryScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<WithdrawalHistoryScreen> createState() =>
      _WithdrawalHistoryScreenState();
}

class _WithdrawalHistoryScreenState extends State<WithdrawalHistoryScreen> with WalletUpdates<WithdrawalHistoryScreen> {
  bool _loading = true;
  int? _next;
  int _generation = 0;
  bool _needsLogin = false;
  String? _error;
  List<CashbackWithdrawal> _items = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  Future<void> syncWallet() async {
    if (_loading) return;
    await refreshOnReceiptChange(widget.api, () async {
      await _load(quiet: true);
      return mounted && _error == null;
    });
  }

  Future<void> _load({bool quiet = false, bool more = false}) async {
    final generation = ++_generation;
    setState(() {
      if (!quiet) _loading = true;
      _error = null;
      _needsLogin = false;
    });
    try {
      // Refresh all previously displayed pages so older decisions stay in sync.
      final oldest = quiet && _items.isNotEmpty ? _items.last.id : null;
      final rows = <CashbackWithdrawal>[if (more) ..._items];
      int? cursor = more ? _next : null;
      do {
        final page = await widget.api.withdrawalPage(before: cursor);
        rows.addAll((page['withdrawals'] as List).map((w) => CashbackWithdrawal.fromJson(w)));
        cursor = page['next'] as int?;
      } while (cursor != null && oldest != null && cursor > oldest);
      if (!mounted || generation != _generation) return;
      setState(() {
        _items = rows;
        _next = cursor;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _loading = false;
        _error = e.message;
        _needsLogin = e.status == 401;
      });
    } catch (_) {
      if (!mounted || generation != _generation) return;
      setState(() {
        _loading = false;
        _error = 'network';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('提现记录')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_loading)
              const _ListSkeleton()
            else if (_error != null || _needsLogin)
              _ErrorView(onRetry: _load, needsLogin: _needsLogin)
            else if (_items.isEmpty)
              _EmptyView(
                message: '暂时没有提现记录',
                actionLabel: '返回返现页',
                onAction: () => Navigator.of(context).pop(),
              )
            else
              ..._items.map((w) => _WithdrawalCard(
                    withdrawal: w,
                    onRetry: () async {
                      await Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => WithdrawScreen(api: widget.api)));
                      _load();
                    },
                  )),
            if (!_loading && _error == null && _next != null)
              TextButton(onPressed: () => _load(more: true), child: const Text('較早的提現記錄')),
          ],
        ),
      ),
    );
  }
}

class _WithdrawalCard extends StatelessWidget {
  const _WithdrawalCard({required this.withdrawal, required this.onRetry});

  final CashbackWithdrawal withdrawal;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final w = withdrawal;
    final date = w.createdAt;
    return Card(
      margin: const EdgeInsets.only(top: 8),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Text(formatHkd(w.amount),
                      style: const TextStyle(
                        fontWeight: FontWeight.bold,
                        fontFeatures: [FontFeature.tabularFigures()],
                      )),
                ),
                _StatusCapsule(
                    label: w.statusText,
                    color: _withdrawalColor(context, w.status)),
              ],
            ),
            const SizedBox(height: 8),
            Text(
              '#${w.id} · ${w.methodText} · ${w.accountName}'
              '${date == null ? '' : ' · ${_dateYmd(date)}'}',
              style:
                  const TextStyle(color: CgColors.textSecondary, fontSize: 12),
            ),
            if (w.allocations.isNotEmpty)
              ExpansionTile(tilePadding: EdgeInsets.zero, title: const Text('對應返現流水'),
                children: [for (final allocation in w.allocations)
                  ListTile(title: Text('返現 #${allocation['transaction_id']}'),
                    trailing: Text(formatHkd((allocation['amount'] as num).toDouble())))]),
            for (final event in w.events.where((e) => e['status'] == 'paid'))
              Text('付款參考編號 ${event['detail']}'),
            if (w.status == 'failed') ...[
              Text(w.holdReleased ? '提現預留已解除，可用金額見返現餘額。' : '舊單退回金額待財務核對。'),
              const SizedBox(height: 6),
              Text(
                  (w.failureReason ?? '').isNotEmpty
                      ? '失败原因：${w.failureReason}'
                      : '处理失败，请重试',
                  style:
                      const TextStyle(color: CgColors.textSecondary, fontSize: 12)),
              const SizedBox(height: 8),
              SizedBox(
                width: double.infinity,
                child: OutlinedButton(
                  onPressed: onRetry,
                  child: const Text('重新提交提现'),
                ),
              ),
            ],
          ],
        ),
      ),
    );
  }
}

/// 本机保存的申诉记录。服务端已有 GET /cashback/missing（Gap 3），
/// 列表优先取接口；本机记录只作兜底（接口失败或无数据时不丢已存记录）。
/// 服务端返回的 id 是 int，本机存的是 String，fromJson 两边都兼容。
class LocalAppeal {
  LocalAppeal({
    required this.id,
    required this.shopName,
    required this.orderNo,
    required this.spentAt,
    required this.amount,
    required this.evidence,
    required this.note,
    required this.status,
    this.reason,
    required this.createdAt,
  });

  factory LocalAppeal.fromJson(Map<String, dynamic> j) => LocalAppeal(
        id: j['id']?.toString() ?? '',
        shopName: j['shop_name'] as String? ?? '',
        orderNo: j['order_no'] as String? ?? '',
        spentAt:
            DateTime.tryParse(j['spent_at'] as String? ?? '') ?? DateTime.now(),
        amount: (j['amount'] as num?)?.toDouble() ?? 0,
        evidence: j['evidence'] as String? ?? '',
        note: j['note'] as String? ?? '',
        status: j['status'] as String? ?? 'submitted',
        reason: j['reason'] as String?,
        createdAt: DateTime.tryParse(j['created_at'] as String? ?? '') ??
            DateTime.now(),
      );

  final String id;
  final String shopName;
  final String orderNo;
  final DateTime spentAt;
  final double amount;
  final String evidence;
  final String note;
  final String status;
  final String? reason;
  final DateTime createdAt;

  Map<String, dynamic> toJson() => {
        'id': id,
        'shop_name': shopName,
        'order_no': orderNo,
        'spent_at': spentAt.toIso8601String(),
        'amount': amount,
        'evidence': evidence,
        'note': note,
        'status': status,
        'reason': reason,
        'created_at': createdAt.toIso8601String(),
      };
}

const _appealsPrefsKey = 'cg_cashback_appeals_v1';

Future<List<LocalAppeal>> _loadLocalAppeals() async {
  final prefs = await SharedPreferences.getInstance();
  final raw = prefs.getString(_appealsPrefsKey);
  if (raw == null || raw.isEmpty) return [];
  final list = jsonDecode(raw) as List;
  return list
      .map((e) => LocalAppeal.fromJson(e as Map<String, dynamic>))
      .toList();
}

Future<void> _saveLocalAppeal(LocalAppeal appeal) async {
  final prefs = await SharedPreferences.getInstance();
  final all = await _loadLocalAppeals();
  all.insert(0, appeal);
  await prefs.setString(
      _appealsPrefsKey, jsonEncode(all.map((e) => e.toJson()).toList()));
}

String _appealStatusText(String status) {
  switch (status) {
    case 'under_review':
      return '审核中';
    case 'resolved':
      return '已通过';
    case 'rejected':
      return '未通过';
    default:
      return '已提交';
  }
}

Color _appealStatusColor(BuildContext context, String status) {
  switch (status) {
    case 'resolved':
      return _positiveColor(context);
    case 'rejected':
      return Theme.of(context).colorScheme.error;
    case 'under_review':
      return _pendingColor(context);
    default:
      return CgColors.textSecondary;
  }
}

/// 漏记申诉表单（UI_SPEC §5.3）：商户、订单号、消费时间、金额、
/// 凭证截图（≤3 张）、备注。消费时间用日期选择器，超过 30 天的日期禁用。
class MissingCashbackScreen extends StatefulWidget {
  const MissingCashbackScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<MissingCashbackScreen> createState() => _MissingCashbackScreenState();
}

class _MissingCashbackScreenState extends State<MissingCashbackScreen> {
  final _orderNo = TextEditingController();
  final _amount = TextEditingController();
  final _note = TextEditingController();
  int? _shopId;
  String _shopName = '';
  List<Shop> _shops = [];
  bool _shopsLoading = true;
  bool _shopsError = false;
  DateTime _spentAt = DateTime.now();
  List<Uint8List> _evidenceBytes = [];
  List<String> _evidenceNames = [];
  String? _evidenceError;
  bool _submitting = false;
  String? _formError;

  @override
  void initState() {
    super.initState();
    _loadShops();
  }

  @override
  void dispose() {
    _orderNo.dispose();
    _amount.dispose();
    _note.dispose();
    super.dispose();
  }

  Future<void> _loadShops() async {
    setState(() {
      _shopsLoading = true;
      _shopsError = false;
    });
    try {
      final data = await widget.api.home();
      if (!mounted) return;
      final list = (data['popular_merchants'] as List? ?? [])
          .map((e) => Shop.fromJson(e))
          .toList();
      setState(() {
        _shops = list;
        _shopsLoading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _shopsLoading = false;
        _shopsError = true;
      });
    }
  }

  bool get _canSubmit =>
      !_submitting &&
      _shopId != null &&
      _orderNo.text.trim().isNotEmpty &&
      (double.tryParse(_amount.text.trim()) ?? 0) > 0;

  Future<void> _pickDate() async {
    final now = DateTime.now();
    final first = DateTime(now.year, now.month, now.day)
        .subtract(const Duration(days: CashbackLimits.missingWindowDays));
    final last = DateTime(now.year, now.month, now.day);
    final picked = await showDatePicker(
      context: context,
      initialDate: _spentAt,
      firstDate: first,
      lastDate: last,
      helpText: '选择消费日期',
    );
    if (picked != null && mounted) setState(() => _spentAt = picked);
  }

  Future<void> _pickImages() async {
    final remaining = CashbackLimits.maxEvidenceImages - _evidenceBytes.length;
    if (remaining <= 0) return;
    setState(() => _evidenceError = null);
    try {
      final files = await ImagePicker().pickMultiImage();
      if (files.isEmpty) return;
      final take = files.take(remaining).toList();
      final bytes = <Uint8List>[];
      final names = <String>[];
      for (final f in take) {
        bytes.add(await f.readAsBytes());
        names.add(f.name);
      }
      if (!mounted) return;
      setState(() {
        _evidenceBytes = [..._evidenceBytes, ...bytes];
        _evidenceNames = [..._evidenceNames, ...names];
      });
    } catch (_) {
      if (mounted) setState(() => _evidenceError = '未能打开相册，请重试');
    }
  }

  Future<void> _submit() async {
    if (!_canSubmit) return;
    setState(() {
      _submitting = true;
      _formError = null;
    });
    final evidence =
        _evidenceNames.isEmpty ? '' : '凭证图片：${_evidenceNames.join('、')}';
    try {
      final res = await widget.api.submitMissingCashback(
          _shopId!,
          _orderNo.text.trim(),
          _spentAt.toIso8601String(),
          double.parse(_amount.text.trim()),
          evidence,
          _note.text.trim());
      final appealJson = res['appeal'] as Map<String, dynamic>?;
      final appeal = LocalAppeal(
        id: appealJson?['id']?.toString() ??
            DateTime.now().millisecondsSinceEpoch.toString(),
        shopName: appealJson?['shop_name'] as String? ?? _shopName,
        orderNo: _orderNo.text.trim(),
        spentAt: _spentAt,
        amount: double.parse(_amount.text.trim()),
        evidence: evidence,
        note: _note.text.trim(),
        status: appealJson?['status'] as String? ?? 'submitted',
        createdAt: DateTime.now(),
      );
      await _saveLocalAppeal(appeal);
      if (!mounted) return;
      Navigator.of(context).pushReplacement(MaterialPageRoute(
          builder: (_) => MissingAppealsScreen(api: widget.api)));
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _formError = e.message;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _submitting = false;
        _formError = '网络不稳定，请重试';
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('申诉返现')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text('消费后 30 天内可申请；调查期最长 120 天。请提供订单号与凭证。',
              style: TextStyle(color: CgColors.textSecondary, fontSize: 12)),
          const SizedBox(height: 16),
          if (_shopsLoading)
            const SkeletonBox(height: 56)
          else if (_shopsError)
            Row(
              children: [
                const Expanded(
                    child: Text('商户列表加载失败',
                        style: TextStyle(color: CgColors.textSecondary))),
                TextButton(
                  onPressed: _loadShops,
                  style: TextButton.styleFrom(minimumSize: const Size(44, 44)),
                  child: const Text('重试'),
                ),
              ],
            )
          else
            DropdownButton<int>(
              value: _shopId,
              isExpanded: true,
              hint: const Text('选择商户'),
              items: _shops
                  .map(
                      (s) => DropdownMenuItem(value: s.id, child: Text(s.name)))
                  .toList(),
              onChanged: (v) => setState(() {
                _shopId = v;
                _shopName = _shops.firstWhere((s) => s.id == v).name;
              }),
            ),
          const SizedBox(height: 12),
          TextField(
            controller: _orderNo,
            decoration: const InputDecoration(labelText: '订单号 / 核销凭证号'),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 12),
          InkWell(
            onTap: _pickDate,
            borderRadius: BorderRadius.circular(12),
            child: InputDecorator(
              decoration: const InputDecoration(labelText: '消费时间'),
              child: Row(
                children: [
                  Expanded(child: Text(_dateYmd(_spentAt))),
                  const Icon(Icons.calendar_today, size: 20),
                ],
              ),
            ),
          ),
          const Padding(
            padding: EdgeInsets.only(top: 6),
            child: Text('消费后 30 天内可申请',
                style: TextStyle(color: CgColors.textSecondary, fontSize: 12)),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _amount,
            keyboardType: const TextInputType.numberWithOptions(decimal: true),
            decoration: const InputDecoration(labelText: '消费金额 HK\$'),
            onChanged: (_) => setState(() {}),
          ),
          const SizedBox(height: 16),
          const Text('凭证截图（最多 3 张）',
              style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 8),
          Wrap(
            spacing: 8,
            runSpacing: 8,
            children: [
              for (var i = 0; i < _evidenceBytes.length; i++)
                Stack(
                  children: [
                    ClipRRect(
                      borderRadius: BorderRadius.circular(8),
                      child: Image.memory(_evidenceBytes[i],
                          width: 72, height: 72, fit: BoxFit.cover),
                    ),
                    Positioned(
                      right: 0,
                      top: 0,
                      child: Semantics(
                        label: '删除凭证 ${_evidenceNames[i]}',
                        button: true,
                        child: GestureDetector(
                          onTap: () => setState(() {
                            _evidenceBytes.removeAt(i);
                            _evidenceNames.removeAt(i);
                          }),
                          child: Container(
                            width: 44,
                            height: 44,
                            alignment: Alignment.topRight,
                            child: Container(
                              decoration: const BoxDecoration(
                                color: Colors.black54,
                                shape: BoxShape.circle,
                              ),
                              padding: const EdgeInsets.all(2),
                              child: const Icon(Icons.close,
                                  size: 16, color: Colors.white),
                            ),
                          ),
                        ),
                      ),
                    ),
                  ],
                ),
              if (_evidenceBytes.length < CashbackLimits.maxEvidenceImages)
                Semantics(
                  label: '添加凭证截图',
                  button: true,
                  child: InkWell(
                    onTap: _pickImages,
                    borderRadius: BorderRadius.circular(8),
                    child: Container(
                      width: 72,
                      height: 72,
                      decoration: BoxDecoration(
                        borderRadius: BorderRadius.circular(8),
                        border: Border.all(color: CgColors.cardBorder),
                      ),
                      child: const Icon(Icons.add_a_photo,
                          color: CgColors.textSecondary),
                    ),
                  ),
                ),
            ],
          ),
          if (_evidenceError != null) ...[
            const SizedBox(height: 6),
            Text(_evidenceError!,
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          const SizedBox(height: 12),
          TextField(
            controller: _note,
            decoration: const InputDecoration(labelText: '备注（可选）'),
            maxLines: 3,
          ),
          if (_formError != null) ...[
            const SizedBox(height: 12),
            Text(_formError!,
                style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ],
          const SizedBox(height: 16),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: _canSubmit ? _submit : null,
              child: _submitting ? const Text('提交中…') : const Text('提交申诉'),
            ),
          ),
        ],
      ),
    );
  }
}

/// 申诉列表页（UI_SPEC §5.3）：已提交 / 审核中 / 已通过 / 未通过（含原因）。
/// Gap 3：列表优先取 GET /cashback/missing；接口失败或返回空时用本机
/// 保存的记录兜底，已提交但服务端暂无的本地记录会合并进列表展示。
class MissingAppealsScreen extends StatefulWidget {
  const MissingAppealsScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<MissingAppealsScreen> createState() => _MissingAppealsScreenState();
}

class _MissingAppealsScreenState extends State<MissingAppealsScreen> {
  bool _loading = true;
  bool _failed = false;
  List<LocalAppeal> _items = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _failed = false;
    });
    List<LocalAppeal> local = [];
    try {
      local = await _loadLocalAppeals();
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _failed = true;
      });
      return;
    }
    try {
      final remote = await widget.api.cashbackMissingAppeals();
      if (!mounted) return;
      final server = remote
          .map((e) => LocalAppeal.fromJson(e as Map<String, dynamic>))
          .toList();
      if (server.isEmpty) {
        // 接口成功但无数据：不丢本地记录。
        setState(() {
          _items = local;
          _loading = false;
        });
        return;
      }
      // 服务端为准；本地有、服务端暂无的记录合并进来（按 id 去重）。
      final serverIds = server.map((a) => a.id).toSet();
      final extras = local.where((a) => !serverIds.contains(a.id)).toList();
      setState(() {
        _items = [...server, ...extras];
        _loading = false;
      });
    } catch (_) {
      // 接口失败：兜底用本地记录。
      if (!mounted) return;
      setState(() {
        _items = local;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('我的申诉')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_loading)
              const _ListSkeleton()
            else if (_failed)
              _ErrorView(onRetry: _load)
            else if (_items.isEmpty)
              _EmptyView(
                message: '暂无申诉记录',
                actionLabel: '提交申诉',
                onAction: () {
                  Navigator.of(context).push(MaterialPageRoute(
                      builder: (_) => MissingCashbackScreen(api: widget.api)));
                },
              )
            else
              ..._items.map((a) => Card(
                    margin: const EdgeInsets.only(top: 8),
                    child: Padding(
                      padding: const EdgeInsets.symmetric(
                          horizontal: 12, vertical: 12),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Row(
                            children: [
                              Expanded(
                                child: Text(
                                    a.shopName.isEmpty ? '商户' : a.shopName,
                                    style: const TextStyle(
                                        fontWeight: FontWeight.w600)),
                              ),
                              _StatusCapsule(
                                label: _appealStatusText(a.status),
                                color: _appealStatusColor(context, a.status),
                              ),
                            ],
                          ),
                          const SizedBox(height: 8),
                          Text(
                            '订单 ${a.orderNo} · ${_dateYmd(a.spentAt)} · ${formatHkd(a.amount)}',
                            style: const TextStyle(
                                color: CgColors.textSecondary, fontSize: 12),
                          ),
                          if (a.status == 'rejected' &&
                              (a.reason ?? '').isNotEmpty) ...[
                            const SizedBox(height: 6),
                            Text('未通过原因：${a.reason}',
                                style: const TextStyle(
                                    color: CgColors.textSecondary,
                                    fontSize: 12)),
                          ],
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
