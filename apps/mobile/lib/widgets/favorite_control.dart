import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../screens/guest_auth.dart' show guestLoginPrompt;
import 'widgets.dart';

/// Session-local state for heart buttons shown in several routes at once.
class FavoritesState extends ChangeNotifier {
  static final _byApi = Expando<FavoritesState>();
  static FavoritesState of(ApiClient api) =>
      _byApi[api] ??= FavoritesState._(api);

  FavoritesState._(this.api) {
    api.addListener(_reset);
  }

  final ApiClient api;
  Set<int>? _coupons, _shops;
  Future<void>? _loading;
  int _generation = 0;
  final _pending = <(bool, int)>{};

  bool isPending(bool shop, int id) => _pending.contains((shop, id));
  bool? contains(bool shop, int id) => (shop ? _shops : _coupons)?.contains(id);

  void _reset() {
    _generation++;
    _coupons = null;
    _shops = null;
    _loading = null;
    _pending.clear();
    notifyListeners();
  }

  Future<void> load() {
    if (!api.isLoggedIn || _coupons != null) return Future.value();
    return _loading ??= _load();
  }

  Future<void> _load() async {
    final generation = _generation;
    try {
      final rows = await Future.wait([
        api.get('/favorite-coupon-list'),
        api.get('/get-collection-shops'),
      ]);
      if (generation != _generation) return;
      _coupons = _ids(rows[0]['coupons'], 'coupon_id');
      _shops = _ids(rows[1]['shops'], 'shop_id');
      notifyListeners();
    } finally {
      if (generation == _generation) _loading = null;
    }
  }

  Set<int> _ids(dynamic rows, String idField) =>
      (rows as List).map((row) => (row[idField] ?? row['id']) as int).toSet();

  Future<void> toggle(bool shop, int id) async {
    final generation = _generation;
    final key = (shop, id);
    if (!_pending.add(key)) return;
    notifyListeners();
    bool? previous;
    void setFavorite(bool value) {
      final ids = (shop ? _shops : _coupons)!;
      value ? ids.add(id) : ids.remove(id);
    }

    try {
      await load();
      if (generation != _generation || !api.isLoggedIn) return;
      previous = contains(shop, id)!;
      setFavorite(!previous);
      notifyListeners();
      final confirmed = shop
          ? await api.toggleFavoriteShop(id)
          : await api.toggleFavoriteCoupon(id);
      if (generation == _generation) setFavorite(confirmed);
    } catch (_) {
      if (generation == _generation) {
        if (previous != null) setFavorite(previous);
        rethrow;
      }
    } finally {
      if (generation == _generation) {
        _pending.remove(key);
        notifyListeners();
      }
    }
  }
}

class FavoriteControl extends StatefulWidget {
  const FavoriteControl({
    super.key,
    required this.api,
    required this.id,
    this.shop = false,
    this.initial = false,
    this.builder,
  });

  final ApiClient api;
  final int id;
  final bool shop;
  final bool initial;
  final Widget Function(bool favorite, VoidCallback? onTap)? builder;

  @override
  State<FavoriteControl> createState() => _FavoriteControlState();
}

class _FavoriteControlState extends State<FavoriteControl> {
  FavoritesState get _state => FavoritesState.of(widget.api);
  bool _authPromptOpen = false;

  @override
  void initState() {
    super.initState();
    _state.load().catchError((Object _) {});
  }

  Future<void> _toggle() async {
    if (_authPromptOpen) return;
    if (!widget.api.isLoggedIn) {
      _authPromptOpen = true;
      try {
        if (!await guestLoginPrompt(context) || !mounted) return;
        await Navigator.of(context).pushNamed('/login');
        if (!mounted || !widget.api.isLoggedIn) return;
      } finally {
        _authPromptOpen = false;
      }
    }
    try {
      await _state.toggle(widget.shop, widget.id);
    } catch (_) {
      if (mounted) {
        showCgToast(context,
            context.tr('未能更新收藏，請重試', 'Could not update favorites. Try again.'));
      }
    }
  }

  @override
  Widget build(BuildContext context) => ListenableBuilder(
        listenable: _state,
        builder: (context, _) {
          final favorite = widget.api.isLoggedIn &&
              (_state.contains(widget.shop, widget.id) ?? widget.initial);
          final onTap =
              _state.isPending(widget.shop, widget.id) ? null : _toggle;
          return widget.builder?.call(favorite, onTap) ??
              IconButton(
                tooltip: favorite
                    ? context.tr('取消收藏', 'Remove from favorites')
                    : context.tr('加入收藏', 'Add to favorites'),
                onPressed: onTap,
                icon: FavoriteGlyph(saved: favorite),
              );
        },
      );
}
