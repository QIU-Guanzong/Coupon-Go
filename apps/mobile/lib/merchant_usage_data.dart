/// Read-only DTO for the settlement-scoped, UTC-dated shop dashboard.
class UsageCounts {
  const UsageCounts(this.claims, this.redemptions);

  static UsageCounts parse(Object? raw) {
    if (raw is! Map) throw const FormatException();
    final claims = raw['claims'];
    final redemptions = raw['redemptions'];
    if (claims is! int ||
        claims < 0 ||
        redemptions is! int ||
        redemptions < 0) {
      throw const FormatException();
    }
    return UsageCounts(claims, redemptions);
  }

  final int claims;
  final int redemptions;
}

class ShopUsage {
  const ShopUsage(this.id, this.name, this.counts);

  final int id;
  final String name;
  final UsageCounts counts;
}

class MerchantUsageData {
  const MerchantUsageData(this.from, this.to, this.totals, this.shops);

  final String from;
  final String to;
  final UsageCounts totals;
  final List<ShopUsage> shops;

  static DateTime? _date(Object? value) {
    if (value is! String || !RegExp(r'^\d{4}-\d{2}-\d{2}$').hasMatch(value)) {
      return null;
    }
    final date = DateTime.tryParse('${value}T00:00:00Z');
    return date != null && date.toIso8601String().substring(0, 10) == value
        ? date
        : null;
  }

  static MerchantUsageData parse(Map<String, dynamic> json, int days) {
    final period = json['period'];
    final rows = json['shops'];
    if (period is! Map || period['days'] != days || rows is! List) {
      throw const FormatException();
    }
    final from = _date(period['from']);
    final to = _date(period['to']);
    if (from == null ||
        to == null ||
        (rows.isNotEmpty && to.difference(from).inDays != days - 1) ||
        (rows.isEmpty && to.isBefore(from))) {
      throw const FormatException();
    }
    final seen = <int>{};
    final shops = <ShopUsage>[];
    for (final row in rows) {
      if (row is! Map) throw const FormatException();
      final id = row['shop_id'];
      final name = row['shop_name'];
      if (id is! int ||
          id <= 0 ||
          !seen.add(id) ||
          name is! String ||
          name.trim().isEmpty) {
        throw const FormatException();
      }
      shops.add(ShopUsage(id, name, UsageCounts.parse(row['totals'])));
    }
    return MerchantUsageData(
      period['from'] as String,
      period['to'] as String,
      UsageCounts.parse(json['totals']),
      shops,
    );
  }

  static String _cell(String value, {bool untrusted = false}) {
    final formula = RegExp(r'^[=+\-@\t\r\n＝＋－＠]');
    final safe =
        untrusted &&
            (formula.hasMatch(value) || formula.hasMatch(value.trimLeft()))
        ? "'$value"
        : value;
    return '"${safe.replaceAll('"', '""')}"';
  }

  String csv(List<String> fields) {
    final lines = [fields.map(_cell).join(',')];
    for (final shop in shops) {
      lines.add(
        [
          _cell('${shop.id}'),
          _cell(shop.name, untrusted: true),
          _cell(from),
          _cell(to),
          _cell('${shop.counts.claims}'),
          _cell('${shop.counts.redemptions}'),
        ].join(','),
      );
    }
    return '${lines.join('\r\n')}\r\n';
  }
}
