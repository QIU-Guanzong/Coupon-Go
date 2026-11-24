import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import '../api_client.dart';
import '../feature_flags.dart';
import '../language.dart';
import '../theme.dart';

enum VisitKind { coupon, shop }

class RecentVisit {
  const RecentVisit(this.kind, this.id, this.name);

  final VisitKind kind;
  final int id;
  final String name;

  Map<String, Object> toJson() => {'kind': kind.name, 'id': id, 'name': name};

  static RecentVisit? fromJson(dynamic data) {
    if (data is! Map) return null;
    final id = data['id'];
    final name = data['name'];
    final kind = switch (data['kind']) {
      'coupon' => VisitKind.coupon,
      'shop' => VisitKind.shop,
      _ => null,
    };
    if (kind == null || id is! int || id <= 0 || name is! String ||
        name.trim().isEmpty || name.length > 160) {
      return null;
    }
    return RecentVisit(kind, id, name);
  }
}

/// This device only. Isolated sessions never share history with real accounts.
class BrowsingHistory {
  static const _trialPrefix = 'coupon_trial_recent_v1_';
  static const _accountPrefix = 'coupon_account_recent_v1_';
  static Future<void> _writes = Future<void>.value();

  static String? keyFor(Object? id) {
    if (id is! int || id <= 0) return null;
    return '${publicTrial ? _trialPrefix : _accountPrefix}$id';
  }

  static List<RecentVisit> _load(SharedPreferences prefs, String key) {
    try {
      final raw = jsonDecode(prefs.getString(key) ?? '[]');
      if (raw is List) {
        return raw.map(RecentVisit.fromJson).whereType<RecentVisit>().take(20).toList();
      }
    } on FormatException {
      // A damaged local cache doesn't prevent browsing.
    }
    return [];
  }

  static Future<List<RecentVisit>> read(String key) async {
    await _writes;
    return _load(await SharedPreferences.getInstance(), key);
  }

  static Future<void> remember(String key, RecentVisit visit) {
    final operation = _writes.then((_) async {
      final prefs = await SharedPreferences.getInstance();
      final old = _load(prefs, key);
      final retained = <RecentVisit>[
        visit,
        ...old.where((row) => row.kind != visit.kind || row.id != visit.id)
      ].take(20);
      await prefs.setString(key, jsonEncode(retained.map((row) => row.toJson()).toList()));
    });
    _writes = operation.then<void>((_) {}, onError: (Object _) {});
    return operation;
  }

  static Future<void> clearTrial() async {
    await _writes;
    final prefs = await SharedPreferences.getInstance();
    for (final key in prefs.getKeys().where((k) => k.startsWith(_trialPrefix)).toList()) {
      await prefs.remove(key);
    }
  }
}

class BrowsingHistoryScreen extends StatefulWidget {
  const BrowsingHistoryScreen({
    super.key, required this.storageKey, required this.api, required this.onOpen,
    this.load = BrowsingHistory.read,
  });

  final String storageKey;
  final ApiClient api;
  final Future<void> Function(RecentVisit) onOpen;
  final Future<List<RecentVisit>> Function(String) load;

  @override
  State<BrowsingHistoryScreen> createState() => _BrowsingHistoryScreenState();
}

class _BrowsingHistoryScreenState extends State<BrowsingHistoryScreen> {
  late Future<List<RecentVisit>> _rows = widget.load(widget.storageKey);
  bool _opening = false;
  late final int _revision;

  void _identityChanged() {
    if (mounted) setState(() {});
  }

  @override
  void initState() {
    super.initState();
    _revision = widget.api.sessionRevision;
    widget.api.addListener(_identityChanged);
  }

  @override
  void dispose() {
    widget.api.removeListener(_identityChanged);
    super.dispose();
  }

  Future<void> _open(RecentVisit visit) async {
    if (_opening || _revision != widget.api.sessionRevision) return;
    setState(() => _opening = true);
    try {
      await widget.onOpen(visit);
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(
          context.tr('暫時無法開啟，請重試。', 'Could not open this item. Try again.'),
        )));
      }
    } finally {
      if (mounted) {
        setState(() {
          _opening = false;
          _rows = widget.load(widget.storageKey);
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(context.tr('瀏覽記錄', 'Browsing history'))),
    body: _revision != widget.api.sessionRevision
        ? Center(child: Text(context.tr('請返回後再開啟。', 'Go back and open this page again.')))
        : FutureBuilder<List<RecentVisit>>(
      future: _rows,
      builder: (context, snapshot) {
        if (snapshot.hasError) {
          return Center(child: Text(context.tr('未能載入記錄。', 'Could not load history.')));
        }
        final rows = snapshot.data;
        if (rows == null) return const Center(child: CircularProgressIndicator());
        if (rows.isEmpty) {
          return Center(child: Text(context.tr('暫時沒有瀏覽記錄。', 'No recently viewed items.')));
        }
        return ListView.builder(
          itemCount: rows.length,
          itemBuilder: (_, index) {
            final row = rows[index];
            return ListTile(
              leading: Icon(row.kind == VisitKind.coupon
                  ? Icons.local_offer_outlined : Icons.storefront_outlined),
              title: Text(row.name),
              trailing: const Icon(Icons.chevron_right),
              onTap: _opening ? null : () => _open(row),
            );
          },
        );
      },
    ),
  );
}

class MyReviewsScreen extends StatefulWidget {
  const MyReviewsScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<MyReviewsScreen> createState() => _MyReviewsScreenState();
}

class _MyReviewsScreenState extends State<MyReviewsScreen> {
  List<Map<String, dynamic>>? _reviews;
  bool _failed = false;
  int _request = 0;

  @override
  void initState() {
    super.initState();
    widget.api.addListener(_load);
    _load();
  }

  @override
  void dispose() {
    widget.api.removeListener(_load);
    super.dispose();
  }

  Future<void> _load() async {
    final request = ++_request, revision = widget.api.sessionRevision;
    setState(() { _reviews = null; _failed = false; });
    if (!widget.api.isLoggedIn) {
      setState(() => _reviews = []);
      return;
    }
    bool current() => mounted && request == _request &&
        widget.api.sessionRevision == revision;
    try {
      final raw = (await widget.api.get('/reviews/mine'))['reviews'];
      if (raw is! List || raw.any((r) => r is! Map<String, dynamic>)) {
        throw const FormatException();
      }
      if (current()) setState(() => _reviews = raw.cast<Map<String, dynamic>>());
    } catch (_) {
      if (current()) setState(() => _failed = true);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(title: Text(context.tr('我的評價', 'My reviews'))),
    body: _failed
        ? Center(child: TextButton(onPressed: _load,
            child: Text(context.tr('未能載入，重試', 'Could not load. Retry'))))
        : _reviews == null
            ? const Center(child: CircularProgressIndicator())
            : _reviews!.isEmpty
                ? Center(child: Text(context.tr('暫時沒有評價。', 'No reviews yet.')))
                : RefreshIndicator(
                    onRefresh: _load,
                    child: ListView.separated(
                      padding: const EdgeInsets.all(CgTheme.pageInset),
                      itemCount: _reviews!.length,
                      separatorBuilder: (_, __) => const Divider(),
                      itemBuilder: (_, index) {
                        final row = _reviews![index];
                        final date = DateTime.tryParse('${row['created_at'] ?? ''}')?.toLocal();
                        final score = row['overall'];
                        final content = row['content'];
                        final reply = row['reply_text'];
                        return ListTile(
                          title: Text(row['shop_name'] is String
                              ? row['shop_name'] as String
                              : context.tr('商店', 'Shop')),
                          subtitle: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              if (score is num && score >= 1 && score <= 5)
                                Text(context.tr('$score / 5 分', '$score / 5')),
                              if (date != null)
                                Text(MaterialLocalizations.of(context).formatShortDate(date)),
                              if (content is String && content.isNotEmpty) Text(content),
                              if (reply is String && reply.isNotEmpty) Text(
                                context.tr('商戶回覆：$reply', 'Merchant reply: $reply'),
                              ),
                            ],
                          ),
                        );
                      },
                    ),
                  ),
  );
}
