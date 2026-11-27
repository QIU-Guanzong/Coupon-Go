import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:share_plus/share_plus.dart';

import 'language.dart';
import 'widgets/widgets.dart';

final _couponRoute = RegExp(r'^/coupon/([1-9][0-9]{0,9})(?:-([a-f0-9]{24}))?$');
final _giftRoute = RegExp(r'^/gift/([A-Za-z0-9_-]{32}\.[a-f0-9]{64})$');

({int id, String? key})? sharedCouponRoute(String route) {
  final match = _couponRoute.firstMatch(route);
  final id = int.tryParse(match?.group(1) ?? '');
  return id != null && id <= 2147483647 ? (id: id, key: match?.group(2)) : null;
}

String? couponGiftToken(String route) => _giftRoute.firstMatch(route)?.group(1);

bool get customerShareAvailable => customerShareUri('coupon', '1') != null;

Uri? customerShareUri(String kind, String id) {
  const configured = String.fromEnvironment('PUBLIC_WEB_URL');
  final current = kIsWeb ? Uri.base : Uri.tryParse(configured);
  return current == null ? null : customerShareUriFrom(current, kind, id);
}

Uri? customerShareUriFrom(Uri current, String kind, String id) {
  final route = '/$kind/$id';
  if ((sharedCouponRoute(route) == null && couponGiftToken(route) == null) ||
      !const {'http', 'https'}.contains(current.scheme) ||
      current.host.isEmpty ||
      current.userInfo.isNotEmpty) {
    return null;
  }
  final path = current.path.endsWith('/index.html')
      ? current.resolve('.').path
      : current.path.endsWith('/')
          ? current.path
          : '${current.path}/';
  return Uri.parse(current.origin).replace(path: path, fragment: route);
}

Future<void> showCustomerShare(BuildContext context,
    {required String title, required Uri uri, String? message}) {
  return showDialog<void>(
    context: context,
    builder: (dialogContext) => AlertDialog(
      title: Text(title),
      content: SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (message != null) ...[Text(message), const SizedBox(height: 12)],
            SelectableText(uri.toString()),
          ],
        ),
      ),
      actions: [
        TextButton(
          onPressed: () => Navigator.of(dialogContext).pop(),
          child: Text(context.tr('關閉', 'Close')),
        ),
        TextButton.icon(
          onPressed: () async {
            try {
              await Clipboard.setData(ClipboardData(text: uri.toString()));
              if (dialogContext.mounted) {
                Navigator.of(dialogContext).pop();
                showCgToast(context, context.tr('已複製連結', 'Link copied'));
              }
            } catch (_) {
              if (context.mounted) {
                showCgToast(context,
                    context.tr('請選取並複製連結', 'Select the link to copy it'));
              }
            }
          },
          icon: const Icon(Icons.link),
          label: Text(context.tr('複製連結', 'Copy link')),
        ),
        if (!kIsWeb)
          Builder(
              builder: (buttonContext) => FilledButton.icon(
                    onPressed: () async {
                      final box =
                          buttonContext.findRenderObject() as RenderBox?;
                      try {
                        await SharePlus.instance.share(ShareParams(
                          text: uri.toString(),
                          sharePositionOrigin: box == null
                              ? null
                              : box.localToGlobal(Offset.zero) & box.size,
                        ));
                      } catch (_) {
                        if (context.mounted) {
                          showCgToast(
                              context,
                              context.tr(
                                  '請複製連結分享', 'Copy the link to share it'));
                        }
                      }
                    },
                    icon: const Icon(Icons.share_outlined),
                    label: Text(context.tr('分享', 'Share')),
                  )),
      ],
    ),
  );
}
