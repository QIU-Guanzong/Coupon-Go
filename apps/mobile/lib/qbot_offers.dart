/// A typed reference supplied by the existing Q仔 public catalog API.
/// Never resolve an offer by title or interpret answer text as a route.
enum QBotOfferKind { coupon, package, bargain }

class QBotOffer {
  const QBotOffer(this.kind, this.id, this.name, this.shop);

  final QBotOfferKind kind;
  final int id;
  final String name, shop;

  static List<QBotOffer> parse(Object? value) {
    if (value is! List) return const [];
    final seen = <(QBotOfferKind, int)>{};
    return [
      for (final entry in value.take(8))
        if (_parseOne(entry) case final offer?)
          if (seen.add((offer.kind, offer.id))) offer,
    ];
  }

  static QBotOffer? _parseOne(Object? entry) {
    if (entry is! Map) return null;
    final kind = switch (entry['type']) {
      'coupon' => QBotOfferKind.coupon,
      'package' => QBotOfferKind.package,
      'bargain' => QBotOfferKind.bargain,
      _ => null,
    };
    final id = entry['id'], name = entry['name'], shop = entry['shop'];
    if (kind == null ||
        id is! int ||
        id <= 0 ||
        name is! String ||
        name.trim().isEmpty ||
        shop is! String ||
        shop.trim().isEmpty) {
      return null;
    }
    return QBotOffer(kind, id, name.trim(), shop.trim());
  }
}
