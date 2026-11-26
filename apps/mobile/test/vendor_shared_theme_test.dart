import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/language.dart';
import 'package:coupon_go/main.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:coupon_go/theme.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _ThemeVendorApi extends ApiClient {
  @override
  bool get isLoggedIn => true;

  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, String>? query,
  }) async {
    if (path == '/me') return {'role': 'vendor'};
    if (path == '/merchant/shops') return {'shops': []};
    if (path == '/get-vendor-coupon-list') return {'coupons': []};
    return {};
  }
}

void main() {
  for (final (name, theme, red) in [
    ('light', CgTheme.light(), CgColors.primary),
    ('dark', CgTheme.dark(), CgColors.primaryOnDark),
  ]) {
    test('$name theme shares button sizes, typography and AppBar treatment',
        () {
      final normal = <WidgetState>{};
      final button = theme.outlinedButtonTheme.style!;
      expect(theme.appBarTheme.backgroundColor, CgColors.primary);
      expect(theme.appBarTheme.foregroundColor, Colors.white);
      expect(theme.appBarTheme.centerTitle, isTrue);
      final nav = theme.bottomNavigationBarTheme;
      expect(nav.selectedItemColor, red);
      expect(nav.backgroundColor, theme.colorScheme.surface);
      double contrast(Color left, Color right) {
        final luminance = [left.computeLuminance(), right.computeLuminance()]
          ..sort();
        return (luminance.last + .05) / (luminance.first + .05);
      }

      expect(contrast(nav.selectedItemColor!, nav.backgroundColor!),
          greaterThanOrEqualTo(4.5));
      expect(contrast(nav.unselectedItemColor!, nav.backgroundColor!),
          greaterThanOrEqualTo(4.5));
      expect(
          contrast(Colors.white, CgColors.primary), greaterThanOrEqualTo(4.5));
      expect(button.minimumSize!.resolve(normal), const Size(64, 56));
      expect(button.foregroundColor!.resolve(normal), red);
      expect(button.side!.resolve(normal)!.color, red);
      expect(
          (button.shape!.resolve(normal) as RoundedRectangleBorder)
              .borderRadius,
          BorderRadius.circular(12));
      expect(
          theme.elevatedButtonTheme.style!.minimumSize!.resolve(normal)!.height,
          56);
      expect(theme.textTheme.titleMedium!.fontSize, 16);
      expect(theme.textTheme.titleLarge!.fontSize, 22);
      expect(theme.textTheme.headlineSmall!.fontSize, 28);
      expect(CgTheme.pageInset, 16);
    });
  }

  test('merchant gold is used for surfaces and readable ink for actions', () {
    final theme = CgTheme.light(merchant: true);
    final filled = theme.filledButtonTheme.style!;
    expect(theme.appBarTheme.backgroundColor, CgColors.vendorGold);
    expect(filled.backgroundColor!.resolve({}), CgColors.vendorGold);
    expect(theme.appBarTheme.foregroundColor, CgColors.vendorInk);
    expect(filled.foregroundColor!.resolve({}), CgColors.vendorInk);
    expect(theme.bottomNavigationBarTheme.selectedItemColor, CgColors.vendorLink);
    for (final (bg, fg) in [
      (CgColors.vendorGold, CgColors.vendorInk),
      (theme.colorScheme.surface, CgColors.vendorLink),
    ]) {
      expect((bg.computeLuminance() + .05) / (fg.computeLuminance() + .05),
          greaterThanOrEqualTo(4.5));
    }
  });

  testWidgets('shared AppBar and outlined buttons fit inside a narrow wrap',
      (tester) async {
    tester.view.devicePixelRatio = 1;
    tester.view.physicalSize = const Size(390, 844);
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    await tester.pumpWidget(MaterialApp(
      theme: CgTheme.light(),
      home: Scaffold(
        appBar: AppBar(title: const Text('企業版')),
        body: Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            OutlinedButton(onPressed: () {}, child: const Text('Packages')),
            OutlinedButton(
                onPressed: () {}, child: const Text('Store details')),
          ],
        ),
      ),
    ));
    expect(
        Theme.of(tester.element(find.byType(AppBar)))
            .appBarTheme
            .backgroundColor,
        CgColors.primary);
    final sizes =
        tester.widgetList<OutlinedButton>(find.byType(OutlinedButton));
    expect(sizes, hasLength(2));
    expect(tester.getSize(find.byType(OutlinedButton).first).height,
        greaterThanOrEqualTo(56));
    expect(tester.takeException(), isNull);
  });

  for (final scale in [1.0, 2.0]) {
    testWidgets('business tools fit 390px at text scale $scale',
        (tester) async {
      tester.view.devicePixelRatio = 1;
      tester.view.physicalSize = const Size(390, 844);
      addTearDown(tester.view.resetPhysicalSize);
      addTearDown(tester.view.resetDevicePixelRatio);
      final api = _ThemeVendorApi();
      final language = AppLanguage()..english = true;
      addTearDown(api.dispose);
      addTearDown(language.dispose);
      await tester.pumpWidget(LanguageScope(
        language: language,
        child: MaterialApp(
          theme: CgTheme.dark(),
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(context)
                .copyWith(textScaler: TextScaler.linear(scale)),
            child: child!,
          ),
          home: VendorHomeScreen(api: api, showAdmin: true),
        ),
      ));
      await tester.pumpAndSettle();
      final grid = find.byKey(const ValueKey('vendor-tools'));
      expect(grid, findsOneWidget);
      final sizes = tester.widget<Wrap>(grid).children.cast<SizedBox>();
      expect(sizes.length, greaterThan(2));
      expect(sizes.first.width, scale == 1 ? 175 : 358);
      expect(find.text('Issue coupon'), findsOneWidget);
      expect(find.text('Business tools'), findsOneWidget);
      expect(tester.takeException(), isNull);
      await tester.pumpWidget(const SizedBox());
    });
  }

  for (final english in [false, true]) {
    for (final theme in [CgTheme.light(), CgTheme.dark()]) {
      testWidgets(
          'merchant nav retains four branded destinations: '
          '$english/${theme.brightness}', (tester) async {
        SharedPreferences.setMockInitialValues({});
        tester.view.devicePixelRatio = 1;
        tester.view.physicalSize = const Size(390, 844);
        addTearDown(tester.view.resetPhysicalSize);
        addTearDown(tester.view.resetDevicePixelRatio);
        final api = _ThemeVendorApi();
        final language = AppLanguage()..english = english;
        addTearDown(api.dispose);
        addTearDown(language.dispose);
        await tester.pumpWidget(LanguageScope(
          language: language,
          child: MaterialApp(
            theme: theme,
            home: HomeShell(
              api: api,
              authed: true,
              vendorMode: true,
              onAuthed: () {},
              onLogout: () {},
              onToggleMode: () {},
            ),
          ),
        ));
        await tester.pumpAndSettle();
        final bar = tester.widget<BottomNavigationBar>(
          find.byType(BottomNavigationBar),
        );
        expect(bar.currentIndex, 0);
        expect(
            bar.items.map((item) => item.label).toList(),
            english
                ? ['Home', 'Scan', 'Analytics', 'Account']
                : ['主頁', '掃碼', '數據', '我的']);
        final icons = bar.items.map((item) => item.icon as Image).toList();
        expect(
            icons.map((icon) => (icon.image as AssetImage).assetName).toList(),
            [
              'assets/images/ic_home.png',
              'assets/images/ic_scanqr_code.png',
              'assets/images/ic_usage_data.png',
              'assets/images/ic_user.png',
            ]);
        expect(
            icons.first.color,
            theme.brightness == Brightness.dark
                ? CgColors.primaryOnDark
                : CgColors.primary);
        // The existing multicolor scan mark needs its original glyph contrast.
        expect(icons[1].color, isNull);
        expect(
            icons.skip(2).every(
                (icon) => icon.color == theme.colorScheme.onSurfaceVariant),
            isTrue);
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox());
      });
    }
  }
}
