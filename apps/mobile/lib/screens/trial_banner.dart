import 'package:flutter/material.dart';

import '../api_client.dart';

/// Persistent controller for switching identities in the isolated trial.
/// It stays outside the navigator while the old route tree is disposed.
class TrialBanner extends StatefulWidget {
  const TrialBanner({super.key, required this.api, required this.onRoleChanged,
    required this.onRoleChanging, required this.onRoleChangeFailed, this.messengerKey});
  final ApiClient api;
  final ValueChanged<String> onRoleChanged;
  final Future<void> Function() onRoleChanging;
  final VoidCallback onRoleChangeFailed;
  final GlobalKey<ScaffoldMessengerState>? messengerKey;

  @override
  State<TrialBanner> createState() => TrialBannerState();
}

class TrialBannerState extends State<TrialBanner> {
  bool _busy = false;

  Future<void> _select(String role) async {
    if (_busy) return;
    setState(() => _busy = true);
    final api = widget.api;
    final attempt = api.beginTrialRoleRequest();
    var changing = false;
    try {
      String? token;
      if (role != 'guest') {
        final result = await api.post('/trial/role', body: {'role': role});
        final value = result['access_token'];
        if (value is! String || value.isEmpty) throw const FormatException();
        token = value;
      }
      final committed = await api.commitTrialRole(
        token,
        attempt: attempt,
        isCurrent: () => mounted && widget.api == api,
        beforeActivate: () {
          changing = true;
          return widget.onRoleChanging();
        },
      );
      if (!mounted) return;
      if (committed) {
        widget.onRoleChanged(role);
      } else if (changing) {
        widget.onRoleChangeFailed();
      }
    } catch (_) {
      if (mounted) {
        if (changing) widget.onRoleChangeFailed();
        WidgetsBinding.instance.addPostFrameCallback((_) {
          if (!mounted) return;
          final messenger = widget.messengerKey?.currentState ??
              ScaffoldMessenger.maybeOf(context);
          messenger?.showSnackBar(const SnackBar(content: Text('切換失敗，請重試')));
        });
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> open(BuildContext context) async {
    if (_busy) return;
    final role = await showDialog<String>(
      context: context,
      builder: (dialogContext) => AlertDialog(
        title: const Text('切換身份'),
        content: SingleChildScrollView(child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Text('每個瀏覽器使用獨立示例資料，付款為模擬，不涉及真實資金。閒置四小時或服務重啟後資料會重置。請勿輸入真實個人資料。'),
            const SizedBox(height: 12),
            for (final item in const [
              ('guest', '訪客瀏覽'), ('customer', '示例顧客'),
              ('vendor', '示例商戶'), ('admin', '示例後台'),
            ])
              ListTile(title: Text(item.$2),
                onTap: () => Navigator.of(dialogContext).pop(item.$1)),
          ],
        )),
        actions: [TextButton(onPressed: () => Navigator.of(dialogContext).pop(),
          child: const Text('取消'))],
      ),
    );
    if (role != null && mounted) await _select(role);
  }

  @override
  Widget build(BuildContext context) => const SizedBox.shrink();
}
