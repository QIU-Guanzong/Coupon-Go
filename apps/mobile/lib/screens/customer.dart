import 'dart:async';

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api_client.dart';
import '../feature_flags.dart';
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
  static const _qbotHintSeen = 'home_qbot_hint_seen_v1';
  final _qbotHint = GlobalKey<TooltipState>();
  Timer? _qbotHintTimer;

  @override
  void initState() {
    super.initState();
    _load();
    WidgetsBinding.instance.addPostFrameCallback((_) => _showQbotHint());
  }

  Future<void> _showQbotHint() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      if (!mounted || ModalRoute.of(context)?.isCurrent != true ||
          prefs.getBool(_qbotHintSeen) == true) {
        return;
      }
      if (_qbotHint.currentState?.ensureTooltipVisible() != true) return;
      _qbotHintTimer = Timer(const Duration(seconds: 4), () {
        if (mounted && ModalRoute.of(context)?.isCurrent == true) {
          Tooltip.dismissAllToolTips();
        }
      });
      await prefs.setBool(_qbotHintSeen, true);
    } catch (_) {
      // The hint is optional when preferences are unavailable.
    }
  }

  void _openQbot() {
    _qbotHintTimer?.cancel();
    Tooltip.dismissAllToolTips();
    Navigator.of(context).pushNamed('/qbot');
  }

  @override
  void dispose() {
    _qbotHintTimer?.cancel();
    super.dispose();
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
                const Flexible(
                  child: Text('COUPON GO!',
                      maxLines: 1, overflow: TextOverflow.ellipsis,
                      style: TextStyle(fontWeight: FontWeight.bold)),
                ),
              ],
            ),
            actions: [
              Tooltip(
                key: _qbotHint,
                message: publicPreview
                    ? context.tr('找優惠、問 Q仔。', 'Ask Q仔 about offers.')
                    : context.tr('找優惠、查積分，問 Q仔。', 'Ask Q仔 for offers or points.'),
                triggerMode: TooltipTriggerMode.manual,
                preferBelow: true,
                excludeFromSemantics: true,
                child: TextButton(
                  onPressed: _openQbot,
                  style: TextButton.styleFrom(
                    foregroundColor: Theme.of(context)
                        .colorScheme.onPrimary,
                    padding: const EdgeInsets.symmetric(horizontal: 8),
                  ),
                  child: Row(mainAxisSize: MainAxisSize.min, children: [
                    Image.asset('assets/images/img_logo_cropped.png',
                        width: 24, excludeFromSemantics: true),
                    const SizedBox(width: 4),
                    const Text('Q仔', maxLines: 1, softWrap: false),
                  ]),
                ),
              ),
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
          if (publicPreview)
            SliverToBoxAdapter(
              child: Container(
                width: double.infinity,
                color: const Color(0xFFFFF5E8),
                padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                child: Row(children: [
                  const Icon(Icons.info_outline, size: 18, color: Color(0xFF785324)),
                  const SizedBox(width: 8),
                  Expanded(child: Text(
                    context.tr('以下商戶及優惠為範例資料，僅供瀏覽。', 'The shops and offers below are sample data for browsing only.'),
                    style: const TextStyle(color: Color(0xFF604317), fontSize: 13),
                  )),
                ]),
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
                  // Category labels come from the catalogue. Each remains a
                  // direct link to the existing category screen.
                  if (categories.isNotEmpty)
                    LayoutBuilder(builder: (context, constraints) {
                      final tileWidth = ((constraints.maxWidth - 40) / 4)
                          .clamp(78.0, 144.0);
                      final textGrowth = (MediaQuery.textScalerOf(context)
                                  .scale(13) - 13)
                              .clamp(0, 24) *
                          2;
                      return SizedBox(
                        height: 120.0 + textGrowth.toDouble(),
                        child: ListView.separated(
                          scrollDirection: Axis.horizontal,
                          padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
                          itemCount: categories.length,
                          separatorBuilder: (_, __) => const SizedBox(width: 8),
                          itemBuilder: (context, index) {
                            final category = categories[index] as Map;
                            final name = category['name'] as String? ?? '';
                            final english = category['name_en'] as String? ?? name;
                            final label = context.tr(name, english);
                            final image = switch (english.toLowerCase()) {
                              'food & drink' => 'assets/images/mvp-offer-dim-sum-platter.jpg',
                              'retail' => 'assets/images/mvp-offer-produce-basket.jpg',
                              'services' => 'assets/images/mvp-offer-hot-stone-spa.jpg',
                              _ => null,
                            };
                            return SizedBox(
                              width: tileWidth,
                              child: Material(
                                color: Colors.white,
                                borderRadius: BorderRadius.circular(14),
                                clipBehavior: Clip.antiAlias,
                                child: InkWell(
                                  onTap: () => Navigator.of(context).pushNamed(
                                    '/category',
                                    arguments: category['id'] as int,
                                  ),
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.stretch,
                                    children: [
                                      Expanded(
                                        child: image == null
                                            ? const ColoredBox(
                                                color: Color(0xFFFCEBEA),
                                                child: Icon(Icons.grid_view_rounded,
                                                  color: CgColors.primary, size: 30),
                                              )
                                            : CatalogPhoto(url: image),
                                      ),
                                      Padding(
                                        padding: const EdgeInsets.symmetric(
                                            horizontal: 6, vertical: 7),
                                        child: Text(label,
                                          maxLines: 2,
                                          overflow: TextOverflow.ellipsis,
                                          textAlign: TextAlign.center,
                                          style: const TextStyle(
                                            fontSize: 13,
                                            fontWeight: FontWeight.w600,
                                            color: CgColors.textPrimary,
                                          )),
                                      ),
                                    ],
                                  ),
                                ),
                              ),
                            );
                          },
                        ),
                      );
                    }),
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
                    LayoutBuilder(builder: (context, constraints) {
                      final columns = (constraints.maxWidth / 260).floor().clamp(2, 5);
                      final itemWidth = (constraints.maxWidth - 24 - (columns - 1) * 8) / columns;
                      final textGrowth = (MediaQuery.textScalerOf(context).scale(16) - 16).clamp(0, 32);
                      return GridView.builder(
                        shrinkWrap: true,
                        physics: const NeverScrollableScrollPhysics(),
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                        gridDelegate: SliverGridDelegateWithFixedCrossAxisCount(
                          crossAxisCount: columns,
                          mainAxisExtent: itemWidth * 9 / 16 + 166 + textGrowth * 4,
                          crossAxisSpacing: 8,
                          mainAxisSpacing: 8,
                        ),
                        itemCount: _packages.length,
                        itemBuilder: (_, i) => _PackageHomeCard(
                          package: _packages[i],
                          onTap: () => _openPackage(_packages[i]),
                        ),
                      );
                    }),
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
                child: Wrap(
                  spacing: 4,
                  crossAxisAlignment: WrapCrossAlignment.center,
                  children: [
                    Text(
                      formatHkd(campaign.originalPrice),
                      style: const TextStyle(
                          color: CgColors.primary,
                          fontWeight: FontWeight.bold,
                          fontSize: 16,
                          fontFeatures: [FontFeature.tabularFigures()]),
                    ),
                    Text(
                      context.tr('底價 ${formatHkd(campaign.floorPrice)}',
                          'Floor ${formatHkd(campaign.floorPrice)}'),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                          color: CgColors.textSecondary, fontSize: 11),
                    ),
                  ],
                ),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(8, 2, 8, 0),
                child: Text(
                  timeLeft == null
                      ? context.tr('已有 ${campaign.helperCount} 人在砍',
                          '${campaign.helperCount} joining in')
                      : context.tr('剩 ${formatCountdown(timeLeft)}',
                          '${formatCountdown(timeLeft)} left'),
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
                      textStyle: const TextStyle(
                        fontFamily: 'Roboto',
                        fontFamilyFallback: ['Coupon UI Fallback', 'Coupon UI Supplemental'],
                        fontSize: 14,
                      ),
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
                      context.tr(coupon.amountText, coupon.amountTextEnglish),
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
                      child: Text(coupon.isPaid
                          ? context.tr('購買', 'Buy')
                          : context.tr('領取','Collect')),
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
  late String _purchaseRef;

  @override
  void initState() {
    super.initState();
    _purchaseRef = 'coupon-${widget.coupon.id}-${DateTime.now().microsecondsSinceEpoch}';
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
    final validDate = c.validTo;
    final validText = validDate?.toLocal().toString().split(' ').first;
    final remaining = _remaining ?? c.remaining;
    final amount = context.tr(c.amountText, c.amountTextEnglish);
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
          Wrap(
            spacing: 10,
            runSpacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(amount,
                  style: const TextStyle(
                      color: CgColors.primary,
                      fontSize: 26,
                      fontWeight: FontWeight.bold)),
              if (c.regularPrice != null)
                Text(formatHkd(c.regularPrice!),
                    style: const TextStyle(
                        decoration: TextDecoration.lineThrough,
                        color: CgColors.textSecondary)),
              if (validDate != null)
                Chip(label: Text(c.daysLeft > 0
                  ? context.tr('剩 ${c.daysLeft} 日', '${c.daysLeft} days left')
                  : validDate.isBefore(DateTime.now())
                      ? context.tr('已到期', 'Expired')
                      : context.tr('即將到期', 'Ends soon'))),
            ],
          ),
          const SizedBox(height: 12),
          if (validText != null)
            Text(context.tr('有效日期：至 $validText', 'Valid until $validText')),
          Text(context.tr('剩餘數量：$remaining / ${c.total}', 'Remaining: $remaining / ${c.total}')),
          if (c.minSpend > 0) Text(context.tr(
              '於本店惠顧滿 ${formatHkd(c.minSpend)}',
              'Minimum spend ${formatHkd(c.minSpend)}')),
          if (c.terms.trim().isNotEmpty) ...[
            const SizedBox(height: 16),
            Text(context.tr('條款和條件', 'Terms and conditions'),
                style: const TextStyle(fontWeight: FontWeight.bold)),
            Text(c.terms.trim()),
          ],
        ],
      ),
      bottomNavigationBar: Padding(
        padding: const EdgeInsets.all(16),
        child: ElevatedButton(
          onPressed: publicPreview ||
                  (!c.isPaid && _claimed) || _claiming || (c.isPaid && remaining <= 0) ||
                  (c.isPaid && (c.salePrice == null || c.salePrice! <= 0))
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
                    if (c.isPaid) {
                      final result = await widget.api.purchasePaidCoupon(c.id, 'card', clientRef: _purchaseRef);
                      final order = PackageOrder.fromJson(result['order'] as Map<String, dynamic>);
                      if (!context.mounted) return;
                      final done = await showPaymentSheet(context, widget.api, order);
                      if (done == true && context.mounted) {
                        _purchaseRef = 'coupon-${c.id}-${DateTime.now().microsecondsSinceEpoch}';
                        await Navigator.of(context).push(MaterialPageRoute(
                          builder: (_) => PaymentResultScreen(api: widget.api, initialOrder: order)));
                      }
                      return;
                    }
                    final result = await widget.api.claimCoupon(c.id);
                    if (!mounted) return;
                    setState(() {
                      _claimed = true;
                      _remaining = result['coupon']?['remaining'] as int?;
                    });
                    if (context.mounted) {
                      final points = widget.api.appConfig.value.pointsClaim;
                      showCgToast(context, points != null && points > 0
                          ? context.tr('已存入 Q Wallet，+$points 積分',
                              'Saved to Q Wallet, +$points points')
                          : context.tr('已存入 Q Wallet', 'Saved to Q Wallet'));
                    }
                  } on ApiException catch (e) {
                    if (!context.mounted) return;
                    if (!c.isPaid && e.status == 409) setState(() => _claimed = true);
                    showCgToast(context, e.message);
                  } catch (_) {
                    if (context.mounted) showCgToast(context, context.tr('未能確認領券結果，請重試', 'Could not confirm the claim. Try again.'));
                  } finally {
                    if (mounted) setState(() => _claiming = false);
                  }
                },
          child: Text(publicPreview
              ? context.tr('只供瀏覽', 'Browse only')
              : c.isPaid && remaining <= 0
              ? context.tr('已售罄', 'Sold out')
              : c.isPaid && (c.salePrice == null || c.salePrice! <= 0)
              ? context.tr('價格暫不可用', 'Price unavailable')
              : !c.isPaid && _claimed
              ? context.tr('已領取', 'Claimed')
              : _claiming
                  ? context.tr('處理中…', 'Processing…')
                  : c.isPaid
                      ? context.tr('購買 HK\$${c.salePrice!.toStringAsFixed(2)}', 'Buy HK\$${c.salePrice!.toStringAsFixed(2)}')
                      : context.tr('立即領取', 'Claim now')),
        ),
      ),
    );
  }
}

/// Shop detail: photos, info, branches, coupons from this shop.
class ShopDetailScreen extends StatefulWidget {
  const ShopDetailScreen({super.key, required this.api, required this.shop,
    required this.onOpenCoupon, this.launchExternal});

  final ApiClient api;
  final Shop shop;
  final void Function(Coupon) onOpenCoupon;
  final Future<bool> Function(Uri)? launchExternal;

  @override
  State<ShopDetailScreen> createState() => _ShopDetailScreenState();
}

class _ShopDetailScreenState extends State<ShopDetailScreen> {
  late Future<Map<String, dynamic>> _detail = widget.api.shopDetails(widget.shop.id);

  void _reload() {
    final result = widget.api.shopDetails(widget.shop.id);
    setState(() { _detail = result; });
  }

  Future<void> _launch(Uri uri) async {
    if (publicPreview) {
      showCgToast(context, context.tr('此商戶未提供聯絡及導航資料。',
          'Contact and directions are unavailable for this shop.'));
      return;
    }
    try {
      final launched = await (widget.launchExternal?.call(uri) ??
          launchUrl(uri, mode: LaunchMode.externalApplication));
      if (launched) return;
    } catch (_) {
      // Stay on the shop screen if no system handler can open the link.
    }
    if (mounted) {
      showCgToast(context, context.tr('未能開啟連結', 'Could not open the link'));
    }
  }

  @override
  Widget build(BuildContext context) {
    final api = widget.api;
    final initialShop = widget.shop;
    return Scaffold(
      appBar: AppBar(
        title: Text(initialShop.name),
        actions: [
          FavoriteControl(api: api, id: initialShop.id, shop: true, initial: initialShop.isFavorite),
        ],
      ),
      body: FutureBuilder<Map<String, dynamic>>(
        future: _detail,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Padding(
              padding: EdgeInsets.all(16),
              child: Column(children: [SkeletonBox(height: 200)]),
            );
          }
          if (snap.hasError || !snap.hasData) {
            return Center(
              child: Column(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Text(context.tr('未能載入商店資料', 'Could not load store details')),
                  TextButton(onPressed: _reload, child: Text(context.tr('重試', 'Retry'))),
                ],
              ),
            );
          }
          final data = snap.data!;
          final shop = data['shop'] is Map<String, dynamic>
              ? Shop.fromJson(data['shop'] as Map<String, dynamic>)
              : initialShop;
          final phone = shop.phone.trim().isEmpty ? initialShop.phone : shop.phone;
          final dialNumber = phone.replaceAll(RegExp(r'[\s().-]'), '');
          final canDial = RegExp(r'^\+?[0-9]+$').hasMatch(dialNumber);
          final coupons = ((data['coupons'] as List?) ?? [])
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
              if (shop.address.isNotEmpty) Text(shop.address),
              if (phone.trim().isNotEmpty)
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  leading: const Icon(Icons.call_outlined),
                  title: Text(phone),
                  onTap: canDial ? () => _launch(Uri(scheme: 'tel', path: dialNumber)) : null,
                ),
              if (shop.description.isNotEmpty) Text(shop.description),
              const SizedBox(height: 8),
              ListTile(
                leading: const Icon(Icons.star, color: CgColors.amber),
                title: Text(context.tr('評分與評價', 'Ratings and reviews')),
                subtitle: Text(context.tr('口味、環境及服務', 'Taste, ambience and service')),
                trailing: const Icon(Icons.chevron_right),
                onTap: () => Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) =>
                        ShopReviewsScreen(api: api, shop: shop))),
              ),
              const SizedBox(height: 8),
              if (branches.isNotEmpty) ...[
                Text(context.tr('分店', 'Branches'),
                    style: const TextStyle(fontWeight: FontWeight.bold)),
                ...branches.map((b) {
                  final address = (b['address'] as String?)?.trim() ?? '';
                  return ListTile(
                    leading: const Icon(Icons.store),
                    title: Text((b['branch_name'] as String?) ?? ''),
                    subtitle: address.isEmpty ? null : Text(address),
                    trailing: address.isEmpty ? null : const Icon(Icons.map_outlined),
                    onTap: address.isEmpty ? null : () => _launch(Uri.https(
                      'www.google.com', '/maps/search/', {'api': '1', 'query': address},
                    )),
                  );
                }),
              ],
              const SizedBox(height: 8),
              Text(context.tr('本店優惠券', 'Store coupons'),
                  style: const TextStyle(fontWeight: FontWeight.bold)),
              ...coupons.map((c) => ListTile(
                    leading: const Icon(Icons.local_offer, color: CgColors.primary),
                    title: Text(c.name),
                    subtitle: Text(context.tr(c.amountText, c.amountTextEnglish)),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () => widget.onOpenCoupon(c),
                  )),
              if (coupons.isEmpty) Text(context.tr('暫時沒有優惠券。', 'No coupons right now.')),
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
        _LimitedBargainPreview(api: api),
        _LimitedNotification(key: ValueKey(api.isLoggedIn), api: api),
        Expanded(child: CouponResults(api: api, load: api.limitedTime, onOpenCoupon: onOpenCoupon)),
      ]),
    );
  }
}

class _LimitedBargainPreview extends StatefulWidget {
  const _LimitedBargainPreview({required this.api});

  final ApiClient api;

  @override
  State<_LimitedBargainPreview> createState() => _LimitedBargainPreviewState();
}

class _LimitedBargainPreviewState extends State<_LimitedBargainPreview> {
  late Future<List<BargainCampaign>> _campaigns = _load();

  Future<List<BargainCampaign>> _load() async =>
      (await widget.api.bargainCampaigns())
          .map((row) => BargainCampaign.fromJson(row))
          .where((c) => c.status == 'active' && !c.isSoldOut &&
              (c.expiresAt?.isAfter(DateTime.now()) ?? true))
          .take(6)
          .toList();

  void _refresh() {
    final result = _load();
    setState(() { _campaigns = result; });
  }

  Future<void> _open(Widget screen) async {
    await Navigator.of(context).push(MaterialPageRoute(builder: (_) => screen));
    if (mounted) _refresh();
  }

  @override
  Widget build(BuildContext context) => FutureBuilder<List<BargainCampaign>>(
        future: _campaigns,
        builder: (context, snap) {
          if (snap.connectionState != ConnectionState.done) {
            return const Padding(
              padding: EdgeInsets.all(12),
              child: SkeletonBox(height: 64),
            );
          }
          if (snap.hasError) {
            return ListTile(
              title: Text(context.tr('未能載入砍價活動', 'Could not load bargains')),
              trailing: TextButton(
                onPressed: _refresh, child: Text(context.tr('重試', 'Retry')),
              ),
            );
          }
          final campaigns = snap.data!;
          if (campaigns.isEmpty) return const SizedBox.shrink();
          return Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              SectionHeader(
                title: context.tr('限時砍價', 'Flash bargains'),
                onViewAll: () => _open(BargainListScreen(api: widget.api)),
              ),
              SizedBox(
                height: 200,
                child: ListView.separated(
                  scrollDirection: Axis.horizontal,
                  padding: const EdgeInsets.symmetric(horizontal: 12),
                  itemCount: campaigns.length,
                  separatorBuilder: (_, __) => const SizedBox(width: 8),
                  itemBuilder: (_, i) => _BargainHomeCard(
                    campaign: campaigns[i],
                    onTap: () => _open(BargainCampaignScreen(
                        api: widget.api, campaign: campaigns[i])),
                  ),
                ),
              ),
            ],
          );
        },
      );
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
