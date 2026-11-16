import 'package:flutter/material.dart';

import 'api_client.dart';
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
  runApp(const CouponGoApp());
}

class CouponGoApp extends StatefulWidget {
  const CouponGoApp({super.key});

  @override
  State<CouponGoApp> createState() => _CouponGoAppState();
}

class _CouponGoAppState extends State<CouponGoApp> {
  final _api = ApiClient();
  bool _ready = false;
  bool _authed = false;
  bool _vendorMode = false;

  @override
  void initState() {
    super.initState();
    _api.loadToken().then((_) {
      setState(() {
        _ready = true;
        _authed = _api.isLoggedIn;
      });
    });
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Coupon GO!',
      theme: CgTheme.light(),
      home: !_ready
          ? const SplashScreen()
          : HomeShell(
              api: _api,
              authed: _authed,
              vendorMode: _vendorMode,
              onAuthed: () => setState(() => _authed = true),
              onLogout: () => setState(() => _authed = false),
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
                    api: _api, onLogout: () => setState(() => _authed = false)));
          case '/guide':
            return MaterialPageRoute(builder: (_) => const GuideScreen());
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
    );
  }
}

/// Splash: original splash.gif, ~3.1s, full screen.
class SplashScreen extends StatefulWidget {
  const SplashScreen({super.key});

  @override
  State<SplashScreen> createState() => _SplashScreenState();
}

class _SplashScreenState extends State<SplashScreen> {
  @override
  void initState() {
    super.initState();
    Future.delayed(const Duration(milliseconds: 3100), () {
      if (mounted) setState(() {});
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: CgColors.primary,
      body: Center(
        child: Image.asset(
          'assets/images/splash.gif',
          fit: BoxFit.cover,
          errorBuilder: (_, _, _) => Image.asset(
            'assets/images/img_logo.png',
            height: 200,
            errorBuilder: (_, _, _) =>
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

  @override
  Widget build(BuildContext context) {
    if (widget.vendorMode) return _vendorShell();
    final pages = <Widget>[
      HomeScreen(
        api: widget.api,
        onOpenCoupon: _openCoupon,
        onOpenShop: _openShop,
        onOpenSearch: _openSearch,
        onOpenLimited: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => LimitedTimeScreen(
                api: widget.api, onOpenCoupon: _openCoupon))),
      ),
      FavoritesScreen(api: widget.api, onOpenCoupon: _openCoupon),
      const SizedBox(), // Let's Go! — handled by onTap below
      LimitedTimeScreen(api: widget.api, onOpenCoupon: _openCoupon),
      widget.authed
          ? WalletScreen(
              api: widget.api,
              onShowQr: (item) => Navigator.of(context).push(MaterialPageRoute(
                  builder: (_) =>
                      QrShowScreen(api: widget.api, item: item))),
            )
          : AuthScreen(api: widget.api, onAuthed: widget.onAuthed),
    ];
    return Scaffold(
      drawer: _drawer(),
      floatingActionButton: FloatingActionButton(
        backgroundColor: CgColors.primary,
        onPressed: () => Navigator.of(context).pushNamed('/qbot'),
        child: Image.asset('assets/images/img_logo_cropped.png',
            width: 36,
            errorBuilder: (_, _, _) =>
                const Text('Q仔', style: TextStyle(color: Colors.white))),
      ),
      body: IndexedStack(index: _index, children: pages),
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _index,
        type: BottomNavigationBarType.fixed,
        selectedItemColor: CgColors.primary,
        onTap: (i) {
          if (i == 2) {
            _openSearch(); // Let's Go!
            return;
          }
          setState(() => _index = i);
        },
        items: [
          const BottomNavigationBarItem(icon: Icon(Icons.home), label: '主頁'),
          const BottomNavigationBarItem(
              icon: Icon(Icons.favorite_border), label: '收藏'),
          BottomNavigationBarItem(
            icon: Container(
              padding: const EdgeInsets.all(8),
              decoration: const BoxDecoration(
                color: CgColors.primary,
                shape: BoxShape.circle,
              ),
              child: const Text("Let's Go!",
                  style: TextStyle(color: Colors.white, fontSize: 10)),
            ),
            label: "Let's Go!",
          ),
          const BottomNavigationBarItem(
              icon: Icon(Icons.timer_outlined), label: '限時'),
          const BottomNavigationBarItem(
              icon: Icon(Icons.account_balance_wallet_outlined),
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
                    errorBuilder: (_, _, _) => const SizedBox()),
                const SizedBox(height: 8),
                const Text('更多',
                    style: TextStyle(
                        color: Colors.white,
                        fontSize: 20,
                        fontWeight: FontWeight.bold)),
              ],
            ),
          ),
          ListTile(
            leading: const Icon(Icons.person, color: CgColors.primary),
            title: Text(widget.authed ? '我的帳戶' : '登錄'),
            onTap: () {
              Navigator.of(context).pop();
              if (!widget.authed) {
                Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) =>
                        AuthScreen(api: widget.api, onAuthed: widget.onAuthed)));
              } else {
                Navigator.of(context).pushNamed('/settings');
              }
            },
          ),
          ListTile(
            leading: const Icon(Icons.content_cut, color: CgColors.primary),
            title: const Text('砍價'),
            onTap: () {
              Navigator.of(context).pop();
              Navigator.of(context).pushNamed('/bargain');
            },
          ),
          ListTile(
            leading: const Icon(Icons.restaurant_menu,
                color: CgColors.primary),
            title: const Text('套餐'),
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
            title: const Text('邀請好友'),
            onTap: () {
              Navigator.of(context).pop();
              showCgToast(context, '邀請您一齊加入Coupon-GO!，探索更多衣食住行優惠券！');
            },
          ),
          ListTile(
            leading: const Icon(Icons.settings, color: CgColors.primary),
            title: const Text('設置'),
            onTap: () {
              Navigator.of(context).pop();
              Navigator.of(context).pushNamed('/settings');
            },
          ),
          ListTile(
            leading: const Icon(Icons.store, color: CgColors.primary),
            title: Text(widget.vendorMode ? '切換到用戶版' : '企業服務'),
            onTap: () {
              Navigator.of(context).pop();
              widget.onToggleMode();
            },
          ),
          ListTile(
            leading:
                const Icon(Icons.storefront, color: CgColors.primary),
            title: const Text('商戶入駐申請'),
            onTap: () {
              Navigator.of(context).pop();
              Navigator.of(context).pushNamed('/admin/apply');
            },
          ),
          ListTile(
            leading: const Icon(Icons.admin_panel_settings,
                color: CgColors.primary),
            title: const Text('管理後台'),
            onTap: () {
              Navigator.of(context).pop();
              Navigator.of(context).pushNamed('/admin');
            },
          ),
          ListTile(
            leading: const Icon(Icons.help_outline, color: CgColors.primary),
            title: const Text('新手指南'),
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
