import 'package:flutter/material.dart';
import '../api_client.dart';
import '../language.dart';
import '../models.dart';
import '../widgets/widgets.dart';
import '../widgets/favorite_control.dart';

/// Keeps one request alive across rebuilds. A changed filter supplies a new key.
class RequestView<T> extends StatefulWidget {
  const RequestView({super.key, required this.load, required this.content});
  final Future<T> Function() load;
  final Widget Function(T) content;
  @override
  State<RequestView<T>> createState() => _RequestViewState<T>();
}

class _RequestViewState<T> extends State<RequestView<T>> {
  late Future<T> request = widget.load();
  @override
  Widget build(BuildContext context) => FutureBuilder<T>(
        future: request,
        builder: (c, result) {
          if (result.connectionState != ConnectionState.done) {
            return const Padding(
                padding: EdgeInsets.all(16), child: SkeletonBox(height: 88));
          }
          if (result.hasError) {
            return Center(
                child: Column(mainAxisSize: MainAxisSize.min, children: [
              Text(c.tr('未能載入資料', 'Could not load data')),
              TextButton(
                  onPressed: () => setState(() {
                        request = widget.load();
                      }),
                  child: Text(c.tr('重試', 'Retry'))),
            ]));
          }
          return widget.content(result.requireData);
        },
      );
}

class DiscoveryScreen extends StatefulWidget {
  const DiscoveryScreen(
      {super.key,
      required this.api,
      required this.onOpenSearch,
      required this.onOpenCoupon});
  final ApiClient api;
  final VoidCallback onOpenSearch;
  final ValueChanged<Coupon> onOpenCoupon;
  @override
  State<DiscoveryScreen> createState() => _DiscoveryScreenState();
}

class _DiscoveryScreenState extends State<DiscoveryScreen> {
  String? area, draftArea;
  int? category, draftCategory;
  bool? regionPanel;

  void open(bool region) => setState(() {
        draftArea = area;
        draftCategory = category;
        regionPanel = regionPanel == region ? null : region;
      });

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    return Scaffold(
      appBar: AppBar(
          title: OutlinedButton.icon(
              style: OutlinedButton.styleFrom(backgroundColor: Colors.white),
              onPressed: widget.onOpenSearch,
              icon: const Icon(Icons.search),
              label: Text(t('搜尋商戶或優惠', 'Search stores and offers')))),
      body: Column(children: [
        Row(mainAxisAlignment: MainAxisAlignment.spaceEvenly, children: [
          TextButton(
              onPressed: () => open(true), child: Text(t('地區', 'Region'))),
          TextButton(
              onPressed: () => open(false), child: Text(t('分類', 'Category'))),
          TextButton(
              onPressed: () => Navigator.pushNamed(context, '/nearby'),
              child: Text(t('附近', 'Nearby'))),
        ]),
        if (regionPanel != null) ...[
          Expanded(
              child: regionPanel!
                  ? ListView(children: [
                      for (final entry in <String?, String>{
                        null: t('不限', 'No Limit'),
                        '香港': t('香港', 'Hong Kong'),
                        '九龍': t('九龍', 'Kowloon'),
                        '新界': t('新界', 'New Territories')
                      }.entries)
                        ListTile(
                            title: Text(entry.value),
                            selected: draftArea == entry.key,
                            trailing: draftArea == entry.key
                                ? const Icon(Icons.check)
                                : null,
                            onTap: () => setState(() => draftArea = entry.key)),
                    ])
                  : RequestView(
                      load: () => widget.api.get('/new-filter-details'),
                      content: (data) => ListView(children: [
                            for (final row in [
                              {'id': null, 'name': t('不限', 'No Limit')},
                              ...data['categories'] as List
                            ])
                              ListTile(
                                  title: Text(row['name']),
                                  selected: row['id'] == draftCategory,
                                  trailing: row['id'] == draftCategory
                                      ? const Icon(Icons.check)
                                      : null,
                                  onTap: () => setState(
                                      () => draftCategory = row['id'])),
                          ]))),
          SafeArea(
              child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceEvenly,
                  children: [
                TextButton(
                    onPressed: () => setState(() => regionPanel = null),
                    child: Text(t('取消', 'Cancel'))),
                FilledButton(
                    onPressed: () => setState(() {
                          area = draftArea;
                          category = draftCategory;
                          regionPanel = null;
                        }),
                    child: Text(t('確定', 'Confirm'))),
              ])),
        ] else
          Expanded(
              child: CouponResults(
                  key: ValueKey((area, category)),
                  api: widget.api,
                  load: () => widget.api
                      .classification(categoryId: category, area: area),
                  onOpenCoupon: widget.onOpenCoupon)),
      ]),
    );
  }
}

class CouponResults extends StatelessWidget {
  const CouponResults(
      {super.key, required this.api, required this.load, required this.onOpenCoupon});
  final ApiClient api;
  final Future<List<dynamic>> Function() load;
  final ValueChanged<Coupon> onOpenCoupon;
  @override
  Widget build(BuildContext context) => RequestView(
      load: load,
      content: (rows) {
        if (rows.isEmpty) {
          return Center(
              child: Text(context.tr('找不到優惠券！', 'Coupon Not Found!')));
        }
        return GridView.builder(
            padding: const EdgeInsets.all(12),
            itemCount: rows.length,
            gridDelegate: const SliverGridDelegateWithFixedCrossAxisCount(
                crossAxisCount: 2,
                childAspectRatio: .78,
                mainAxisSpacing: 8,
                crossAxisSpacing: 8),
            itemBuilder: (c, i) {
              final coupon = Coupon.fromJson(rows[i]);
              return FavoriteControl(
                key: ValueKey(coupon.id),
                api: api,
                id: coupon.id,
                initial: coupon.isFavorite,
                builder: (favorite, onFavorite) => CouponCard(
                  coupon: coupon,
                  isFavorite: favorite,
                  onFavorite: onFavorite,
                  onTap: () => onOpenCoupon(coupon),
                ),
              );
            });
      });
}

class BranchResults extends StatelessWidget {
  const BranchResults(
      {super.key,
      required this.api,
      required this.shopId,
      required this.openShop,
      required this.keyword});
  final ApiClient api;
  final int shopId;
  final VoidCallback openShop;
  final String keyword;
  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: Text(context.tr('分店', 'Branches'))),
        body: RequestView(
            load: () =>
                api.get('/get-nearest-branchs', query: {'shop_id': '$shopId'}),
            content: (data) => ListView(children: [
                  ListTile(title: Text(keyword)),
                  if ((data['branches'] as List).isEmpty)
                    Center(
                        child: Text(context.tr('找不到分店', 'No branches found'))),
                  for (final branch in data['branches'])
                    ListTile(
                        title: Text(branch['branch_name']),
                        subtitle: Text(branch['address'] ?? ''),
                        trailing: const Icon(Icons.chevron_right),
                        onTap: () => Navigator.push(
                            context,
                            MaterialPageRoute(
                                builder: (c) => Scaffold(
                                      appBar: AppBar(
                                          title: Text(branch['branch_name'])),
                                      body: RequestView(
                                          load: () => api.get('/branch-details',
                                                  query: {
                                                    'branch_id':
                                                        "${branch['branch_id']}"
                                                  }),
                                          content: (details) => ListView(children: [
                                            ListTile(title: Text(details['address'] ?? ''),
                                                subtitle: Text(details['phone'] ?? '')),
                                            ListTile(leading: const Icon(Icons.storefront),
                                                title: Text(c.tr('商戶詳情及優惠', 'Merchant details and offers')),
                                                trailing: const Icon(Icons.chevron_right),
                                                onTap: openShop),
                                          ])),
                                    )))),
                ])),
      );
}
