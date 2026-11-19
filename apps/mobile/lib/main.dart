import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart' show LicenseRegistry, LicenseEntryWithLineBreaks;
import 'package:flutter/services.dart' show rootBundle;
import 'package:flutter_localizations/flutter_localizations.dart';

import 'api_client.dart';
import 'language.dart';
import 'screens/guest_auth.dart' show guestLoginPrompt;
import 'package:share_plus/share_plus.dart';
import 'models.dart';
import 'screens/account.dart';
import 'screens/admin_v2.dart';
import 'screens/bargain.dart';
import 'screens/cashback.dart';
import 'screens/customer.dart';
import 'screens/nearby.dart';
import 'screens/packages.dart';
import 'screens/reviews_admin.dart';
import 'screens/vendor.dart';
import 'theme.dart';
import 'widgets/widgets.dart';

void main() {
  LicenseRegistry.addLicense(() async* {
    final license = await rootBundle.loadString('fonts/OFL.txt');
    yield LicenseEntryWithLineBreaks(['Coupon UI Fallback (Noto Sans HK)', 'Coupon UI Supplemental (Noto Sans SC)'], license);
  });
  runApp(const CouponGoApp());
}

class CouponGoApp extends StatefulWidget {
  const CouponGoApp({super.key});

  @override
  State<CouponGoApp> createState() => _CouponGoAppState();
}

class _CouponGoAppState extends State<CouponGoApp> {
  final _api = ApiClient();
  final _language = AppLanguage();
  bool _ready = false;
  bool _authed = false;
  bool _vendorMode = false;

  @override
  void initState() {
    super.initState();
    _api.addListener(_sessionChanged);
    Future.wait([_api.loadToken(), _language.load()]).then((_) {
      setState(() {
        _ready = true;
        _authed = _api.isLoggedIn;
      });
    });
  }

  void _sessionChanged() {
    setState(() {
      _authed = _api.isLoggedIn;
      if (!_authed) _vendorMode = false;
    });
  }

  @override
  void dispose() {
    _api.removeListener(_sessionChanged);
    _api.dispose();
    _language.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!_ready) return const Directionality(textDirection: TextDirection.ltr, child: SplashScreen());
    return LanguageScope(language: _language, child: Builder(builder: (c) => MaterialApp(
      locale: LanguageScope.of(c).english ? const Locale('en') : const Locale.fromSubtags(languageCode: 'zh', scriptCode: 'Hant', countryCode: 'HK'),
      supportedLocales: const [Locale('en'), Locale.fromSubtags(languageCode: 'zh', scriptCode: 'Hant', countryCode: 'HK')],
      localizationsDelegates: GlobalMaterialLocalizations.delegates,
      title: 'Coupon GO!',
      theme: CgTheme.light(),
      home: HomeShell(
              api: _api,
              authed: _authed,
              vendorMode: _vendorMode,
              onAuthed: () => setState(() => _authed = true),
              onLogout: () => setState(() { _authed = false; _vendorMode = false; }),
              onToggleMode: () =>
                  setState(() => _vendorMode = !_vendorMode),
            ),
      onGenerateRoute: (settings) {
        switch (settings.name) {
          case '/notifications':
            return MaterialPageRoute(
                builder: (_) => NotificationsScreen(api: _api));
          case '/settings':
            return MaterialPageRoute(
                builder: (_) => SettingsScreen(
                    api: _api, onLogout: () => setState(() { _authed = false; _vendorMode = false; })));
          case '/guide':
            return MaterialPageRoute(builder: (_) => const GuideScreen());
          case '/login':
            // 返现页 401 时的登录入口：登录成功后回原界面。
            return MaterialPageRoute(
                builder: (routeContext) => AuthScreen(
                    api: _api,
                    onAuthed: () {
                      setState(() => _authed = true);
                      Navigator.of(routeContext).pop();
                    }));
          case '/nearby': // line A: nearby shops (list + marker view)
            return MaterialPageRoute(
                builder: (_) => NearbyScreen(api: _api));
          case '/bargain':
            return MaterialPageRoute(
                builder: (_) => BargainListScreen(api: _api));
          case '/bargain/session':
            return MaterialPageRoute(
                builder: (_) => BargainDetailScreen(
                    api: _api, sessionId: settings.arguments as int? ?? 0));
          case '/packages':
            return MaterialPageRoute(
                builder: (_) => PackageListScreen(api: _api));
          case '/orders':
            return MaterialPageRoute(
                builder: (_) => OrdersScreen(api: _api));
          case '/package-details':
            return MaterialPageRoute(
                builder: (_) => PackageDetailScreen(
                    api: _api,
                    packageId: settings.arguments as int? ?? 0));
          case '/qbot':
            return MaterialPageRoute(builder: (_) => QBotScreen(api: _api));
          case '/cashback':
            return MaterialPageRoute(
                builder: (_) => CashbackScreen(api: _api));
          case '/category':
            return MaterialPageRoute(
                builder: (routeContext) => CategoryScreen(
                      api: _api,
                      categoryId: settings.arguments as int? ?? 1,
                      onOpenCoupon: (c) => Navigator.of(routeContext).push(
                          MaterialPageRoute(
                              builder: (_) =>
                                  CouponDetailScreen(api: _api, coupon: c))),
                    ));
          case '/vendor/issue':
            return MaterialPageRoute(
                builder: (_) => IssueCouponScreen(api: _api));
          case '/vendor/scan':
            return MaterialPageRoute(builder: (_) => ScanScreen(api: _api));
          case '/vendor/usage':
            return MaterialPageRoute(builder: (_) => UsageScreen(api: _api));
          case '/vendor/plans':
            return MaterialPageRoute(
                builder: (_) => SubscriptionScreen(api: _api));
          // Line F: admin v2 home. Line B's v1 dashboard moved to
          // '/admin/v1' (same switch cannot have two '/admin' cases);
          // AdminHomeScreen links to it (see screens/admin_v2.dart).
          case '/admin':
            return MaterialPageRoute(
                builder: (_) => AdminHomeScreen(api: _api));
          case '/admin/v1':
            return MaterialPageRoute(builder: (_) => AdminScreen(api: _api));
          case '/admin/apply':
            return MaterialPageRoute(
                builder: (_) => MerchantApplicationFormScreen(api: _api));
          case '/admin/settlements':
            return MaterialPageRoute(
                builder: (_) => SettlementsScreen(api: _api));
          case '/admin/reconciliation':
            return MaterialPageRoute(
                builder: (_) => ReconciliationScreen(api: _api));
          case '/admin/invoices':
            return MaterialPageRoute(
                builder: (_) => InvoicesScreen(api: _api));
          case '/admin/content':
            return MaterialPageRoute(
                builder: (_) => ContentScreen(api: _api));
          case '/admin/roles':
            return MaterialPageRoute(
                builder: (_) => RolesScreen(api: _api));
          case '/admin/disputes':
            return MaterialPageRoute(
                builder: (_) => ReviewDisputesScreen(api: _api));
        }
        return null;
      },
    )));
  }
}

/// Splash: original splash.gif, ~3.1s, full screen.
class SplashScreen extends StatelessWidget {
  const SplashScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return ColoredBox(
      color: CgColors.primary,
      child: Center(
        child: Image.asset(
          'assets/images/splash.gif',
          fit: BoxFit.cover,
          errorBuilder: (_, __, ___) => Image.asset(
            'assets/images/img_logo.png',
            height: 200,
            errorBuilder: (_, __, ___) =>
                const Text('COUPON GO!',
                    style: TextStyle(color: Colors.white, fontSize: 32)),
          ),
        ),
      ),
    );
  }
}

class HomeShell extends StatefulWidget {
  const HomeShell({
    super.key,
    required this.api,
    required this.authed,
    required this.vendorMode,
    required this.onAuthed,
    required this.onLogout,
    required this.onToggleMode,
  });

  final ApiClient api;
  final bool authed;
  final bool vendorMode;
  final VoidCallback onAuthed;
  final VoidCallback onLogout;
  final VoidCallback onToggleMode;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0;
  final _visited = <int>{0};

  @override
  void didUpdateWidget(HomeShell oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.authed && !widget.authed) {
      _index = 0;
      _visited.clear();
      _visited.add(0);
    }
  }

  void _openCoupon(Coupon c) {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => CouponDetailScreen(api: widget.api, coupon: c)));
  }

  void _openShop(Shop s) {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => ShopDetailScreen(
            api: widget.api, shop: s, onOpenCoupon: _openCoupon)));
  }

  void _openSearch() {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => SearchScreen(
            api: widget.api, onOpenCoupon: _openCoupon, onOpenShop: _openShop)));
  }

  Future<bool> _authenticate({bool vendor = false}) async {
    return await Navigator.of(context).push<bool>(MaterialPageRoute(
      builder: (c) => AuthScreen(api: widget.api, vendor: vendor, onAuthed: () {
        widget.onAuthed();
        Navigator.pop(c, true);
      }),
    )) ?? false;
  }

  Future<bool> _allowMember() async {
    if (widget.api.isLoggedIn) return true;
    if (!await guestLoginPrompt(context) || !mounted) return false;
    return _authenticate();
  }

  Future<void> _openCorporate() async {
    if (!widget.vendorMode) {
      if (widget.api.isLoggedIn) {
        try {
          final user = await widget.api.get('/me');
          if (!mounted) return;
          if (!['vendor', 'admin'].contains(user['role']) && !await _authenticate(vendor: true)) return;
        } catch (_) {
          if (mounted) showCgToast(context, context.tr('未能確認企業帳戶，請重試', 'Could not verify your business account. Please retry.'));
          return;
        }
      } else if (!await _authenticate(vendor: true)) {
        return;
      }
    }
    if (mounted) widget.onToggleMode();
  }

  void _openDiscovery() {
    Navigator.of(context).push(MaterialPageRoute(builder: (_) => DiscoveryScreen(
      api: widget.api, onOpenSearch: _openSearch, onOpenCoupon: _openCoupon,
    )));
  }

  @override
  Widget build(BuildContext context) {
    if (widget.vendorMode) return _vendorShell();
    final pages = <Widget>[
      HomeScreen(
        api: widget.api,
        onOpenCoupon: _openCoupon,
        onOpenShop: _openShop,
        onOpenSearch: _openSearch,
        onNotifications: () async {
          if (await _allowMember() && context.mounted) Navigator.of(context).pushNamed('/notifications');
        },
        onOpenLimited: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => LimitedTimeScreen(
                api: widget.api, onOpenCoupon: _openCoupon))),
      ),
      widget.api.isLoggedIn ? FavoritesScreen(api: widget.api, onOpenCoupon: _openCoupon) : const SizedBox.shrink(),
      const SizedBox(), // Let's Go! — handled by onTap below
      LimitedTimeScreen(api: widget.api, onOpenCoupon: _openCoupon),
      widget.api.isLoggedIn
          ? WalletScreen(
              active: _index == 4,
              api: widget.api,
              onShowQr: (item) => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) =>
                      QrShowScreen(api: widget.api, item: item))),
            )
          : const SizedBox.shrink(),
    ];
    return Scaffold(
      drawer: _drawer(),
      body: IndexedStack(index: _index, children: [for (var i = 0; i < pages.length; i++) _visited.contains(i) ? pages[i] : const SizedBox.shrink()]),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _index,
        type: BottomNavigationBarType.fixed,
        selectedItemColor: CgColors.primary,
        onTap: (i) async {
          if (i == 2) {
            _openDiscovery();
            return;
          }
          if ((i == 1 || i == 4) && !await _allowMember()) return;
          if (mounted) setState(() { _visited.add(i); _index = i; });
        },
        items: [
          BottomNavigationBarItem(icon: Image.asset('assets/images/ic_home.png',height: 24, width: 24), label: context.tr('主頁', 'Home')),
          BottomNavigationBarItem(
              icon: Image.asset('assets/images/ic_heart_empty.png',height: 24, width: 24), label: context.tr('收藏', 'Collect')),
          BottomNavigationBarItem(
            icon: Image.asset('assets/images/ic_lets_go.png', height: 36, width: 42),
            label: "Let's Go!",
          ),
          BottomNavigationBarItem(
              icon: Image.asset('assets/images/ic_limited_time.png',height: 24, width: 24), label: context.tr('限時', 'Limited Time')),
          BottomNavigationBarItem(
              icon: Image.asset('assets/images/ic_wallet.png',height: 24, width: 24),
              label: 'Q Wallet'),
        ],
      ),
    );
  }

  Widget _vendorShell() {
    return Scaffold(
      drawer: _drawer(),
      body: VendorHomeScreen(api: widget.api),
      bottomNavigationBar: BottomNavigationBar(
        type: BottomNavigationBarType.fixed,
        selectedItemColor: CgColors.amber,
        onTap: (i) {
          switch (i) {
            case 1:
              Navigator.of(context).pushNamed('/vendor/scan');
            case 2:
              Navigator.of(context).pushNamed('/vendor/usage');
            case 3:
              Navigator.of(context).pushNamed('/vendor/plans');
          }
        },
        items: const [
          BottomNavigationBarItem(icon: Icon(Icons.home), label: '首頁'),
          BottomNavigationBarItem(
              icon: Icon(Icons.qr_code_scanner), label: '掃碼'),
          BottomNavigationBarItem(
              icon: Icon(Icons.bar_chart), label: '數據'),
          BottomNavigationBarItem(icon: Icon(Icons.person), label: '我的'),
        ],
      ),
    );
  }

  Drawer _drawer() {
    return Drawer(
      child: ListView(
        padding: EdgeInsets.zero,
        children: [
          DrawerHeader(
            decoration: const BoxDecoration(color: CgColors.primary),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Image.asset('assets/images/img_logo.png', height: 64,
                    errorBuilder: (_, __, ___) => const SizedBox()),
                const SizedBox(height: 8),
                Text(context.tr('更多','More'),
                    style: const TextStyle(
                        color: Colors.white,
                        fontSize: 20,
                        fontWeight: FontWeight.bold)),
              ],
            ),
          ),
          ListTile(
            leading: const Icon(Icons.person, color: CgColors.primary),
            title: Text(widget.api.isLoggedIn ? context.tr('我的帳戶','Account') : context.tr('登錄','Login')),
            onTap: () {
              Navigator.of(context).pop();
              if (!widget.api.isLoggedIn) {
                _authenticate();
              } else {
                Navigator.of(context).pushNamed('/settings');
              }
            },
          ),
          ListTile(
            leading: const Icon(Icons.content_cut, color: CgColors.primary),
            title: Text(context.tr('砍價','Bargains')),
            onTap: () {
              Navigator.of(context).pop();
              Navigator.of(context).pushNamed('/bargain');
            },
          ),
          ListTile(
            leading: const Icon(Icons.restaurant_menu,
                color: CgColors.primary),
            title: Text(context.tr('套餐','Packages')),
            onTap: () {
              Navigator.of(context).pop();
              Navigator.of(context).pushNamed('/packages');
            },
          ),
          if (widget.authed)
            ListTile(
              leading:
                  const Icon(Icons.receipt_long, color: CgColors.primary),
              title: const Text('我的訂單'),
              onTap: () {
                Navigator.of(context).pop();
                Navigator.of(context).pushNamed('/orders');
              },
            ),          ListTile(
            leading: const Icon(Icons.card_giftcard, color: CgColors.primary),
            title: Text(context.tr('邀請好友','Invite Friends')),
            onTap: () async {
              Navigator.of(context).pop();
              final box = context.findRenderObject() as RenderBox?;
              try {
                await SharePlus.instance.share(ShareParams(
                  text: "${context.tr('邀請您一齊加入 Coupon GO！', 'Join me on Coupon GO!')} "
                      'https://play.google.com/store/apps/details?id=com.techmatch.coupongonew',
                  sharePositionOrigin: box == null ? null : box.localToGlobal(Offset.zero) & box.size,
                ));
              } catch (_) {
                if (mounted) showCgToast(context, context.tr('未能開啟分享', 'Sharing is unavailable'));
              }
            },
          ),
          ListTile(
            leading: const Icon(Icons.settings, color: CgColors.primary),
            title: Text(context.tr('設置','Settings')),
            onTap: () {
              Navigator.of(context).pop();
              Navigator.of(context).pushNamed('/settings');
            },
          ),
          ListTile(
            leading: const Icon(Icons.store, color: CgColors.primary),
            title: Text(widget.vendorMode ? context.tr('切換到用戶版','Consumer') : context.tr('企業服務','Corporate')),
            onTap: () {
              Navigator.of(context).pop();
              _openCorporate();
            },
          ),
          ListTile(
            leading:
                const Icon(Icons.storefront, color: CgColors.primary),
            title: Text(context.tr('商戶入駐申請','Merchant application')),
            onTap: () {
              Navigator.of(context).pop();
              Navigator.of(context).pushNamed('/admin/apply');
            },
          ),
          ListTile(
            leading: const Icon(Icons.admin_panel_settings,
                color: CgColors.primary),
            title: Text(context.tr('管理後台','Administration')),
            onTap: () {
              Navigator.of(context).pop();
              Navigator.of(context).pushNamed('/admin');
            },
          ),
          ListTile(leading: const Icon(Icons.chat_outlined), title: const Text('Q仔'),
            onTap: () { Navigator.of(context).pop(); Navigator.of(context).pushNamed('/qbot'); }),
          ListTile(
            leading: const Icon(Icons.help_outline, color: CgColors.primary),
            title: Text(context.tr('新手指南','Guide')),
            onTap: () {
              Navigator.of(context).pop();
              Navigator.of(context).pushNamed('/guide');
            },
          ),
        ],
      ),
    );
  }
}

/// Favorites tab: favorite coupons + shops.
class FavoritesScreen extends StatefulWidget {
  const FavoritesScreen({super.key, required this.api, required this.onOpenCoupon});

  final ApiClient api;
  final void Function(Coupon) onOpenCoupon;

  @override
  State<FavoritesScreen> createState() => _FavoritesScreenState();
}

class _FavoritesScreenState extends State<FavoritesScreen> {
  List<Coupon> _coupons = [];
  List<Shop> _shops = [];

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final c = await widget.api.get('/favorite-coupon-list');
      final s = await widget.api.get('/get-collection-shops');
      if (!mounted) return;
      setState(() {
        _coupons =
            (c['coupons'] as List).map((e) => Coupon.fromJson(e)).toList();
        _shops = (s['shops'] as List).map((e) => Shop.fromJson(e)).toList();
      });
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('收藏')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(12),
          children: [
            const Text('收藏優惠券', style: TextStyle(fontWeight: FontWeight.bold)),
            if (_coupons.isEmpty) const Text('暫時沒有收藏的優惠券。'),
            ..._coupons.map((c) => Card(
                  margin: const EdgeInsets.only(top: 8),
                  child: ListTile(
                    leading: const Icon(Icons.local_offer,
                        color: CgColors.primary),
                    title: Text(c.name),
                    subtitle: Text(c.shopName),
                    onTap: () => widget.onOpenCoupon(c),
                  ),
                )),
            const SizedBox(height: 16),
            const Text('收藏商店', style: TextStyle(fontWeight: FontWeight.bold)),
            if (_shops.isEmpty) const Text('暫時沒有收藏的商店。'),
            ..._shops.map((s) => Card(
                  margin: const EdgeInsets.only(top: 8),
                  child: ListTile(
                    leading:
                        const Icon(Icons.store, color: CgColors.primary),
                    title: Text(s.name),
                    subtitle: Text(s.address),
                  ),
                )),
          ],
        ),
      ),
    );
  }
}
