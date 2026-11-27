import 'dart:async';

import 'package:flutter/material.dart';

import '../api_client.dart';
import '../feature_flags.dart';
import '../language.dart';
import '../models.dart';
import '../share_links.dart';

const _requestTimeout = Duration(seconds: 15);
const couponGiftAvailable = !publicTrial && !publicDemo;

String? _giftDeadline(BuildContext context, Object? raw) {
  final date = raw is String ? DateTime.tryParse(raw) : null;
  if (date == null) return null;
  // Legacy API timestamps without an offset are UTC.
  final utc = date.isUtc
      ? date
      : DateTime.utc(date.year, date.month, date.day, date.hour, date.minute);
  final hk = utc.add(const Duration(hours: 8));
  final localization = MaterialLocalizations.of(context);
  final time = localization.formatTimeOfDay(TimeOfDay.fromDateTime(hk));
  return context.tr(
    '領取期限：${localization.formatShortDate(hk)} $time（香港時間）',
    'Claim by ${localization.formatShortDate(hk)} $time (Hong Kong time).',
  );
}

class CouponGiftDialog extends StatefulWidget {
  const CouponGiftDialog({
    super.key,
    required this.api,
    required this.item,
    required this.session,
  });

  final ApiClient api;
  final WalletItem item;
  final int session;

  @override
  State<CouponGiftDialog> createState() => _CouponGiftDialogState();
}

class _CouponGiftDialogState extends State<CouponGiftDialog> {
  bool _busy = false;
  bool _cancelled = false;
  Uri? _link;
  Object? _expiry;
  String? _error;

  bool get _current => mounted && widget.api.sessionRevision == widget.session;
  bool get _pending =>
      !_cancelled && (_link != null || widget.item.giftStatus == 'pending');
  String get _path => '/wallet/items/${widget.item.id}/gift';

  Future<void> _create() async {
    if (!_current || _busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await widget.api.post(_path).timeout(_requestTimeout);
      if (!_current) return;
      final token = result['token'];
      final uri = token is String && token.isNotEmpty
          ? customerShareUri('gift', token)
          : null;
      if (uri == null) {
        throw StateError('The gift link could not be opened');
      }
      setState(() {
        _link = uri;
        _expiry = result['expires_at'];
        _cancelled = false;
      });
    } on ApiException catch (error) {
      if (_current) {
        setState(
          () => _error = switch (error.status) {
            404 || 409 || 410 => 'unavailable',
            _ => 'create',
          },
        );
      }
    } catch (_) {
      if (_current) setState(() => _error = 'create');
    } finally {
      if (_current) setState(() => _busy = false);
    }
  }

  Future<void> _cancel() async {
    if (!_current || _busy) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.delete(_path).timeout(_requestTimeout);
      if (_current) {
        setState(() {
          _cancelled = true;
          _link = null;
          _expiry = null;
        });
      }
    } on ApiException catch (error) {
      if (_current) {
        setState(
          () => _error =
              error.code == 'gift_already_claimed' ? 'claimed' : 'cancel',
        );
      }
    } catch (_) {
      if (_current) setState(() => _error = 'cancel');
    } finally {
      if (_current) setState(() => _busy = false);
    }
  }

  String? _notice(BuildContext context) => switch (_error) {
        'create' => context.tr(
            '未能取得轉贈連結，請重試。',
            'Could not get the gift link. Try again.',
          ),
        'cancel' => context.tr(
            '未能確認取消，請重試或返回錢包查看。',
            'Could not confirm cancellation. Try again or check your wallet.',
          ),
        'claimed' => context.tr(
            '好友已領取，無法取消轉贈。',
            'Your friend already claimed this coupon.',
          ),
        'unavailable' => context.tr(
            '此券目前無法轉贈，請返回錢包查看。',
            'This coupon is unavailable to gift. Check your wallet.',
          ),
        _ => null,
      };

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: widget.api,
        builder: (context, _) {
          final notice = _notice(context);
          final deadline = _giftDeadline(context, _expiry);
          final available =
              _current && _error != 'claimed' && _error != 'unavailable';
          final canShare = customerShareAvailable;
          return AlertDialog(
            title: Text(context.tr('轉贈優惠券', 'Gift a coupon')),
            scrollable: true,
            content: !_current
                ? Text(
                    context.tr(
                      '帳戶已切換，請關閉此視窗。',
                      'Your account changed. Close this window.',
                    ),
                  )
                : Column(
                    mainAxisSize: MainAxisSize.min,
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        widget.item.couponName,
                        style: Theme.of(context).textTheme.titleSmall,
                      ),
                      const SizedBox(height: 8),
                      Text(
                        context.tr(
                          '一位好友可透過連結領取。對方領取後，此券會從你的待使用清單移除。',
                          'One friend can claim this coupon from the link. It leaves your list of unused coupons once they claim it.',
                        ),
                      ),
                      if (_pending) ...[
                        const SizedBox(height: 8),
                        Text(
                          context.tr(
                            '好友領取前，你可以取消轉贈或自行使用此券。',
                            'Until your friend claims it, you can cancel the gift or use the coupon yourself.',
                          ),
                        ),
                      ],
                      if (!canShare) ...[
                        const SizedBox(height: 8),
                        Text(context.tr(
                          '此版本未設定轉贈連結。',
                          'Gift links are unavailable in this version.',
                        )),
                      ],
                      if (deadline != null) ...[
                        const SizedBox(height: 8),
                        Text(deadline),
                      ],
                      if (_cancelled || notice != null) ...[
                        const SizedBox(height: 8),
                        Semantics(
                          liveRegion: true,
                          child: Text(
                            notice ??
                                context.tr(
                                  '已取消轉贈。此券仍在你的錢包。',
                                  'Gift cancelled. The coupon is still in your wallet.',
                                ),
                          ),
                        ),
                      ],
                      if (_busy) ...[
                        const SizedBox(height: 12),
                        const LinearProgressIndicator(),
                      ],
                    ],
                  ),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(context).pop(),
                child: Text(context.tr('關閉', 'Close')),
              ),
              if (available && _pending)
                TextButton(
                  onPressed: _busy ? null : _cancel,
                  child: Text(context.tr('取消轉贈', 'Cancel gift')),
                ),
              if (available && canShare && !_cancelled)
                FilledButton(
                  onPressed: _busy
                      ? null
                      : () async {
                          final link = _link;
                          if (link == null) {
                            await _create();
                            return;
                          }
                          await showCustomerShare(
                            context,
                            title: context.tr('轉贈優惠券', 'Gift a coupon'),
                            uri: link,
                            message: widget.item.couponName,
                          );
                        },
                  child: Text(
                    _link != null
                        ? context.tr('分享連結', 'Share link')
                        : _pending
                            ? context.tr('取得連結', 'Get link')
                            : context.tr('建立連結', 'Create link'),
                  ),
                ),
            ],
          );
        },
      );
}

class CouponGiftReceiveScreen extends StatefulWidget {
  const CouponGiftReceiveScreen({
    super.key,
    required this.api,
    required this.token,
    required this.onOpenWallet,
  });

  final ApiClient api;
  final String token;
  final Future<void> Function(BuildContext, int) onOpenWallet;

  @override
  State<CouponGiftReceiveScreen> createState() =>
      _CouponGiftReceiveScreenState();
}

class _CouponGiftReceiveScreenState extends State<CouponGiftReceiveScreen> {
  Map<String, dynamic>? _preview;
  int? _walletItemId;
  bool _busy = false;
  bool _signIn = false;
  bool _acceptBusy = false;
  String? _error;
  late int _session;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    if (couponGiftAvailable && widget.api.isLoggedIn) unawaited(_load());
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    super.dispose();
  }

  void _sessionChanged() {
    final next = widget.api.sessionRevision;
    if (_session == next) return;
    setState(() {
      _session = next;
      _preview = null;
      _walletItemId = null;
      _busy = _acceptBusy = _signIn = false;
      _error = null;
    });
    if (couponGiftAvailable && widget.api.isLoggedIn) unawaited(_load());
  }

  bool _current(int session) => mounted && _session == session;

  void _failed(Object error, {required bool accept}) {
    final code = error is ApiException ? error.code : null;
    final status = error is ApiException ? error.status : null;
    setState(() {
      _signIn = status == 401;
      _error = switch (code) {
        'gift_self' => 'self',
        'coupon_already_claimed' => 'duplicate',
        _ => switch (status) {
            403 => 'role',
            404 || 409 || 410 || 422 => 'unavailable',
            _ => accept ? 'accept' : 'load',
          },
      };
    });
  }

  Future<void> _load() async {
    if (_busy || _acceptBusy) return;
    final session = _session;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final data = await widget.api.post('/wallet/gifts/preview',
          body: {'token': widget.token}).timeout(_requestTimeout);
      if (!_current(session)) return;
      final walletId = data['wallet_item_id'];
      setState(() {
        _preview = data;
        _walletItemId = walletId is int && walletId > 0 ? walletId : null;
      });
    } catch (error) {
      if (_current(session)) _failed(error, accept: false);
    } finally {
      if (_current(session)) setState(() => _busy = false);
    }
  }

  Future<void> _accept() async {
    if (_busy || _acceptBusy || _walletItemId != null) return;
    final session = _session;
    setState(() {
      _acceptBusy = true;
      _error = null;
    });
    try {
      final data = await widget.api.post('/wallet/gifts/accept',
          body: {'token': widget.token}).timeout(_requestTimeout);
      if (!_current(session)) return;
      final walletId = data['wallet_item_id'];
      if (walletId is! int || walletId < 1) {
        throw StateError('The gift receipt is missing');
      }
      setState(() => _walletItemId = walletId);
    } catch (error) {
      if (_current(session)) _failed(error, accept: true);
    } finally {
      if (_current(session)) setState(() => _acceptBusy = false);
    }
  }

  String? _notice(BuildContext context) => switch (_error) {
        'self' => context.tr(
            '這是你建立的轉贈連結，請把它分享給好友。',
            'You created this gift. Share the link with a friend.',
          ),
        'duplicate' => context.tr(
            '你的錢包已有這款優惠券。',
            'You already have this coupon in your wallet.',
          ),
        'role' => context.tr(
            '請使用顧客帳戶領取優惠券。',
            'Use a customer account to claim this coupon.',
          ),
        'unavailable' => context.tr(
            '此轉贈連結已失效或已被領取。',
            'This gift link is unavailable or has already been claimed.',
          ),
        'load' => context.tr(
            '未能載入轉贈優惠券，請重試。',
            'Could not load the gift. Try again.',
          ),
        'accept' => context.tr(
            '未能確認領取結果。再次確認不會重複領取。',
            'Could not confirm your claim. Checking again will not claim another coupon.',
          ),
        _ => null,
      };

  @override
  Widget build(BuildContext context) {
    final preview = _preview;
    final notice = _notice(context);
    final deadline = _giftDeadline(context, preview?['expires_at']);
    final walletItemId = _walletItemId;
    final needsSignIn = _signIn || !widget.api.isLoggedIn;
    return Scaffold(
      appBar: AppBar(title: Text(context.tr('領取好友轉贈', 'Claim a gift'))),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 560),
          child: ListView(
            padding: const EdgeInsets.all(20),
            children: [
              Card(
                child: Padding(
                  padding: const EdgeInsets.all(20),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(
                        Icons.card_giftcard_outlined,
                        color: Theme.of(context).colorScheme.primary,
                      ),
                      const SizedBox(height: 12),
                      if (!couponGiftAvailable)
                        Text(
                          publicTrial
                              ? context.tr(
                                  '試用版錢包各自獨立。請在正式版開啟好友的轉贈連結。',
                                  'Trial wallets are separate. Open your friend’s gift link on the full site.',
                                )
                              : context.tr(
                                  '請在正式版開啟好友的轉贈連結。',
                                  'Open your friend’s gift link on the full site.',
                                ),
                        )
                      else if (needsSignIn) ...[
                        Text(
                          context.tr(
                            '登入後即可查看並領取好友的優惠券。',
                            'Sign in to view and claim your friend’s coupon.',
                          ),
                        ),
                        const SizedBox(height: 12),
                        FilledButton(
                          onPressed: () =>
                              Navigator.of(context).pushNamed('/login'),
                          child: Text(context.tr('登入', 'Sign in')),
                        ),
                      ] else if (_busy)
                        const LinearProgressIndicator()
                      else ...[
                        if (preview != null) ...[
                          Text(
                            preview['coupon_name'] as String? ?? '',
                            style: Theme.of(context).textTheme.titleLarge,
                          ),
                          Text(preview['shop_name'] as String? ?? ''),
                          const SizedBox(height: 8),
                          if (deadline != null && walletItemId == null)
                            Text(deadline),
                        ],
                        if (walletItemId != null) ...[
                          Semantics(
                            liveRegion: true,
                            child: Text(
                              context.tr(
                                '已領取，優惠券已存入 Q Wallet。',
                                'Gift claimed and added to Q Wallet.',
                              ),
                            ),
                          ),
                          const SizedBox(height: 12),
                          FilledButton(
                            onPressed: () =>
                                widget.onOpenWallet(context, walletItemId),
                            child: Text(
                              context.tr('前往 Q Wallet', 'Open Q Wallet'),
                            ),
                          ),
                        ] else ...[
                          if (notice != null)
                            Semantics(liveRegion: true, child: Text(notice)),
                          if (_acceptBusy)
                            const LinearProgressIndicator()
                          else if (_error == 'load' ||
                              _error == 'accept' ||
                              (preview != null && _error == null)) ...[
                            const SizedBox(height: 12),
                            FilledButton(
                              onPressed: _error == 'load' ? _load : _accept,
                              child: Text(switch (_error) {
                                'load' => context.tr('重試', 'Retry'),
                                'accept' => context.tr('確認領取結果', 'Check claim'),
                                _ => context.tr('領取優惠券', 'Claim coupon'),
                              }),
                            ),
                          ],
                        ],
                      ],
                    ],
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
