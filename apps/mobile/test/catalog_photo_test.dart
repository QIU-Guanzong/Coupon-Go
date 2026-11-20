import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/widgets/widgets.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('API base preserves a public deployment path', () {
    expect(ApiClient.resolveBaseUrl('', Uri.parse('https://example.com/#/orders')),
        'https://example.com');
    expect(
        ApiClient.resolveBaseUrl('', Uri.parse('https://example.com/coupongo/#/orders')),
        'https://example.com/coupongo');
    expect(
        ApiClient.resolveBaseUrl('https://api.example.com/v1', Uri.parse('https://other.example/')),
        'https://api.example.com/v1');
  });

  testWidgets('catalogue photos support bundled, absolute and upload paths', (tester) async {
    Future<ImageProvider> provider(String url, {String? apiBaseUrl}) async {
      await tester.pumpWidget(MaterialApp(
        home: SizedBox(width: 100, height: 100,
          child: CatalogPhoto(url: url, apiBaseUrl: apiBaseUrl)),
      ));
      return tester.widget<Image>(find.byType(Image)).image;
    }

    final bundled = await provider('assets/images/mvp-shop-sushi-bar.jpg');
    expect(bundled, isA<AssetImage>());

    final remote = await provider('https://images.example.com/photo.jpg');
    expect((remote as NetworkImage).url, 'https://images.example.com/photo.jpg');

    for (final base in ['https://example.com/coupongo', 'https://example.com/coupongo/']) {
      final upload = await provider('/uploads/review-images/test.jpg', apiBaseUrl: base);
      expect((upload as NetworkImage).url,
          'https://example.com/coupongo/uploads/review-images/test.jpg');
    }
  });
}
