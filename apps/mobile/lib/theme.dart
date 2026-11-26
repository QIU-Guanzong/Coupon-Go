import 'package:flutter/material.dart';

/// Design tokens — colours recovered from the Coupon GO! 1.2.6 APK
/// (REVERSE.md §4.2): screenshot-measured red #CE2E39, code literals
/// #c32d36 / #ce2b37, orange #FF6C22, amber #FFB025, page #F5F5F6.
class CgColors {
  static const primary = Color(0xFFCE2E39);
  static const primaryDark = Color(0xFF9C3B41);
  static const orange = Color(0xFFFF6C22);
  static const amber = Color(0xFFFFB025);
  static const vendorGold = Color(0xFFFFB025);
  static const vendorInk = Color(0xFF30200A);
  static const vendorLink = Color(0xFF805000);
  static const pageBg = Color(0xFFF5F5F6);
  static const cardBorder = Color(0xFFE0E0E0);
  static const textPrimary = Color(0xFF1A1A1A);
  static const textSecondary = Color(0xFF737373);
  static const success = Color(0xFF1E9E5A);
  static const primaryOnDark = Color(0xFFF3656E);
  static const noticeBg = Color(0xFFFFF5E8);
  static const noticeIcon = Color(0xFF785324);
  static const noticeText = Color(0xFF604317);
  static const statusAttentionBg = Color(0xFFFCEBEA);
  static const statusActiveBg = Color(0xFFE5F4E9);
  static const statusActiveText = Color(0xFF176836);
  static const statusPendingBg = Color(0xFFFFF2D5);
  static const statusPendingText = Color(0xFF704900);

  /// 金额语义色（UI_SPEC §0），只新增这三个，对比度 ≥ 4.5:1。
  /// 浅色组用于浅色背景上的文字；深色组用于深色背景上的文字。
  static const moneyPositive = Color(0xFF157347); // 可提现（浅色）
  static const moneyPending = Color(0xFF7A5200); // 待确认（浅色）
  static const moneyVoid = Color(0xFF5F6B7A); // 已失效（浅色）
  static const moneyPositiveDark = Color(0xFF4ADE80); // 可提现（深色）
  static const moneyPendingDark = Color(0xFFFBBF24); // 待确认（深色）
  static const moneyVoidDark = Color(0xFF9CA3AF); // 已失效（深色）
}

/// UI_SPEC §0 — amounts are always HK$ 1,234.50 (two decimals, thousands
/// separators). Values come from the server; this only formats them.
String formatHkd(double amount) {
  final negative = amount < 0;
  final abs = amount.abs();
  final fixed = abs.toStringAsFixed(2);
  final parts = fixed.split('.');
  final intPart = parts[0];
  final buf = StringBuffer();
  for (var i = 0; i < intPart.length; i++) {
    if (i > 0 && (intPart.length - i) % 3 == 0) buf.write(',');
    buf.write(intPart[i]);
  }
  return '${negative ? '-' : ''}HK\$$buf.${parts[1]}';
}

/// UI_SPEC §0 — countdowns: >= 24h as "2天 3小时", under 24h as "03:12:45".
String formatCountdown(Duration d) {
  if (d.isNegative || d.inSeconds <= 0) return '已結束';
  if (d.inHours >= 24) {
    final days = d.inDays;
    final hours = d.inHours % 24;
    return '$days天 $hours小時';
  }
  final h = d.inHours.toString().padLeft(2, '0');
  final m = (d.inMinutes % 60).toString().padLeft(2, '0');
  final s = (d.inSeconds % 60).toString().padLeft(2, '0');
  return '$h:$m:$s';
}

/// UI_SPEC §6 — distances: under 1 km in metres ("320 米"), else km
/// with one decimal ("1.2 公里").
String formatDistance(double? km) {
  if (km == null) return '';
  if (km < 1) return '${(km * 1000).round()} 米';
  return '${km.toStringAsFixed(1)} 公里';
}

/// Tabular figures for money / countdown digits (UI_SPEC §0).
const tabularFigures =
    TextStyle(fontFeatures: [FontFeature.tabularFigures()]);

class CgTheme {
  static const double space4 = 4;
  static const double space8 = 8;
  static const double space12 = 12;
  static const double space16 = 16;
  static const double space20 = 20;
  static const double space24 = 24;
  static const double dividerHeight = 32;
  static const double listMaxWidth = 880;
  static const double formMaxWidth = 720;
  static const double pageInset = space16;
  static const double buttonHeight = 56;
  static const double buttonRadius = 12;

  static ThemeData _withSharedSurface(ThemeData base) {
    final text = base.textTheme;
    final action = base.brightness == Brightness.dark
        ? CgColors.primaryOnDark
        : base.colorScheme.primary;
    return base.copyWith(
      bottomNavigationBarTheme: base.bottomNavigationBarTheme.copyWith(
        backgroundColor: base.colorScheme.surface,
        selectedItemColor: action,
        unselectedItemColor: base.colorScheme.onSurfaceVariant,
        selectedLabelStyle: const TextStyle(fontSize: 12, fontWeight: FontWeight.w700),
        unselectedLabelStyle: const TextStyle(fontSize: 12),
        showUnselectedLabels: true,
      ),
      outlinedButtonTheme: OutlinedButtonThemeData(
        style: OutlinedButton.styleFrom(
          foregroundColor: action,
          side: BorderSide(color: action),
          // A finite width keeps buttons usable in Rows and Wraps.
          minimumSize: const Size(64, buttonHeight),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(buttonRadius),
          ),
        ),
      ),
      textTheme: text.copyWith(
        titleLarge: text.titleLarge?.copyWith(
          fontSize: 22,
          fontWeight: FontWeight.bold,
        ),
        titleMedium: text.titleMedium?.copyWith(
          fontSize: 16,
          fontWeight: FontWeight.bold,
        ),
        headlineSmall: text.headlineSmall?.copyWith(
          fontSize: 28,
          fontWeight: FontWeight.bold,
          fontFeatures: tabularFigures.fontFeatures,
        ),
      ),
    );
  }

  static ThemeData light({bool merchant = false}) {
    final bar = merchant ? CgColors.vendorGold : CgColors.primary;
    final ink = merchant ? CgColors.vendorInk : Colors.white;
    final action = merchant ? CgColors.vendorLink : CgColors.primary;
    return _withSharedSurface(ThemeData(
      useMaterial3: true,
      fontFamily: 'Roboto',
      fontFamilyFallback: const ['Coupon UI Fallback', 'Coupon UI Supplemental'],
      colorScheme: ColorScheme.fromSeed(
        seedColor: bar,
        primary: action,
      ),
      scaffoldBackgroundColor: CgColors.pageBg,
      appBarTheme: AppBarTheme(
        backgroundColor: bar,
        foregroundColor: ink,
        elevation: 0,
        centerTitle: true,
      ),
      filledButtonTheme: merchant
          ? FilledButtonThemeData(style: FilledButton.styleFrom(
              backgroundColor: bar, foregroundColor: ink))
          : null,
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: bar,
          foregroundColor: ink,
          minimumSize: const Size.fromHeight(buttonHeight),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(buttonRadius),
          ),
          textStyle: const TextStyle(fontFamily: 'Roboto',
              fontFamilyFallback: ['Coupon UI Fallback', 'Coupon UI Supplemental'], fontSize: 17, fontWeight: FontWeight.w600),
        ),
      ),
      cardTheme: CardThemeData(
        color: Colors.white,
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: const BorderSide(color: CgColors.cardBorder),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: Colors.white,
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: CgColors.cardBorder),
        ),
      ),
    ));
  }

  static ThemeData dark() {
    return _withSharedSurface(ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      fontFamily: 'Roboto',
      fontFamilyFallback: const ['Coupon UI Fallback', 'Coupon UI Supplemental'],
      colorScheme: ColorScheme.fromSeed(
        seedColor: CgColors.primary,
        primary: CgColors.primary,
        brightness: Brightness.dark,
      ),
      scaffoldBackgroundColor: const Color(0xFF121212),
      appBarTheme: const AppBarTheme(
        backgroundColor: CgColors.primary,
        foregroundColor: Colors.white,
        elevation: 0,
        centerTitle: true,
      ),
      elevatedButtonTheme: ElevatedButtonThemeData(
        style: ElevatedButton.styleFrom(
          backgroundColor: CgColors.primary,
          foregroundColor: Colors.white,
          minimumSize: const Size.fromHeight(buttonHeight),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(buttonRadius),
          ),
          textStyle: const TextStyle(fontFamily: 'Roboto',
              fontFamilyFallback: ['Coupon UI Fallback', 'Coupon UI Supplemental'], fontSize: 17, fontWeight: FontWeight.w600),
        ),
      ),
      cardTheme: CardThemeData(
        color: const Color(0xFF1E1E1E),
        elevation: 0,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(12),
          side: const BorderSide(color: Color(0xFF3A3A3A)),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: const Color(0xFF1E1E1E),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
          borderSide: const BorderSide(color: Color(0xFF3A3A3A)),
        ),
      ),
    ));
  }
}
