import 'package:flutter/material.dart';

import '../api_client.dart';
import '../discovery_location.dart';
import '../language.dart';
import '../models.dart';
import '../qbot_offers.dart';
import '../widgets/widgets.dart';
import '../widgets/shop_discovery_card.dart';
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
    if (match.start > offset) {
      spans.add(TextSpan(text: text.substring(offset, match.start)));
    }
    spans.add(TextSpan(
        text: match[1], style: const TextStyle(fontWeight: FontWeight.bold)));
    offset = match.end;
  }
  if (offset < text.length) spans.add(TextSpan(text: text.substring(offset)));
  return TextSpan(children: spans);
}

typedef _PackageComparison = ({int people, String budget});

class _PackageComparisonDialog extends StatefulWidget {
  const _PackageComparisonDialog();

  @override
  State<_PackageComparisonDialog> createState() =>
      _PackageComparisonDialogState();
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
                labelText: t(r'整份預算（HK$，可留空）', r'Total budget (HK$, optional)'),
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
  final _messages = <(String, String, String?, List<QBotOffer>, List<Shop>)>[];
  bool _locating = false;
  bool _failedLocal = false;
  ({double lat, double lng})? _location;
  ({double lat, double lng})? _devicePosition;
  String? _nearbyQuestion;
  static final _nearbyMention = RegExp(
      r'附近|周邊|周边|就近|離我最近|\b(?:nearby|near me|around me|closest)\b',
      caseSensitive: false);
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
          _scroll.animateTo(end,
              duration: const Duration(milliseconds: 180),
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

  Future<void> _chooseArea({String? followUp}) async {
    final area = await showModalBottomSheet<(String, String)>(
      context: context,
      showDragHandle: true,
      useSafeArea: true,
      sheetAnimationStyle: MediaQuery.disableAnimationsOf(context)
          ? AnimationStyle.noAnimation
          : null,
      builder: (sheet) => Padding(
        padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
        child: ListView(
          shrinkWrap: true,
          children: [
            Text(sheet.tr('選擇地區', 'Choose a district'),
                style: Theme.of(sheet).textTheme.titleLarge),
            const SizedBox(height: 12),
            Wrap(spacing: 8, runSpacing: 4, children: [
              for (final area in const [
                ('中環', 'Central'),
                ('灣仔', 'Wan Chai'),
                ('銅鑼灣', 'Causeway Bay'),
                ('尖沙咀', 'Tsim Sha Tsui'),
                ('旺角', 'Mong Kok'),
                ('觀塘', 'Kwun Tong'),
                ('沙田', 'Sha Tin'),
                ('荃灣', 'Tsuen Wan'),
                ('香港島', 'Hong Kong Island'),
                ('九龍', 'Kowloon'),
                ('新界', 'New Territories'),
              ])
                ActionChip(
                    label: Text(sheet.tr(area.$1, area.$2)),
                    onPressed: () => Navigator.pop(sheet, area)),
            ]),
          ],
        ),
      ),
    );
    if (!mounted || area == null) return;
    final t = context.tr;
    final question = followUp == null
        ? t('${area.$1}有咩商戶？', 'Shops in ${area.$2}?')
        : '${t(area.$1, area.$2)} · $followUp';
    await _ask(question, localLookup: true);
  }

  Future<void> _useLocation({String? followUp}) async {
    if (_locating || _sending || _askingPermission) return;
    final generation = _conversation;
    setState(() => _locating = true);
    try {
      final position = await findDiscoveryPosition();
      if (!mounted || generation != _conversation) return;
      if (position != null) {
        _devicePosition = position;
        await _ask(
            followUp ?? context.tr('我附近有咩商戶？', 'What shops are near me?'),
            localLookup: true,
            position: position);
        return;
      }
    } catch (_) {
      // Choosing a district is also available when the OS location fails.
    } finally {
      if (mounted) setState(() => _locating = false);
    }
    if (mounted && generation == _conversation) {
      showCgToast(
          context,
          context.tr('未能取得位置，可以手動選擇地區。',
              'Location is unavailable. You can choose a district instead.'));
    }
  }

  Future<bool> _confirmSharing({required bool catalogComparison}) async {
    if (catalogComparison
        ? _allowedCatalogForThisVisit
        : _allowedForThisVisit) {
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
                '自由問答可能會把你的問題及最近最多 6 則對話傳送給 DeepSeek。地區和固定格式的套餐比較直接查詢 Coupon GO 目錄。請勿輸入密碼、驗證碼、兌換碼或其他個人資料。',
                'Free-form questions and up to 6 recent messages may be sent to DeepSeek. District searches and fixed-format package comparisons check the Coupon GO catalog directly. Do not enter passwords, verification codes, redemption codes or other personal information.',
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
      {bool retry = false,
      bool catalogComparison = false,
      bool localLookup = false,
      ({double lat, double lng})? position}) async {
    final q = input.trim();
    final searchPosition = position ??
        (!localLookup && !catalogComparison && _nearbyMention.hasMatch(q)
            ? _devicePosition
            : null);
    final useCatalog = localLookup || searchPosition != null;
    if (_editingComparison ||
        _sending ||
        q.isEmpty ||
        (!useCatalog &&
            !await _confirmSharing(catalogComparison: catalogComparison))) {
      return;
    }
    if (!mounted || _sending) return;
    final id = _conversation;
    final history = _messages.take(_messages.length - (retry ? 1 : 0)).toList();
    final previous = history.skip(history.length > 6 ? history.length - 6 : 0);
    final english = LanguageScope.of(context).english;
    setState(() {
      if (!retry) _messages.add(('user', q, null, const [], const []));
      _location = searchPosition;
      _nearbyQuestion = null;
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
        if (searchPosition != null)
          'location': {'lat': searchPosition.lat, 'lng': searchPosition.lng},
        'history': [
          if (!catalogComparison && !useCatalog)
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
      setState(() {
        _nearbyQuestion = data['needs_location'] == true ? q : null;
        _messages.add((
          'assistant',
          answer,
          data['points_balance'] is int ? '${data['points_balance']}' : null,
          QBotOffer.parse(data['offers']),
          [
            if (data['places'] case final List places)
              for (final row
                  in places.whereType<Map<String, dynamic>>().take(6))
                if ((row['shop_id'] is int) && (row['shop_name'] is String))
                  Shop.fromJson(row),
          ],
        ));
      });
    } catch (error) {
      if (mounted && id == _conversation) {
        setState(() {
          _failed = true;
          _failedComparison = catalogComparison;
          _failedLocal = useCatalog;
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
        final following =
            !_scroll.hasClients || _scroll.position.extentAfter < 80;
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
          page =
              CouponDetailScreen(api: widget.api, coupon: Coupon.fromJson(raw));
        case QBotOfferKind.package:
          page = PackageDetailScreen(api: widget.api, packageId: offer.id);
        case QBotOfferKind.bargain:
          final rows = await widget.api.bargainCampaigns();
          final raw = rows
              .whereType<Map<String, dynamic>>()
              .where((row) => row['id'] == offer.id)
              .firstOrNull;
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
        showCgToast(context,
            context.tr('暫時未能開啟，請重試。', 'Could not open this offer. Try again.'));
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
            ? const SizedBox.square(
                dimension: 18, child: CircularProgressIndicator(strokeWidth: 2))
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
                      _failedLocal = false;
                      _location = null;
                      _devicePosition = null;
                      _nearbyQuestion = null;
                      _rateLimited = false;
                      _invalidInput = false;
                    })),
      ]),
      body: SafeArea(
          child: Center(
              child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 720),
                  child: Column(children: [
                    Expanded(
                        child: ListView(
                            controller: _scroll,
                            padding: const EdgeInsets.all(16),
                            children: [
                          if (_messages.isEmpty) ...[
                            Text(t('附近有咩優惠？', 'Where would you like to go?'),
                                style: Theme.of(context)
                                    .textTheme
                                    .headlineSmall
                                    ?.copyWith(fontWeight: FontWeight.w800)),
                            const SizedBox(height: 6),
                            Text(t('試下問「旺角有咩火鍋優惠？」',
                                'Try “Coffee deals near Mong Kok”.')),
                            const SizedBox(height: 12),
                          ],
                          Wrap(
                            spacing: 8,
                            runSpacing: 4,
                            children: [
                              ActionChip(
                                avatar: const Icon(Icons.my_location, size: 17),
                                label: Text(_locating
                                    ? t('定位中…', 'Locating…')
                                    : t('使用我的位置', 'Use my location')),
                                tooltip: t('以約略位置查 Coupon GO 附近商戶',
                                    'Find Coupon GO shops using approximate location'),
                                onPressed:
                                    _sending || _locating ? null : _useLocation,
                              ),
                              ActionChip(
                                avatar:
                                    const Icon(Icons.place_outlined, size: 17),
                                label: Text(t('選擇地區', 'Choose a district')),
                                onPressed:
                                    _sending || _locating ? null : _chooseArea,
                              ),
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
                                  margin:
                                      const EdgeInsets.symmetric(vertical: 6),
                                  padding: const EdgeInsets.all(12),
                                  constraints: BoxConstraints(
                                      maxWidth:
                                          (MediaQuery.sizeOf(context).width *
                                                  .82)
                                              .clamp(0.0, 540.0)
                                              .toDouble()),
                                  decoration: BoxDecoration(
                                      color: m.$1 == 'user'
                                          ? CgColors.primary
                                          : Colors.white,
                                      borderRadius: BorderRadius.circular(12)),
                                  child: Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      children: [
                                        if (m.$1 == 'user')
                                          SelectableText(m.$2,
                                              style: const TextStyle(
                                                  color: Colors.white))
                                        else
                                          SelectableText.rich(
                                              formatQBotAnswer(m.$2),
                                              style: const TextStyle(
                                                  color: CgColors.textPrimary)),
                                        if (m.$3 != null)
                                          Text(t('你的積分：${m.$3}',
                                              'Your points: ${m.$3}')),
                                        for (final offer in m.$4)
                                          _offerTile(offer),
                                        for (final shop in m.$5)
                                          Padding(
                                            padding:
                                                const EdgeInsets.only(top: 8),
                                            child: ShopDiscoveryCard(
                                              key: ValueKey(
                                                  'qbot-shop-${shop.id}'),
                                              shop: shop,
                                              apiBaseUrl: widget.api.baseUrl,
                                              onOpen: () =>
                                                  Navigator.of(context).push(
                                                MaterialPageRoute<void>(
                                                    builder: (page) => ShopDetailScreen(
                                                        api: widget.api,
                                                        shop: shop,
                                                        onOpenCoupon: (coupon) => Navigator
                                                                .of(page)
                                                            .push(MaterialPageRoute<
                                                                    void>(
                                                                builder: (_) =>
                                                                    CouponDetailScreen(
                                                                        api: widget
                                                                            .api,
                                                                        coupon:
                                                                            coupon))))),
                                              ),
                                            ),
                                          ),
                                      ]),
                                )),
                          if (_nearbyQuestion case final question?)
                            Wrap(spacing: 8, children: [
                              TextButton.icon(
                                onPressed: _locating
                                    ? null
                                    : () => _useLocation(followUp: question),
                                icon: const Icon(Icons.my_location),
                                label: Text(t('用我的位置搜尋', 'Search near me')),
                              ),
                              TextButton(
                                onPressed: _locating
                                    ? null
                                    : () => _chooseArea(followUp: question),
                                child: Text(t('改選地區', 'Pick a district')),
                              ),
                            ]),
                          if (_sending)
                            Semantics(
                                liveRegion: true,
                                child: Text(t('Q仔正在回覆…', 'Q仔 is answering…'))),
                          if (_failed)
                            Column(children: [
                              Semantics(
                                liveRegion: true,
                                child: Text(_invalidInput
                                    ? t('問題無法處理，請修改後再送出。',
                                        'Could not process this question. Edit it and send again.')
                                    : _rateLimited
                                        ? t('提問太頻密，請稍後再試。',
                                            'Too many requests. Try again shortly.')
                                        : t('暫時未能回答，請稍後再試。',
                                            'Could not answer just now. Try again shortly.')),
                              ),
                              if (!_invalidInput)
                                TextButton(
                                    onPressed: () => _ask(_messages.last.$2,
                                        retry: true,
                                        catalogComparison: _failedComparison,
                                        localLookup: _failedLocal,
                                        position: _location),
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
                                      hintText: t('問 Q仔…', 'Ask Q仔…'),
                                      counterText: ''),
                                  onSubmitted: _ask)),
                          ValueListenableBuilder<TextEditingValue>(
                              valueListenable: _ctrl,
                              builder: (context, value, _) => IconButton(
                                  key: const ValueKey('qbot-send'),
                                  tooltip: t('送出', 'Send'),
                                  color: CgColors.primary,
                                  onPressed:
                                      _sending || value.text.trim().isEmpty
                                          ? null
                                          : () => _ask(value.text),
                                  icon: const Icon(Icons.send))),
                        ])),
                  ])))),
    );
  }
}
