import 'package:coupon_go/api_client.dart';
import 'package:coupon_go/screens/vendor.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class IssueFieldsApi extends ApiClient {
  IssueFieldsApi() : super(baseUrl: 'https://example.test');
  bool categoriesAvailable = true;
  Map<String, dynamic>? previewBody;
  Map<String, dynamic>? createBody;

  @override
  Future<Map<String, dynamic>> get(
    String path, {
    Map<String, String>? query,
  }) async {
    if (path == '/merchant/shops') {
      return {
        'shops': [
          {'id': 9, 'name': '測試店', 'can_manage_coupons': true},
        ],
      };
    }
    if (path == '/get-category-list') {
      if (!categoriesAvailable) throw ApiException(503, 'unavailable');
      return {
        'categories': [
          {'id': 23, 'name': '美食', 'name_en': 'Dining'},
        ],
      };
    }
    return {'branches': []};
  }

  @override
  Future<Map<String, dynamic>> couponPreview(Map<String, dynamic> body) async {
    previewBody = Map.of(body);
    return {'coupon_name': '新券', 'amount_text': '85折', 'total': 100};
  }

  @override
  Future<Map<String, dynamic>> couponCreate(Map<String, dynamic> body) async {
    createBody = Map.of(body);
    return {'message': '已提交'};
  }
}

Future<void> visitReview(WidgetTester tester) async {
  FocusManager.instance.primaryFocus?.unfocus();
  await tester.pumpAndSettle();
  final review = find.text('檢查並提交');
  await tester.dragUntilVisible(
    review,
    find.byType(Scrollable).first,
    const Offset(0, -240),
  );
  await tester.pumpAndSettle();
  await Scrollable.ensureVisible(tester.element(review), alignment: .5);
  await tester.pumpAndSettle();
  await tester.tap(review);
  await tester.pumpAndSettle();
}

void main() {
  testWidgets('new coupon previews and submits exact selected fields', (
    tester,
  ) async {
    final api = IssueFieldsApi()..categoriesAvailable = false;
    await tester.pumpWidget(MaterialApp(home: IssueCouponScreen(api: api)));
    await tester.pumpAndSettle();
    api.categoriesAvailable = true;
    await tester.tap(find.text('重新讀取分類'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, '優惠券名稱'), '新券');
    await tester.enterText(
      find.widgetWithText(TextField, '優惠券英文名稱（選填）'),
      '  Dinner offer  ',
    );
    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('issue-category')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('美食').last);
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, '每人限領'), '3');
    await visitReview(tester);
    expect(find.textContaining('每人限領：3'), findsOneWidget);
    expect(find.textContaining('Dinner offer'), findsWidgets);
    expect(api.previewBody, containsPair('name_en', 'Dinner offer'));
    expect(api.previewBody, containsPair('category_id', 23));
    expect(api.previewBody, containsPair('per_user_limit', 3));
    await tester.tap(find.text('提交審核'));
    await tester.pumpAndSettle();
    expect(api.createBody, equals(api.previewBody));
  });

  testWidgets('invalid quantity never previews; unset category is omitted', (
    tester,
  ) async {
    final api = IssueFieldsApi();
    await tester.pumpWidget(MaterialApp(home: IssueCouponScreen(api: api)));
    await tester.pumpAndSettle();
    final limit = find.widgetWithText(TextField, '每人限領');
    await tester.enterText(limit, '0');
    await visitReview(tester);
    expect(api.previewBody, isNull);
    await tester.pump(const Duration(seconds: 4));
    await tester.pumpAndSettle();
    await tester.dragUntilVisible(
      limit,
      find.byType(Scrollable).first,
      const Offset(0, 240),
    );
    await tester.enterText(limit, '1');
    await visitReview(tester);
    expect(api.previewBody, isNotNull);
    expect(api.previewBody, containsPair('per_user_limit', 1));
    expect(api.previewBody!.containsKey('category_id'), isFalse);
  });
}
