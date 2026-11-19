import 'dart:async';

import 'package:flutter/widgets.dart';

import '../api_client.dart';

/// Observe merchant-side changes only while this customer page is visible.
mixin WalletUpdates<T extends StatefulWidget> on State<T> {
  Timer? _poll;
  bool _busy = false;
  String? _revision;

  bool get walletPageActive => true;
  Future<void> syncWallet();

  Future<void> refreshOnReceiptChange(
      ApiClient api, Future<bool> Function() refresh) async {
    final data = await api.get('/wallet/revision');
    final revision = data['revision'] as String;
    if (mounted && _revision != revision && await refresh()) {
      _revision = revision;
    }
  }

  Future<void> _tick() async {
    final lifecycle = WidgetsBinding.instance.lifecycleState;
    if (!mounted ||
        !walletPageActive ||
        _busy ||
        lifecycle == AppLifecycleState.hidden ||
        lifecycle == AppLifecycleState.paused ||
        ModalRoute.of(context)?.isCurrent != true) {
      return;
    }
    _busy = true;
    try {
      await syncWallet();
    } catch (_) {
      // Keep the visible receipt while offline, then retry on the next tick.
    } finally {
      _busy = false;
    }
  }

  @override
  void initState() {
    super.initState();
    _poll = Timer.periodic(const Duration(seconds: 5), (_) => _tick());
  }

  @override
  void dispose() {
    _poll?.cancel();
    super.dispose();
  }
}
