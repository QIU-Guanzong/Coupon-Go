import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';

import '../api_client.dart';
import '../language.dart';
import 'discovery.dart';
import 'guest_auth.dart' show guestLoginPrompt;
export 'discovery.dart' show DiscoveryScreen;
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import '../widgets/favorite_control.dart';
import 'bargain.dart';
import 'packages.dart';
import 'reviews_admin.dart';

/// Home (UI_SPEC §2): search bar + area chips, category strip,
/// flash-bargain horizontal cards, hot-package 2-col grid, nearby
/// coupon list with distance. Guest browsing needs no login; buying /
/// helping asks for login on the target page, then returns.
class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.api, required this.onOpenCoupon,
    required this.onOpenShop, required this.onOpenSearch, required this.onOpenLimited, required this.onNotifications});

  final ApiClient api;
  final void Function(Coupon) onOpenCoupon;
  final void Function(Shop) onOpenShop;
  final VoidCallback onOpenSearch;
  final VoidCallback onOpenLimited;
  final VoidCallback onNotifications;

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
    } catch (_) {
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
              IconButton(tooltip: 'Q仔', icon: Image.asset('assets/images/img_logo_cropped.png', width: 30),
                onPressed: () => Navigator.of(context).pushNamed('/qbot')),
              IconButton(
                icon: const Icon(Icons.notifications_outlined),
                tooltip: context.tr('通知','Notifications'),
                onPressed: widget.onNotifications,
              ),
            ],
            bottom: PreferredSize(
              preferredSize: const Size.fromHeight(56),
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 10),
                child: Semantics(
                  button: true,
                  label: context.tr('搜尋商戶或優惠','Search stores and offers'),
                  child: GestureDetector(
                    onTap: widget.onOpenSearch,
                    child: Container(
                      height: 44,
                      padding: const EdgeInsets.symmetric(horizontal: 14),
                      decoration: BoxDecoration(
                        color: Colors.white,
                        borderRadius: BorderRadius.circular(22),
                      ),
                      child: Row(
                        children: [
                          const Icon(Icons.search, color: CgColors.textSecondary),
                          const SizedBox(width: 8),
                          Text(context.tr('搜尋商戶或優惠','Search stores and offers'),
                              style: const TextStyle(color: CgColors.textSecondary)),
                        ],
                      ),
                    ),
                  ),
                ),
              ),
            ),
          ),
          if (_loading)
            const SliverToBoxAdapter(
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
                      Text(context.tr('網絡不穩定，請重試','Connection problem. Please retry.'), textAlign: TextAlign.center),
                      const SizedBox(height: 16),
                      ElevatedButton(
                        style: ElevatedButton.styleFrom(
                            minimumSize: const Size(160, 48)),
                        onPressed: _load,
                        child: Text(context.tr('重試','Retry')),
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
                          label: Text(context.tr(a, const {'全部地區':'All regions','香港':'Hong Kong','九龍':'Kowloon','新界':'New Territories'}[a] ?? a)),
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
                        label: Text(context.tr('附近商戶 · 按距離排序','Nearby shops by distance')),
                      ),
                    ),
                  ),
                  // 限时砍价：无进行中活动时整块不显示
                  if (activeCampaigns.isNotEmpty) ...[
                    SectionHeader(title: context.tr('限時砍價','Limited-time bargains'), onViewAll: _openBargainList),
                    SizedBox(
                      height: 216 + (MediaQuery.textScalerOf(context).scale(16) - 16).clamp(0, 32) * 4,
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
                  SectionHeader(title: context.tr('熱門套餐','Popular packages'), onViewAll: _openPackageList),
                  if (_packages.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Text(context.tr('暫時沒有熱門套餐。','No popular packages yet.'),
                          style: const TextStyle(color: CgColors.textSecondary)),
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
                  SectionHeader(title: context.tr('附近優惠','Nearby offers')),
                  if (nearby.isEmpty)
                    Padding(
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(context.tr('附近暫時沒有優惠。','No nearby offers yet.'),
                              style:
                                  const TextStyle(color: CgColors.textSecondary)),
                          const SizedBox(height: 12),
                          OutlinedButton(
                            style: OutlinedButton.styleFrom(
                                minimumSize: const Size(160, 48)),
                            onPressed: _load,
                            child: Text(context.tr('重新整理','Refresh')),
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
              Expanded(child: SizedBox(width: double.infinity, child: CatalogPhoto(url: campaign.image))),
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
              const SizedBox(height: 4),
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
                    child: Text(context.tr('去砍價','Bargain')),
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
              child: CatalogPhoto(url: package.image),
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
                  child: Text(context.tr('立即搶購','Buy now')),
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
                  child: CatalogPhoto(url: coupon.image),
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
                      child: Text(context.tr('領取','Collect')),
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
  String query = '';
  @override
  void dispose() { _ctrl.dispose(); super.dispose(); }

  Future<void> open(Map row) async {
    try {
      final id = row['id'] as int;
      if (row['kind'] == 'branch' || row['has_branch'] == 1) {
        await Navigator.push(context, MaterialPageRoute(builder: (_) => BranchResults(api: widget.api, shopId: id, keyword: query, openShop: () async {
          try {
            final data = await widget.api.shopDetails(id);
            if (mounted) widget.onOpenShop(Shop.fromJson(data['shop']));
          } catch (_) {
            if (mounted) showCgToast(context, context.tr('未能載入，請重試','Could not load. Please retry.'));
          }
        })));
      } else if (row['kind'] == 'coupon') {
        final data = await widget.api.couponDetails(id);
        if (mounted) widget.onOpenCoupon(Coupon.fromJson(data['coupon']));
      } else {
        final data = await widget.api.shopDetails(id);
        if (mounted) widget.onOpenShop(Shop.fromJson(data['shop']));
      }
    } catch (_) {
      if (mounted) showCgToast(context, context.tr('未能載入，請重試','Could not load. Please retry.'));
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    return Scaffold(
      appBar: AppBar(title: TextField(controller: _ctrl, autofocus: true,
        decoration: InputDecoration(hintText: t('搜尋商戶或優惠','Search stores and offers'), prefixIcon: const Icon(Icons.search)),
        onChanged: (value) => setState(() => query = value.trim()))),
      body: query.isEmpty ? ListView(padding: const EdgeInsets.all(16), children: [
        Text(t('最近搜尋','Recent Searches'),style: Theme.of(context).textTheme.titleMedium),
        if (!widget.api.isLoggedIn) Text(t('沒有資料','No Data Found')) else
          RequestView(load: () => widget.api.get('/get-search-history'), content: (data) {
            final history = data['history'] as List;
            if (history.isEmpty) return Text(t('沒有資料','No Data Found'));
            return Wrap(children: [for (final item in history) ActionChip(label: Text(item['search_value']),
              onPressed: () => setState(() { _ctrl.text = item['search_value']; query = _ctrl.text; }))]);
          }),
        const SizedBox(height: 16), Text(t('熱門搜尋','Popular Searches'),style: Theme.of(context).textTheme.titleMedium),
        Text(t('沒有資料','No Data Found')),
      ]) : RequestView(key: ValueKey(query), load: () => widget.api.suggestions(query), content: (rows) =>
        rows.isEmpty ? Center(child: Text(t('沒有資料','No Data Found'))) : ListView(children: [
          for (final row in rows) ListTile(title: Text(row['title']), subtitle: Text(row['subtitle'] ?? ''), onTap: () => open(row)),
        ])),
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
  bool _claiming = false;
  int? _remaining;

  @override
  void initState() {
    super.initState();
    if (widget.api.isLoggedIn && widget.coupon.packageId == null) _readClaim();
  }

  Future<void> _readClaim() async {
    try {
      final detail = await widget.api.couponDetails(widget.coupon.id);
      if (!mounted || _claiming || _claimed) return;
      setState(() {
        _claimed = detail['is_claimed'] == true;
        _remaining = detail['coupon']?['remaining'] as int?;
      });
    } catch (_) {
      // The claim operation can still confirm ownership if this read failed.
    }
  }

  @override
  Widget build(BuildContext context) {
    final c = widget.coupon;
    if (c.packageId != null) {
      return PackageDetailScreen(api: widget.api, packageId: c.packageId!);
    }
    return Scaffold(
      appBar: AppBar(
        title: Text(context.tr('優惠券詳情', 'Coupon details')),
        actions: [
          FavoriteControl(api: widget.api, id: c.id, initial: c.isFavorite),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: CatalogPhoto(url: c.image, height: 200),
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
          Text('剩餘數量：${_remaining ?? c.remaining} / ${c.total}'),
          if (c.minSpend > 0) Text('於本店惠顧滿 \$${c.minSpend.toStringAsFixed(0)}'),
          const SizedBox(height: 16),
          const Text('條款和條件', style: TextStyle(fontWeight: FontWeight.bold)),
          Text(c.terms.isEmpty ? '每人限領一次；不可與其他優惠同時使用。' : c.terms),
        ],
      ),
      bottomNavigationBar: Padding(
        padding: const EdgeInsets.all(16),
        child: ElevatedButton(
          onPressed: _claimed || _claiming
              ? null
              : () async {
                  if (_claiming) return;
                  setState(() => _claiming = true);
                  try {
                    if (!widget.api.isLoggedIn) {
                      if (!await guestLoginPrompt(context) || !context.mounted) return;
                      await Navigator.of(context).pushNamed('/login');
                      if (!widget.api.isLoggedIn || !mounted) return;
                    }
                    final result = await widget.api.claimCoupon(c.id);
                    if (!mounted) return;
                    setState(() {
                      _claimed = true;
                      _remaining = result['coupon']?['remaining'] as int?;
                    });
                    if (context.mounted) {
                      showCgToast(context, '已存入 Q Wallet（+10 分）');
                    }
                  } on ApiException catch (e) {
                    if (!context.mounted) return;
                    if (e.status == 409) setState(() => _claimed = true);
                    showCgToast(context, e.message);
                  } catch (_) {
                    if (context.mounted) showCgToast(context, '未能確認領券結果，請重試');
                  } finally {
                    if (mounted) setState(() => _claiming = false);
                  }
                },
          child: Text(_claimed ? '已領取' : _claiming ? '領取中…' : '立即領取'),
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
      appBar: AppBar(
        title: Text(shop.name),
        actions: [
          FavoriteControl(api: api, id: shop.id, shop: true, initial: shop.isFavorite),
        ],
      ),
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
                child: CatalogPhoto(url: shop.image, height: 200),
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
      body: CouponResults(api: api, load: () => api.classification(categoryId: categoryId), onOpenCoupon: onOpenCoupon),
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
      appBar: AppBar(title: Text(context.tr('限時','Limited Time'))),
      body: Column(children: [
        _LimitedNotification(key: ValueKey(api.isLoggedIn), api: api),
        Expanded(child: CouponResults(api: api, load: api.limitedTime, onOpenCoupon: onOpenCoupon)),
      ]),
    );
  }
}

class _LimitedNotification extends StatefulWidget {
  const _LimitedNotification({super.key, required this.api});
  final ApiClient api;
  @override
  State<_LimitedNotification> createState() => _LimitedNotificationState();
}

class _LimitedNotificationState extends State<_LimitedNotification> {
  bool enabled = false, busy = false, failed = false;
  @override
  void initState() {
    super.initState();
    if (widget.api.isLoggedIn) load();
  }

  Future<void> load() async {
    setState(() { busy = true; failed = false; });
    try {
      final data = await widget.api.get('/get-profile');
      if (mounted) setState(() => enabled = data['limited_time_notify'] as bool);
    } catch (_) {
      if (mounted) setState(() => failed = true);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  Future<void> change(bool value) async {
    if (!widget.api.isLoggedIn) {
      if (await guestLoginPrompt(context) && mounted) {
        await Navigator.of(context).pushNamed('/login');
      }
      return;
    }
    setState(() => busy = true);
    try {
      final data = await widget.api.post('/limited-time-notification', query: {'enabled': '$value'});
      if (mounted) setState(() => enabled = data['limited_time_notification'] as bool);
    } catch (_) {
      if (mounted) showCgToast(context, context.tr('未能儲存通知設定','Could not save notification settings'));
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final label = context.tr('限時優惠券通知','Limited-time coupon notifications');
    if (failed) return ListTile(title: Text(label), trailing: TextButton(onPressed: load, child: Text(context.tr('重試','Retry'))));
    return SwitchListTile(title: Text(label),
      subtitle: Text(context.tr('在此查看限時優惠券','Find limited-time coupons here')),
      value: enabled, onChanged: busy ? null : change);
  }
}
