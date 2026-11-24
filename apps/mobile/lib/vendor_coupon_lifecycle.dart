/// Existing vendor coupon endpoints and the states they acknowledge.
enum VendorCouponAction {
  pause('pause', {'paused'}),
  resume('resume', {'active', 'scheduled'}),
  unlist('unlist', {'draft'}),
  submit('submit', {'pending_review'});

  const VendorCouponAction(this.endpoint, this.confirmedStates);

  final String endpoint;
  final Set<String> confirmedStates;

  static List<VendorCouponAction> forStatus(String status) => switch (status) {
    'active' => const [pause, unlist],
    'paused' => const [resume, unlist],
    'scheduled' => const [unlist],
    'draft' || 'rejected' => const [submit],
    _ => const [],
  };
}
