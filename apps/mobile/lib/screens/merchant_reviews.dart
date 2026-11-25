import 'package:flutter/material.dart';

import '../api_client.dart';
import '../feature_flags.dart';
import '../language.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/merchant_layout.dart';
import '../widgets/widgets.dart';
import 'merchant_review_shops.dart';

enum _ReviewFilter { all, pending, replied, low, disputed }

/// Merchant workspace with a scoped public-list fallback for older servers.
class MerchantReviewsScreen extends StatefulWidget {
  const MerchantReviewsScreen({
    super.key,
    required this.api,
    this.shopRoster,
    this.expectedSessionRevision,
    this.canAdministerAllShops = false,
    this.onFirstPagePending,
    this.onPendingChanged,
    this.onPrivateAccessDenied,
  });

  final ApiClient api;
  final List<Map<String, dynamic>>? shopRoster;
  final int? expectedSessionRevision;
  final bool canAdministerAllShops;
  final ValueChanged<int>? onFirstPagePending;
  final VoidCallback? onPendingChanged;
  final VoidCallback? onPrivateAccessDenied;

  @override
  State<MerchantReviewsScreen> createState() => _MerchantReviewsScreenState();
}

class _MerchantReviewsScreenState extends State<MerchantReviewsScreen> {
  final Map<int, Map<int, Review>> _disputeReceipts = {};
  final Map<int, int> _firstPagePending = {};
  late final int _session;
  Map<int, String?> _shops = {};
  int? _shopId;
  int _generation = 0;
  int _page = 1;
  int _pages = 1;
  int? _total;
  int? _shopPending;
  bool _serverScoped = false;
  bool _merchantRouteMissing = false;
  bool _forbidden = false;
  ShopRating? _rating;
  bool _loading = true;
  bool _failed = false;
  String _sort = 'latest';
  _ReviewFilter _filter = _ReviewFilter.pending;
  List<Review> _rows = [];
  String? _operationSuccess;

  bool get _current => mounted && widget.api.sessionRevision == _session;
  bool _accept(int generation) => _current && _generation == generation;

  @override
  void initState() {
    super.initState();
    _session = widget.expectedSessionRevision ?? widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    _loadRoster();
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    super.dispose();
  }

  void _sessionChanged() {
    if (_current || !mounted) return;
    setState(() {
      _generation++;
      _shops = {};
      _shopId = null;
      _rows = [];
      _disputeReceipts.clear();
      _firstPagePending.clear();
      _total = null;
      _shopPending = null;
      _forbidden = false;
      _rating = null;
      _operationSuccess = null;
    });
  }

  Future<void> _loadRoster() async {
    final generation = ++_generation;
    if (!_current) return;
    setState(() {
      _loading = true;
      _failed = false;
    });
    try {
      final supplied = widget.shopRoster;
      final rows = supplied ??
          ((await widget.api.get('/merchant/shops'))['shops'] as List)
              .whereType<Map>()
              .map((row) => Map<String, dynamic>.from(row))
              .toList();
      if (!_accept(generation)) return;
      final shops = MerchantReviewShopPicker.allowedShops(
        rows,
        canAdministerAllShops: supplied != null && widget.canAdministerAllShops,
      );
      setState(() {
        _shops = shops;
        _shopId = shops.keys.firstOrNull;
        _loading = _shopId != null;
      });
      if (_shopId != null) await _loadPage(page: 1);
    } catch (_) {
      if (_accept(generation)) {
        setState(() {
          _loading = false;
          _failed = true;
        });
      }
    }
  }

  Future<void> _loadRating(int shopId, int generation) async {
    try {
      final value = await widget.api.shopRating(shopId);
      if (_accept(generation)) {
        setState(() => _rating = ShopRating.fromJson(value));
      }
    } catch (_) {
      // A missing aggregate is omitted; it is not a zero rating.
    }
  }

  Future<void> _loadPage({
    int? page,
    bool refreshRating = true,
    bool keepConfirmation = false,
  }) async {
    final shop = _shopId;
    if (!_current || shop == null) return;
    final target = page ?? _page;
    final generation = ++_generation;
    final filter = _filter;
    final sort = _sort;
    setState(() {
      _loading = true;
      _failed = false;
      _forbidden = false;
      _rows = [];
      _total = null;
      if (keepConfirmation) _shopPending = null;
      if (!keepConfirmation) _operationSuccess = null;
    });
    try {
      Map<String, dynamic>? payload;
      if (!publicTrial && !_merchantRouteMissing) {
        try {
          payload = await widget.api.merchantReviews(
            shop,
            page: target,
            sort: sort,
            status: switch (filter) {
              _ReviewFilter.all => 'all',
              _ReviewFilter.disputed => 'disputed',
              _ => 'visible',
            },
            unreplied: switch (filter) {
              _ReviewFilter.pending => true,
              _ReviewFilter.replied => false,
              _ => null,
            },
            maxStars: filter == _ReviewFilter.low ? 2 : null,
          );
          if (!_accept(generation)) return;
          if (payload['shop_id'] != shop) {
            throw const FormatException('Merchant review scope mismatch');
          }
        } on ApiException catch (error) {
          if (!_accept(generation)) return;
          if (error.status != 404) rethrow;
          _merchantRouteMissing = true;
        }
      }
      final serverScoped = payload != null;
      payload ??= await widget.api.shopReviews(shop, page: target, sort: sort);
      if (!_accept(generation)) return;
      final received = (payload['reviews'] as List)
          .map(
              (item) => Review.fromJson(Map<String, dynamic>.from(item as Map)))
          .where((row) => row.id > 0 && row.shopId == shop)
          .where((row) => !serverScoped
              ? row.status.isEmpty || row.status == 'visible'
              : switch (filter) {
                  _ReviewFilter.all =>
                    row.status == 'visible' || row.status == 'disputed',
                  _ReviewFilter.disputed => row.status == 'disputed',
                  _ => row.status == 'visible',
                })
          .toList();
      final current = payload['current_page'];
      final pages = payload['total_pages'];
      final total = payload['total'];
      setState(() {
        final excluded = _disputeReceipts[shop] ?? const <int, Review>{};
        _serverScoped = serverScoped;
        if (!serverScoped) _shopPending = null;
        _rows = serverScoped
            ? received
            : received.where((row) => !excluded.containsKey(row.id)).toList();
        _page = current is int && current > 0 ? current : target;
        _pages = pages is int && pages >= _page ? pages : _page;
        _total = total is int && total >= 0 ? total : null;
        _loading = false;
      });
      if (refreshRating) _loadRating(shop, generation);
      if (serverScoped) {
        await _loadPending(shop, generation);
      } else {
        _publishFirstPagePending();
      }
    } catch (error) {
      if (!_accept(generation)) return;
      final denied =
          error is ApiException && (error.status == 401 || error.status == 403);
      setState(() {
        _loading = false;
        _failed = true;
        _forbidden = denied;
        _shopPending = null;
        _rating = null;
        _rows = [];
        if (denied) {
          _disputeReceipts.clear();
          _firstPagePending.clear();
        }
      });
      if (denied) widget.onPrivateAccessDenied?.call();
    }
  }

  Future<void> _loadPending(int shop, int generation) async {
    int? value;
    try {
      value = await widget.api.merchantReviewPendingCount(shopId: shop);
    } catch (_) {
      // An unavailable count cannot be inferred from one filtered page.
    }
    if (_accept(generation)) setState(() => _shopPending = value);
  }

  void _selectFilter(_ReviewFilter filter) {
    if (filter == _filter) return;
    setState(() {
      _filter = filter;
      _operationSuccess = null;
    });
    if ((!publicTrial && !_merchantRouteMissing) || _failed) {
      _loadPage(page: 1);
    }
  }

  void _publishFirstPagePending() {
    final shop = _shopId;
    if (!_current || shop == null || _page != 1 || _sort != 'latest') return;
    _firstPagePending[shop] =
        _rows.where((review) => review.replyText.isEmpty).length;
    widget.onFirstPagePending?.call(
        _firstPagePending.values.fold(0, (total, count) => total + count));
  }

  void _switchShop(int shop) {
    if (!_shops.containsKey(shop) || shop == _shopId) return;
    setState(() {
      _shopId = shop;
      _total = null;
      _shopPending = null;
      _rating = null;
      _page = 1;
      _pages = 1;
    });
    _loadPage(page: 1);
  }

  List<Review> get _visible {
    if (_serverScoped) return _rows;
    if (_filter == _ReviewFilter.disputed) {
      return _disputeReceipts[_shopId]?.values.toList().reversed.toList() ?? [];
    }
    return _rows.where((review) {
      return switch (_filter) {
        _ReviewFilter.all => true,
        _ReviewFilter.pending => review.replyText.isEmpty,
        _ReviewFilter.replied => review.replyText.isNotEmpty,
        _ReviewFilter.low => review.overall > 0 && review.overall <= 2,
        _ReviewFilter.disputed => false,
      };
    }).toList();
  }

  String _filterLabel(BuildContext context, _ReviewFilter filter) =>
      switch (filter) {
        _ReviewFilter.all => context.tr('全部', 'All'),
        _ReviewFilter.pending => context.tr('待回覆', 'Awaiting reply'),
        _ReviewFilter.replied => context.tr('已回覆', 'Replied'),
        _ReviewFilter.low => context.tr('差評（≤2）', 'Low ratings (≤2)'),
        _ReviewFilter.disputed => context.tr('已申訴', 'Disputed'),
      };

  Future<void> _edit(Review review, {required bool dispute}) async {
    if (!_current || publicTrial || review.shopId != _shopId) return;
    final generation = _generation;
    final receipt = await showModalBottomSheet<Review>(
      context: context,
      isScrollControlled: true,
      isDismissible: false,
      enableDrag: false,
      builder: (_) => _ReviewActionSheet(
        api: widget.api,
        review: review,
        session: _session,
        dispute: dispute,
      ),
    );
    if (receipt == null ||
        !_accept(generation) ||
        receipt.id != review.id ||
        receipt.shopId != _shopId) {
      return;
    }
    setState(() {
      if (dispute) {
        if (!_serverScoped) {
          (_disputeReceipts[_shopId!] ??= {})[receipt.id] = receipt;
        }
        _rows.removeWhere((row) => row.id == receipt.id);
        // The receipt confirms this row only, not the current aggregate.
        _total = null;
        _rating = null;
      } else {
        _rows = [
          for (final row in _rows)
            if (row.id == receipt.id) receipt else row,
        ];
      }
      _operationSuccess = dispute
          ? context.tr('申訴已提交', 'Dispute submitted')
          : context.tr('回覆已發送', 'Reply sent');
    });
    if (_serverScoped) {
      widget.onPendingChanged?.call();
      await _loadPage(keepConfirmation: true);
    } else {
      _publishFirstPagePending();
      if (dispute) _loadRating(receipt.shopId, generation);
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = context;
    final shop = _shopId;
    final count = _rows.where((row) => row.replyText.isEmpty).length;
    final title = c.tr('評價管理', 'Manage reviews');
    final reviews = _visible;
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: CgTheme.listMaxWidth),
          child: !_current
              ? Center(
                  child: Text(c.tr('帳戶已切換，請返回重試。',
                      'The account changed. Go back and try again.')))
              : shop == null && !_loading && !_failed
                  ? Center(child: Text(c.tr('沒有可管理的門店。', 'No eligible shops.')))
                  : RefreshIndicator(
                      onRefresh: shop == null ? _loadRoster : _loadPage,
                      child: ListView(
                        physics: const AlwaysScrollableScrollPhysics(),
                        padding: const EdgeInsets.all(CgTheme.pageInset),
                        children: [
                          if (_shops.length > 1)
                            DropdownButtonFormField<int>(
                              initialValue: shop,
                              isExpanded: true,
                              decoration: InputDecoration(
                                  labelText: c.tr('門店', 'Shop')),
                              onChanged: (id) {
                                if (id != null) _switchShop(id);
                              },
                              items: [
                                for (final entry in _shops.entries)
                                  DropdownMenuItem(
                                    value: entry.key,
                                    child: Text(entry.value ??
                                        c.tr('門店 #${entry.key}',
                                            'Shop #${entry.key}')),
                                  ),
                              ],
                            ),
                          if (_shops.length == 1 && _shops[shop] != null)
                            Text(_shops[shop]!,
                                style: Theme.of(c).textTheme.titleMedium),
                          if (!_loading && !_failed) ...[
                            const SizedBox(height: CgTheme.space12),
                            _ReviewSummary(
                              rating: _rating?.overall,
                              total: _total,
                              pending: _serverScoped ? _shopPending : count,
                              complete: _serverScoped,
                            ),
                          ],
                          const SizedBox(height: CgTheme.space12),
                          SingleChildScrollView(
                            scrollDirection: Axis.horizontal,
                            child: Row(
                              children: [
                                for (final filter in _ReviewFilter.values)
                                  Padding(
                                    padding: const EdgeInsets.only(
                                        right: CgTheme.space8),
                                    child: ChoiceChip(
                                      label: Text(_filterLabel(c, filter)),
                                      selected: _filter == filter,
                                      onSelected: (_) => _selectFilter(filter),
                                    ),
                                  ),
                              ],
                            ),
                          ),
                          const SizedBox(height: CgTheme.space12),
                          if ((_serverScoped ||
                                  _filter != _ReviewFilter.disputed) &&
                              shop != null)
                            DropdownButtonFormField<String>(
                              initialValue: _sort,
                              decoration: InputDecoration(
                                labelText: c.tr('排序', 'Sort'),
                              ),
                              onChanged: (value) {
                                if (value == null || value == _sort) return;
                                setState(() => _sort = value);
                                _loadPage(page: 1);
                              },
                              items: [
                                DropdownMenuItem(
                                    value: 'latest',
                                    child: Text(c.tr('最新', 'Latest'))),
                                DropdownMenuItem(
                                    value: 'helpful',
                                    child: Text(c.tr('最有用', 'Most helpful'))),
                              ],
                            ),
                          const SizedBox(height: CgTheme.space12),
                          if (_operationSuccess != null)
                            Semantics(
                                liveRegion: true,
                                child: Text(_operationSuccess!)),
                          if (!_serverScoped &&
                              !_loading &&
                              !_failed &&
                              _filter == _ReviewFilter.disputed)
                            Text(c.tr('此處只顯示本次開啟頁面時提交的申訴。',
                                'This view shows disputes submitted while this workspace is open.')),
                          if (_loading) ...[
                            const SkeletonBox(height: 124),
                            const SizedBox(height: CgTheme.space8),
                            const SkeletonBox(height: 124),
                          ] else if (_failed) ...[
                            Text(_forbidden
                                ? c.tr('無法管理此門店的評價。',
                                    'You cannot manage reviews for this shop.')
                                : c.tr('未能載入評價。', 'Could not load reviews.')),
                            Align(
                              alignment: Alignment.centerLeft,
                              child: OutlinedButton(
                                onPressed:
                                    shop == null ? _loadRoster : _loadPage,
                                child: Text(c.tr('重試', 'Try again')),
                              ),
                            ),
                          ] else ...[
                            if (reviews.isEmpty)
                              Padding(
                                padding: const EdgeInsets.symmetric(
                                    vertical: CgTheme.space16),
                                child: Text(_serverScoped
                                    ? c.tr('沒有符合條件的評價。', 'No matching reviews.')
                                    : _filter == _ReviewFilter.disputed
                                        ? c.tr('本次沒有提交申訴。',
                                            'No disputes submitted here yet.')
                                        : c.tr('本頁沒有符合條件的評價。',
                                            'No matching reviews on this page.')),
                              ),
                            for (final review in reviews)
                              _MerchantReviewCard(
                                key: ValueKey('merchant-review-${review.id}'),
                                api: widget.api,
                                review: review,
                                onReply:
                                    publicTrial || review.status == 'disputed'
                                        ? null
                                        : () => _edit(review, dispute: false),
                                onDispute:
                                    publicTrial || review.status == 'disputed'
                                        ? null
                                        : () => _edit(review, dispute: true),
                              ),
                            if ((_serverScoped ||
                                    _filter != _ReviewFilter.disputed) &&
                                _pages > 1)
                              Wrap(
                                alignment: WrapAlignment.center,
                                crossAxisAlignment: WrapCrossAlignment.center,
                                children: [
                                  TextButton(
                                    onPressed: _page > 1
                                        ? () => _loadPage(page: _page - 1)
                                        : null,
                                    child: Text(c.tr('上一頁', 'Previous')),
                                  ),
                                  Text(c.tr('第 $_page / $_pages 頁',
                                      'Page $_page of $_pages')),
                                  TextButton(
                                    onPressed: _page < _pages
                                        ? () => _loadPage(page: _page + 1)
                                        : null,
                                    child: Text(c.tr('下一頁', 'Next')),
                                  ),
                                ],
                              ),
                          ],
                        ],
                      ),
                    ),
        ),
      ),
    );
  }
}

class _ReviewSummary extends StatelessWidget {
  const _ReviewSummary({
    required this.rating,
    required this.total,
    required this.pending,
    required this.complete,
  });
  final double? rating;
  final int? total;
  final int? pending;
  final bool complete;

  @override
  Widget build(BuildContext context) => Card(
        margin: EdgeInsets.zero,
        child: Padding(
          padding: const EdgeInsets.all(CgTheme.space16),
          child: Wrap(
            spacing: CgTheme.space16,
            runSpacing: CgTheme.space8,
            children: [
              if (rating != null)
                Text(context.tr('${rating!.toStringAsFixed(1)} 分',
                    '${rating!.toStringAsFixed(1)} / 5')),
              if (total != null)
                Text(complete
                    ? context.tr('符合條件 $total 條', '$total matching reviews')
                    : context.tr('共 $total 條公開評價', '$total public reviews')),
              if (pending != null)
                Text(complete
                    ? context.tr('$pending 條待回覆', '$pending awaiting reply')
                    : context.tr('本頁 $pending 條待回覆',
                        '$pending awaiting reply on this page')),
            ],
          ),
        ),
      );
}

class _MerchantReviewCard extends StatefulWidget {
  const _MerchantReviewCard({
    super.key,
    required this.api,
    required this.review,
    this.onReply,
    this.onDispute,
  });

  final ApiClient api;
  final Review review;
  final VoidCallback? onReply;
  final VoidCallback? onDispute;

  @override
  State<_MerchantReviewCard> createState() => _MerchantReviewCardState();
}

class _MerchantReviewCardState extends State<_MerchantReviewCard> {
  bool _expanded = false;

  Uri? _imageUri(String value) {
    final uri = Uri.tryParse(value);
    if (uri == null) return null;
    if (uri.hasAuthority && !uri.hasScheme) return null;
    final resolved = Uri.parse(widget.api.baseUrl).resolveUri(uri);
    if (!const {'http', 'https'}.contains(resolved.scheme) ||
        !resolved.hasAuthority ||
        resolved.userInfo.isNotEmpty) {
      return null;
    }
    return resolved;
  }

  Widget _image(Uri uri, {double? size}) => Image.network(
        uri.toString(),
        width: size,
        height: size,
        fit: size == null ? BoxFit.contain : BoxFit.cover,
        errorBuilder: (context, error, stackTrace) => SizedBox(
          width: size,
          height: size ?? 120,
          child: const Icon(Icons.broken_image_outlined),
        ),
      );

  @override
  Widget build(BuildContext context) {
    final review = widget.review;
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final score = review.overall.toStringAsFixed(1);
    final created = review.createdAt;
    final replyAt = review.repliedAt;
    final withReply = review.replyText.isNotEmpty;
    final disputed = review.status == 'disputed';
    final photos = review.images.map(_imageUri).whereType<Uri>().toList();
    return Card(
      margin: const EdgeInsets.only(bottom: CgTheme.space12),
      child: Padding(
        padding: const EdgeInsets.all(CgTheme.space16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Wrap(
              crossAxisAlignment: WrapCrossAlignment.center,
              spacing: CgTheme.space12,
              runSpacing: CgTheme.space4,
              children: [
                Text(
                    review.userName.trim().isEmpty
                        ? context.tr('顧客', 'Customer')
                        : review.userName,
                    style: theme.textTheme.titleMedium),
                if (review.overall > 0)
                  Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.star, size: 18, color: CgColors.amber),
                      Text('$score/5'),
                    ],
                  ),
                if (created != null)
                  Text(reviewLocalDate(created),
                      style: theme.textTheme.labelMedium),
                if (disputed)
                  MerchantStatusPill(
                    label: context.tr('申訴中', 'Under dispute'),
                    active: false,
                  ),
              ],
            ),
            if (review.taste > 0 &&
                review.ambience > 0 &&
                review.service > 0) ...[
              const SizedBox(height: CgTheme.space8),
              Text(
                  context.tr(
                    '口味 ${review.taste} · 環境 ${review.ambience} · 服務 ${review.service}',
                    'Taste ${review.taste} · Ambience ${review.ambience} · Service ${review.service}',
                  ),
                  style: theme.textTheme.bodySmall),
            ],
            if (review.couponName?.trim().isNotEmpty == true) ...[
              const SizedBox(height: CgTheme.space8),
              Text(review.couponName!),
            ],
            if (review.content.isNotEmpty) ...[
              const SizedBox(height: CgTheme.space8),
              LayoutBuilder(builder: (context, constraints) {
                final style = theme.textTheme.bodyMedium;
                final painter = TextPainter(
                  text: TextSpan(text: review.content, style: style),
                  textDirection: Directionality.of(context),
                  textScaler: MediaQuery.textScalerOf(context),
                  maxLines: 3,
                )..layout(maxWidth: constraints.maxWidth);
                final overflows = painter.didExceedMaxLines;
                painter.dispose();
                return Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(review.content,
                        style: style,
                        maxLines: _expanded ? null : 3,
                        overflow: _expanded
                            ? TextOverflow.visible
                            : TextOverflow.ellipsis),
                    if (overflows)
                      TextButton(
                        onPressed: () => setState(() => _expanded = !_expanded),
                        child: Text(_expanded
                            ? context.tr('收合', 'Show less')
                            : context.tr('展開', 'Read more')),
                      ),
                  ],
                );
              }),
            ],
            if (photos.isNotEmpty) ...[
              const SizedBox(height: CgTheme.space12),
              Wrap(
                spacing: CgTheme.space8,
                runSpacing: CgTheme.space8,
                children: [
                  for (final photo in photos)
                    InkWell(
                      onTap: () => showDialog<void>(
                        context: context,
                        builder: (context) => Dialog(
                          child: Stack(
                            alignment: Alignment.topRight,
                            children: [
                              InteractiveViewer(child: _image(photo)),
                              IconButton.filled(
                                tooltip: context.tr('關閉', 'Close'),
                                onPressed: () => Navigator.pop(context),
                                icon: const Icon(Icons.close),
                              ),
                            ],
                          ),
                        ),
                      ),
                      child: ClipRRect(
                        borderRadius: BorderRadius.circular(8),
                        child: _image(photo, size: 64),
                      ),
                    ),
                ],
              ),
            ],
            if (withReply) ...[
              const SizedBox(height: CgTheme.space12),
              Container(
                width: double.infinity,
                decoration: BoxDecoration(
                  color: colors.surfaceContainerLow,
                  borderRadius: BorderRadius.circular(8),
                ),
                padding: const EdgeInsets.all(CgTheme.space12),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                        [
                          context.tr('商戶回覆', 'Merchant reply'),
                          if (replyAt != null) reviewLocalDate(replyAt),
                        ].join(' · '),
                        style: theme.textTheme.labelLarge),
                    const SizedBox(height: CgTheme.space4),
                    Text(review.replyText),
                  ],
                ),
              ),
            ],
            const SizedBox(height: CgTheme.space8),
            Wrap(
              spacing: CgTheme.space8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                Text(
                    context.tr('有用 ${review.helpfulCount}',
                        'Helpful ${review.helpfulCount}'),
                    style: theme.textTheme.labelMedium),
                if (widget.onReply != null)
                  TextButton(
                    onPressed: widget.onReply,
                    child: Text(withReply
                        ? context.tr('修改回覆', 'Edit reply')
                        : context.tr('回覆', 'Reply')),
                  ),
                if (widget.onDispute != null || disputed)
                  TextButton(
                    onPressed: widget.onDispute,
                    child: Text(context.tr('申訴', 'Dispute')),
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

class _ReviewActionSheet extends StatefulWidget {
  const _ReviewActionSheet({
    required this.api,
    required this.review,
    required this.session,
    required this.dispute,
  });

  final ApiClient api;
  final Review review;
  final int session;
  final bool dispute;

  @override
  State<_ReviewActionSheet> createState() => _ReviewActionSheetState();
}

class _ReviewActionSheetState extends State<_ReviewActionSheet> {
  late final TextEditingController _text;
  bool _sending = false;
  String? _error;

  bool get _current => mounted && widget.api.sessionRevision == widget.session;

  @override
  void initState() {
    super.initState();
    _text = TextEditingController(
      text: widget.dispute ? '' : widget.review.replyText,
    );
  }

  @override
  void dispose() {
    _text.dispose();
    super.dispose();
  }

  Future<void> _send() async {
    final body = _text.text.trim();
    if (!_current || _sending || publicTrial || body.isEmpty) return;
    setState(() {
      _sending = true;
      _error = null;
    });
    try {
      final payload = widget.dispute
          ? await widget.api.disputeReview(widget.review.id, body)
          : await widget.api.replyReview(widget.review.id, body);
      if (!_current) return;
      final receipt =
          Review.fromJson(Map<String, dynamic>.from(payload['review'] as Map));
      if (receipt.id != widget.review.id ||
          receipt.shopId != widget.review.shopId ||
          (widget.dispute
              ? receipt.status != 'disputed'
              : receipt.replyText.isEmpty)) {
        throw const FormatException('Unexpected review receipt');
      }
      setState(() => _sending = false);
      if (!mounted) return;
      Navigator.of(context).pop(receipt);
    } catch (error) {
      if (!_current) return;
      setState(() {
        _sending = false;
        _error = error is ApiException
            ? error.message
            : context.tr(
                '未能確認結果，請重試。', 'Could not confirm the result. Try again.');
      });
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.api,
        builder: (context, _) => PopScope(
          canPop: !_current || !_sending,
          child: SafeArea(
            child: Padding(
              padding: EdgeInsets.only(
                  bottom: MediaQuery.viewInsetsOf(context).bottom),
              child: SingleChildScrollView(
                padding: const EdgeInsets.all(CgTheme.space24),
                child: !_current
                    ? Column(
                        children: [
                          Text(context.tr('帳戶已切換。', 'The account changed.')),
                          TextButton(
                            onPressed: () => Navigator.pop(context),
                            child: Text(context.tr('關閉', 'Close')),
                          ),
                        ],
                      )
                    : Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          Text(
                              widget.dispute
                                  ? context.tr('申訴評價', 'Dispute review')
                                  : widget.review.replyText.isEmpty
                                      ? context.tr('回覆評價', 'Reply to review')
                                      : context.tr('修改回覆', 'Edit reply'),
                              style: Theme.of(context).textTheme.titleLarge),
                          const SizedBox(height: CgTheme.space12),
                          if (widget.review.content.isNotEmpty) ...[
                            Text(widget.review.content),
                            const SizedBox(height: CgTheme.space12),
                          ],
                          if (widget.dispute) ...[
                            Text(context.tr('申訴期間此評價暫不計入星級。',
                                'This review will not count toward the rating during the dispute.')),
                            const SizedBox(height: CgTheme.space12),
                          ],
                          TextField(
                            controller: _text,
                            onChanged: (_) => setState(() {}),
                            enabled: !_sending,
                            maxLength: 500,
                            minLines: 2,
                            maxLines: 4,
                            decoration: InputDecoration(
                              labelText: widget.dispute
                                  ? context.tr(
                                      '請輸入申訴原因', 'Reason for the dispute')
                                  : context.tr('回覆內容', 'Your reply'),
                            ),
                          ),
                          if (_error != null)
                            Text(_error!,
                                style: TextStyle(
                                    color:
                                        Theme.of(context).colorScheme.error)),
                          Wrap(
                            spacing: CgTheme.space8,
                            alignment: WrapAlignment.end,
                            children: [
                              TextButton(
                                onPressed: _sending
                                    ? null
                                    : () => Navigator.pop(context),
                                child: Text(context.tr('取消', 'Cancel')),
                              ),
                              FilledButton(
                                onPressed: _sending || _text.text.trim().isEmpty
                                    ? null
                                    : _send,
                                child: Text(_sending
                                    ? context.tr('處理中…', 'Sending…')
                                    : widget.dispute
                                        ? context.tr('提交申訴', 'Submit dispute')
                                        : context.tr('發送回覆', 'Send reply')),
                              ),
                            ],
                          ),
                        ],
                      ),
              ),
            ),
          ),
        ),
      );
}
