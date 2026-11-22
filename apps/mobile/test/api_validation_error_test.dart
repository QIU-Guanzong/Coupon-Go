import 'dart:convert';
import 'dart:io';

import 'package:coupon_go/api_client.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('格式錯誤返回可讀提示，不顯示原始驗證結構', () async {
    final server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((request) async {
      request.response.statusCode = 422;
      request.response.headers.contentType = ContentType.json;
      request.response.write(jsonEncode({
        'detail': [
          {
            'loc': ['body', 'email'],
            'msg': 'internal validation details'
          }
        ]
      }));
      await request.response.close();
    });
    final api = ApiClient(baseUrl: 'http://127.0.0.1:${server.port}');
    try {
      await expectLater(
          api.post('/login', body: {'email': 'invalid'}),
          throwsA(isA<ApiException>()
              .having((e) => e.message, 'message', '請輸入有效的電郵地址')));
    } finally {
      api.dispose();
      await server.close(force: true);
    }
  });
}
