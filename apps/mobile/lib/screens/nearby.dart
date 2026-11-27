import 'package:flutter/material.dart';

import '../api_client.dart';
import '../discovery_location.dart';
import '../language.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import '../widgets/shop_discovery_card.dart';
import 'customer.dart';

enum _NearbyView { list, map }

enum _NearbySort { distance, rating, newest }

class NearbyScreen extends StatefulWidget {
  const NearbyScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<NearbyScreen> createState() => _NearbyScreenState();
}

class _NearbyScreenState extends State<NearbyScreen> {
  static const _presets = {
    '中環': (22.2845, 114.1580),
    '旺角': (22.3190, 114.1694),
    '沙田': (22.3828, 114.1882),
    '尖沙咀': (22.2976, 114.1722),
    '銅鑼灣': (22.2804, 114.1858),
    '觀塘': (22.3121, 114.2263),
  };

  double _lat = 22.2845;
  double _lng = 114.1580;
  String _place = '中環';
  double _radiusKm = 5;
  List<Shop> _shops = [];
  bool _loading = false;
  int _loadGeneration = 0;
  String? _error;
  int? _selectedShopId;
  bool _locating = false;
  _NearbyView _view = _NearbyView.list;
  _NearbySort _sort = _NearbySort.distance;
  int? _categoryId;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    // Area, radius and sort can change while a previous request is in flight.
    // Only the newest request owns the results, error and loading state.
    final generation = ++_loadGeneration;
    bool isCurrent() => mounted && generation == _loadGeneration;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await widget.api.nearbyShops(
          lat: _lat, lng: _lng, radiusKm: _radiusKm, sort: _sort.name);
      if (!isCurrent()) return;
      setState(() {
        _shops = ((data['shops'] as List?) ?? [])
            .map((e) => Shop.fromJson(e as Map<String, dynamic>))
            .toList();
      });
    } on ApiException catch (_) {
      if (isCurrent()) setState(() => _error = '網絡不穩定，請重試');
    } finally {
      if (isCurrent()) setState(() => _loading = false);
    }
  }

  Future<void> _requestLocation() async {
    if (_locating) return;
    final generation = _loadGeneration;
    setState(() => _locating = true);
    try {
      final position = await findDiscoveryPosition();
      if (!mounted || generation != _loadGeneration) return;
      if (position != null) {
        setState(() {
          _lat = position.lat;
          _lng = position.lng;
          _place = context.tr('我的位置', 'My location');
          _selectedShopId = null;
        });
        await _load();
        return;
      }
    } catch (_) {
      // Manual district selection stays available when the OS cannot locate.
    } finally {
      if (mounted) setState(() => _locating = false);
    }
    if (mounted && generation == _loadGeneration) {
      showCgToast(
          context,
          context.tr('未能取得位置，可手動選擇地區。',
              'Location is unavailable. Choose a district instead.'));
    }
  }

  void _openShop(Shop shop) {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => ShopDetailScreen(
            api: widget.api,
            shop: shop,
            onOpenCoupon: (c) => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) =>
                    CouponDetailScreen(api: widget.api, coupon: c))))));
  }

  Widget _shopCard(Shop shop) => Padding(
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
        child: ShopDiscoveryCard(
          shop: shop,
          apiBaseUrl: widget.api.baseUrl,
          onOpen: () => _openShop(shop),
          selected: shop.id == _selectedShopId,
        ),
      );

  Widget _emptyState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Text('此範圍內暫無商戶'),
          const SizedBox(height: 12),
          ElevatedButton(
            onPressed: () {
              setState(() => _radiusKm = 10);
              _load();
            },
            child: const Text('擴大範圍至 10 公里'),
          ),
        ],
      ),
    );
  }

  Widget _errorState() {
    return Center(
      child: Column(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          const Text('網絡不穩定，請重試'),
          const SizedBox(height: 12),
          ElevatedButton(onPressed: _load, child: const Text('重試')),
        ],
      ),
    );
  }

  Widget _loadingState() {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: const [
        SkeletonBox(height: 96),
        SizedBox(height: 8),
        SkeletonBox(height: 96),
        SizedBox(height: 8),
        SkeletonBox(height: 96),
      ],
    );
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    final categories = {
      for (final shop in _shops)
        if (shop.categoryId case final id?)
          if (shop.categoryName.isNotEmpty) id: shop.categoryName,
    };
    final filter = categories.containsKey(_categoryId) ? _categoryId : null;
    final shops = filter == null
        ? _shops
        : _shops.where((shop) => shop.categoryId == filter).toList();
    final selectedShop =
        shops.where((shop) => shop.id == _selectedShopId).firstOrNull;
    return Scaffold(
      appBar: AppBar(title: Text(t('附近商戶', 'Nearby shops'))),
      body: Center(
          child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 960),
              child: Column(
                children: [
                  Padding(
                    padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
                    child: Row(children: [
                      Expanded(
                          child: Text(
                              '$_place · ${t('直線距離', 'Straight-line distance')}',
                              style: Theme.of(context).textTheme.titleMedium)),
                      IconButton(
                        onPressed: _locating ? null : _requestLocation,
                        tooltip: t('使用我的位置', 'Use my location'),
                        icon: _locating
                            ? const SizedBox.square(
                                dimension: 18,
                                child:
                                    CircularProgressIndicator(strokeWidth: 2))
                            : const Icon(Icons.my_location),
                      ),
                    ]),
                  ),
                  SizedBox(
                    height: 48,
                    child: ListView.separated(
                      scrollDirection: Axis.horizontal,
                      padding: const EdgeInsets.symmetric(horizontal: 16),
                      itemCount: _presets.length,
                      separatorBuilder: (_, __) => const SizedBox(width: 8),
                      itemBuilder: (_, index) {
                        final entry = _presets.entries.elementAt(index);
                        return ChoiceChip(
                          label: Text(entry.key),
                          selected: _place == entry.key,
                          onSelected: (selected) {
                            if (!selected) return;
                            setState(() {
                              _lat = entry.value.$1;
                              _lng = entry.value.$2;
                              _place = entry.key;
                              _selectedShopId = null;
                            });
                            _load();
                          },
                        );
                      },
                    ),
                  ),
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 16),
                    child: Wrap(
                      spacing: 12,
                      runSpacing: 2,
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        DropdownButton<double>(
                          value: _radiusKm,
                          onChanged: (value) {
                            if (value == null || value == _radiusKm) return;
                            setState(() => _radiusKm = value);
                            _load();
                          },
                          items: [
                            for (final radius in const [1.0, 3.0, 5.0, 10.0])
                              DropdownMenuItem(
                                  value: radius,
                                  child: Text(t('${radius.toInt()} 公里',
                                      '${radius.toInt()} km'))),
                          ],
                        ),
                        DropdownButton<_NearbySort>(
                          value: _sort,
                          onChanged: (value) {
                            if (value == null || value == _sort) return;
                            setState(() => _sort = value);
                            _load();
                          },
                          items: [
                            DropdownMenuItem(
                                value: _NearbySort.distance,
                                child: Text(t('距離最近', 'Nearest'))),
                            DropdownMenuItem(
                                value: _NearbySort.rating,
                                child: Text(t('評分最高', 'Top rated'))),
                            DropdownMenuItem(
                                value: _NearbySort.newest,
                                child: Text(t('最新', 'Newest'))),
                          ],
                        ),
                        SegmentedButton<_NearbyView>(
                          showSelectedIcon: false,
                          segments: [
                            ButtonSegment(
                                value: _NearbyView.list,
                                icon: const Icon(Icons.list),
                                tooltip: t('列表', 'List')),
                            ButtonSegment(
                                value: _NearbyView.map,
                                icon: const Icon(Icons.map_outlined),
                                tooltip: t('位置圖', 'Locations')),
                          ],
                          selected: {_view},
                          onSelectionChanged: (value) =>
                              setState(() => _view = value.first),
                        ),
                      ],
                    ),
                  ),
                  if (categories.isNotEmpty)
                    SizedBox(
                      height: 46,
                      child: ListView(
                        scrollDirection: Axis.horizontal,
                        padding: const EdgeInsets.symmetric(horizontal: 16),
                        children: [
                          ChoiceChip(
                              label: Text(t('全部分類', 'All categories')),
                              selected: filter == null,
                              onSelected: (_) =>
                                  setState(() => _categoryId = null)),
                          for (final entry in categories.entries)
                            Padding(
                              padding:
                                  const EdgeInsetsDirectional.only(start: 8),
                              child: ChoiceChip(
                                  label: Text(entry.value),
                                  selected: filter == entry.key,
                                  onSelected: (selected) => setState(() {
                                        _categoryId =
                                            selected ? entry.key : null;
                                        _selectedShopId = null;
                                      })),
                            ),
                        ],
                      ),
                    ),
                  const SizedBox(height: 8),
                  Expanded(
                    child: _loading
                        ? _loadingState()
                        : _error != null
                            ? _errorState()
                            : shops.isEmpty
                                ? _emptyState()
                                : _view == _NearbyView.map
                                    ? Column(
                                        children: [
                                          Padding(
                                            padding: const EdgeInsets.symmetric(
                                                horizontal: 16),
                                            child: _MarkerMap(
                                              shops: shops,
                                              centerLat: _lat,
                                              centerLng: _lng,
                                              selectedShopId: _selectedShopId,
                                              onSelect: (s) => setState(
                                                  () => _selectedShopId = s.id),
                                            ),
                                          ),
                                          if (selectedShop != null)
                                            _shopCard(selectedShop),
                                          Expanded(
                                            child: RefreshIndicator(
                                              onRefresh: _load,
                                              child: ListView.builder(
                                                itemCount: shops.length,
                                                itemBuilder: (_, i) =>
                                                    _shopCard(shops[i]),
                                              ),
                                            ),
                                          ),
                                        ],
                                      )
                                    : RefreshIndicator(
                                        onRefresh: _load,
                                        child: ListView.builder(
                                          itemCount: shops.length,
                                          itemBuilder: (_, i) =>
                                              _shopCard(shops[i]),
                                        ),
                                      ),
                  ),
                ],
              ))),
    );
  }
}

/// Marker colour by category (UI_SPEC §6).
Color _categoryColor(String category) {
  if (category.contains('餐')) return Colors.red.shade400;
  if (category.contains('咖啡')) return Colors.brown;
  if (category.contains('美容')) return Colors.pink.shade300;
  if (category.contains('娛樂') || category.contains('娱乐')) {
    return Colors.purple.shade300;
  }
  return CgColors.primary;
}

/// Schematic marker view: shops as dots positioned by lat/lng within the
/// visible bounds. Labelled as a schematic — it is not a map tile layer.
class _MarkerMap extends StatelessWidget {
  const _MarkerMap(
      {required this.shops,
      required this.centerLat,
      required this.centerLng,
      required this.selectedShopId,
      required this.onSelect});

  final List<Shop> shops;
  final double centerLat;
  final double centerLng;
  final int? selectedShopId;
  final void Function(Shop) onSelect;

  @override
  Widget build(BuildContext context) {
    final lats = shops.map((s) => s.latitude ?? centerLat).toList();
    final lngs = shops.map((s) => s.longitude ?? centerLng).toList();
    var minLat = [...lats, centerLat].reduce((a, b) => a < b ? a : b);
    var maxLat = [...lats, centerLat].reduce((a, b) => a > b ? a : b);
    var minLng = [...lngs, centerLng].reduce((a, b) => a < b ? a : b);
    var maxLng = [...lngs, centerLng].reduce((a, b) => a > b ? a : b);
    if (maxLat - minLat < 0.002) {
      minLat -= 0.001;
      maxLat += 0.001;
    }
    if (maxLng - minLng < 0.002) {
      minLng -= 0.001;
      maxLng += 0.001;
    }
    return Container(
      height: 180,
      decoration: BoxDecoration(
        color: const Color(0xFFF5F1E8),
        borderRadius: BorderRadius.circular(12),
        border: Border.all(color: const Color(0xFFE0D9C8)),
      ),
      child: LayoutBuilder(
        builder: (context, constraints) {
          final w = constraints.maxWidth - 24;
          final h = constraints.maxHeight - 24;
          double px(double lng) => (lng - minLng) / (maxLng - minLng) * w;
          double py(double lat) => (1 - (lat - minLat) / (maxLat - minLat)) * h;
          return Stack(
            children: [
              Positioned(
                left: 8,
                top: 8,
                child: Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
                  decoration: BoxDecoration(
                    color: Colors.white,
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: const Text('位置示意圖（非實景地圖）',
                      style: TextStyle(fontSize: 12)),
                ),
              ),
              Positioned(
                left: px(centerLng),
                top: py(centerLat),
                child: const Icon(Icons.my_location,
                    color: CgColors.primary, size: 22),
              ),
              for (final s in shops)
                if (s.latitude != null && s.longitude != null)
                  Positioned(
                    left: px(s.longitude!),
                    top: py(s.latitude!),
                    child: Semantics(
                      label: '商戶 ${s.name}',
                      button: true,
                      child: GestureDetector(
                        onTap: () => onSelect(s),
                        child: Icon(Icons.place,
                            color: s.id == selectedShopId
                                ? CgColors.primary
                                : _categoryColor(s.categoryName),
                            size: s.id == selectedShopId ? 30 : 24),
                      ),
                    ),
                  ),
            ],
          );
        },
      ),
    );
  }
}
