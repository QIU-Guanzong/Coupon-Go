import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import 'bargain.dart';
import 'packages.dart';
import 'reviews_admin.dart';

/// Home (UI_SPEC §2): search bar + area chips, category strip,
/// flash-bargain horizontal cards, hot-package 2-col grid, nearby
/// coupon list with distance. Guest browsing needs no login; buying /
/// helping asks for login on the target page, then returns.
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
  List<Package> _packages = [];
  List<BargainCampaign> _campaigns = [];
  List<Coupon> _areaCoupons = [];
  String _area = '全部地區';
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
      // Gap 10: best-effort last-known position only — no permission
      // prompt on the home page. When the OS has no cached position
      // (or permission was never granted) home() is called without a
      // location and the server returns distance_km null, which the
      // coupon card already hides.
      double? lat;
      double? lng;
      try {
        final permission = await Geolocator.checkPermission();
        if (permission == LocationPermission.always ||
            permission == LocationPermission.whileInUse) {
          final pos = await Geolocator.getLastKnownPosition();
          lat = pos?.latitude;
          lng = pos?.longitude;
        }
      } catch (_) {
        lat = null;
        lng = null;
      }
      final data = await widget.api.home(lat: lat, lng: lng);
      // Packages + bargain campaigns are best-effort: a failure here
      // must not blank the whole home page.
      var packages = <Package>[];
      var campaigns = <BargainCampaign>[];
      try {
        packages = (await widget.api.packages())
            .map((e) => Package.fromJson(e as Map<String, dynamic>))
            .toList();
      } on ApiException {
        packages = <Package>[];
      }
      try {
        campaigns = (await widget.api.bargainCampaigns())
            .map((e) => BargainCampaign.fromJson(e as Map<String, dynamic>))
            .toList();
      } on ApiException {
        campaigns = <BargainCampaign>[];
      }
      if (!mounted) return;
      setState(() {
        _home = data;
        _packages = packages;
        _campaigns = campaigns;
        _loading = false;
        _error = false;
      });
      _loadAreaCoupons();
    } on ApiException catch (_) {
      if (mounted) {
        setState(() {
          _loading = false;
          _error = true;
        });
      }
    }
  }

  Future<void> _loadAreaCoupons() async {
    try {
      final list = _area == '全部地區'
          ? _coupons('popular_coupons')
          : (await widget.api.classification(area: _area))
              .map((e) => Coupon.fromJson(e as Map<String, dynamic>))
              .toList();
      if (mounted) setState(() => _areaCoupons = list);
    } on ApiException {
      // Keep the previous list; area filtering must not blank the page.
    }
  }

  List<Coupon> _coupons(String key) =>
      ((_home?[key] as List?) ?? []).map((e) => Coupon.fromJson(e)).toList();

  void _openBargainList() {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => BargainListScreen(api: widget.api)));
  }

  void _openPackageList() {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => PackageListScreen(api: widget.api)));
  }

  void _openPackage(Package p) {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => PackageDetailScreen(api: widget.api, packageId: p.id)));
  }

  void _openCampaign(BargainCampaign c) {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => BargainCampaignScreen(api: widget.api, campaign: c)));
  }

  @override
  Widget build(BuildContext context) {
    final categories = (_home?['categories'] as List?) ?? [];
    // 进行中的砍价活动（未售罄），最多 6 张；无活动时整块不显示。
    final activeCampaigns =
        _campaigns.where((c) => c.status == 'active' && !c.isSoldOut).take(6).toList();
    final nearby = _areaCoupons;
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
                    errorBuilder: (_, __, ___) => const SizedBox()),
                const SizedBox(width: 8),
                const Text('COUPON GO!',
                    style: TextStyle(fontWeight: FontWeight.bold)),
              ],
            ),
            actions: [
              IconButton(
                icon: const Icon(Icons.notifications_outlined),
                tooltip: '通知',
                onPressed: () => Navigator.of(context).pushNamed('/notifications'),
              ),
            ],
            bottom: PreferredSize(
              preferredSize: const Size.fromHeight(56),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
                child: Semantics(
                  button: true,
                  label: '搜索商户或优惠',
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
                          Text('搜索商户或优惠',
                              style: TextStyle(color: CgColors.textSecondary)),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
          if (_loading)
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
          else if (_error)
            SliverFillRemaining(
              child: Center(
                child: Padding(
                  padding: const EdgeInsets.all(24),
                  child: Column(
                    mainAxisSize: MainAxisSize.min,
                    children: [
                      const Icon(Icons.wifi_off,
                          size: 48, color: CgColors.textSecondary),
                      const SizedBox(height: 12),
                      const Text('网络不稳定，请重试', textAlign: TextAlign.center),
                      const SizedBox(height: 16),
                      ElevatedButton(
                        style: ElevatedButton.styleFrom(
                            minimumSize: const Size(160, 48)),
                        onPressed: _load,
                        child: const Text('重试'),
                      ),
                    ],
                  ),
                ),
              ),
            )
          else ...[
            SliverToBoxAdapter(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  // 定位城市 / 区域芯片
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
                          onSelected: (_) {
                            setState(() => _area = a);
                            _loadAreaCoupons();
                          },
                        );
                      }).toList(),
                    ),
                  ),
                  // 分类横滑（沿用现有数据）
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
                          child: Semantics(
                            button: true,
                            label: cat['name'] as String,
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
                          ),
                        );
                      },
                    ),
                  ),
                  // 附近商户入口（按距離排序）
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
                  // 限时砍价：无进行中活动时整块不显示
                  if (activeCampaigns.isNotEmpty) ...[
                    SectionHeader(title: '限时砍价', onViewAll: _openBargainList),
                    SizedBox(
                      height: 216,
                      child: ListView.builder(
                        scrollDirection: Axis.horizontal,
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                        itemCount: activeCampaigns.length + 1,
                        itemBuilder: (_, i) {
                          if (i == activeCampaigns.length) {
                            return Padding(
                              padding: const EdgeInsets.only(right: 8),
                              child: SizedBox(
                                width: 96,
                                height: 200,
                                child: Card(
                                  child: InkWell(
                                    onTap: _openBargainList,
                                    child: const Column(
                                      mainAxisAlignment: MainAxisAlignment.center,
                                      children: [
                                        Icon(Icons.chevron_right,
                                            color: CgColors.primary, size: 32),
                                        SizedBox(height: 4),
                                        Text('查看全部',
                                            style: TextStyle(
                                                color: CgColors.primary,
                                                fontWeight: FontWeight.bold)),
                                      ],
                                    ),
                                  ),
                                ),
                              ),
                            );
                          }
                          return Padding(
                            padding: const EdgeInsets.only(right: 8),
                            child: _BargainHomeCard(
                              campaign: activeCampaigns[i],
                              onTap: () => _openCampaign(activeCampaigns[i]),
                            ),
                          );
                        },
                      ),
                    ),
                  ],
                  // 热门套餐：2 列网格
                  SectionHeader(title: '热门套餐', onViewAll: _openPackageList),
                  if (_packages.isEmpty)
                    const Padding(
                      padding: EdgeInsets.symmetric(horizontal: 16),
                      child: Text('暂时没有热门套餐。',
                          style: TextStyle(color: CgColors.textSecondary)),
                    )
                  else
                    GridView.builder(
                      shrinkWrap: true,
                      physics: const NeverScrollableScrollPhysics(),
                      padding: const EdgeInsets.symmetric(horizontal: 12),
                      gridDelegate:
                          const SliverGridDelegateWithFixedCrossAxisCount(
                        crossAxisCount: 2,
                        childAspectRatio: 0.62,
                        crossAxisSpacing: 8,
                        mainAxisSpacing: 8,
                      ),
                      itemCount: _packages.length,
                      itemBuilder: (_, i) => _PackageHomeCard(
                        package: _packages[i],
                        onTap: () => _openPackage(_packages[i]),
                      ),
                    ),
                  // 附近优惠：纵向列表（沿用领券卡片，右下加距离）
                  const SectionHeader(title: '附近优惠'),
                  if (nearby.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          const Text('附近暂时没有优惠。',
                              style:
                                  TextStyle(color: CgColors.textSecondary)),
                          const SizedBox(height: 12),
                          OutlinedButton(
                            style: OutlinedButton.styleFrom(
                                minimumSize: const Size(160, 48)),
                            onPressed: _load,
                            child: const Text('重新整理'),
                          ),
                        ],
                      ),
                    )
                  else
                    ListView.builder(
                      shrinkWrap: true,
                      physics: const NeverScrollableScrollPhysics(),
                      padding: const EdgeInsets.symmetric(horizontal: 12),
                      itemCount: nearby.length,
                      itemBuilder: (_, i) => _NearbyCouponRow(
                        coupon: nearby[i],
                        onTap: () => widget.onOpenCoupon(nearby[i]),
                      ),
                    ),
                  const SizedBox(height: 24),
                ],
              ),
            ),
          ],
        ],
      ),
    );
  }
}

/// 首页砍价卡片 160×200：商户图、套餐名、当前价（大）、底价（小，灰）、
/// 剩余时间、「去砍价」（强调色实心）。
class _BargainHomeCard extends StatelessWidget {
  const _BargainHomeCard({required this.campaign, required this.onTap});

  final BargainCampaign campaign;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final timeLeft = campaign.timeLeft;
    return SizedBox(
      width: 160,
      height: 200,
      child: Card(
        clipBehavior: Clip.antiAlias,
        child: InkWell(
          onTap: onTap,
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              SizedBox(
                height: 88,
                width: double.infinity,
                child: _HomeImage(url: campaign.image),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(8, 6, 8, 0),
                child: Text(
                  campaign.name,
                  maxLines: 1,
                  overflow: TextOverflow.ellipsis,
                  style: const TextStyle(
                      fontWeight: FontWeight.w600, fontSize: 13),
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(8, 2, 8, 0),
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
                        '底价 ${formatHkd(campaign.floorPrice)}',
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
                padding: const EdgeInsets.fromLTRB(8, 2, 8, 0),
                child: Text(
                  timeLeft == null
                      ? '已有 ${campaign.helperCount} 人在砍'
                      : '剩 ${formatCountdown(timeLeft)}',
                  style: const TextStyle(
                      color: CgColors.textSecondary, fontSize: 11),
                ),
              ),
              const Spacer(),
              Padding(
                padding: const EdgeInsets.fromLTRB(8, 0, 8, 8),
                child: SizedBox(
                  width: double.infinity,
                  height: 44,
                  child: ElevatedButton(
                    style: ElevatedButton.styleFrom(
                      backgroundColor: CgColors.orange,
                      foregroundColor: Colors.white,
                      padding: EdgeInsets.zero,
                      textStyle: const TextStyle(fontSize: 14),
                    ),
                    onPressed: onTap,
                    child: const Text('去砍价'),
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 首页热门套餐卡片：图、名称、套餐价、原价划线、已售数、「立即抢购」。
class _PackageHomeCard extends StatelessWidget {
  const _PackageHomeCard({required this.package, required this.onTap});

  final Package package;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            AspectRatio(
              aspectRatio: 16 / 9,
              child: _HomeImage(url: package.image),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 8, 10, 0),
              child: Text(
                package.name,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 4, 10, 0),
              child: Row(
                children: [
                  Text(
                    formatHkd(package.packagePrice),
                    style: const TextStyle(
                        color: CgColors.primary,
                        fontWeight: FontWeight.bold,
                        fontSize: 16,
                        fontFeatures: [FontFeature.tabularFigures()]),
                  ),
                  const SizedBox(width: 6),
                  Flexible(
                    child: Text(
                      formatHkd(package.originalPrice),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          color: CgColors.textSecondary,
                          fontSize: 12,
                          decoration: TextDecoration.lineThrough),
                    ),
                  ),
                ],
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 2, 10, 0),
              child: Text(
                '已售 ${package.soldCount}',
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
                  style: ElevatedButton.styleFrom(padding: EdgeInsets.zero),
                  onPressed: onTap,
                  child: const Text('立即抢购'),
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 附近优惠行：沿用领券卡片信息（图、名称、商户、优惠），右下加距离，
/// 「领取」为描边按钮。
class _NearbyCouponRow extends StatelessWidget {
  const _NearbyCouponRow({required this.coupon, required this.onTap});

  final Coupon coupon;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(bottom: 8),
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: SizedBox(
                  width: 88,
                  height: 88,
                  child: _HomeImage(url: coupon.image),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      coupon.name,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(fontWeight: FontWeight.w600),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      coupon.shopName,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          color: CgColors.textSecondary, fontSize: 12),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      coupon.amountText,
                      style: const TextStyle(
                          color: CgColors.primary,
                          fontWeight: FontWeight.bold,
                          fontSize: 16),
                    ),
                  ],
                ),
              ),
              const SizedBox(width: 8),
              Column(
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  SizedBox(
                    height: 44,
                    child: OutlinedButton(
                      style: OutlinedButton.styleFrom(
                        side: const BorderSide(color: CgColors.primary),
                        foregroundColor: CgColors.primary,
                        padding:
                            const EdgeInsets.symmetric(horizontal: 16),
                      ),
                      onPressed: onTap,
                      child: const Text('领取'),
                    ),
                  ),
                  const SizedBox(height: 6),
                  if (coupon.distanceText.isNotEmpty)
                    Row(
                      mainAxisSize: MainAxisSize.min,
                      children: [
                        const Icon(Icons.near_me,
                            size: 12, color: CgColors.textSecondary),
                        const SizedBox(width: 2),
                        Text(
                          coupon.distanceText,
                          style: const TextStyle(
                              color: CgColors.textSecondary, fontSize: 12),
                        ),
                      ],
                    ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}

/// 图片：服务端 http(s) 地址用网络图，其余用本地占位图。
class _HomeImage extends StatelessWidget {
  const _HomeImage({required this.url});

  final String url;

  @override
  Widget build(BuildContext context) {
    if (url.startsWith('http://') || url.startsWith('https://')) {
      return Image.network(
        url,
        fit: BoxFit.cover,
        width: double.infinity,
        height: double.infinity,
        errorBuilder: (_, __, ___) =>
            Container(color: CgColors.pageBg),
      );
    }
    return Image.asset(
      'assets/images/dummy_image_1.png',
      fit: BoxFit.cover,
      width: double.infinity,
      height: double.infinity,
      errorBuilder: (_, __, ___) => Container(color: CgColors.pageBg),
    );
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
            hintText: '搜索商户或优惠',
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
                errorBuilder: (_, __, ___) => Container(
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
                    errorBuilder: (_, __, ___) => Container(
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
