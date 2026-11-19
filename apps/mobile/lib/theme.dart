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
  static const pageBg = Color(0xFFF5F5F6);
  static const cardBorder = Color(0xFFE0E0E0);
  static const textPrimary = Color(0xFF1A1A1A);
  static const textSecondary = Color(0xFF737373);
  static const success = Color(0xFF1E9E5A);

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
  if (d.isNegative || d.inSeconds <= 0) return '已结束';
  if (d.inHours >= 24) {
    final days = d.inDays;
    final hours = d.inHours % 24;
    return '$days天 $hours小时';
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
  static ThemeData light() {
    return ThemeData(
      useMaterial3: true,
      fontFamily: 'Roboto',
      fontFamilyFallback: const ['Coupon UI Fallback'],
      colorScheme: ColorScheme.fromSeed(
        seedColor: CgColors.primary,
        primary: CgColors.primary,
      ),
      scaffoldBackgroundColor: CgColors.pageBg,
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
          minimumSize: const Size.fromHeight(56),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          textStyle: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
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
    );
  }

  static ThemeData dark() {
    return ThemeData(
      useMaterial3: true,
      brightness: Brightness.dark,
      fontFamily: 'Roboto',
      fontFamilyFallback: const ['Coupon UI Fallback'],
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
          minimumSize: const Size.fromHeight(56),
          shape: RoundedRectangleBorder(
            borderRadius: BorderRadius.circular(12),
          ),
          textStyle: const TextStyle(fontSize: 17, fontWeight: FontWeight.w600),
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
    );
  }
}
