import 'package:flutter/material.dart';

import '../api_client.dart';
import '../theme.dart';

/// Stores and branches the current merchant can actually access.
class MerchantBranchesScreen extends StatefulWidget {
  const MerchantBranchesScreen({
    super.key,
    required this.api,
    this.staffBuilder,
  });
  final ApiClient api;
  final Widget Function(int shopId)? staffBuilder;

  @override
  State<MerchantBranchesScreen> createState() => _MerchantBranchesScreenState();
}

class _MerchantBranchesScreenState extends State<MerchantBranchesScreen> {
  List<Map<String, dynamic>> _shops = [];
  List<Map<String, dynamic>> _branches = [];
  int? _shopId;
  bool _loading = true;
  bool _busy = false;
  String? _error;
  late int _session;
  int _loadRevision = 0;

  Map<String, dynamic>? get _shop {
    for (final shop in _shops) {
      if (shop['id'] == _shopId) return shop;
    }
    return null;
  }

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    _loadShops();
  }

  void _sessionChanged() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    _loadRevision++;
    setState(() {
      _shops = [];
      _branches = [];
      _shopId = null;
      _busy = false;
      _loading = true;
      _error = null;
    });
    _loadShops();
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    super.dispose();
  }

  List<Map<String, dynamic>> _rows(Object? value) => value is List
      ? value
          .whereType<Map>()
          .map((row) => Map<String, dynamic>.from(row))
          .toList()
      : [];

  Future<void> _loadShops() async {
    final session = _session;
    final revision = ++_loadRevision;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await widget.api.get('/merchant/shops');
      if (!mounted || session != _session || revision != _loadRevision) return;
      final shops = _rows(data['shops']).where((s) => s['id'] is int).toList();
      final current = shops.any((s) => s['id'] == _shopId)
          ? _shopId
          : (shops.isEmpty ? null : shops.first['id'] as int);
      setState(() {
        _shops = shops;
        _shopId = current;
        _branches = [];
      });
      if (current == null) {
        setState(() => _loading = false);
      } else {
        await _loadBranches(current);
      }
    } catch (error) {
      if (mounted && session == _session && revision == _loadRevision) {
        setState(() {
          _loading = false;
          _error = _message(error);
        });
      }
    }
  }

  Future<void> _loadBranches(int shopId) async {
    final session = _session;
    final revision = ++_loadRevision;
    setState(() {
      _loading = true;
      _error = null;
      _branches = [];
    });
    try {
      final data = await widget.api.get('/merchant/shops/$shopId/branches');
      if (!mounted ||
          session != _session ||
          revision != _loadRevision ||
          shopId != _shopId) {
        return;
      }
      setState(() {
        _branches = _rows(data['branches']);
        _loading = false;
      });
    } catch (error) {
      if (mounted &&
          session == _session &&
          revision == _loadRevision &&
          shopId == _shopId) {
        setState(() {
          _loading = false;
          _error = _message(error);
        });
      }
    }
  }

  String _message(Object error) =>
      error is ApiException ? error.message : '連線未完成，請重試。';

  Future<void> _edit([Map<String, dynamic>? branch]) async {
    final shopId = _shopId;
    final session = _session;
    if (shopId == null || _shop?['can_manage_branches'] != true || _busy) {
      return;
    }
    final values = await showDialog<Map<String, String>>(
      context: context,
      builder: (_) => _BranchEditor(branch: branch),
    );
    if (!mounted ||
        session != _session ||
        shopId != _shopId ||
        values == null ||
        _busy) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      if (branch == null) {
        await widget.api.post('/merchant/shops/$shopId/branches', body: values);
      } else {
        await widget.api.post(
          '/update-branch-profile',
          query: {'branch_id': '${branch['id']}', ...values},
        );
      }
      if (!mounted || session != _session || shopId != _shopId) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(branch == null ? '分店已建立' : '分店資料已更新')),
      );
      await _loadBranches(shopId);
    } catch (error) {
      if (mounted && session == _session && shopId == _shopId) {
        setState(() => _error = '${_message(error)} 如結果未確認，請先重新載入核對。');
      }
    } finally {
      if (mounted && session == _session) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          backgroundColor: CgColors.vendorGold,
          title: const Text('店舖與分店'),
        ),
        body: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 720),
            child: RefreshIndicator(
              onRefresh: _loadShops,
              child: ListView(
                physics: const AlwaysScrollableScrollPhysics(),
                padding: const EdgeInsets.all(20),
                children: [
                  const Text('選擇店舖，查看或更新其分店資料。'),
                  if (_shops.isNotEmpty) ...[
                    const SizedBox(height: 16),
                    DropdownButtonFormField<int>(
                      initialValue: _shopId,
                      decoration: const InputDecoration(
                        labelText: '店舖',
                        border: OutlineInputBorder(),
                      ),
                      items: _shops
                          .map(
                            (s) => DropdownMenuItem<int>(
                              value: s['id'] as int,
                              child: Text('${s['name']}'),
                            ),
                          )
                          .toList(),
                      onChanged: _loading || _busy
                          ? null
                          : (value) {
                              if (value != null && value != _shopId) {
                                setState(() => _shopId = value);
                                _loadBranches(value);
                              }
                            },
                    ),
                  ],
                  if (_error != null) ...[
                    const SizedBox(height: 16),
                    Text(
                      _error!,
                      style:
                          TextStyle(color: Theme.of(context).colorScheme.error),
                    ),
                    TextButton(
                      onPressed: _busy ? null : _loadShops,
                      child: const Text('重新載入'),
                    ),
                  ],
                  if (_loading)
                    const Padding(
                      padding: EdgeInsets.all(24),
                      child: Center(child: CircularProgressIndicator()),
                    ),
                  if (!_loading && _error == null && _shops.isEmpty)
                    const Padding(
                      padding: EdgeInsets.only(top: 24),
                      child: Text('目前沒有可管理的店舖。'),
                    ),
                  if (!_loading && _shopId != null) ...[
                    const SizedBox(height: 20),
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            '分店',
                            style: Theme.of(context).textTheme.titleLarge,
                          ),
                        ),
                        if (_shop?['can_manage_branches'] == true)
                          FilledButton.icon(
                            onPressed: _busy ? null : () => _edit(),
                            icon: const Icon(Icons.add),
                            label: const Text('新增分店'),
                          ),
                      ],
                    ),
                    if (_branches.isEmpty && _error == null)
                      const Padding(
                        padding: EdgeInsets.symmetric(vertical: 18),
                        child: Text('尚未新增分店。'),
                      ),
                    for (final branch in _branches)
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text('${branch['name'] ?? ''}'),
                        subtitle: Text(
                          [
                            branch['address'],
                            branch['phone'],
                          ]
                              .where((v) => v is String && v.isNotEmpty)
                              .join(' · '),
                        ),
                        trailing: _shop?['can_manage_branches'] == true
                            ? TextButton(
                                onPressed: _busy ? null : () => _edit(branch),
                                child: const Text('編輯'),
                              )
                            : null,
                      ),
                    if (_shop?['can_manage_branches'] != true)
                      const Text('你可以查看分店；如需修改，請聯絡店主。'),
                    if (_shop?['can_manage_members'] == true &&
                        widget.staffBuilder != null) ...[
                      const SizedBox(height: 20),
                      OutlinedButton.icon(
                        onPressed: _busy
                            ? null
                            : () => Navigator.of(context).push(
                                  MaterialPageRoute(
                                    builder: (_) =>
                                        widget.staffBuilder!(_shopId!),
                                  ),
                                ),
                        icon: const Icon(Icons.people_outline),
                        label: const Text('員工與角色'),
                      ),
                    ],
                  ],
                ],
              ),
            ),
          ),
        ),
      );
}

class _BranchEditor extends StatefulWidget {
  const _BranchEditor({this.branch});
  final Map<String, dynamic>? branch;
  @override
  State<_BranchEditor> createState() => _BranchEditorState();
}

class _BranchEditorState extends State<_BranchEditor> {
  final _form = GlobalKey<FormState>();
  late final TextEditingController _name, _address, _phone;
  @override
  void initState() {
    super.initState();
    _name = TextEditingController(
      text: widget.branch?['name'] as String? ?? '',
    );
    _address = TextEditingController(
      text: widget.branch?['address'] as String? ?? '',
    );
    _phone = TextEditingController(
      text: widget.branch?['phone'] as String? ?? '',
    );
  }

  @override
  void dispose() {
    _name.dispose();
    _address.dispose();
    _phone.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: Text(widget.branch == null ? '新增分店' : '編輯分店'),
        content: SingleChildScrollView(
          child: Form(
            key: _form,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextFormField(
                  controller: _name,
                  decoration: const InputDecoration(labelText: '分店名稱（必填）'),
                  validator: (value) =>
                      (value?.trim().isEmpty ?? true) ? '請輸入分店名稱' : null,
                ),
                TextFormField(
                  controller: _address,
                  decoration: const InputDecoration(labelText: '地址'),
                ),
                TextFormField(
                  controller: _phone,
                  keyboardType: TextInputType.phone,
                  decoration: const InputDecoration(labelText: '電話'),
                ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: () {
              if (_form.currentState!.validate()) {
                Navigator.pop(context, {
                  'name': _name.text.trim(),
                  'address': _address.text.trim(),
                  'phone': _phone.text.trim(),
                });
              }
            },
            child: const Text('儲存'),
          ),
        ],
      );
}
