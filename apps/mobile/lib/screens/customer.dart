import 'package:flutter/material.dart';

import '../api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import 'reviews_admin.dart';

/// Home: red header + search bar, 4 categories, banner, 7 sections
/// (熱門優惠券 → 最多人搶 → 限時搶購 → 本週精選 → 為你推薦 → 常用商戶 → 新加入商戶).
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.api, required this.onOpenCoupon,
    required this.onOpenShop, required this.onOpenSearch, required this.onOpenLimited});

  final ApiClient api;
  final void Function(Coupon) onOpenCoupon;
  final void Function(Shop) onOpenShop;
  final VoidCallback onOpenSearch;
  final VoidCallback onOpenLimited;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  Map<String, dynamic>? _home;
  String _area = '全部地區';

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await widget.api.home();
      if (mounted) setState(() => _home = data);
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  List<Coupon> _coupons(String key) =>
      ((_home?[key] as List?) ?? []).map((e) => Coupon.fromJson(e)).toList();
  List<Shop> _shops(String key) =>
      ((_home?[key] as List?) ?? []).map((e) => Shop.fromJson(e)).toList();

  Future<void> _claim(Coupon c) async {
    try {
      await widget.api.claimCoupon(c.id);
      if (!mounted) return;
      showCgToast(context, '已存入 Q Wallet（+10 分）');
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final popular = _coupons('popular_coupons');
    final mostGrab = _coupons('most_grab');
    final limited = _coupons('limited_time_coupons');
    final week = _coupons('week_coupons');
    final recommended = _coupons('recommended');
    final shops = _shops('popular_merchants');
    final categories = (_home?['categories'] as List?) ?? [];
    return RefreshIndicator(
      onRefresh: _load,
      child: CustomScrollView(
        slivers: [
          SliverAppBar(
            pinned: true,
            backgroundColor: CgColors.primary,
            title: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Image.asset('assets/images/img_logo.png', height: 32,
                    errorBuilder: (_, _, _) => const SizedBox()),
                const SizedBox(width: 8),
                const Text('COUPON GO!',
                    style: TextStyle(fontWeight: FontWeight.bold)),
              ],
            ),
            actions: [
              IconButton(
                icon: const Icon(Icons.notifications_outlined),
                onPressed: () => Navigator.of(context).pushNamed('/notifications'),
              ),
            ],
            bottom: PreferredSize(
              preferredSize: const Size.fromHeight(56),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
                child: GestureDetector(
                  onTap: widget.onOpenSearch,
                  child: Container(
                    height: 44,
                    padding: const EdgeInsets.symmetric(horizontal: 14),
                    decoration: BoxDecoration(
                      color: Colors.white,
                      borderRadius: BorderRadius.circular(22),
                    ),
                    child: const Row(
                      children: [
                        Icon(Icons.search, color: CgColors.textSecondary),
                        SizedBox(width: 8),
                        Text('搜尋商店和優惠',
                            style: TextStyle(color: CgColors.textSecondary)),
                      ],
                    ),
                  ),
                ),
              ),
            ),
          ),
          if (_home == null)
            const SliverFillRemaining(
              child: Padding(
                padding: EdgeInsets.all(16),
                child: Column(children: [
                  SkeletonBox(height: 120),
                  SizedBox(height: 12),
                  SkeletonBox(height: 180),
                  SizedBox(height: 12),
                  SkeletonBox(height: 180),
                ]),
              ),
            )
          else ...[
            SliverToBoxAdapter(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // categories
                  SizedBox(
                    height: 92,
                    child: ListView.builder(
                      scrollDirection: Axis.horizontal,
                      padding: const EdgeInsets.symmetric(horizontal: 12),
                      itemCount: categories.length,
                      itemBuilder: (_, i) {
                        final cat = categories[i] as Map;
                        return Padding(
                          padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 10),
                          child: GestureDetector(
                            onTap: () => Navigator.of(context).pushNamed(
                                '/category',
                                arguments: cat['id'] as int),
                            child: Column(
                              children: [
                                CircleAvatar(
                                  radius: 26,
                                  backgroundColor: Colors.white,
                                  child: Text(
                                    (cat['name'] as String).characters.first,
                                    style: const TextStyle(
                                        color: CgColors.primary,
                                        fontWeight: FontWeight.bold),
                                  ),
                                ),
                                const SizedBox(height: 4),
                                Text(cat['name'] as String,
                                    style: const TextStyle(fontSize: 12)),
                              ],
                            ),
                          ),
                        );
                      },
                    ),
                  ),
                  // line A: nearby shops entry (list + marker view + navigation)
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                    child: SizedBox(
                      width: double.infinity,
                      child: OutlinedButton.icon(
                        onPressed: () =>
                            Navigator.of(context).pushNamed('/nearby'),
                        icon: const Icon(Icons.near_me),
                        label: const Text('附近商户 · 按距離排序'),
                      ),
                    ),
                  ),
                  // banner
                  Container(
                    margin: const EdgeInsets.symmetric(horizontal: 16),
                    height: 120,
                    decoration: BoxDecoration(
                      borderRadius: BorderRadius.circular(12),
                      image: const DecorationImage(
                        image: AssetImage('assets/images/bg_main.png'),
                        fit: BoxFit.cover,
                      ),
                      color: CgColors.primary,
                    ),
                    child: const Padding(
                      padding: EdgeInsets.all(16),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        mainAxisAlignment: MainAxisAlignment.center,
                        children: [
                          Text('全新優惠券平台',
                              style: TextStyle(
                                  color: Colors.white,
                                  fontSize: 20,
                                  fontWeight: FontWeight.bold)),
                          Text('一齊攞盡 Coupon 著數',
                              style: TextStyle(color: Colors.white)),
                        ],
                      ),
                    ),
                  ),
                  // area filter
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                    child: Wrap(
                      spacing: 8,
                      children: ['全部地區', '香港', '九龍', '新界'].map((a) {
                        final selected = _area == a;
                        return ChoiceChip(
                          label: Text(a),
                          selected: selected,
                          selectedColor: CgColors.primary,
                          labelStyle: TextStyle(
                              color: selected ? Colors.white : CgColors.textPrimary),
                          onSelected: (_) => setState(() => _area = a),
                        );
                      }).toList(),
                    ),
                  ),
                  SectionHeader(title: '熱門優惠券', onViewAll: () {}),
                  _couponGrid(popular),
                  SectionHeader(title: '最多人搶', onViewAll: () {}),
                  _couponRow(mostGrab),
                  SectionHeader(
                      title: '限時搶購', onViewAll: widget.onOpenLimited),
                  _couponRow(limited),
                  SectionHeader(title: '本週精選', onViewAll: () {}),
                  _couponGrid(week),
                  SectionHeader(title: '為你推薦', onViewAll: () {}),
                  _couponRow(recommended),
                  const SectionHeader(title: '常用商戶'),
                  _shopRow(shops),
                  const SectionHeader(title: '新加入商戶'),
                  _shopRow(shops),
                  const SizedBox(height: 24),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }

  Widget _couponGrid(List<Coupon> list) {
    if (list.isEmpty) return const SizedBox();
    return GridView.builder(
      shrinkWrap: true,
      physics: const NeverScrollableScrollPhysics(),
      padding: const EdgeInsets.symmetric(horizontal: 12),
      gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
        crossAxisCount: 2,
        childAspectRatio: 0.78,
        crossAxisSpacing: 8,
        mainAxisSpacing: 8,
      ),
      itemCount: list.length,
      itemBuilder: (_, i) => CouponCard(
        coupon: list[i],
        onTap: () => widget.onOpenCoupon(list[i]),
        onFavorite: () => _toggleFavCoupon(list[i]),
        // claim via long-press on the card CTA is handled in detail page;
        // tapping the card opens details.
      ),
    );
  }

  Widget _couponRow(List<Coupon> list) {
    if (list.isEmpty) return const SizedBox();
    return SizedBox(
      height: 250,
      child: ListView.builder(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        itemCount: list.length,
        itemBuilder: (_, i) => SizedBox(
          width: 180,
          child: Padding(
            padding: const EdgeInsets.only(right: 8),
            child: CouponCard(
              coupon: list[i],
              onTap: () => widget.onOpenCoupon(list[i]),
              onFavorite: () => _toggleFavCoupon(list[i]),
            ),
          ),
        ),
      ),
    );
  }

  Widget _shopRow(List<Shop> list) {
    if (list.isEmpty) return const SizedBox();
    return SizedBox(
      height: 150,
      child: ListView.builder(
        scrollDirection: Axis.horizontal,
        padding: const EdgeInsets.symmetric(horizontal: 12),
        itemCount: list.length,
        itemBuilder: (_, i) {
          final s = list[i];
          return SizedBox(
            width: 160,
            child: Card(
              clipBehavior: Clip.antiAlias,
              child: InkWell(
                onTap: () => widget.onOpenShop(s),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Expanded(
                      child: Image.asset(
                        'assets/images/dummy_image_1.png',
                        fit: BoxFit.cover,
                        width: double.infinity,
                        errorBuilder: (_, _, _) =>
                            Container(color: CgColors.pageBg),
                      ),
                    ),
                    Padding(
                      padding: const EdgeInsets.all(8),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(s.name,
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(fontWeight: FontWeight.w600)),
                          Text(s.areaName,
                              style: const TextStyle(
                                  color: CgColors.textSecondary, fontSize: 12)),
                        ],
                      ),
                    ),
                  ],
                ),
              ),
            ),
          );
        },
      ),
    );
  }

  Future<void> _toggleFavCoupon(Coupon c) async {
    try {
      await widget.api.toggleFavoriteCoupon(c.id);
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }
}

/// Search page: live suggestions (three branches), recent + hot searches.
class SearchScreen extends StatefulWidget {
  const SearchScreen({super.key, required this.api, required this.onOpenCoupon,
    required this.onOpenShop});

  final ApiClient api;
  final void Function(Coupon) onOpenCoupon;
  final void Function(Shop) onOpenShop;

  @override
  State<SearchScreen> createState() => _SearchScreenState();
}

class _SearchScreenState extends State<SearchScreen> {
  final _ctrl = TextEditingController();
  List<dynamic> _suggestions = [];
  final _recent = <String>['壽司', '火鍋', '咖啡'];
  final _hot = ['壽司', '咖啡', '火鍋', '美容', '拉麵', '健身', '眼鏡', 'Spa'];

  Future<void> _onChanged(String kw) async {
    if (kw.isEmpty) {
      setState(() => _suggestions = []);
      return;
    }
    try {
      final list = await widget.api.suggestions(kw);
      if (mounted) setState(() => _suggestions = list);
    } on ApiException catch (_) {}
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: TextField(
          controller: _ctrl,
          autofocus: true,
          decoration: const InputDecoration(
            hintText: '搜尋商店和優惠',
            border: InputBorder.none,
            prefixIcon: Icon(Icons.search),
          ),
          onChanged: _onChanged,
        ),
      ),
      body: _suggestions.isEmpty
          ? ListView(
              padding: const EdgeInsets.all(16),
              children: [
                const Text('最近搜尋',
                    style: TextStyle(fontWeight: FontWeight.bold)),
                Wrap(
                  spacing: 8,
                  children: _recent
                      .map((w) => ActionChip(
                          label: Text(w),
                          onPressed: () {
                            _ctrl.text = w;
                            _onChanged(w);
                          }))
                      .toList(),
                ),
                const SizedBox(height: 16),
                const Text('熱門搜尋',
                    style: TextStyle(fontWeight: FontWeight.bold)),
                Wrap(
                  spacing: 8,
                  children: _hot
                      .map((w) => ActionChip(
                          label: Text(w),
                          onPressed: () {
                            _ctrl.text = w;
                            _onChanged(w);
                          }))
                      .toList(),
                ),
              ],
            )
          : ListView.builder(
              itemCount: _suggestions.length,
              itemBuilder: (_, i) {
                final s = _suggestions[i] as Map;
                final isCoupon = s['kind'] == 'coupon';
                return ListTile(
                  leading: Icon(isCoupon ? Icons.local_offer : Icons.store),
                  title: Text(s['title'] as String),
                  subtitle: Text((s['subtitle'] as String?) ?? ''),
                  onTap: () async {
                    final id = s['id'] as int;
                    if (isCoupon) {
                      final d = await widget.api.couponDetails(id);
                      widget.onOpenCoupon(Coupon.fromJson(d['coupon']));
                    } else {
                      final d = await widget.api.shopDetails(id);
                      widget.onOpenShop(Shop.fromJson(d['shop']));
                    }
                  },
                );
              },
            ),
    );
  }
}

/// Coupon detail: image, amount, validity, terms, claim button (>=56px).
class CouponDetailScreen extends StatefulWidget {
  const CouponDetailScreen({super.key, required this.api, required this.coupon});

  final ApiClient api;
  final Coupon coupon;

  @override
  State<CouponDetailScreen> createState() => _CouponDetailScreenState();
}

class _CouponDetailScreenState extends State<CouponDetailScreen> {
  bool _claimed = false;

  @override
  Widget build(BuildContext context) {
    final c = widget.coupon;
    return Scaffold(
      appBar: AppBar(title: const Text('優惠券詳情')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: Image.asset('assets/images/dummy_image_1.png',
                height: 200, fit: BoxFit.cover,
                errorBuilder: (_, _, _) => Container(
                    height: 200, color: CgColors.pageBg)),
          ),
          const SizedBox(height: 16),
          Text(c.name,
              style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
          const SizedBox(height: 6),
          Text(c.shopName, style: const TextStyle(color: CgColors.textSecondary)),
          const SizedBox(height: 12),
          Row(
            children: [
              Text(c.amountText,
                  style: const TextStyle(
                      color: CgColors.primary,
                      fontSize: 26,
                      fontWeight: FontWeight.bold)),
              if (c.regularPrice != null) ...[
                const SizedBox(width: 10),
                Text('\$${c.regularPrice!.toStringAsFixed(0)}',
                    style: const TextStyle(
                        decoration: TextDecoration.lineThrough,
                        color: CgColors.textSecondary)),
              ],
              const Spacer(),
              Chip(label: Text(c.daysLeft > 0 ? '剩 ${c.daysLeft} 日' : '即將到期')),
            ],
          ),
          const SizedBox(height: 12),
          Text('有效日期：至 ${c.validTo?.toLocal().toString().split(' ').first ?? ''}'),
          Text('剩餘數量：${c.remaining} / ${c.total}'),
          if (c.minSpend > 0) Text('於本店惠顧滿 \$${c.minSpend.toStringAsFixed(0)}'),
          const SizedBox(height: 16),
          const Text('條款和條件', style: TextStyle(fontWeight: FontWeight.bold)),
          Text(c.terms.isEmpty ? '每人限領一次；不可與其他優惠同時使用。' : c.terms),
        ],
      ),
      bottomNavigationBar: Padding(
        padding: const EdgeInsets.all(16),
        child: ElevatedButton(
          onPressed: _claimed
              ? null
              : () async {
                  try {
                    await widget.api.claimCoupon(c.id);
                    setState(() => _claimed = true);
                    if (context.mounted) {
                      showCgToast(context, '已存入 Q Wallet（+10 分）');
                    }
                  } on ApiException catch (e) {
                    if (context.mounted) showCgToast(context, e.message);
                  }
                },
          child: Text(_claimed ? '已領取' : '立即領取'),
        ),
      ),
    );
  }
}

/// Shop detail: photos, info, branches, coupons from this shop.
class ShopDetailScreen extends StatelessWidget {
  const ShopDetailScreen({super.key, required this.api, required this.shop,
    required this.onOpenCoupon});

  final ApiClient api;
  final Shop shop;
  final void Function(Coupon) onOpenCoupon;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(shop.name)),
      body: FutureBuilder<Map<String, dynamic>>(
        future: api.shopDetails(shop.id),
        builder: (context, snap) {
          if (!snap.hasData) {
            return const Padding(
              padding: EdgeInsets.all(16),
              child: Column(children: [SkeletonBox(height: 200)]),
            );
          }
          final data = snap.data!;
          final coupons = (data['coupons'] as List)
              .map((e) => Coupon.fromJson(e))
              .toList();
          final branches = (data['branches'] as List?) ?? [];
          return ListView(
            padding: const EdgeInsets.all(16),
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(12),
                child: Image.asset('assets/images/dummy_image_1.png',
                    height: 200, fit: BoxFit.cover,
                    errorBuilder: (_, _, _) => Container(
                        height: 200, color: CgColors.pageBg)),
              ),
              const SizedBox(height: 12),
              Text(shop.name,
                  style: const TextStyle(fontSize: 22, fontWeight: FontWeight.bold)),
              Text('${shop.areaName} · ${shop.categoryName}'),
              Text(shop.address),
              Text(shop.phone),
              if (shop.description.isNotEmpty) Text(shop.description),
              const SizedBox(height: 8),
              ListTile(
                leading: const Icon(Icons.star, color: CgColors.amber),
                title: const Text('評分與評價'),
                subtitle: const Text('口味 / 環境 / 服務三維評分'),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) =>
                        ShopReviewsScreen(api: api, shop: shop))),
              ),
              const SizedBox(height: 8),
              if (branches.isNotEmpty) ...[
                const Text('分店', style: TextStyle(fontWeight: FontWeight.bold)),
                ...branches.map((b) => ListTile(
                      leading: const Icon(Icons.store),
                      title: Text(b['branch_name'] as String),
                      subtitle: Text((b['address'] as String?) ?? ''),
                    )),
              ],
              const SizedBox(height: 8),
              const Text('本店優惠券', style: TextStyle(fontWeight: FontWeight.bold)),
              ...coupons.map((c) => ListTile(
                    leading: const Icon(Icons.local_offer, color: CgColors.primary),
                    title: Text(c.name),
                    subtitle: Text(c.amountText),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => onOpenCoupon(c),
                  )),
              if (coupons.isEmpty) const Text('暫時沒有優惠券。'),
            ],
          );
        },
      ),
    );
  }
}

/// Category page: coupons + shops of one category.
class CategoryScreen extends StatelessWidget {
  const CategoryScreen({super.key, required this.api, required this.categoryId,
    required this.onOpenCoupon});

  final ApiClient api;
  final int categoryId;
  final void Function(Coupon) onOpenCoupon;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('分類')),
      body: FutureBuilder<List<dynamic>>(
        future: api.classification(categoryId: categoryId),
        builder: (context, snap) {
          if (!snap.hasData) {
            return const Padding(
              padding: EdgeInsets.all(16),
              child: Column(children: [SkeletonBox(height: 180)]),
            );
          }
          final coupons =
              snap.data!.map((e) => Coupon.fromJson(e)).toList();
          if (coupons.isEmpty) return const Center(child: Text('找不到優惠券！'));
          return GridView.builder(
            padding: const EdgeInsets.all(12),
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
              crossAxisCount: 2,
              childAspectRatio: 0.78,
              crossAxisSpacing: 8,
              mainAxisSpacing: 8,
            ),
            itemCount: coupons.length,
            itemBuilder: (_, i) => CouponCard(
              coupon: coupons[i],
              onTap: () => onOpenCoupon(coupons[i]),
            ),
          );
        },
      ),
    );
  }
}

/// Limited-time list page with countdown banner.
class LimitedTimeScreen extends StatelessWidget {
  const LimitedTimeScreen({super.key, required this.api, required this.onOpenCoupon});

  final ApiClient api;
  final void Function(Coupon) onOpenCoupon;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('限時搶購')),
      body: FutureBuilder<List<dynamic>>(
        future: api.limitedTime(),
        builder: (context, snap) {
          if (!snap.hasData) {
            return const Padding(
              padding: EdgeInsets.all(16),
              child: Column(children: [SkeletonBox(height: 180)]),
            );
          }
          final coupons = snap.data!.map((e) => Coupon.fromJson(e)).toList();
          return ListView.builder(
            padding: const EdgeInsets.all(12),
            itemCount: coupons.length,
            itemBuilder: (_, i) => Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: CouponCard(
                  coupon: coupons[i], onTap: () => onOpenCoupon(coupons[i])),
            ),
          );
        },
      ),
    );
  }
}
