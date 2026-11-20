import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../api_client.dart';
import '../language.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';

/// Line B screens: 3-dim review form + shop reviews (F6 v1) and the
/// admin v1 management pages (F4 v1: onboarding review + read-only
/// dashboard). Gap 9: helpful votes + server-side star distribution.
/// Gap 11: the review form picks photos and uploads them through
/// POST /uploads/review-images (see ApiClient.uploadReviewImages)
/// instead of asking for image URLs. No Flutter SDK is available on
/// this machine, so this file has NOT been through a real-device build
/// (see the gap report for what is unverified).

String _stars(int n) => '★' * n + '☆' * (5 - n);

List<String> _splitList(String raw) => raw
    .split(RegExp(r'[,，\n]'))
    .map((e) => e.trim())
    .where((e) => e.isNotEmpty)
    .toList();

/// Review form: opened from a used (status 2) wallet item. Three 1-5
/// dimensions, text, tags, photos. Gap 11: photos are picked from the
/// camera roll (image_picker) and uploaded to
/// POST /uploads/review-images first; the returned paths are sent as
/// images[] to POST /reviews. The points checkbox is the
/// points/stars split: taking the +15 points means the review does NOT
/// count toward the shop rating.
class ReviewFormScreen extends StatefulWidget {
  const ReviewFormScreen(
      {super.key, required this.api, required this.item, this.redemptionId});

  final ApiClient api;
  final WalletItem item;
  final int? redemptionId;

  @override
  State<ReviewFormScreen> createState() => _ReviewFormScreenState();
}

class _ReviewFormScreenState extends State<ReviewFormScreen> {
  int _taste = 5;
  int _ambience = 5;
  int _service = 5;
  bool _claimPoints = true;
  final _content = TextEditingController();
  final _tags = TextEditingController();
  // Gap 11: picked photos (bytes) awaiting upload; uploaded server
  // paths are resolved at submit time via uploadReviewImages.
  final List<Uint8List> _pickedBytes = [];
  final List<String> _pickedNames = [];
  bool _submitting = false;

  @override
  void dispose() {
    _content.dispose();
    _tags.dispose();
    super.dispose();
  }

  Future<void> _pickImages() async {
    const maxImages = 6;
    if (_pickedBytes.length >= maxImages) {
      showCgToast(context, '圖片最多 6 張');
      return;
    }
    try {
      final files = await ImagePicker().pickMultiImage();
      if (files.isEmpty) return;
      final take = files.take(maxImages - _pickedBytes.length).toList();
      final bytes = <Uint8List>[];
      final names = <String>[];
      for (final f in take) {
        bytes.add(await f.readAsBytes());
        names.add(f.name);
      }
      if (!mounted) return;
      setState(() {
        _pickedBytes.addAll(bytes);
        _pickedNames.addAll(names);
      });
    } catch (_) {
      if (mounted) showCgToast(context, context.tr('未能打開相簿，請重試', 'Could not open your photos. Try again.'));
    }
  }

  Widget _dimRow(String label, int value, void Function(int) onPick) {
    return Row(
      children: [
        SizedBox(width: 56, child: Text(label)),
        ...List.generate(5, (i) {
          final n = i + 1;
          return IconButton(
            padding: EdgeInsets.zero,
            constraints: const BoxConstraints(minWidth: 36, minHeight: 36),
            icon: Icon(n <= value ? Icons.star : Icons.star_border,
                color: CgColors.amber),
            onPressed: () => setState(() => onPick(n)),
          );
        }),
        Text('$value 分', style: const TextStyle(color: CgColors.textSecondary)),
      ],
    );
  }

  Future<void> _submit() async {
    if (_submitting) return;
    if (_content.text.trim().length > 500) {
      showCgToast(context, '文字最多 500 字');
      return;
    }
    setState(() => _submitting = true);
    try {
      // Gap 11: upload picked photos first; the server returns the
      // /uploads/review-images/... paths to send as images[].
      var images = <String>[];
      if (_pickedBytes.isNotEmpty) {
        final uploaded = await widget.api.uploadReviewImages(_pickedBytes);
        images = uploaded.map((e) => '$e').toList();
      }
      final data = await widget.api.submitReview(
        walletItemId: widget.item.id,
        redemptionId: widget.redemptionId,
        taste: _taste,
        ambience: _ambience,
        service: _service,
        content: _content.text.trim(),
        images: images,
        tags: _splitList(_tags.text),
        claimPoints: _claimPoints,
      );
      if (!mounted) return;
      // UI_SPEC §7: points are separate from stars. The server message
      // already states the points; show the earned-points line plainly.
      final pointsLine = _claimPoints
          ? '已獲得 15 積分'
          : '評價已提交，已計入商戶星級';
      await showDialog<void>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('評價成功'),
          content: Text(
              '${data['message'] as String? ?? ''}\n$pointsLine'),
          actions: [
            TextButton(
                onPressed: () => Navigator.pop(c),
                child: const Text('確定')),
          ],
        ),
      );
      if (!mounted) return;
      Navigator.of(context).pop(true);
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('寫評價')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text(widget.item.couponName,
              style:
                  const TextStyle(fontSize: 18, fontWeight: FontWeight.bold)),
          Text(widget.item.shopName,
              style: const TextStyle(color: CgColors.textSecondary)),
          const SizedBox(height: 16),
          _dimRow('口味', _taste, (v) => _taste = v),
          _dimRow('環境', _ambience, (v) => _ambience = v),
          _dimRow('服務', _service, (v) => _service = v),
          const SizedBox(height: 12),
          TextField(
            controller: _content,
            maxLines: 4,
            maxLength: 500,
            decoration: const InputDecoration(
                hintText: '分享你的體驗…（最多 500 字）',
                border: OutlineInputBorder()),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _tags,
            decoration: const InputDecoration(
                hintText: '推薦菜 / 標籤（用逗號分隔）',
                border: OutlineInputBorder()),
          ),
          const SizedBox(height: 12),
          // Gap 11: pick photos from the camera roll; they are uploaded
          // to POST /uploads/review-images on submit (no URL typing).
          OutlinedButton.icon(
            onPressed: _submitting ? null : _pickImages,
            icon: const Icon(Icons.photo_library),
            label: Text(_pickedBytes.isEmpty
                ? '選擇圖片（最多 6 張）'
                : '已選 ${_pickedBytes.length} 張（最多 6 張）'),
          ),
          if (_pickedBytes.isNotEmpty) ...[
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: List.generate(_pickedBytes.length, (i) {
                return Stack(
                  children: [
                    ClipRRect(
                      borderRadius: BorderRadius.circular(8),
                      child: Image.memory(_pickedBytes[i],
                          width: 72, height: 72, fit: BoxFit.cover),
                    ),
                    Positioned(
                      right: 0,
                      top: 0,
                      child: GestureDetector(
                        onTap: () => setState(() {
                          _pickedBytes.removeAt(i);
                          _pickedNames.removeAt(i);
                        }),
                        child: Container(
                          decoration: const BoxDecoration(
                              color: Colors.black54,
                              shape: BoxShape.circle),
                          child: const Icon(Icons.close,
                              size: 18, color: Colors.white),
                        ),
                      ),
                    ),
                  ],
                );
              }),
            ),
          ],
          const SizedBox(height: 8),
          CheckboxListTile(
            title: Text(context.tr('領取評價積分', 'Earn review points')),
            subtitle: Text(context.tr('核銷撤銷後，這筆評價積分也會扣回。',
                'These points are removed if the redemption is reversed.')),
            value: _claimPoints,
            onChanged: (v) => setState(() => _claimPoints = v ?? true),
          ),
          const SizedBox(height: 8),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
              onPressed: _submitting ? null : _submit,
              child: Text(_submitting ? '提交中…' : '提交評價'),
            ),
          ),
        ],
      ),
    );
  }
}

/// Shop reviews: aggregate header (total + 3 dims + count) + visible
/// review list. Reviews that took points are labelled as not counting
/// toward the stars.
class ShopReviewsScreen extends StatefulWidget {
  const ShopReviewsScreen({super.key, required this.api, required this.shop});

  final ApiClient api;
  final Shop shop;

  @override
  State<ShopReviewsScreen> createState() => _ShopReviewsScreenState();
}

class _ShopReviewsScreenState extends State<ShopReviewsScreen> {
  ShopRating? _rating;
  List<Review> _reviews = [];
  int _total = 0;
  bool _hasImagesOnly = false;
  bool _loading = false;
  String? _error;
  String _sort = 'latest'; // latest | helpful
  // Gap 9: vote results applied locally after a tap (the model is
  // immutable); keys are review ids.
  final Map<int, int> _helpfulCountOverrides = {};
  final Map<int, bool> _helpedOverrides = {};

  @override
  void initState() {
    super.initState();
    _load();
  }

  int _helpfulCount(Review rev) =>
      _helpfulCountOverrides[rev.id] ?? rev.helpfulCount;

  bool _helpedByMe(Review rev) =>
      _helpedOverrides[rev.id] ?? rev.helpedByMe;

  Future<void> _vote(Review rev) async {
    if (!widget.api.isLoggedIn) {
      showCgToast(context, context.tr('請先登入再投票', 'Sign in to vote.'));
      return;
    }
    final helpful = !_helpedByMe(rev);
    try {
      final res =
          await widget.api.toggleHelpfulReview(rev.id, helpful);
      if (!mounted) return;
      setState(() {
        _helpfulCountOverrides[rev.id] =
            res['helpful_count'] as int? ?? _helpfulCount(rev);
        _helpedOverrides[rev.id] =
            res['helped_by_me'] as bool? ?? helpful;
      });
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  /// Resolve a review image path to a loadable URL: absolute http(s)
  /// URLs pass through, /uploads/... paths are prefixed with the API
  /// base URL (Gap 11: the list returns server upload paths).
  String _imageUrl(String path) {
    if (path.startsWith('http://') || path.startsWith('https://')) {
      return path;
    }
    if (path.startsWith('/')) return '${widget.api.baseUrl}$path';
    return path;
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final rating = await widget.api.shopRating(widget.shop.id);
      final list = await widget.api.shopReviews(widget.shop.id,
          hasImages: _hasImagesOnly, sort: _sort);
      if (!mounted) return;
      setState(() {
        _rating = ShopRating.fromJson(rating);
        _reviews = (list['reviews'] as List)
            .map((e) => Review.fromJson(e))
            .toList();
        _total = list['total'] as int? ?? _reviews.length;
        _loading = false;
      });
    } on ApiException catch (_) {
      if (mounted) {
        setState(() {
          _error = '網絡不穩定，請重試';
          _loading = false;
        });
      }
    }
  }

  /// Gap 9: star distribution from the server (ALL visible reviews),
  /// never computed from the loaded page.
  Map<int, int> get _distribution {
    final d = _rating?.ratingDistribution;
    if (d == null || d.isEmpty) return {5: 0, 4: 0, 3: 0, 2: 0, 1: 0};
    return {for (var s = 1; s <= 5; s++) s: d[s] ?? 0};
  }

  Widget _distributionBar(int star, int count, int total) {
    final frac = total == 0 ? 0.0 : count / total;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 2),
      child: Row(
        children: [
          SizedBox(width: 28, child: Text('$star 星')),
          Expanded(
            child: LinearProgressIndicator(
              value: frac,
              backgroundColor: const Color(0xFFE8E8E8),
              color: CgColors.amber,
              minHeight: 8,
            ),
          ),
          const SizedBox(width: 8),
          SizedBox(width: 28, child: Text('$count')),
        ],
      ),
    );
  }

  Widget _replyBox(String reply) {
    return Container(
      margin: const EdgeInsets.only(left: 16, top: 8),
      padding: const EdgeInsets.all(10),
      decoration: BoxDecoration(
        color: const Color(0xFFF5F5F6),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text('商戶回覆：$reply',
          style: const TextStyle(color: CgColors.textSecondary)),
    );
  }

  @override
  Widget build(BuildContext context) {
    final r = _rating;
    final dist = _distribution;
    // Gap 9: distribution is server-side over ALL visible reviews.
    final distTotal = dist.values.fold<int>(0, (a, b) => a + b);
    return Scaffold(
      appBar: AppBar(title: Text('${widget.shop.name} · 評價')),
      body: Column(
        children: [
          Card(
            margin: const EdgeInsets.all(12),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: _loading && r == null
                  ? const SkeletonBox(height: 64)
                  : r == null || r.reviewCount == 0
                      ? const Text('暫無計入星級的評價。')
                      : Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                                '${r.overall?.toStringAsFixed(1) ?? '-'} 分 · ${r.reviewCount} 條計入星級的評價',
                                style: const TextStyle(
                                    fontSize: 18,
                                    fontWeight: FontWeight.bold)),
                            const SizedBox(height: 4),
                            Text(
                                '口味 ${r.taste?.toStringAsFixed(1) ?? '-'}　環境 ${r.ambience?.toStringAsFixed(1) ?? '-'}　服務 ${r.service?.toStringAsFixed(1) ?? '-'}'),
                            const SizedBox(height: 8),
                            ...[5, 4, 3, 2, 1].map((s) =>
                                _distributionBar(
                                    s, dist[s] ?? 0, distTotal)),
                            const SizedBox(height: 4),
                            const Text('只有已核銷的用戶可以寫評價。',
                                style: TextStyle(
                                    color: CgColors.textSecondary,
                                    fontSize: 12)),
                          ],
                        ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Row(
              children: [
                Text('評價列表（共 $_total 條）'),
                const Spacer(),
                ChoiceChip(
                  label: const Text('最新'),
                  selected: _sort == 'latest',
                  onSelected: (_) {
                    setState(() => _sort = 'latest');
                    _load();
                  },
                ),
                const SizedBox(width: 6),
                ChoiceChip(
                  label: const Text('最有用'),
                  selected: _sort == 'helpful',
                  onSelected: (_) {
                    // Gap 9: server sort by helpful_count.
                    setState(() => _sort = 'helpful');
                    _load();
                  },
                ),
                const SizedBox(width: 6),
                FilterChip(
                  label: const Text('只看有圖'),
                  selected: _hasImagesOnly,
                  onSelected: (v) {
                    setState(() => _hasImagesOnly = v);
                    _load();
                  },
                ),
              ],
            ),
          ),
          Expanded(
            child: _loading
                ? ListView(
                    padding: const EdgeInsets.all(12),
                    children: const [
                      SkeletonBox(height: 96),
                      SizedBox(height: 8),
                      SkeletonBox(height: 96),
                    ],
                  )
                : _error != null
                    ? Center(
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            const Text('網絡不穩定，請重試'),
                            const SizedBox(height: 12),
                            ElevatedButton(
                                onPressed: _load, child: const Text('重試')),
                          ],
                        ),
                      )
                    : _reviews.isEmpty
                        ? Center(
                            child: Column(
                              mainAxisAlignment: MainAxisAlignment.center,
                              children: [
                                const Text('暫時沒有評價。'),
                                const SizedBox(height: 12),
                                ElevatedButton(
                                    onPressed: _load,
                                    child: const Text('重新載入')),
                              ],
                            ),
                          )
                        : RefreshIndicator(
                            onRefresh: _load,
                            child: ListView.builder(
                              itemCount: _reviews.length,
                              itemBuilder: (_, i) {
                                final rev = _reviews[i];
                                return Card(
                                  margin:
                                      const EdgeInsets.fromLTRB(12, 8, 12, 0),
                                  child: Padding(
                                    padding: const EdgeInsets.all(12),
                                    child: Column(
                                      crossAxisAlignment:
                                          CrossAxisAlignment.start,
                                      children: [
                                        Text(
                                            '${rev.userName.isEmpty ? '用戶' : rev.userName} · ${rev.overall.toStringAsFixed(1)} 分',
                                            style: const TextStyle(
                                                fontWeight: FontWeight.bold)),
                                        const SizedBox(height: 4),
                                        Text(
                                            '口味 ${_stars(rev.taste)}　環境 ${_stars(rev.ambience)}　服務 ${_stars(rev.service)}'),
                                        if (rev.content.isNotEmpty)
                                          Padding(
                                            padding:
                                                const EdgeInsets.only(top: 4),
                                            child: Text(rev.content),
                                          ),
                                        // Gap 11: thumbnails of the
                                        // uploaded review images.
                                        if (rev.images.isNotEmpty)
                                          Padding(
                                            padding:
                                                const EdgeInsets.only(top: 8),
                                            child: Wrap(
                                              spacing: 8,
                                              runSpacing: 8,
                                              children: rev.images
                                                  .map((p) => ClipRRect(
                                                        borderRadius:
                                                            BorderRadius
                                                                .circular(8),
                                                        child: Image.network(
                                                          _imageUrl(p),
                                                          width: 72,
                                                          height: 72,
                                                          fit: BoxFit.cover,
                                                          errorBuilder: (_, __,
                                                                  ___) =>
                                                              const SizedBox(
                                                            width: 72,
                                                            height: 72,
                                                            child: Icon(Icons
                                                                .broken_image),
                                                          ),
                                                        ),
                                                      ))
                                                  .toList(),
                                            ),
                                          ),
                                        // Gap 9: helpful vote toggle.
                                        Align(
                                          alignment: Alignment.centerRight,
                                          child: TextButton.icon(
                                            onPressed: () => _vote(rev),
                                            icon: Icon(
                                                _helpedByMe(rev)
                                                    ? Icons.thumb_up
                                                    : Icons.thumb_up_outlined,
                                                size: 18),
                                            label: Text(_helpedByMe(rev)
                                                ? '已投有用 (${_helpfulCount(rev)})'
                                                : '有用 (${_helpfulCount(rev)})'),
                                          ),
                                        ),
                                        if (rev.tags.isNotEmpty)
                                          Text(
                                              '標籤：${rev.tags.join('、')}'),
                                        if (!rev.countsTowardRating)
                                          const Text('此評價未計入星級',
                                              style: TextStyle(
                                                  color:
                                                      CgColors.textSecondary,
                                                  fontSize: 12)),
                                        if (rev.replyText.isNotEmpty)
                                          _replyBox(rev.replyText),
                                      ],
                                    ),
                                  ),
                                );
                              },
                            ),
                          ),
          ),
        ],
      ),
    );
  }
}

/// Merchant reply to bad reviews (UI_SPEC §7): reviews of 2 stars or
/// less without a reply, for the shop's own merchant / admin.
/// Reached from the vendor home ("評價回覆"). The reply endpoint
/// enforces ownership server-side; this screen only lists the shop's
/// own reviews.
class MerchantReviewReplyScreen extends StatefulWidget {
  const MerchantReviewReplyScreen(
      {super.key, required this.api, required this.shopId, this.shopName});

  final ApiClient api;
  final int shopId;
  final String? shopName;

  @override
  State<MerchantReviewReplyScreen> createState() =>
      _MerchantReviewReplyScreenState();
}

class _MerchantReviewReplyScreenState
    extends State<MerchantReviewReplyScreen> {
  List<Review> _reviews = [];
  bool _loading = false;
  String? _error;

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
      final list = await widget.api.shopReviews(widget.shopId);
      if (!mounted) return;
      final all = (list['reviews'] as List)
          .map((e) => Review.fromJson(e))
          .toList();
      setState(() {
        _reviews = all
            .where((r) => r.overall <= 2 && r.replyText.isEmpty)
            .toList();
        _loading = false;
      });
    } on ApiException catch (_) {
      if (mounted) {
        setState(() {
          _error = '網絡不穩定，請重試';
          _loading = false;
        });
      }
    }
  }

  Future<void> _reply(Review rev) async {
    final ctrl = TextEditingController();
    final text = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('回覆差評'),
        content: TextField(
          controller: ctrl,
          maxLines: 4,
          maxLength: 500,
          decoration: const InputDecoration(hintText: '寫下商戶回覆…'),
        ),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(c),
              child: const Text('取消')),
          TextButton(
              onPressed: () => Navigator.pop(c, ctrl.text.trim()),
              child: const Text('發送回覆')),
        ],
      ),
    );
    if (text == null || text.isEmpty) return;
    try {
      await widget.api.replyReview(rev.id, text);
      if (mounted) showCgToast(context, '回覆已發送');
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
          title: Text('${widget.shopName ?? '商戶'} · 差評回覆')),
      body: _loading
          ? ListView(
              padding: const EdgeInsets.all(12),
              children: const [
                SkeletonBox(height: 96),
                SizedBox(height: 8),
                SkeletonBox(height: 96),
              ],
            )
          : _error != null
              ? Center(
                  child: Column(
                    mainAxisAlignment: MainAxisAlignment.center,
                    children: [
                      const Text('網絡不穩定，請重試'),
                      const SizedBox(height: 12),
                      ElevatedButton(
                          onPressed: _load, child: const Text('重試')),
                    ],
                  ),
                )
              : _reviews.isEmpty
                  ? Center(
                      child: Column(
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          const Text('暫無待回覆的差評。'),
                          const SizedBox(height: 12),
                          ElevatedButton(
                              onPressed: _load,
                              child: const Text('重新載入')),
                        ],
                      ),
                    )
                  : RefreshIndicator(
                      onRefresh: _load,
                      child: ListView.builder(
                        itemCount: _reviews.length,
                        itemBuilder: (_, i) {
                          final rev = _reviews[i];
                          return Card(
                            margin: const EdgeInsets.fromLTRB(12, 8, 12, 0),
                            child: Padding(
                              padding: const EdgeInsets.all(12),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  Text(
                                      '${rev.userName.isEmpty ? '用戶' : rev.userName} · ${rev.overall.toStringAsFixed(1)} 分',
                                      style: const TextStyle(
                                          fontWeight: FontWeight.bold)),
                                  if (rev.content.isNotEmpty)
                                    Padding(
                                      padding: const EdgeInsets.only(top: 4),
                                      child: Text(rev.content),
                                    ),
                                  const SizedBox(height: 8),
                                  Align(
                                    alignment: Alignment.centerRight,
                                    child: ElevatedButton(
                                      onPressed: () => _reply(rev),
                                      child: const Text('回覆'),
                                    ),
                                  ),
                                ],
                              ),
                            ),
                          );
                        },
                      ),
                    ),
    );
  }
}

/// Merchant onboarding application form (admin v1). Submits to
/// POST /admin/merchant-applications; the applicant is notified of the
/// decision in-app (v1 has no push for this). Document fields are text
/// references in v1 — no file upload yet.
class MerchantApplicationFormScreen extends StatefulWidget {
  const MerchantApplicationFormScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<MerchantApplicationFormScreen> createState() =>
      _MerchantApplicationFormScreenState();
}

class _MerchantApplicationFormScreenState
    extends State<MerchantApplicationFormScreen> {
  final _shopName = TextEditingController();
  final _contact = TextEditingController();
  final _phone = TextEditingController();
  final _address = TextEditingController();
  final _license = TextEditingController();
  final _idCard = TextEditingController();
  final _industry = TextEditingController();
  final _photos = TextEditingController();

  @override
  void dispose() {
    for (final c in [
      _shopName,
      _contact,
      _phone,
      _address,
      _license,
      _idCard,
      _industry,
      _photos
    ]) {
      c.dispose();
    }
    super.dispose();
  }

  Future<void> _submit() async {
    if (_shopName.text.trim().isEmpty) {
      showCgToast(context, '請填寫商店名稱');
      return;
    }
    try {
      await widget.api.submitMerchantApplication({
        'shop_name': _shopName.text.trim(),
        'contact_name': _contact.text.trim(),
        'phone': _phone.text.trim(),
        'address': _address.text.trim(),
        'business_license': _license.text.trim(),
        'id_card': _idCard.text.trim(),
        'shop_photos': _splitList(_photos.text),
        'industry_license': _industry.text.trim(),
      });
      if (!mounted) return;
      showCgToast(context, '入駐申請已提交，請等待審核');
      Navigator.of(context).pop();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('商戶入駐申請')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
              controller: _shopName,
              decoration: const InputDecoration(labelText: '商店名稱 *')),
          TextField(
              controller: _contact,
              decoration: const InputDecoration(labelText: '聯絡人')),
          TextField(
              controller: _phone,
              decoration: const InputDecoration(labelText: '電話')),
          TextField(
              controller: _address,
              decoration: const InputDecoration(labelText: '地址')),
          TextField(
              controller: _license,
              decoration: const InputDecoration(labelText: '營業執照')),
          TextField(
              controller: _idCard,
              decoration: const InputDecoration(labelText: '法人身份證')),
          TextField(
              controller: _industry,
              decoration:
                  const InputDecoration(labelText: '行業許可證（如食品經營許可證）')),
          TextField(
              controller: _photos,
              decoration: const InputDecoration(
                  labelText: '門店照片連結（用逗號分隔，v1 預留欄位）')),
          const SizedBox(height: 16),
          const Text('提交後由管理員審核；被拒絕可修改後重新提交。',
              style: TextStyle(color: CgColors.textSecondary)),
          const SizedBox(height: 12),
          SizedBox(
            width: double.infinity,
            child: ElevatedButton(
                onPressed: _submit, child: const Text('提交申請')),
          ),
        ],
      ),
    );
  }
}

/// Admin v1 home (admin role only; other roles see the 403 message from
/// the API). Tabs: 概覽 (read-only dashboard) / 入駐審核 / 商户 / 券 /
/// 核銷. Users + audit logs are listed from the 概覽 tab. All dashboard
/// lists are read-only in v1 — no edits, no settlements, no banners,
/// no role changes (F line, admin v2).
class AdminScreen extends StatefulWidget {
  const AdminScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<AdminScreen> createState() => _AdminScreenState();
}

class _AdminScreenState extends State<AdminScreen>
    with SingleTickerProviderStateMixin {
  late final TabController _tab = TabController(length: 5, vsync: this);
  Map<String, dynamic>? _dashboard;
  List<MerchantApplication> _applications = [];
  List<dynamic> _shops = [];
  List<dynamic> _coupons = [];
  List<dynamic> _redemptions = [];
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _tab.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    try {
      final d = await widget.api.adminDashboard();
      final a = await widget.api.adminApplications();
      final s = await widget.api.adminShops();
      final c = await widget.api.adminCoupons();
      final r = await widget.api.adminRedemptions();
      if (!mounted) return;
      setState(() {
        _dashboard = d;
        _applications = (a['applications'] as List)
            .map((e) => MerchantApplication.fromJson(e))
            .toList();
        _shops = s['shops'] as List;
        _coupons = c['coupons'] as List;
        _redemptions = r['redemptions'] as List;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    }
  }

  Future<void> _reviewApplication(MerchantApplication app, bool approve) async {
    String reason = '';
    if (!approve) {
      reason = await showDialog<String>(
            context: context,
            builder: (c) {
              final ctrl = TextEditingController();
              return AlertDialog(
                title: const Text('拒絕原因'),
                content: TextField(
                    controller: ctrl,
                    decoration:
                        const InputDecoration(hintText: '請填寫拒絕原因（必填）')),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(c, ''),
                      child: const Text('取消')),
                  TextButton(
                      onPressed: () => Navigator.pop(c, ctrl.text.trim()),
                      child: const Text('確認拒絕')),
                ],
              );
            },
          ) ??
          '';
      if (reason.isEmpty) return;
    }
    try {
      await widget.api.reviewMerchantApplication(app.id, approve,
          rejectReason: reason);
      if (mounted) {
        showCgToast(context, approve ? '入駐申請已通過' : '入駐申請已拒絕');
      }
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  Widget _statCard(String label, String value) {
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(value,
                style:
                    const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
            Text(label, style: const TextStyle(color: CgColors.textSecondary)),
          ],
        ),
      ),
    );
  }

  Widget _overviewTab() {
    final d = _dashboard;
    if (d == null) return const Center(child: Text('載入中…'));
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(12),
        children: [
          GridView.count(
            crossAxisCount: 2,
            shrinkWrap: true,
            physics: const NeverScrollableScrollPhysics(),
            childAspectRatio: 2.2,
            children: [
              _statCard('用戶', '${d['total_users'] ?? 0}'),
              _statCard('商戶', '${d['total_shops'] ?? 0}'),
              _statCard('優惠券', '${d['total_coupons'] ?? 0}'),
              _statCard('核銷', '${d['total_redemptions'] ?? 0}'),
              _statCard('評價', '${d['total_reviews'] ?? 0}'),
              _statCard('待審核入駐', '${d['pending_applications'] ?? 0}'),
              _statCard('待審核券', '${d['pending_coupons'] ?? 0}'),
              _statCard('計入星級評價', '${d['rated_reviews'] ?? 0}'),
            ],
          ),
          const SizedBox(height: 8),
          const Text('看板為隻讀；結算、內容管理、角色分權在後台 v2（F 線）。',
              style: TextStyle(color: CgColors.textSecondary)),
        ],
      ),
    );
  }

  Widget _applicationsTab() {
    if (_applications.isEmpty) return const Center(child: Text('暫無入駐申請。'));
    return ListView.builder(
      itemCount: _applications.length,
      itemBuilder: (_, i) {
        final a = _applications[i];
        return Card(
          margin: const EdgeInsets.fromLTRB(12, 8, 12, 0),
          child: ListTile(
            title: Text(a.shopName),
            subtitle: Text(
                '${a.applicantEmail} · ${a.contactName} ${a.phone}\n狀態：${a.status}${a.rejectReason.isNotEmpty ? '（${a.rejectReason}）' : ''}'),
            isThreeLine: true,
            trailing: a.status == 'pending'
                ? Row(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      TextButton(
                          onPressed: () => _reviewApplication(a, true),
                          child: const Text('通過')),
                      TextButton(
                          onPressed: () => _reviewApplication(a, false),
                          child: const Text('拒絕',
                              style: TextStyle(color: Colors.red))),
                    ],
                  )
                : null,
          ),
        );
      },
    );
  }

  Widget _simpleListTab(List<dynamic> items, String Function(dynamic) title,
      String Function(dynamic) subtitle, String emptyText) {
    if (items.isEmpty) return Center(child: Text(emptyText));
    return ListView.builder(
      itemCount: items.length,
      itemBuilder: (_, i) {
        final e = items[i] as Map<String, dynamic>;
        return Card(
          margin: const EdgeInsets.fromLTRB(12, 8, 12, 0),
          child: ListTile(title: Text(title(e)), subtitle: Text(subtitle(e))),
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    if (_error != null) {
      return Scaffold(
        appBar: AppBar(title: const Text('管理後台')),
        body: Center(child: Text(_error!)),
      );
    }
    return Scaffold(
      appBar: AppBar(
        title: const Text('管理後台'),
        bottom: TabBar(
          controller: _tab,
          isScrollable: true,
          tabs: const [
            Tab(text: '概覽'),
            Tab(text: '入駐審核'),
            Tab(text: '商戶'),
            Tab(text: '券'),
            Tab(text: '核銷'),
          ],
        ),
      ),
      body: TabBarView(
        controller: _tab,
        children: [
          _overviewTab(),
          _applicationsTab(),
          _simpleListTab(_shops, (e) => '${e['shop_name']}',
              (e) => '券 ${e['coupon_count']} · 核銷 ${e['redemption_count']}',
              '暫無商戶。'),
          _simpleListTab(_coupons, (e) => '${e['coupon_name']}',
              (e) => '${e['shop_name']} · ${e['status']}',
              '暫無優惠券。'),
          _simpleListTab(_redemptions, (e) => '${e['coupon_name']}',
              (e) => '${e['shop_name']} · ${e['scanned_at'] ?? ''}',
              '暫無核銷記錄。'),
        ],
      ),
    );
  }
}
