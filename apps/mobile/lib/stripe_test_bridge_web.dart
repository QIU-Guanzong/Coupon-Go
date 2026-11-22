// The test-payment page is served from this same web application origin.
// No payment details or bearer token are placed into the URL.
import 'dart:convert';
import 'package:web/web.dart' as web;

Future<bool> openStripeTestPayment(Map<String, dynamic> details) async {
  try {
    web.window.sessionStorage.setItem('coupon_go_stripe_test', jsonEncode(details));
    web.window.location.assign(Uri.base.resolve('stripe-test-payment.html').toString());
    return true;
  } catch (_) {
    return false;
  }
}
