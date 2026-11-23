import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('Android download copy keeps the existing public web gate', () {
    final source = File('lib/main.dart').readAsStringSync();
    expect(
        source, contains("context.tr('下載 Android 版', 'Download Android app')"));
    expect(source,
        contains("if (kIsWeb && publicPreview)"));
    expect(source, isNot(contains('下載 Android 示範版')));
    expect(source, isNot(contains('Download Android demo')));
  });

  test('home and Q仔 disclose sample data in both languages', () {
    for (final path in ['lib/screens/customer.dart', 'lib/screens/qbot.dart']) {
      final source = File(path).readAsStringSync();
      expect(source, contains('以下商戶及優惠為範例資料，僅供瀏覽。'), reason: path);
      expect(
          source,
          contains(
              'The shops and offers below are sample data for browsing only.'),
          reason: path);
      expect(source, isNot(contains('暫未開放')), reason: path);
    }
  });
}
