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
}

class CgTheme {
  static ThemeData light() {
    return ThemeData(
      useMaterial3: true,
      fontFamily: 'Roboto',
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
      cardTheme: CardTheme(
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
}
