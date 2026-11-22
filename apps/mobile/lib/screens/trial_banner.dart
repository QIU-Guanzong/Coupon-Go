import 'package:flutter/material.dart';

import '../api_client.dart';

/// Visible on every route in the isolated public trial. The backend creates a
/// separate synthetic dataset for each browser and never contacts Stripe.
class TrialBanner extends StatefulWidget {
  const TrialBanner({super.key, required this.api, required this.onRoleChanged,
    required this.onRoleChanging, required this.onRoleChangeFailed});
  final ApiClient api;
  final ValueChanged<String> onRoleChanged;
  final Future<void> Function() onRoleChanging;
  final VoidCallback onRoleChangeFailed;

  @override
  State<TrialBanner> createState() => _TrialBannerState();
}

class _TrialBannerState extends State<TrialBanner> {
  bool _busy = false;
  bool _open = false;
  String? _error;

  Future<void> _select(String role) async {
    if (_busy) return;
    setState(() { _busy = true; _open = false; _error = null; });
    final previousToken = widget.api.paymentAccessToken;
    var staged = false;
    var changing = false;
    try {
      String? token;
      if (role != 'guest') {
        final result = await widget.api.post('/trial/role', body: {'role': role});
        final value = result['access_token'];
        if (value is! String || value.isEmpty) throw const FormatException();
        token = value;
      }
      await widget.api.prepareTrialToken(token);
      staged = true;
      if (!mounted) {
        await widget.api.prepareTrialToken(previousToken);
        return;
      }
      changing = true;
      await widget.onRoleChanging();
      if (!mounted) {
        await widget.api.prepareTrialToken(previousToken);
        return;
      }
      widget.api.activateTrialToken(token);
      widget.onRoleChanged(role);
    } catch (_) {
      if (staged) {
        try { await widget.api.prepareTrialToken(previousToken); } catch (_) {}
      }
      if (mounted) {
        if (changing) widget.onRoleChangeFailed();
        setState(() => _error = '切換失敗，請重試');
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Material(
      color: const Color(0xFFFFF1D6),
      child: SafeArea(
        bottom: false,
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
          child: Column(mainAxisSize: MainAxisSize.min, children: [
            Row(children: [
              const Expanded(child: Text('示例數據・模擬付款｜資料會重置',
                  style: TextStyle(color: Color(0xFF493306), fontSize: 12))),
              TextButton(
                onPressed: _busy ? null : () => setState(() => _open = !_open),
                child: Text(_busy ? '切換中…' : '切換身份 ▾',
                    style: const TextStyle(color: Color(0xFF493306),
                        fontSize: 12, fontWeight: FontWeight.w700)),
              ),
            ]),
            if (_open) Wrap(spacing: 4, children: [
              for (final item in const [
                ('guest', '訪客瀏覽'), ('customer', '示例顧客'),
                ('vendor', '示例商戶'), ('admin', '示例後台'),
              ])
                TextButton(onPressed: () => _select(item.$1), child: Text(item.$2)),
            ]),
            if (_open) const Align(alignment: Alignment.centerLeft,
                child: Text('每個瀏覽器使用獨立示例資料；閒置四小時或服務重啟後重置。請勿輸入真實個人資料。',
                    style: TextStyle(color: Color(0xFF493306), fontSize: 12))),
            if (_error != null) Align(alignment: Alignment.centerLeft,
                child: Text(_error!, style: const TextStyle(color: Colors.red))),
          ]),
        ),
      ),
    );
  }
}
