import 'package:flutter/material.dart';

import '../api_client.dart';
import '../theme.dart';
import '../widgets/widgets.dart';

/// Admin v2 management screens (line F): settlements, reconciliation,
/// invoices, CMS content, roles, review disputes.
///
/// Note: no Flutter SDK is available on this dev machine, so these
/// screens have NOT been through `flutter analyze` or a real-device
/// build. They call the same /api/v3/admin/* endpoints covered by the
/// backend tests in apps/api/tests/test_line_f.py.
class AdminHomeScreen extends StatelessWidget {
  const AdminHomeScreen({super.key, required this.api});

  final ApiClient api;

  @override
  Widget build(BuildContext context) {
    final entries = [
      ('結算單', Icons.receipt_long, '/admin/settlements'),
      ('對賬', Icons.compare_arrows, '/admin/reconciliation'),
      ('發票', Icons.request_quote, '/admin/invoices'),
      ('內容管理', Icons.web, '/admin/content'),
      ('角色與權限', Icons.admin_panel_settings, '/admin/roles'),
      ('評價申訴', Icons.rate_review, '/admin/disputes'),
      // Merge note: line B's v1 dashboard (入駐審核 / 看板) lives at
      // '/admin/v1' — '/admin' itself is this v2 home (one case label only).
      ('入駐審核 / 看板 (v1)', Icons.admin_panel_settings, '/admin/v1'),
    ];
    return Scaffold(
      appBar: AppBar(
        backgroundColor: CgColors.vendorGold,
        foregroundColor: CgColors.textPrimary,
        title: const Text('管理後台'),
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          FutureBuilder<Map<String, dynamic>>(
            future: api.adminOverview(),
            builder: (context, snap) {
              if (snap.hasError) {
                final err = snap.error;
                final msg = err is ApiException ? err.message : '載入失敗';
                return Card(
                  child: Padding(
                    padding: const EdgeInsets.all(16),
                    child: Text('總覽載入失敗：$msg（需要財務 / 審核 / 客服或管理員權限）'),
                  ),
                );
              }
              if (!snap.hasData) {
                return const Center(child: CircularProgressIndicator());
              }
              final data = snap.data!;
              final byStatus =
                  (data['settlements_by_status'] as Map<String, dynamic>?) ?? {};
              return Card(
                child: Padding(
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      const Text('總覽', style: TextStyle(fontWeight: FontWeight.bold)),
                      Text('待審核發票：${data['invoices_pending'] ?? 0}'),
                      Text('未處理差異：${data['discrepancies_open'] ?? 0}'),
                      Text('申訴中評價：${data['reviews_disputed'] ?? 0}'),
                      Text('結算單：${byStatus.entries.map((e) => '${e.key} ${e.value}').join(' · ')}'),
                    ],
                  ),
                ),
              );
            },
          ),
          const SizedBox(height: 16),
          ...entries.map((e) => Card(
                margin: const EdgeInsets.only(top: 8),
                child: ListTile(
                  leading: Icon(e.$2, color: CgColors.amber),
                  title: Text(e.$1),
                  trailing: const Icon(Icons.chevron_right),
                  onTap: () => Navigator.of(context).pushNamed(e.$3),
                ),
              )),
        ],
      ),
    );
  }
}

/// Settlements: generate for a shop + YYYY-MM period, then approve /
/// dispute / resolve / pay. Amounts come from redeemed coupons' face
/// value (see backend); commission_rate is entered per generation.
class SettlementsScreen extends StatefulWidget {
  const SettlementsScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<SettlementsScreen> createState() => _SettlementsScreenState();
}

class _SettlementsScreenState extends State<SettlementsScreen> {
  List<dynamic> _items = [];
  bool _loading = true;
  final _shopCtrl = TextEditingController();
  final _periodCtrl = TextEditingController(
      text: '${DateTime.now().year}-${DateTime.now().month.toString().padLeft(2, '0')}');
  final _rateCtrl = TextEditingController(text: '0.10');

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _shopCtrl.dispose();
    _periodCtrl.dispose();
    _rateCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await widget.api.settlements();
      if (!mounted) return;
      setState(() {
        _items = data['settlements'] as List<dynamic>;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  Future<void> _act(Future<Map<String, dynamic>> Function() call) async {
    try {
      await call();
      if (mounted) showCgToast(context, '已更新');
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('結算單')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            TextField(
              controller: _shopCtrl,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: '商户 ID'),
            ),
            TextField(
              controller: _periodCtrl,
              decoration: const InputDecoration(labelText: '結算週期（YYYY-MM）'),
            ),
            TextField(
              controller: _rateCtrl,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: '佣金比例（0–1）'),
            ),
            const SizedBox(height: 8),
            ElevatedButton(
              onPressed: () async {
                final shopId = int.tryParse(_shopCtrl.text.trim());
                final rate = double.tryParse(_rateCtrl.text.trim());
                if (shopId == null || rate == null) {
                  showCgToast(context, '請填寫正確的商户 ID 與佣金比例');
                  return;
                }
                await _act(() => widget.api.generateSettlement(
                    shopId, _periodCtrl.text.trim(), rate));
              },
              child: const Text('生成結算單'),
            ),
            const SizedBox(height: 16),
            if (_loading)
              const Center(child: CircularProgressIndicator())
            else
              ..._items.map((s) => Card(
                    margin: const EdgeInsets.only(top: 8),
                    child: ListTile(
                      title: Text(
                          '#${s['id']} · ${s['shop_name']} · ${s['period']}'),
                      subtitle: Text(
                          '毛額 ${s['gross_amount']} · 佣金 ${s['commission']} · 淨額 ${s['net_amount']} · ${s['status']}'),
                      trailing: PopupMenuButton<String>(
                        onSelected: (v) {
                          final id = s['id'] as int;
                          switch (v) {
                            case 'approve':
                              _act(() => widget.api.approveSettlement(id));
                            case 'pay':
                              _act(() => widget.api.paySettlement(id));
                            case 'dispute':
                              _act(() => widget.api.disputeSettlement(
                                  id, '金額有疑問，請核對'));
                          }
                        },
                        itemBuilder: (_) => const [
                          PopupMenuItem(value: 'approve', child: Text('確認')),
                          PopupMenuItem(value: 'dispute', child: Text('提出爭議')),
                          PopupMenuItem(value: 'pay', child: Text('付款')),
                        ],
                      ),
                    ),
                  )),
            if (!_loading && _items.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 24),
                child: Text('暫時沒有結算單'),
              ),
          ],
        ),
      ),
    );
  }
}

/// Reconciliation: run a shop + period comparison, then resolve each
/// open discrepancy with a note.
class ReconciliationScreen extends StatefulWidget {
  const ReconciliationScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<ReconciliationScreen> createState() => _ReconciliationScreenState();
}

class _ReconciliationScreenState extends State<ReconciliationScreen> {
  List<dynamic> _reports = [];
  bool _loading = true;
  final _shopCtrl = TextEditingController();
  final _periodCtrl = TextEditingController(
      text: '${DateTime.now().year}-${DateTime.now().month.toString().padLeft(2, '0')}');

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _shopCtrl.dispose();
    _periodCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await widget.api.reconciliationReports();
      if (!mounted) return;
      setState(() {
        _reports = data['reports'] as List<dynamic>;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('對賬')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            TextField(
              controller: _shopCtrl,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(labelText: '商户 ID'),
            ),
            TextField(
              controller: _periodCtrl,
              decoration: const InputDecoration(labelText: '週期（YYYY-MM）'),
            ),
            const SizedBox(height: 8),
            ElevatedButton(
              onPressed: () async {
                final shopId = int.tryParse(_shopCtrl.text.trim());
                if (shopId == null) {
                  showCgToast(context, '請填寫正確的商户 ID');
                  return;
                }
                try {
                  final res = await widget.api.reconciliationRun(
                      shopId, _periodCtrl.text.trim());
                  if (!mounted) return;
                  showCgToast(context, res['message'] as String? ?? '對賬完成');
                  _load();
                } on ApiException catch (e) {
                  if (mounted) showCgToast(context, e.message);
                }
              },
              child: const Text('執行對賬'),
            ),
            const SizedBox(height: 16),
            if (_loading)
              const Center(child: CircularProgressIndicator())
            else
              ..._reports.map((r) => Card(
                    margin: const EdgeInsets.only(top: 8),
                    child: ListTile(
                      title: Text(
                          '#${r['id']} · ${r['shop_name']} · ${r['period']}'),
                      subtitle: Text(
                          '核銷 ${r['redemption_count']} · 結算行 ${r['settlement_line_count']} · 差異 ${r['discrepancy_count']} · ${r['status']}'),
                    ),
                  )),
            if (!_loading && _reports.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 24),
                child: Text('暫時沒有對賬報告'),
              ),
          ],
        ),
      ),
    );
  }
}

/// Invoices: list, request (against a settlement or standalone),
/// approve / reject / issue. Finance / admin only for decisions.
class InvoicesScreen extends StatefulWidget {
  const InvoicesScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<InvoicesScreen> createState() => _InvoicesScreenState();
}

class _InvoicesScreenState extends State<InvoicesScreen> {
  List<dynamic> _items = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await widget.api.invoices();
      if (!mounted) return;
      setState(() {
        _items = data['invoices'] as List<dynamic>;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  Future<void> _act(Future<Map<String, dynamic>> Function() call) async {
    try {
      await call();
      if (mounted) showCgToast(context, '已更新');
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('發票')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_loading)
              const Center(child: CircularProgressIndicator())
            else
              ..._items.map((inv) => Card(
                    margin: const EdgeInsets.only(top: 8),
                    child: ListTile(
                      title: Text('#${inv['id']} · ${inv['title']}'),
                      subtitle: Text(
                          '${inv['shop_name']} · 金額 ${inv['amount']} · ${inv['status']}'),
                      trailing: PopupMenuButton<String>(
                        onSelected: (v) {
                          final id = inv['id'] as int;
                          switch (v) {
                            case 'approve':
                              _act(() => widget.api.approveInvoice(id));
                            case 'reject':
                              _act(() => widget.api
                                  .rejectInvoice(id, '資料不全，請補充'));
                            case 'issue':
                              _act(() => widget.api.issueInvoice(id));
                          }
                        },
                        itemBuilder: (_) => const [
                          PopupMenuItem(value: 'approve', child: Text('批准')),
                          PopupMenuItem(value: 'reject', child: Text('拒絕')),
                          PopupMenuItem(value: 'issue', child: Text('開具')),
                        ],
                      ),
                    ),
                  )),
            if (!_loading && _items.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 24),
                child: Text('暫時沒有發票申請'),
              ),
          ],
        ),
      ),
    );
  }
}

/// CMS content: banners / announcements / categories in three tabs.
class ContentScreen extends StatefulWidget {
  const ContentScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<ContentScreen> createState() => _ContentScreenState();
}

class _ContentScreenState extends State<ContentScreen> {
  List<dynamic> _banners = [];
  List<dynamic> _announcements = [];
  List<dynamic> _categories = [];
  bool _loading = true;
  final _bannerTitleCtrl = TextEditingController();
  final _annTitleCtrl = TextEditingController();
  final _catNameCtrl = TextEditingController();

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _bannerTitleCtrl.dispose();
    _annTitleCtrl.dispose();
    _catNameCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final banners = await widget.api.adminBanners();
      final anns = await widget.api.adminAnnouncements();
      final cats = await widget.api.adminCategories();
      if (!mounted) return;
      setState(() {
        _banners = banners;
        _announcements = anns;
        _categories = cats;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  Future<void> _act(Future<dynamic> Function() call) async {
    try {
      await call();
      if (mounted) showCgToast(context, '已更新');
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return DefaultTabController(
      length: 3,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('內容管理'),
          bottom: const TabBar(tabs: [
            Tab(text: '橫幅'),
            Tab(text: '公告'),
            Tab(text: '分類'),
          ]),
        ),
        body: TabBarView(
          children: [
            _tab(
              _loading,
              TextField(
                controller: _bannerTitleCtrl,
                decoration: const InputDecoration(labelText: '橫幅標題'),
              ),
              ElevatedButton(
                onPressed: () {
                  final title = _bannerTitleCtrl.text.trim();
                  if (title.isEmpty) {
                    showCgToast(context, '請填寫標題');
                    return;
                  }
                  _act(() => widget.api
                      .createBanner({'title': title, 'status': 'draft'}));
                },
                child: const Text('新增橫幅（草稿）'),
              ),
              _banners.map((b) => ListTile(
                    title: Text(b['title'] as String),
                    subtitle: Text('${b['status']} · 排序 ${b['sort_order']}'),
                    trailing: IconButton(
                      icon: const Icon(Icons.archive),
                      tooltip: '下架',
                      onPressed: () =>
                          _act(() => widget.api.deleteBanner(b['id'] as int)),
                    ),
                  )),
            ),
            _tab(
              _loading,
              TextField(
                controller: _annTitleCtrl,
                decoration: const InputDecoration(labelText: '公告標題'),
              ),
              ElevatedButton(
                onPressed: () {
                  final title = _annTitleCtrl.text.trim();
                  if (title.isEmpty) {
                    showCgToast(context, '請填寫標題');
                    return;
                  }
                  _act(() => widget.api.createAnnouncement(
                      {'title': title, 'status': 'draft'}));
                },
                child: const Text('新增公告（草稿）'),
              ),
              _announcements.map((a) => ListTile(
                    title: Text(a['title'] as String),
                    subtitle: Text('${a['status']}'),
                    trailing: IconButton(
                      icon: const Icon(Icons.archive),
                      tooltip: '下架',
                      onPressed: () => _act(() =>
                          widget.api.deleteAnnouncement(a['id'] as int)),
                    ),
                  )),
            ),
            _tab(
              _loading,
              TextField(
                controller: _catNameCtrl,
                decoration: const InputDecoration(labelText: '分類名稱'),
              ),
              ElevatedButton(
                onPressed: () {
                  final name = _catNameCtrl.text.trim();
                  if (name.isEmpty) {
                    showCgToast(context, '請填寫名稱');
                    return;
                  }
                  _act(() =>
                      widget.api.createCategory({'name': name}));
                },
                child: const Text('新增分類'),
              ),
              _categories.map((c) => ListTile(
                    title: Text(c['name'] as String),
                    subtitle: Text(
                        (c['is_active'] as bool) ? '啟用中' : '已停用'),
                    trailing: IconButton(
                      icon: Icon((c['is_active'] as bool)
                          ? Icons.block
                          : Icons.check_circle),
                      tooltip: (c['is_active'] as bool) ? '停用' : '啟用',
                      onPressed: () => _act(() => (c['is_active'] as bool)
                          ? widget.api.deactivateCategory(c['id'] as int)
                          : widget.api.activateCategory(c['id'] as int)),
                    ),
                  )),
            ),
          ],
        ),
      ),
    );
  }

  Widget _tab(bool loading, Widget input, Widget button,
      Iterable<Widget> rows) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        input,
        const SizedBox(height: 8),
        button,
        const SizedBox(height: 16),
        if (loading) const Center(child: CircularProgressIndicator()),
        ...rows,
      ],
    );
  }
}

/// Roles: view the role -> permission map and change a user's role
/// (admin only; the backend blocks self-change and unknown roles).
class RolesScreen extends StatefulWidget {
  const RolesScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<RolesScreen> createState() => _RolesScreenState();
}

class _RolesScreenState extends State<RolesScreen> {
  Map<String, dynamic>? _roles;
  bool _loading = true;
  final _userCtrl = TextEditingController();
  String _role = 'finance';

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _userCtrl.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await widget.api.adminRoles();
      if (!mounted) return;
      setState(() {
        _roles = data;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final perms =
        (_roles?['permissions'] as Map<String, dynamic>?) ?? {};
    return Scaffold(
      appBar: AppBar(title: const Text('角色與權限')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
            controller: _userCtrl,
            keyboardType: TextInputType.number,
            decoration: const InputDecoration(labelText: '用戶 ID'),
          ),
          DropdownButton<String>(
            value: _role,
            isExpanded: true,
            items: const [
              'customer',
              'vendor',
              'merchant-admin',
              'finance',
              'auditor',
              'support',
              'admin',
            ]
                .map((r) => DropdownMenuItem(value: r, child: Text(r)))
                .toList(),
            onChanged: (v) {
              if (v != null) setState(() => _role = v);
            },
          ),
          ElevatedButton(
            onPressed: () async {
              final uid = int.tryParse(_userCtrl.text.trim());
              if (uid == null) {
                showCgToast(context, '請填寫正確的用戶 ID');
                return;
              }
              try {
                await widget.api.changeUserRole(uid, _role);
                if (mounted) showCgToast(context, '角色已更新');
              } on ApiException catch (e) {
                if (mounted) showCgToast(context, e.message);
              }
            },
            child: const Text('更新角色'),
          ),
          const SizedBox(height: 16),
          if (_loading)
            const Center(child: CircularProgressIndicator())
          else
            ...perms.entries.map((e) => Card(
                  margin: const EdgeInsets.only(top: 8),
                  child: ListTile(
                    title: Text(e.key),
                    subtitle: Text(
                        ((e.value as List<dynamic>)).join(', ')),
                  ),
                )),
        ],
      ),
    );
  }
}

/// Review disputes: disputed reviews queue; uphold hides the review,
/// dismiss restores it into the rating aggregate.
class ReviewDisputesScreen extends StatefulWidget {
  const ReviewDisputesScreen({super.key, required this.api});

  final ApiClient api;

  @override
  State<ReviewDisputesScreen> createState() => _ReviewDisputesScreenState();
}

class _ReviewDisputesScreenState extends State<ReviewDisputesScreen> {
  List<dynamic> _items = [];
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await widget.api.reviewDisputes();
      if (!mounted) return;
      setState(() {
        _items = data['reviews'] as List<dynamic>;
        _loading = false;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _loading = false);
      showCgToast(context, e.message);
    }
  }

  Future<void> _resolve(int id, String decision) async {
    try {
      final res = await widget.api.resolveReviewDispute(
          id, decision, decision == 'uphold' ? '申訴成立' : '申訴不成立');
      if (mounted) showCgToast(context, res['message'] as String? ?? '已處理');
      _load();
    } on ApiException catch (e) {
      if (mounted) showCgToast(context, e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('評價申訴')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_loading)
              const Center(child: CircularProgressIndicator())
            else
              ..._items.map((r) => Card(
                    margin: const EdgeInsets.only(top: 8),
                    child: ListTile(
                      title: Text(
                          '#${r['id']} · ${r['content'] ?? ''}'),
                      subtitle: Text(
                          '申訴原因：${r['dispute_reason'] ?? ''} · ${r['taste']}/${r['ambience']}/${r['service']} 分'),
                      trailing: PopupMenuButton<String>(
                        onSelected: (v) =>
                            _resolve(r['id'] as int, v),
                        itemBuilder: (_) => const [
                          PopupMenuItem(
                              value: 'uphold', child: Text('申訴成立（隱藏）')),
                          PopupMenuItem(
                              value: 'dismiss', child: Text('申訴不成立（恢復）')),
                        ],
                      ),
                    ),
                  )),
            if (!_loading && _items.isEmpty)
              const Padding(
                padding: EdgeInsets.only(top: 24),
                child: Text('暫時沒有申訴中的評價'),
              ),
          ],
        ),
      ),
    );
  }
}
