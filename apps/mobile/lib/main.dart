import 'screens/merchant_onboarding.dart';
import 'screens/trial_banner.dart';
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter/foundation.dart' show kIsWeb, LicenseRegistry, LicenseEntryWithLineBreaks;
import 'package:flutter/services.dart' show Clipboard, ClipboardData, rootBundle;
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:url_launcher/url_launcher.dart';

import 'api_client.dart';
import 'feature_flags.dart';
import 'language.dart';
import 'package:share_plus/share_plus.dart';
import 'models.dart';
import 'screens/account.dart';
import 'screens/admin_v2.dart';
import 'screens/bargain.dart';
import 'screens/cashback.dart';
import 'screens/customer.dart';
import 'screens/guest_auth.dart' show PreviewWelcomeScreen;
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
  String? _trialError;
  bool _changingTrialRole = false;
  final _navigatorKey = GlobalKey<NavigatorState>();
  final _messengerKey = GlobalKey<ScaffoldMessengerState>();
  final _trialRoleKey = GlobalKey<TrialBannerState>();

  @override
  void initState() {
    super.initState();
    _api.addListener(_sessionChanged);
    unawaited(_initialise());
  }

  Future<void> _initialise() async {
    if (mounted) setState(() { _trialError = null; _ready = false; });
    try {
      if (const bool.fromEnvironment('PUBLIC_TRIAL')) {
        final session = await _api.post('/trial/session');
        if (session['reset'] == true) await _api.clearToken();
      }
      await Future.wait([if (!publicPreview) _api.loadToken(), _language.load()]);
      unawaited(_api.appConfig.load());
      if (mounted) setState(() { _ready = true; _authed = _api.isLoggedIn; });
    } catch (_) {
      if (mounted) setState(() { _trialError = '體驗暫時無法開始，請稍後重試。'; });
    }
  }

  Future<void> _trialRoleChanging() async {
    if (!mounted) return;
    setState(() => _changingTrialRole = true);
    // Wait until the old navigation tree is disposed before using the new identity.
    await WidgetsBinding.instance.endOfFrame;
  }

  void _trialRoleChangeFailed() {
    if (mounted) setState(() => _changingTrialRole = false);
  }

  void _trialRoleChanged(String role) {
    if (!mounted) return;
    setState(() {
      _changingTrialRole = false;
      _authed = _api.isLoggedIn;
      _vendorMode = role == 'vendor';
    });
    if (role == 'admin') {
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted && !_changingTrialRole) _navigatorKey.currentState?.pushNamed('/admin');
      });
    }
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
    if (!_ready) {
      if (_trialError != null) {
        return MaterialApp(home: Scaffold(body: Center(child: Column(
          mainAxisSize: MainAxisSize.min, children: [Text(_trialError!),
          TextButton(onPressed: _initialise, child: const Text('重試'))]))));
      }
      return const Directionality(textDirection: TextDirection.ltr, child: SplashScreen());
    }
    return LanguageScope(language: _language, child: Builder(builder: (c) => MaterialApp(
      locale: LanguageScope.of(c).english ? const Locale('en') : const Locale.fromSubtags(languageCode: 'zh', scriptCode: 'Hant', countryCode: 'HK'),
      supportedLocales: const [Locale('en'), Locale.fromSubtags(languageCode: 'zh', scriptCode: 'Hant', countryCode: 'HK')],
      localizationsDelegates: GlobalMaterialLocalizations.delegates,
      title: 'Coupon GO!',
      navigatorKey: _navigatorKey,
      scaffoldMessengerKey: _messengerKey,
      builder: (context, child) => const bool.fromEnvironment('PUBLIC_TRIAL')
          ? Column(verticalDirection: VerticalDirection.up, children: [
              Expanded(child: _changingTrialRole ? const SizedBox.shrink() : (child ?? const SizedBox.shrink())),
              TrialBanner(key: _trialRoleKey, api: _api, messengerKey: _messengerKey, onRoleChanging: _trialRoleChanging,
                  onRoleChangeFailed: _trialRoleChangeFailed, onRoleChanged: _trialRoleChanged)])
          : (child ?? const SizedBox.shrink()),
      theme: CgTheme.light(),
      home: HomeShell(
              api: _api,
              authed: _authed,
              vendorMode: _vendorMode,
              onTrialRoleSwitch: const bool.fromEnvironment('PUBLIC_TRIAL')
                  ? (context) => _trialRoleKey.currentState?.open(context) : null,
              onAuthed: () => setState(() => _authed = true),
              onLogout: () => setState(() { _authed = false; _vendorMode = false; }),
              onToggleMode: () =>
                  setState(() => _vendorMode = !_vendorMode),
            ),
      onGenerateRoute: (settings) {
        final route = settings.name ?? '';
        if (publicPreview && (route == '/admin' || route.startsWith('/admin/') ||
            route == '/vendor' || route.startsWith('/vendor/'))) {
          return MaterialPageRoute(settings: settings,
              builder: (_) => const PreviewWelcomeScreen());
        }
        switch (settings.name) {
          case '/notifications':
            return MaterialPageRoute(
                builder: (_) => NotificationsScreen(api: _api, guest: !_authed));
          case '/settings':
            return MaterialPageRoute(
                builder: (_) => SettingsScreen(
                    api: _api, onLogout: () => setState(() { _authed = false; _vendorMode = false; })));
          case '/guide':
            return MaterialPageRoute(builder: (_) => GuideScreen(api: _api));
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
                builder: (_) => OrdersScreen(api: _api, guest: !_authed));
          case '/package-details':
            return MaterialPageRoute(
                builder: (_) => PackageDetailScreen(
                    api: _api,
                    packageId: settings.arguments as int? ?? 0));
          case '/qbot':
            return MaterialPageRoute(builder: (_) => QBotScreen(api: _api));
          case '/cashback':
            if (!cashbackEnabled) return null;
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
          case '/vendor/settlements':
            return MaterialPageRoute(builder: (_) => SettlementsScreen(api: _api, merchant: true));
          case '/vendor/records':
            return MaterialPageRoute(builder: (_) => RedemptionRecordsScreen(api: _api));
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
                builder: (_) => MerchantOnboardingScreen(api: _api));
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
    this.onTrialRoleSwitch,
  });

  final ApiClient api;
  final bool authed;
  final bool vendorMode;
  final VoidCallback onAuthed;
  final VoidCallback onLogout;
  final VoidCallback onToggleMode;
  final ValueChanged<BuildContext>? onTrialRoleSwitch;

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int _index = 0, _vendorIndex = 0, _roleRequest = 0, _vendorTabRequest = 0;
  String? _role;
  bool _canRedeem = false, _canAnalytics = false;
  final _visited = <int>{0};
  LocalHistoryEntry? _tabHistory;
  final _vendorScaffoldKey = GlobalKey<ScaffoldState>();

  void _clearTabHistory() {
    final entry = _tabHistory;
    _tabHistory = null;
    entry?.remove();
  }

  void _syncTabHistory() {
    if (!mounted) return;
    final activeTab = widget.vendorMode ? _vendorIndex : _index;
    if (activeTab == 0) {
      _clearTabHistory();
      return;
    }
    if (_tabHistory != null) return;
    final route = ModalRoute.of(context);
    if (route == null) return;
    late final LocalHistoryEntry entry;
    entry = LocalHistoryEntry(
      impliesAppBarDismissal: false,
      onRemove: () {
        if (!identical(_tabHistory, entry)) return;
        _tabHistory = null;
        _vendorTabRequest++;
        if (!mounted) return;
        setState(() {
          if (widget.vendorMode) {
            _vendorIndex = 0;
          } else {
            _index = 0;
          }
        });
      },
    );
    _tabHistory = entry;
    route.addLocalHistoryEntry(entry);
  }

  @override
  void initState() {
    super.initState();
    widget.api.addListener(_readRole);
    _readRole();
  }

  Future<void> _readRole() async {
    final request = ++_roleRequest;
    _vendorTabRequest++;
    if (widget.vendorMode && _vendorIndex != 0) _clearTabHistory();
    setState(() { _role = null; _canRedeem = false; _canAnalytics = false; _vendorIndex = 0; });
    if (!widget.api.isLoggedIn) return;
    try {
      final role = (await widget.api.get('/me'))['role'];
      if (mounted && request == _roleRequest) {
        setState(() => _role = role is String ? role : null);
      }
    } catch (_) {
      // Leave privileged navigation hidden when role verification fails.
    }
  }

  @override
  void dispose() {
    _clearTabHistory();
    widget.api.removeListener(_readRole);
    super.dispose();
  }

  @override
  void didUpdateWidget(HomeShell oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.api != widget.api) {
      oldWidget.api.removeListener(_readRole);
      widget.api.addListener(_readRole);
      _readRole();
    }
    if (!widget.vendorMode) {
      _vendorIndex = 0;
      if (oldWidget.vendorMode) _vendorTabRequest++;
    }
    if (oldWidget.authed && !widget.authed) {
      _index = 0;
      _visited.clear();
      _visited.add(0);
    }
    if (oldWidget.vendorMode != widget.vendorMode ||
        (oldWidget.authed && !widget.authed)) {
      WidgetsBinding.instance.addPostFrameCallback((_) => _syncTabHistory());
    }
  }

  Future<void> _openCoupon(Coupon c) async {
    await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => CouponDetailScreen(api: widget.api, coupon: c)));
  }

  Future<void> _openShop(Shop s) async {
    await Navigator.of(context).push(MaterialPageRoute(
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

  Future<void> _openCorporate() async {
    if (!widget.vendorMode) {
      if (widget.api.isLoggedIn) {
        try {
          final user = await widget.api.get('/me');
          if (!mounted) return;
          if (!['vendor', 'admin', 'merchant-admin'].contains(user['role']) && !await _authenticate(vendor: true)) return;
        } catch (_) {
          if (mounted) showCgToast(context, context.tr('未能確認企業帳戶，請重試', 'Could not verify your business account. Please retry.'));
          return;
        }
      } else if (!await _authenticate(vendor: true)) {
        return;
      }
      final session = widget.api.sessionRevision;
      try {
        final values = await Future.wait([widget.api.get('/merchant/shops'), widget.api.get('/me')]);
        final shops = values[0]['shops'];
        if (shops is! List) throw const FormatException();
        if (!mounted || session != widget.api.sessionRevision) return;
        setState(() {
          _canRedeem = shops.any((shop) => shop is Map && shop['can_redeem'] == true);
          _canAnalytics = values[1]['role'] == 'admin' || shops.any((shop) => shop is Map && shop['is_owner'] == true);
        });
      } catch (_) {
        if (mounted && session == widget.api.sessionRevision) {
          showCgToast(context, '未能確認店舖權限，請重試。');
        }
        return;
      }
    }
    if (mounted) widget.onToggleMode();
  }

  Future<void> _selectVendorTab(int index) async {
    final request = ++_vendorTabRequest;
    if (index == 1 || index == 2) {
      final session = widget.api.sessionRevision;
      try {
        final values = await Future.wait([widget.api.get('/merchant/shops'), widget.api.get('/me')]);
        final shops = values[0]['shops'];
        if (shops is! List) throw const FormatException();
        if (!mounted || session != widget.api.sessionRevision || request != _vendorTabRequest) return;
        final canRedeem = shops.any((shop) => shop is Map && shop['can_redeem'] == true);
        final canAnalytics = values[1]['role'] == 'admin' || shops.any((shop) => shop is Map && shop['is_owner'] == true);
        setState(() { _canRedeem = canRedeem; _canAnalytics = canAnalytics; });
      } catch (_) {
        if (mounted && session == widget.api.sessionRevision && request == _vendorTabRequest) showCgToast(context, '未能確認店舖權限，請重試。');
        return;
      }
    }
    if (!mounted || request != _vendorTabRequest) return;
    if (index == 1 && !_canRedeem) {
      showCgToast(context, '你沒有店舖核銷權限。');
      return;
    }
    if (index == 2 && !_canAnalytics) {
      showCgToast(context, '目前帳戶無法查看此數據。');
      return;
    }
    setState(() => _vendorIndex = index);
    _syncTabHistory();
  }

  Future<void> _inviteFriends() async {
    // The website shares its own public address. Native builds can supply
    // PUBLIC_WEB_URL when their companion website is available.
    const configured = String.fromEnvironment('PUBLIC_WEB_URL');
    final link = kIsWeb
        ? Uri.base.replace(query: null, fragment: null).toString().split('#').first.split('?').first
        : configured;
    await showDialog<void>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: Text(context.tr('邀請好友', 'Invite friends')),
        content: SelectableText(link.isEmpty
            ? context.tr('分享連結尚未設定。', 'The sharing link is not available yet.')
            : link),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialogContext),
            child: Text(context.tr('關閉', 'Close')),
          ),
          if (link.isNotEmpty)
            TextButton(
              onPressed: () async {
                try {
                  await Clipboard.setData(ClipboardData(text: link));
                  if (dialogContext.mounted) {
                    showCgToast(dialogContext, dialogContext.tr('連結已複製', 'Link copied'));
                  }
                } catch (_) {
                  if (dialogContext.mounted) {
                    showCgToast(dialogContext, dialogContext.tr('未能複製，請手動選取連結', 'Select the link to copy it'));
                  }
                }
              },
              child: Text(context.tr('複製連結', 'Copy link')),
            ),
          if (link.isNotEmpty)
            TextButton(
              onPressed: () async {
                final box = dialogContext.findRenderObject() as RenderBox?;
                try {
                  await SharePlus.instance.share(ShareParams(
                    text: link,
                    sharePositionOrigin: box == null ? null : box.localToGlobal(Offset.zero) & box.size,
                  ));
                } catch (_) {
                  if (dialogContext.mounted) {
                    showCgToast(dialogContext, dialogContext.tr('請複製連結分享', 'Copy the link to share'));
                  }
                }
              },
              child: Text(context.tr('分享', 'Share')),
            ),
        ],
      ),
    );
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
        onNotifications: () => Navigator.of(context).pushNamed('/notifications'),
        onOpenLimited: () => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => LimitedTimeScreen(
                api: widget.api, onOpenCoupon: _openCoupon))),
      ),
      FavoritesScreen(
        api: widget.api, onOpenCoupon: _openCoupon, onOpenShop: _openShop,
        active: _index == 1, guest: !widget.authed),
      const SizedBox(), // Let's Go! — handled by onTap below
      LimitedTimeScreen(api: widget.api, onOpenCoupon: _openCoupon),
      WalletScreen(
        active: _index == 4,
        guest: !widget.authed,
        api: widget.api,
        onShowQr: (item) => Navigator.of(context).push(MaterialPageRoute(
            builder: (_) => QrShowScreen(api: widget.api, item: item))),
      ),
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
          if (mounted) {
            setState(() { _visited.add(i); _index = i; });
            _syncTabHistory();
          }
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
    String tr(String zh, String en) => context.tr(zh, en);
    return Scaffold(
      key: _vendorScaffoldKey,
      drawer: _drawer(),
      body: switch (_vendorIndex) {
        1 => ScanScreen(api: widget.api),
        2 => UsageScreen(api: widget.api),
        3 => VendorAccountScreen(api: widget.api),
        _ => VendorHomeScreen(api: widget.api, showAdmin: _role == 'admin',
            onOpenMenu: () => _vendorScaffoldKey.currentState?.openDrawer()),
      },
      bottomNavigationBar: BottomNavigationBar(
        currentIndex: _vendorIndex,
        type: BottomNavigationBarType.fixed,
        selectedItemColor: CgColors.amber,
        onTap: _selectVendorTab,
        items: [
          BottomNavigationBarItem(icon: const Icon(Icons.home), label: tr('首頁', 'Home')),
          BottomNavigationBarItem(
              icon: const Icon(Icons.qr_code_scanner), label: tr('掃碼', 'Scan')),
          BottomNavigationBarItem(
              icon: const Icon(Icons.bar_chart), label: tr('數據', 'Analytics')),
          BottomNavigationBarItem(
              icon: const Icon(Icons.person), label: tr('我的', 'Account')),
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
          if (widget.onTrialRoleSwitch != null)
            ListTile(
              leading: const Icon(Icons.switch_account, color: CgColors.primary),
              title: Text(context.tr('切換身份', 'Switch identity')),
              onTap: () {
                Navigator.of(context).pop();
                widget.onTrialRoleSwitch?.call(context);
              },
            ),
          ListTile(
            leading: const Icon(Icons.person, color: CgColors.primary),
            title: Text(publicPreview ? context.tr('訪客瀏覽', 'Guest browsing') : widget.api.isLoggedIn ? context.tr('我的帳戶','Account') : context.tr('登錄','Login')),
            onTap: () {
              Navigator.of(context).pop();
              if (!widget.api.isLoggedIn) {
                _authenticate();
              } else {
                Navigator.of(context).pushNamed('/settings');
              }
            },
          ),
          if (!widget.vendorMode) ...[
            ListTile(
              leading: const Icon(Icons.content_cut, color: CgColors.primary),
              title: Text(context.tr('砍價', 'Bargains')),
              onTap: () {
                Navigator.of(context).pop();
                Navigator.of(context).pushNamed('/bargain');
              },
            ),
            ListTile(
              leading: const Icon(Icons.restaurant_menu, color: CgColors.primary),
              title: Text(context.tr('套餐', 'Packages')),
              onTap: () {
                Navigator.of(context).pop();
                Navigator.of(context).pushNamed('/packages');
              },
            ),
          ],
          ListTile(
            leading: const Icon(Icons.receipt_long, color: CgColors.primary),
            title: Text(context.tr('我的訂單', 'My orders')),
            onTap: () {
              Navigator.of(context).pop();
              Navigator.of(context).pushNamed('/orders');
            },
          ),
          ListTile(
            leading: const Icon(Icons.card_giftcard, color: CgColors.primary),
            title: Text(context.tr('邀請好友', 'Invite friends')),
            onTap: () {
              Navigator.of(context).pop();
              _inviteFriends();
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
          if (_role == 'admin' || _role == 'support')
            ListTile(
              leading: const Icon(Icons.admin_panel_settings,
                  color: CgColors.primary),
              title: Text(context.tr('管理後台','Administration')),
              onTap: () {
                final role = _role;
                if (role == null) return;
                Navigator.of(context).pop();
                Navigator.of(context).push(MaterialPageRoute(
                    builder: (_) => AdminHomeScreen(api: widget.api, role: role)));
              },
            ),
          ListTile(leading: const Icon(Icons.chat_outlined), title: const Text('Q仔'),
            onTap: () { Navigator.of(context).pop(); Navigator.of(context).pushNamed('/qbot'); }),
          if (kIsWeb && publicPreview)
            ListTile(
              leading: const Icon(Icons.android, color: CgColors.primary),
              title: Text(context.tr('下載 Android 版', 'Download Android app')),
              onTap: () async {
                const publicUrl = String.fromEnvironment('PUBLIC_WEB_URL');
                final base = publicUrl.isNotEmpty
                    ? Uri.parse(publicUrl)
                    : Uri.base.resolve('/coupongo/');
                final uri = base.resolve('downloads/coupon-go-demo.apk');
                Navigator.of(context).pop();
                try {
                  if (await launchUrl(uri, webOnlyWindowName: '_self')) return;
                } catch (_) {
                  // The feedback below covers blocked or unsupported launches.
                }
                if (mounted) {
                  ScaffoldMessenger.of(context).showSnackBar(SnackBar(
                    content: Text(context.tr('暫時無法下載，請稍後再試。', 'Download unavailable. Please try again.')),
                  ));
                }
              },
            ),
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
  const FavoritesScreen({
    super.key, required this.api, required this.onOpenCoupon,
    required this.onOpenShop, this.active = true, this.guest = false,
  });

  final ApiClient api;
  final Future<void> Function(Coupon) onOpenCoupon;
  final Future<void> Function(Shop) onOpenShop;
  final bool active;
  final bool guest;

  @override
  State<FavoritesScreen> createState() => _FavoritesScreenState();
}

class _FavoritesScreenState extends State<FavoritesScreen> {
  List<Coupon> _coupons = [];
  List<Shop> _shops = [];
  bool _loading = true;
  bool _hasError = false;
  int _request = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void didUpdateWidget(FavoritesScreen oldWidget) {
    super.didUpdateWidget(oldWidget);
    if ((widget.active && !oldWidget.active) || widget.guest != oldWidget.guest) {
      _load();
    }
  }

  Future<void> _load() async {
    final request = ++_request;
    if (widget.guest) {
      if (mounted) {
        setState(() {
          _coupons = [];
          _shops = [];
          _loading = false;
          _hasError = false;
        });
      }
      return;
    }
    setState(() {
      _loading = true;
      _hasError = false;
    });
    try {
      final rows = await Future.wait([
        widget.api.get('/favorite-coupon-list'),
        widget.api.get('/get-collection-shops'),
      ]);
      if (!mounted || widget.guest || request != _request) return;
      setState(() {
        _coupons = (rows[0]['coupons'] as List)
            .map((e) => Coupon.fromJson(e))
            .toList();
        _shops = (rows[1]['shops'] as List)
            .map((e) => Shop.fromJson(e))
            .toList();
        _loading = false;
      });
    } catch (_) {
      if (!mounted || request != _request) return;
      setState(() {
        _hasError = true;
        _loading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('收藏', 'Favorites'))),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          physics: const AlwaysScrollableScrollPhysics(),
          padding: const EdgeInsets.all(12),
          children: [
            if (_hasError) ListTile(
              title: Text(context.tr('未能載入收藏', 'Could not load favorites')),
              trailing: TextButton(onPressed: _load, child: Text(context.tr('重試', 'Retry'))),
            ),
            if (_loading && _coupons.isEmpty && _shops.isEmpty)
              const SkeletonBox(height: 72),
            Text(context.tr('收藏優惠券', 'Saved coupons'), style: const TextStyle(fontWeight: FontWeight.bold)),
            if (!_loading && !_hasError && _coupons.isEmpty) Text(widget.guest
                ? (publicPreview
                    ? context.tr('暫時沒有收藏的優惠券。', 'No saved coupons yet.')
                    : context.tr('登入後可查看收藏的優惠券。', 'Sign in to view saved coupons.'))
                : context.tr('暫時沒有收藏的優惠券。', 'No saved coupons yet.')),
            ..._coupons.map((c) => Card(
                  margin: const EdgeInsets.only(top: 8),
                  child: ListTile(
                    leading: const Icon(Icons.local_offer,
                        color: CgColors.primary),
                    title: Text(c.name),
                    subtitle: Text(c.shopName),
                    onTap: () async {
                      await widget.onOpenCoupon(c);
                      if (mounted) await _load();
                    },
                  ),
                )),
            const SizedBox(height: 16),
            Text(context.tr('收藏商店', 'Saved stores'), style: const TextStyle(fontWeight: FontWeight.bold)),
            if (!_loading && !_hasError && _shops.isEmpty) Text(widget.guest
                ? (publicPreview
                    ? context.tr('暫時沒有收藏的商店。', 'No saved stores yet.')
                    : context.tr('登入後可查看收藏的商店。', 'Sign in to view saved shops.'))
                : context.tr('暫時沒有收藏的商店。', 'No saved stores yet.')),
            ..._shops.map((s) => Card(
                  margin: const EdgeInsets.only(top: 8),
                  child: ListTile(
                    leading:
                        const Icon(Icons.store, color: CgColors.primary),
                    title: Text(s.name),
                    subtitle: Text(s.address),
                    trailing: const Icon(Icons.chevron_right),
                    onTap: () async {
                      await widget.onOpenShop(s);
                      if (mounted) await _load();
                    },
                  ),
                )),
          ],
        ),
      ),
    );
  }
}
