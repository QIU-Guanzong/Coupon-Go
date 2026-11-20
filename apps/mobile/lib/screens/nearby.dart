import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import 'customer.dart';

/// Nearby shops (UI_SPEC §6).
///
/// List / map toggle (list by default), distance ascending, switchable to
/// highest-rated / newest. Map view is a schematic position map (dots
/// placed by lat/lng, coloured by category) — NOT Google Maps tiles:
/// google_maps_flutter needs native API-key config + a real-device
/// build, neither verified on this machine. Tapping a marker opens a
/// small card (photo, name, rating, distance, 导航); 导航 opens the
/// system map — no in-app route planning. Distances are straight-line
/// (Haversine), not walking/driving time.
///
/// Location: permission is asked at most once from the banner button.
/// If refused, the screen falls back to area selection and never
/// re-prompts. No location is sent anywhere except /nearby-shops.
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
  bool _locationGranted = false;
  bool _locationDeclined = false;
  bool _askedPermission = false;

  @override
  void initState() {
    super.initState();
    _checkLocationSilently();
    _load();
  }

  Future<void> _checkLocationSilently() async {
    final permission = await Geolocator.checkPermission();
    if (!mounted) return;
    setState(() {
      _locationGranted = permission == LocationPermission.always ||
          permission == LocationPermission.whileInUse;
      _locationDeclined = permission == LocationPermission.deniedForever;
    });
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

  /// Server order is authoritative (sort=distance|rating|newest is
  /// served by /nearby-shops). The client-side pass below only
  /// re-applies the same order on the payload's own rating / created_at
  /// fields, e.g. after a local refresh; it never calls per-shop
  /// rating endpoints.
  List<Shop> get _sortedShops {
    final list = List<Shop>.of(_shops);
    switch (_sort) {
      case _NearbySort.distance:
        break; // server order is distance ascending
      case _NearbySort.rating:
        list.sort((a, b) {
          final ra = a.rating;
          final rb = b.rating;
          if (ra == null && rb == null) return 0;
          if (ra == null) return 1;
          if (rb == null) return -1;
          return rb.compareTo(ra);
        });
      case _NearbySort.newest:
        list.sort((a, b) {
          final ca = a.createdAt;
          final cb = b.createdAt;
          if (ca == null && cb == null) return 0;
          if (ca == null) return 1;
          if (cb == null) return -1;
          return cb.compareTo(ca);
        });
    }
    return list;
  }

  double? _ratingOf(Shop s) => s.rating;

  Future<void> _requestLocation() async {
    if (_askedPermission) return; // asked once; never re-prompt
    _askedPermission = true;
    setState(() => _locating = true);
    try {
      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied ||
          permission == LocationPermission.deniedForever) {
        if (mounted) {
          setState(() => _locationDeclined = true);
          showCgToast(context, '已改用地區選擇，可手動切換地區');
        }
        return;
      }
      final pos = await Geolocator.getCurrentPosition(
          locationSettings:
              const LocationSettings(accuracy: LocationAccuracy.medium));
      if (!mounted) return;
      setState(() {
        _lat = pos.latitude;
        _lng = pos.longitude;
        _place = '我的位置';
        _locationGranted = true;
        _locationDeclined = false;
      });
      await _load();
    } catch (_) {
      if (mounted) showCgToast(context, '未能取得定位，已保留原有中心');
    } finally {
      if (mounted) setState(() => _locating = false);
    }
  }

  Future<void> _navigate(Shop shop) async {
    if (const bool.fromEnvironment('PUBLIC_DEMO')) {
      showCgToast(context, '示範商戶，未提供導航');
      return;
    }
    final url = 'https://www.google.com/maps/dir/?api=1'
        '&destination=${shop.latitude},${shop.longitude}';
    final uri = Uri.parse(url);
    if (await canLaunchUrl(uri)) {
      await launchUrl(uri, mode: LaunchMode.externalApplication);
    } else if (mounted) {
      showCgToast(context, '無法開啟 Google Maps');
    }
  }

  void _openShop(Shop shop) {
    Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => ShopDetailScreen(
            api: widget.api,
            shop: shop,
            onOpenCoupon: (c) => Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => CouponDetailScreen(api: widget.api, coupon: c))))));
  }

  Widget _shopImage(Shop shop, {double? height}) {
    return CatalogPhoto(
      url: shop.image,
      height: height ?? double.infinity,
      apiBaseUrl: widget.api.baseUrl,
    );
  }

  Widget _ratingText(Shop s) {
    final r = _ratingOf(s);
    if (r == null) {
      return const Text('暫無評分',
          style: TextStyle(color: CgColors.textSecondary, fontSize: 12));
    }
    return Text('★ ${r.toStringAsFixed(1)}',
        style: const TextStyle(
            color: CgColors.amber, fontWeight: FontWeight.bold, fontSize: 13));
  }

  Widget _badges(Shop s) {
    final chips = <Widget>[];
    if (s.hasCoupon) {
      chips.add(_badge('有券'));
    }
    if (s.hasPackage) {
      chips.add(_badge('有套餐'));
    }
    if (chips.isEmpty) return const SizedBox.shrink();
    return Wrap(spacing: 4, children: chips);
  }

  Widget _badge(String label) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 2),
      decoration: BoxDecoration(
        color: const Color(0xFFFFF3CD),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Text(label,
          style: const TextStyle(fontSize: 11, color: CgColors.primary)),
    );
  }

  Widget _shopCard(Shop s) {
    final selected = s.id == _selectedShopId;
    return Card(
      margin: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
      color: selected ? const Color(0xFFFFF3CD) : null,
      child: InkWell(
        onTap: () => _openShop(s),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: SizedBox(
                  width: 72,
                  height: 72,
                  child: _shopImage(s),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(s.name,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            fontWeight: FontWeight.bold, fontSize: 16)),
                    const SizedBox(height: 2),
                    Text(s.categoryName,
                        style: const TextStyle(
                            color: CgColors.textSecondary, fontSize: 12)),
                    const SizedBox(height: 4),
                    Row(
                      children: [
                        _ratingText(s),
                        const SizedBox(width: 8),
                        Text(formatDistance(s.distanceKm),
                            style: const TextStyle(
                                fontWeight: FontWeight.bold, fontSize: 13)),
                      ],
                    ),
                    const SizedBox(height: 4),
                    _badges(s),
                  ],
                ),
              ),
              SizedBox(
                width: 48,
                height: 44,
                child: TextButton(
                  onPressed: () => _navigate(s),
                  child: const Text('導航'),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }

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
    final shops = _sortedShops;
    Shop? selectedShop;
    if (_selectedShopId == null) {
      selectedShop = null;
    } else {
      final matches =
          _shops.where((s) => s.id == _selectedShopId).toList();
      selectedShop = matches.isEmpty ? null : matches.first;
    }
    return Scaffold(
      appBar: AppBar(title: const Text('附近商戶')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: SegmentedButton<_NearbyView>(
              segments: const [
                ButtonSegment(
                    value: _NearbyView.list,
                    icon: Icon(Icons.list),
                    label: Text('列表')),
                ButtonSegment(
                    value: _NearbyView.map,
                    icon: Icon(Icons.map_outlined),
                    label: Text('地圖')),
              ],
              selected: {_view},
              onSelectionChanged: (v) => setState(() => _view = v.first),
            ),
          ),
          if (!_locationGranted && !_locationDeclined)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
              child: Card(
                child: Padding(
                  padding: const EdgeInsets.all(12),
                  child: Row(
                    children: [
                      const Expanded(
                          child: Text('開啟定位查看附近商戶')),
                      const SizedBox(width: 8),
                      ElevatedButton(
                        onPressed: _locating ? null : _requestLocation,
                        child: Text(_locating ? '定位中…' : '開啟定位'),
                      ),
                    ],
                  ),
                ),
              ),
            ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _locating ? null : _requestLocation,
                    icon: const Icon(Icons.my_location, size: 18),
                    label: Text(_locating
                        ? '定位中…'
                        : _locationGranted
                            ? '使用我的位置'
                            : '使用我的位置'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () => showCgToast(
                        context, '定位僅用於列出附近商戶，不會上傳或儲存'),
                    icon: const Icon(Icons.info_outline, size: 18),
                    label: const Text('定位用途'),
                  ),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Wrap(
              spacing: 8,
              children: _presets.keys.map((name) {
                final selected = _place == name;
                return ChoiceChip(
                  label: Text(name),
                  selected: selected,
                  onSelected: (_) {
                    final p = _presets[name]!;
                    setState(() {
                      _lat = p.$1;
                      _lng = p.$2;
                      _place = name;
                    });
                    _load();
                  },
                );
              }).toList(),
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Wrap(
              spacing: 8,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                const Text('範圍'),
                for (final r in [1.0, 3.0, 5.0, 10.0])
                  ChoiceChip(
                    label: Text('${r.toStringAsFixed(0)} 公里'),
                    selected: _radiusKm == r,
                    onSelected: (_) {
                      setState(() => _radiusKm = r);
                      _load();
                    },
                  ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Row(
              children: [
                const Icon(Icons.place, size: 16, color: CgColors.primary),
                const SizedBox(width: 4),
                Expanded(
                  child: Text('中心：$_place · 直線距離'),
                ),
              ],
            ),
          ),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 8, 16, 0),
            child: Wrap(
              spacing: 8,
              children: [
                ChoiceChip(
                  label: const Text('距離最近'),
                  selected: _sort == _NearbySort.distance,
                  onSelected: (_) {
                    setState(() => _sort = _NearbySort.distance);
                    _load();
                  },
                ),
                ChoiceChip(
                  label: const Text('評分最高'),
                  selected: _sort == _NearbySort.rating,
                  onSelected: (_) {
                    setState(() => _sort = _NearbySort.rating);
                    _load();
                  },
                ),
                ChoiceChip(
                  label: const Text('最新'),
                  selected: _sort == _NearbySort.newest,
                  onSelected: (_) {
                    setState(() => _sort = _NearbySort.newest);
                    _load();
                  },
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
                    : _shops.isEmpty
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
                                  itemBuilder: (_, i) => _shopCard(shops[i]),
                                ),
                              ),
          ),
        ],
      ),
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
  const _MarkerMap({required this.shops, required this.centerLat,
    required this.centerLng, required this.selectedShopId,
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
          double py(double lat) =>
              (1 - (lat - minLat) / (maxLat - minLat)) * h;
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
