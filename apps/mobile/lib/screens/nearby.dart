import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api_client.dart';
import '../models.dart';
import '../theme.dart';
import '../widgets/widgets.dart';
import 'customer.dart';

/// Nearby shops (line A, report §5.2 / §10 F5).
///
/// List + marker view, Haversine distance sorting (server-side), Google
/// Maps navigation jump. Notes on scope:
/// - The marker view is a schematic position map (dots placed by lat/lng),
///   NOT Google Maps tiles: google_maps_flutter needs native API-key
///   config + a real-device build, neither verified on this machine.
/// - "使用我的位置" uses geolocator; denied / unavailable falls back to
///   the preset centre (中環) and manual presets. No location is sent
///   anywhere except the /nearby-shops request itself.
/// - Distances are straight-line (Haversine), not walking/driving time.
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
  String? _error;
  int? _selectedShopId;
  bool _locating = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await widget.api.nearbyShops(
          lat: _lat, lng: _lng, radiusKm: _radiusKm);
      if (!mounted) return;
      setState(() {
        _shops = ((data['shops'] as List?) ?? [])
            .map((e) => Shop.fromJson(e as Map<String, dynamic>))
            .toList();
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _useMyLocation() async {
    setState(() => _locating = true);
    try {
      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied ||
          permission == LocationPermission.deniedForever) {
        if (mounted) {
          showCgToast(context, '未授權定位，已保留中環為中心；可手動選擇地區');
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
      });
      await _load();
    } catch (_) {
      if (mounted) showCgToast(context, '未能取得定位，已保留原有中心');
    } finally {
      if (mounted) setState(() => _locating = false);
    }
  }

  Future<void> _navigate(Shop shop) async {
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('附近商户')),
      body: Column(
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
            child: Row(
              children: [
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: _locating ? null : _useMyLocation,
                    icon: const Icon(Icons.my_location, size: 18),
                    label: Text(_locating ? '定位中…' : '使用我的位置'),
                  ),
                ),
                const SizedBox(width: 8),
                Expanded(
                  child: OutlinedButton.icon(
                    onPressed: () => showCgToast(
                        context, '定位僅用於列出附近商户，不會上傳或儲存'),
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
                  child: Text('中心：$_place（${_lat.toStringAsFixed(4)}, '
                      '${_lng.toStringAsFixed(4)}）· 直線距離排序'),
                ),
              ],
            ),
          ),
          if (_shops.isNotEmpty)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
              child: _MarkerMap(
                shops: _shops,
                centerLat: _lat,
                centerLng: _lng,
                selectedShopId: _selectedShopId,
                onSelect: (s) => setState(() => _selectedShopId = s.id),
              ),
            ),
          const SizedBox(height: 8),
          Expanded(
            child: _loading
                ? const Padding(
                    padding: EdgeInsets.all(16),
                    child: Column(children: [SkeletonBox(height: 96)]),
                  )
                : _error != null
                    ? Center(child: Text(_error!))
                    : _shops.isEmpty
                        ? const Center(
                            child: Text('此範圍內暫無商户，可擴大範圍或切換地區'))
                        : RefreshIndicator(
                            onRefresh: _load,
                            child: ListView.builder(
                              itemCount: _shops.length,
                              itemBuilder: (_, i) {
                                final s = _shops[i];
                                final selected = s.id == _selectedShopId;
                                return Card(
                                  margin: const EdgeInsets.symmetric(
                                      horizontal: 16, vertical: 6),
                                  color: selected
                                      ? const Color(0xFFFFF3CD)
                                      : null,
                                  child: ListTile(
                                    leading: const Icon(Icons.store,
                                        color: CgColors.primary),
                                    title: Text(s.name),
                                    subtitle: Text(
                                        '${s.areaName} · ${s.categoryName}\n${s.address}'),
                                    isThreeLine: true,
                                    trailing: Column(
                                      mainAxisAlignment:
                                          MainAxisAlignment.center,
                                      crossAxisAlignment:
                                          CrossAxisAlignment.end,
                                      children: [
                                        Text(
                                          s.distanceKm == null
                                              ? ''
                                              : '${s.distanceKm!.toStringAsFixed(2)} 公里',
                                          style: const TextStyle(
                                              fontWeight: FontWeight.bold),
                                        ),
                                        TextButton(
                                          onPressed: () => _navigate(s),
                                          child: const Text('導航'),
                                        ),
                                      ],
                                    ),
                                    onTap: () => _openShop(s),
                                  ),
                                );
                              },
                            ),
                          ),
          ),
        ],
      ),
    );
  }
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
              // centre marker
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
                    child: GestureDetector(
                      onTap: () => onSelect(s),
                      child: Icon(Icons.place,
                          color: s.id == selectedShopId
                              ? CgColors.primary
                              : Colors.red.shade300,
                          size: s.id == selectedShopId ? 30 : 24),
                    ),
                  ),
            ],
          );
        },
      ),
    );
  }
}
