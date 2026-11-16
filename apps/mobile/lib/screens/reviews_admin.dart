import 'package:flutter/material.dart';

import '../api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';

/// Line B screens: 3-dim review form + shop reviews (F6 v1) and the
/// admin v1 management pages (F4 v1: onboarding review + read-only
/// dashboard). No Flutter SDK is available on this machine, so this file
/// has NOT been through `flutter analyze` or a real-device build.

String _stars(int n) => '★' * n + '☆' * (5 - n);

List<String> _splitList(String raw) => raw
    .split(RegExp(r'[,，\n]'))
    .map((e) => e.trim())
    .where((e) => e.isNotEmpty)
    .toList();

/// Review form: opened from a used (status 2) wallet item. Three 1-5
/// dimensions, text, tags, image URLs (reserved field — v1 has no upload
/// endpoint, the URLs are stored as-is). The points checkbox is the
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
  final _images = TextEditingController();
  bool _submitting = false;

  @override
  void dispose() {
    _content.dispose();
    _tags.dispose();
    _images.dispose();
    super.dispose();
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
    setState(() => _submitting = true);
    try {
      final data = await widget.api.submitReview(
        walletItemId: widget.item.id,
        redemptionId: widget.redemptionId,
        taste: _taste,
        ambience: _ambience,
        service: _service,
        content: _content.text.trim(),
        images: _splitList(_images.text),
        tags: _splitList(_tags.text),
        claimPoints: _claimPoints,
      );
      if (!mounted) return;
      showCgToast(context, data['message'] as String? ?? '評價成功');
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
            maxLength: 1000,
            decoration: const InputDecoration(
                hintText: '分享你的體驗…', border: OutlineInputBorder()),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _tags,
            decoration: const InputDecoration(
                hintText: '推薦菜 / 標籤（用逗號分隔）',
                border: OutlineInputBorder()),
          ),
          const SizedBox(height: 12),
          TextField(
            controller: _images,
            decoration: const InputDecoration(
                hintText: '晒圖連結（用逗號分隔，v1 預留欄位）',
                border: OutlineInputBorder()),
          ),
          const SizedBox(height: 8),
          CheckboxListTile(
            title: const Text('領取 +15 積分'),
            subtitle: const Text(
                '領積分的評價不計入商户星級（防誘導評價）；不領積分則計入星級。'),
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
  bool _hasImagesOnly = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final rating = await widget.api.shopRating(widget.shop.id);
      final list = await widget.api.shopReviews(widget.shop.id,
          hasImages: _hasImagesOnly);
      if (!mounted) return;
      setState(() {
        _rating = ShopRating.fromJson(rating);
        _reviews = (list['reviews'] as List)
            .map((e) => Review.fromJson(e))
            .toList();
      });
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final r = _rating;
    return Scaffold(
      appBar: AppBar(title: Text('${widget.shop.name} · 評價')),
      body: Column(
        children: [
          Card(
            margin: const EdgeInsets.all(12),
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: r == null
                  ? const SkeletonBox(height: 64)
                  : r.reviewCount == 0
                      ? const Text('暫無計入星級的評價。\n領取積分的評價不計入星級。')
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
                          ],
                        ),
            ),
          ),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 12),
            child: Row(
              children: [
                const Text('評價列表'),
                const Spacer(),
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
            child: _reviews.isEmpty
                ? const Center(child: Text('暫時沒有評價。'))
                : RefreshIndicator(
                    onRefresh: _load,
                    child: ListView.builder(
                      itemCount: _reviews.length,
                      itemBuilder: (_, i) {
                        final rev = _reviews[i];
                        return Card(
                          margin: const EdgeInsets.fromLTRB(12, 8, 12, 0),
                          child: ListTile(
                            title: Text(
                                '${rev.userName.isEmpty ? '用戶' : rev.userName} · ${rev.overall.toStringAsFixed(1)} 分'),
                            subtitle: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                    '口味 ${_stars(rev.taste)}　環境 ${_stars(rev.ambience)}　服務 ${_stars(rev.service)}'),
                                if (rev.content.isNotEmpty)
                                  Text(rev.content),
                                if (rev.images.isNotEmpty)
                                  Text('有圖 ×${rev.images.length}'),
                                if (rev.tags.isNotEmpty)
                                  Text('標籤：${rev.tags.join('、')}'),
                                if (!rev.countsTowardRating)
                                  const Text('積分評價 · 不計入星級',
                                      style: TextStyle(
                                          color: CgColors.textSecondary,
                                          fontSize: 12)),
                                if (rev.replyText.isNotEmpty)
                                  Text('商户回覆：${rev.replyText}',
                                      style: const TextStyle(
                                          color: CgColors.textSecondary)),
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
              _statCard('商户', '${d['total_shops'] ?? 0}'),
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
            Tab(text: '商户'),
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
              '暫無商户。'),
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
