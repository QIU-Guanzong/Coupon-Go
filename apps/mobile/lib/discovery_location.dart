import 'package:geolocator/geolocator.dart';

/// Ask only from a location control. Approximate to about 100 m before
/// sending to Coupon GO, and keep the result in this screen's session.
Future<({double lat, double lng})?> findDiscoveryPosition() async {
  final permission = await Geolocator.checkPermission();
  if (permission == LocationPermission.deniedForever) return null;
  if (permission == LocationPermission.denied) {
    final granted = await Geolocator.requestPermission();
    if (granted == LocationPermission.denied ||
        granted == LocationPermission.deniedForever) {
      return null;
    }
  }
  final position = await Geolocator.getCurrentPosition(
    locationSettings: const LocationSettings(
      accuracy: LocationAccuracy.medium,
      timeLimit: Duration(seconds: 8),
    ),
  );
  return (
    lat: (position.latitude * 1000).round() / 1000,
    lng: (position.longitude * 1000).round() / 1000,
  );
}
