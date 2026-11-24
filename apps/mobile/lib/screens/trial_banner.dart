import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';

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

  Future<void> select(String role) async {
    if (_busy || (role != 'customer' && role != 'vendor')) return;
    setState(() => _busy = true);
    final api = widget.api;
    final attempt = api.beginTrialRoleRequest();
    var changing = false;
    try {
      final token = await api.requestTrialRole(role);
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
          messenger?.showSnackBar(SnackBar(content: Text(context.tr(
              '切換失敗，請重試', 'Could not switch identity. Try again.'))));
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
      builder: (dialogContext) {
        final t = dialogContext.tr;
        return AlertDialog(
          title: Text(t('切換身份', 'Switch identity')),
          content: Column(mainAxisSize: MainAxisSize.min, children: [
            ListTile(
              leading: const Icon(Icons.person_outline),
              title: Text(t('顧客', 'Customer')),
              onTap: () => Navigator.of(dialogContext).pop('customer'),
            ),
            ListTile(
              leading: const Icon(Icons.storefront_outlined),
              title: Text(t('商戶', 'Merchant')),
              onTap: () => Navigator.of(dialogContext).pop('vendor'),
            ),
          ]),
          actions: [
            TextButton(onPressed: () => Navigator.of(dialogContext).pop(),
                child: Text(t('取消', 'Cancel'))),
          ],
        );
      },
    );
    if (role != null && mounted) await select(role);
  }

  @override
  Widget build(BuildContext context) => const SizedBox.shrink();
}
