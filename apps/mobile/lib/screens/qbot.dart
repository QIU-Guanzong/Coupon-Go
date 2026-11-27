import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../models.dart';
import '../qbot_offers.dart';
import '../widgets/widgets.dart';
import 'bargain.dart';
import 'customer.dart';
import 'packages.dart';
import '../theme.dart';

/// Present simple model formatting without modifying the stored chat history.
/// Unmatched markup is kept as ordinary text; no HTML or links are executed.
TextSpan formatQBotAnswer(String raw) {
  final text = raw.replaceAllMapped(
    RegExp(r'^([ \t]*)[-*] (?=\S)', multiLine: true),
    (match) => '${match[1]}• ',
  );
  final spans = <InlineSpan>[];
  var offset = 0;
  for (final match in RegExp(r'\*\*([^\n]+?)\*\*').allMatches(text)) {
    if (match.start > offset) spans.add(TextSpan(text: text.substring(offset, match.start)));
    spans.add(TextSpan(text: match[1], style: const TextStyle(fontWeight: FontWeight.bold)));
    offset = match.end;
  }
  if (offset < text.length) spans.add(TextSpan(text: text.substring(offset)));
  return TextSpan(children: spans);
}

typedef _PackageComparison = ({int people, String budget});

class _PackageComparisonDialog extends StatefulWidget {
  const _PackageComparisonDialog();

  @override
  State<_PackageComparisonDialog> createState() => _PackageComparisonDialogState();
}

class _PackageComparisonDialogState extends State<_PackageComparisonDialog> {
  final _form = GlobalKey<FormState>();
  final _people = TextEditingController(text: '2');
  final _budget = TextEditingController();
  bool _submitted = false;

  @override
  void dispose() {
    _people.dispose();
    _budget.dispose();
    super.dispose();
  }

  void _submit() {
    if (_submitted || !_form.currentState!.validate()) return;
    _submitted = true;
    Navigator.pop<_PackageComparison>(context, (
      people: int.parse(_people.text.trim()),
      budget: _budget.text.trim(),
    ));
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    return AlertDialog(
      title: Text(t('比較套餐', 'Compare packages')),
      scrollable: true,
      content: Form(
        key: _form,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            TextFormField(
              key: const ValueKey('qbot-compare-people'),
              controller: _people,
              keyboardType: TextInputType.number,
              textInputAction: TextInputAction.next,
              decoration: InputDecoration(labelText: t('用餐人數', 'Party size')),
              validator: (value) =>
                  RegExp(r'^[1-9][0-9]?$').hasMatch(value?.trim() ?? '')
                      ? null
                      : t('請輸入 1 至 99 人。', 'Enter 1 to 99 people.'),
            ),
            const SizedBox(height: 12),
            TextFormField(
              key: const ValueKey('qbot-compare-budget'),
              controller: _budget,
              keyboardType:
                  const TextInputType.numberWithOptions(decimal: true),
              textInputAction: TextInputAction.done,
              decoration: InputDecoration(
                labelText: t(r'整份預算（HK$，可留空）',
                    r'Total budget (HK$, optional)'),
                prefixText: r'HK$ ',
              ),
              validator: (value) {
                final budget = value?.trim() ?? '';
                if (budget.isEmpty ||
                    RegExp(r'^[0-9]{1,8}(?:\.[0-9]{1,2})?$').hasMatch(budget)) {
                  return null;
                }
                return t('請輸入港元金額，最多兩位小數。',
                    'Enter an HK dollar amount with up to two decimals.');
              },
              onFieldSubmitted: (_) => _submit(),
            ),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.pop(context),
          child: Text(t('取消', 'Cancel')),
        ),
        FilledButton(
          key: const ValueKey('qbot-compare-submit'),
          onPressed: _submit,
          child: Text(t('查詢套餐', 'Find packages')),
        ),
      ],
    );
  }
}

/// Q仔 uses the Coupon GO server for catalog-grounded answers.
class QBotScreen extends StatefulWidget {
  const QBotScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<QBotScreen> createState() => _QBotScreenState();
}

class _QBotScreenState extends State<QBotScreen> {
  final _ctrl = TextEditingController();
  final _scroll = ScrollController();
  final _messages = <(String, String, String?, List<QBotOffer>)>[];
  QBotOffer? _openingOffer;
  bool _sending = false;
  bool _failed = false;
  bool _rateLimited = false;
  bool _invalidInput = false;
  bool _askingPermission = false;
  bool _editingComparison = false;
  bool _allowedCatalogForThisVisit = false;
  bool _failedComparison = false;
  bool _allowedForThisVisit = false;
  int _conversation = 0;

  @override
  void dispose() {
    _ctrl.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _scrollDown() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && _scroll.hasClients) {
        final end = _scroll.position.maxScrollExtent;
        if (MediaQuery.disableAnimationsOf(context)) {
          _scroll.jumpTo(end);
        } else {
          _scroll.animateTo(end, duration: const Duration(milliseconds: 180),
              curve: Curves.easeOutCubic);
        }
      }
    });
  }

  Future<void> _openComparison() async {
    if (_editingComparison || _askingPermission || _sending) return;
    final generation = _conversation;
    _editingComparison = true;
    final comparison = await showDialog<_PackageComparison>(
      context: context,
      builder: (_) => const _PackageComparisonDialog(),
    );
    _editingComparison = false;
    if (!mounted || comparison == null || generation != _conversation) return;
    final total = comparison.budget;
    final amount = 'HK\$$total';
    final question = context.tr(
      '比較${comparison.people}人套餐${total.isEmpty ? '' : '，總預算$amount'}',
      'Compare packages for ${comparison.people}${total.isEmpty ? '' : ', total budget $amount'}',
    );
    await _ask(question, catalogComparison: true);
  }

  Future<bool> _confirmSharing({required bool catalogComparison}) async {
    if (catalogComparison ? _allowedCatalogForThisVisit : _allowedForThisVisit) {
      return true;
    }
    if (_askingPermission) return false;
    _askingPermission = true;
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(dialog.tr('使用 Q仔', 'Use Q仔')),
        content: Text(catalogComparison
            ? dialog.tr(
                '這次只會用人數和整份預算查詢 Coupon GO 套餐，不會把對話傳送給 DeepSeek。',
                'Coupon GO checks the package catalog using only the party size and total budget. This lookup does not send your chat to DeepSeek.',
              )
            : dialog.tr(
                '自由問答會把你的問題及最近最多 6 則對話傳送給 DeepSeek。符合固定格式的套餐比較直接查詢目錄。請勿輸入密碼、驗證碼、兌換碼或其他個人資料。',
                'Free-form questions and up to 6 recent messages are sent to DeepSeek. Fixed-format package comparisons check the catalog directly. Do not enter passwords, verification codes, redemption codes or other personal information.',
              )),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: Text(dialog.tr('取消', 'Cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialog, true),
            child: Text(dialog.tr('同意並送出', 'Agree and send')),
          ),
        ],
      ),
    );
    _askingPermission = false;
    if (!mounted || accepted != true) return false;
    if (catalogComparison) {
      _allowedCatalogForThisVisit = true;
    } else {
      _allowedForThisVisit = true;
    }
    return true;
  }

  Future<void> _ask(String input,
      {bool retry = false, bool catalogComparison = false}) async {
    final q = input.trim();
    if (_editingComparison || _sending || q.isEmpty ||
        !await _confirmSharing(catalogComparison: catalogComparison)) {
      return;
    }
    if (!mounted || _sending) return;
    final id = _conversation;
    final history = _messages.take(_messages.length - (retry ? 1 : 0)).toList();
    final previous = history.skip(history.length > 6 ? history.length - 6 : 0);
    final english = LanguageScope.of(context).english;
    setState(() {
      if (!retry) _messages.add(('user', q, null, const []));
      _sending = true;
      _failed = false;
      _rateLimited = false;
      _invalidInput = false;
      // A suggested question should not erase a different draft in the composer.
      if (!catalogComparison && !retry && _ctrl.text.trim() == q) _ctrl.clear();
    });
    _scrollDown();
    try {
      final data = await widget.api.post('/qbot/chat', body: {
        'message': q,
        'language': english ? 'en' : 'zh-Hant',
        'history': [
          if (!catalogComparison)
            for (final m in previous)
              {
                'role': m.$1,
                'text': m.$2.characters.take(1100).toString(),
              },
        ],
      }).timeout(const Duration(seconds: 35));
      if (!mounted || id != _conversation) return;
      final answer = data['answer'];
      if (answer is! String || answer.trim().isEmpty) {
        throw const FormatException('missing answer');
      }
      setState(() => _messages.add((
            'assistant',
            answer,
            data['points_balance'] is int ? '${data['points_balance']}' : null,
            QBotOffer.parse(data['offers']),
          )));
    } catch (error) {
      if (mounted && id == _conversation) {
        setState(() {
          _failed = true;
          _failedComparison = catalogComparison;
          _rateLimited = error is ApiException && error.status == 429;
          _invalidInput = error is ApiException && error.status == 422;
          if (_invalidInput) {
            if (!catalogComparison && _ctrl.text.isEmpty) _ctrl.text = q;
            if (_messages.isNotEmpty && _messages.last.$1 == 'user') {
              _messages.removeLast();
            }
          }
        });
      }
    } finally {
      if (mounted && id == _conversation) {
        final following = !_scroll.hasClients || _scroll.position.extentAfter < 80;
        setState(() => _sending = false);
        if (following) _scrollDown();
      }
    }
  }

  Future<void> _openOffer(QBotOffer offer) async {
    if (_openingOffer != null) return;
    final generation = _conversation;
    setState(() => _openingOffer = offer);
    try {
      final Widget page;
      switch (offer.kind) {
        case QBotOfferKind.coupon:
          final data = await widget.api.couponDetails(offer.id);
          final raw = data['coupon'];
          if (raw is! Map<String, dynamic> || raw['id'] != offer.id) {
            throw const FormatException('invalid coupon');
          }
          page = CouponDetailScreen(api: widget.api, coupon: Coupon.fromJson(raw));
        case QBotOfferKind.package:
          page = PackageDetailScreen(api: widget.api, packageId: offer.id);
        case QBotOfferKind.bargain:
          final rows = await widget.api.bargainCampaigns();
          final raw = rows.whereType<Map<String, dynamic>>()
              .where((row) => row['id'] == offer.id).firstOrNull;
          if (raw == null || raw['status'] != 'active') {
            throw const FormatException('unavailable campaign');
          }
          final campaign = BargainCampaign.fromJson(raw);
          if (campaign.isSoldOut ||
              !(campaign.expiresAt?.isAfter(DateTime.now()) ?? false)) {
            throw const FormatException('unavailable campaign');
          }
          page = BargainCampaignScreen(api: widget.api, campaign: campaign);
      }
      if (!mounted || generation != _conversation) return;
      await Navigator.of(context).push(MaterialPageRoute(builder: (_) => page));
    } catch (_) {
      if (mounted && generation == _conversation) {
        showCgToast(context, context.tr('暫時未能開啟，請重試。',
            'Could not open this offer. Try again.'));
      }
    } finally {
      if (mounted && generation == _conversation) {
        setState(() => _openingOffer = null);
      }
    }
  }

  Widget _offerTile(QBotOffer offer) {
    final label = switch (offer.kind) {
      QBotOfferKind.coupon => context.tr('優惠券', 'Coupon'),
      QBotOfferKind.package => context.tr('套餐', 'Package'),
      QBotOfferKind.bargain => context.tr('砍價', 'Bargain'),
    };
    return Card(
      margin: const EdgeInsets.only(top: 8),
      child: ListTile(
        key: ValueKey('qbot-offer-${offer.kind.name}-${offer.id}'),
        enabled: _openingOffer == null,
        title: Text(offer.name),
        subtitle: Text('$label · ${offer.shop}'),
        trailing: identical(_openingOffer, offer)
            ? const SizedBox.square(dimension: 18,
                child: CircularProgressIndicator(strokeWidth: 2))
            : const Icon(Icons.chevron_right),
        onTap: () => _openOffer(offer),
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    return Scaffold(
      appBar: AppBar(title: const Text('Q仔'), actions: [
        IconButton(
            tooltip: t('清除對話', 'Clear chat'),
            icon: const Icon(Icons.delete_outline),
            onPressed: _messages.isEmpty
                ? null
                : () => setState(() {
                      _conversation++;
                      _messages.clear();
                      _openingOffer = null;
                      _sending = false;
                      _failed = false;
                      _failedComparison = false;
                      _rateLimited = false;
                      _invalidInput = false;
                    })),
      ]),
      body: SafeArea(
          child: Center(child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 720),
          child: Column(children: [
        Expanded(
            child: ListView(
                controller: _scroll,
                padding: const EdgeInsets.all(16),
                children: [
              Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
                Image.asset('assets/images/img_logo_cropped.png',
                    height: 48, width: 56, excludeFromSemantics: true),
                const SizedBox(width: 12),
                Expanded(child: Text(widget.api.isLoggedIn
                  ? t('我係 Q仔，可以幫你搵優惠券、查積分，或者講解點用券。',
                      'I can help you find coupons, check points, and redeem offers.')
                  : t('我係 Q仔，可以幫你搵優惠券，或者講解點用券。',
                      'I can help you find coupons and explain how to use them.'),
                    style: Theme.of(context).textTheme.titleMedium)),
              ]),
              const SizedBox(height: 8),
              Text(
                  t('固定格式的套餐比較直接查詢目錄；自由問答由 DeepSeek 回覆。價格及供應以套餐詳情為準。',
                      'Fixed-format package comparisons check the catalog. DeepSeek answers free-form questions. Check package details for the current price and availability.'),
                  style: Theme.of(context).textTheme.bodySmall),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  ActionChip(
                    key: const ValueKey('qbot-compare-packages'),
                    label: Text(t('比較套餐', 'Compare packages')),
                    onPressed: _sending ? null : _openComparison,
                  ),
                  if (_messages.isEmpty)
                    for (final question in [
                      t('有咩優惠', 'What offers are available?'),
                      if (widget.api.isLoggedIn)
                        t('我有幾多積分', 'How many points do I have?')
                      else
                        t('點樣領取優惠券', 'How do I claim a coupon?'),
                      t('點樣用券', 'How do I use a coupon?'),
                    ])
                      ActionChip(
                        label: Text(question),
                        onPressed: () => _ask(question),
                      ),
                ],
              ),
              for (final m in _messages)
                Align(
                    alignment: m.$1 == 'user'
                        ? Alignment.centerRight
                        : Alignment.centerLeft,
                    child: Container(
                      margin: const EdgeInsets.symmetric(vertical: 6),
                      padding: const EdgeInsets.all(12),
                      constraints: BoxConstraints(
                          maxWidth: (MediaQuery.sizeOf(context).width * .82).clamp(0.0, 540.0).toDouble()),
                      decoration: BoxDecoration(
                          color: m.$1 == 'user'
                              ? CgColors.primary
                              : Colors.white,
                          borderRadius: BorderRadius.circular(12)),
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            if (m.$1 == 'user')
                              SelectableText(m.$2, style: const TextStyle(color: Colors.white))
                            else
                              SelectableText.rich(formatQBotAnswer(m.$2),
                                  style: const TextStyle(color: CgColors.textPrimary)),
                            if (m.$3 != null)
                              Text(t('你的積分：${m.$3}', 'Your points: ${m.$3}')),
                            for (final offer in m.$4) _offerTile(offer),
                          ]),
                    )),
              if (_sending)
                Semantics(
                    liveRegion: true,
                    child: Text(t('Q仔正在回覆…', 'Q仔 is answering…'))),
              if (_failed)
                Column(children: [
                  Semantics(
                    liveRegion: true,
                    child: Text(_invalidInput
                        ? t('問題無法處理，請修改後再送出。', 'Could not process this question. Edit it and send again.')
                        : _rateLimited
                            ? t('提問太頻密，請稍後再試。', 'Too many requests. Try again shortly.')
                            : t('暫時未能回答，請稍後再試。', 'Could not answer just now. Try again shortly.')),
                  ),
                  if (!_invalidInput)
                    TextButton(
                        onPressed: () =>
                            _ask(_messages.last.$2, retry: true,
                                catalogComparison: _failedComparison),
                        child: Text(t('重試', 'Retry'))),
                ]),
            ])),
        Padding(
            padding: const EdgeInsets.all(12),
            child: Row(children: [
              Expanded(
                  child: TextField(
                      controller: _ctrl,
                      maxLength: 1000,
                      textInputAction: TextInputAction.send,
                      decoration: InputDecoration(
                          hintText: t('問 Q仔…', 'Ask Q仔…'), counterText: ''),
                      onSubmitted: _ask)),
              ValueListenableBuilder<TextEditingValue>(
                  valueListenable: _ctrl,
                  builder: (context, value, _) => IconButton(
                      key: const ValueKey('qbot-send'),
                      tooltip: t('送出', 'Send'),
                      color: CgColors.primary,
                      onPressed: _sending || value.text.trim().isEmpty
                          ? null
                          : () => _ask(value.text),
                      icon: const Icon(Icons.send))),
            ])),
      ])))),
    );
  }
}
