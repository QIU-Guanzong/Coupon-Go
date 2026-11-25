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

  /// Coupon workspace and package workspace share this menu policy.
  static bool canEditListing(String status) => const {
        'draft',
        'rejected',
        'pending_review',
        'active',
        'paused',
        'scheduled',
      }.contains(status);

  static List<VendorCouponAction> forManagementStatus(String status) =>
      switch (status) {
        'pending_review' => const [unlist],
        'rejected' => const [unlist, submit],
        _ => forStatus(status),
      };
}
