import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

void main() {
  test('catalogue, checkout and Q仔 copy omit the old browsing restrictions', () {
    const paths = [
      'lib/screens/customer.dart', 'lib/screens/qbot.dart',
      'lib/screens/bargain.dart', 'lib/screens/packages.dart',
    ];
    final retired = RegExp(
      r'publicPreview|範例資料|只供瀏覽|體驗版|模擬付款|模擬退款|'
      r'sample data for browsing only|not available in this trial',
      caseSensitive: false,
    );
    for (final path in paths) {
      expect(File(path).readAsStringSync(), isNot(matches(retired)), reason: path);
    }
  });
}
