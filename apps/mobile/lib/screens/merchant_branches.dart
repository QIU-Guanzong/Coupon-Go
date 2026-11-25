import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';

const _trialMode = bool.fromEnvironment('PUBLIC_TRIAL');

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
  bool _requiresRefresh = false;
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
      _requiresRefresh = false;
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
        _requiresRefresh = false;
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

  bool get _canEditShop => !_trialMode && _shop?['is_owner'] == true;

  Future<void> _editShop() async {
    final api = widget.api;
    final shopId = _shopId;
    final session = _session;
    if (shopId == null || !_canEditShop || _busy || _requiresRefresh) return;
    bool current() =>
        mounted &&
        identical(widget.api, api) &&
        _session == session &&
        api.sessionRevision == session &&
        _shopId == shopId &&
        _canEditShop;
    final path = '/merchant/shops/$shopId';
    final loadFailure = context.tr('未能讀取店舖資料，請重試。',
        'Could not load shop details. Try again.');
    final saveFailure = context.tr('未能確認儲存結果，請重新載入核對。',
        'Could not confirm the save. Reload to check the shop details.');
    final invalidDetails = context.tr('請核對店舖資料後再儲存。',
        'Check the shop details and try again.');
    final denied = context.tr('你沒有權限修改此店舖。',
        'You do not have permission to edit this shop.');
    final saved = context.tr('店舖資料已更新。', 'Shop details updated.');
    setState(() {
      _busy = true;
      _error = null;
    });
    var saving = false;
    try {
      final detail = await api.get(path);
      if (!current()) return;
      final raw = detail['shop'];
      if (raw is! Map || raw['id'] != shopId || raw['shop_name'] is! String) {
        throw const FormatException();
      }
      final shop = Map<String, dynamic>.from(raw);
      Map<String, Object> draft = {};
      String? editorError;
      while (current()) {
        if (!mounted) return;
        final changes = await showDialog<Map<String, Object>>(
          context: context,
          builder: (_) => _ShopTextEditor(
            api: api,
            session: session,
            shop: shop,
            draft: draft,
            error: editorError,
          ),
        );
        if (!current() || changes == null || changes.isEmpty) return;
        saving = true;
        try {
          final updated = await api.patch(path, body: changes);
          if (!current()) return;
          final confirmed = updated['shop'];
          if (confirmed is! Map || confirmed['id'] != shopId ||
              confirmed['shop_name'] is! String ||
              (changes.containsKey('category_id') &&
                  confirmed['category_id'] != changes['category_id'])) {
            throw const FormatException();
          }
        } on ApiException catch (error) {
          if (!current()) return;
          if (error.status == 400 || error.status == 422) {
            draft = changes;
            editorError = invalidDetails;
            continue;
          }
          if (error.status == 403) {
            setState(() => _error = denied);
            return;
          }
          rethrow;
        }
        if (!mounted || !current()) return;
        setState(() => _requiresRefresh = true);
        ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(saved)));
        await _loadShops();
        return;
      }
    } catch (_) {
      if (current()) {
        setState(() {
          _requiresRefresh = saving;
          _error = saving ? saveFailure : loadFailure;
        });
      }
    } finally {
      if (mounted && identical(widget.api, api) && session == _session) {
        setState(() => _busy = false);
      }
    }
  }

  Future<void> _edit([Map<String, dynamic>? branch]) async {
    final shopId = _shopId;
    final session = _session;
    if (shopId == null ||
        _shop?['can_manage_branches'] != true ||
        _trialMode ||
        _busy ||
        _requiresRefresh) {
      return;
    }
    Map<String, String>? draft;
    String? editorError;
    while (session == _session && shopId == _shopId) {
      if (!mounted) return;
      final values = await showDialog<Map<String, String>>(
        context: context,
        builder: (_) => _BranchEditor(
            branch: branch, draft: draft, error: editorError),
      );
      if (!mounted || session != _session || shopId != _shopId ||
          values == null || _busy) {
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
        setState(() => _requiresRefresh = true);
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(branch == null ? '分店已建立' : '分店資料已更新')),
        );
        await _loadBranches(shopId);
        return;
      } catch (error) {
        if (mounted && session == _session && shopId == _shopId) {
          // A validation or authorization response explicitly rejects the
          // operation. Keep the draft so it can be corrected or copied.
          if (error is ApiException &&
              (error.status == 400 || error.status == 403 || error.status == 422)) {
            draft = values;
            editorError = error.message;
            continue;
          }
          setState(() {
            _requiresRefresh = true;
            _error = '${_message(error)} 如結果未確認，請先重新載入核對。';
          });
        }
        return;
      } finally {
        if (mounted && session == _session) setState(() => _busy = false);
      }
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(
          title: Text(context.tr('店舖與分店', 'Shops and branches')),
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
                  Text(_trialMode
                      ? context.tr('選擇店舖，查看其分店資料。', 'Choose a shop to view its branches.')
                      : context.tr('選擇店舖，查看或更新其分店資料。', 'Choose a shop to view or update its branches.')),
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
                      child: Text(context.tr('重新載入', 'Reload')),
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
                    if (_canEditShop) ...[
                      const SizedBox(height: 16),
                      OutlinedButton.icon(
                        key: const ValueKey('edit-shop-details'),
                        onPressed: _busy || _requiresRefresh ? null : _editShop,
                        icon: const Icon(Icons.edit_outlined),
                        label: Text(context.tr('編輯店舖資料', 'Edit shop details')),
                      ),
                    ],
                    const SizedBox(height: 20),
                    Row(
                      children: [
                        Expanded(
                          child: Text(
                            '分店',
                            style: Theme.of(context).textTheme.titleLarge,
                          ),
                        ),
                        if (!_trialMode && _shop?['can_manage_branches'] == true)
                          FilledButton.icon(
                            onPressed: _busy || _requiresRefresh
                                ? null
                                : () => _edit(),
                            icon: const Icon(Icons.add),
                            label: Text(context.tr('新增分店', 'Add branch')),
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
                        trailing: !_trialMode && _shop?['can_manage_branches'] == true
                            ? TextButton(
                                onPressed: _busy || _requiresRefresh
                                    ? null
                                    : () => _edit(branch),
                                child: Text(context.tr('編輯', 'Edit')),
                              )
                            : null,
                      ),
                    if (_shop?['can_manage_branches'] != true)
                      Text(context.tr('你可以查看分店；如需修改，請聯絡店主。', 'Contact the shop owner to edit branch details.')),
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
                        label: Text(context.tr('員工與角色', 'Staff and roles')),
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
  const _BranchEditor({this.branch, this.draft, this.error});
  final Map<String, dynamic>? branch;
  final Map<String, String>? draft;
  final String? error;
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
      text: widget.draft?['name'] ?? widget.branch?['name'] as String? ?? '',
    );
    _address = TextEditingController(
      text: widget.draft?['address'] ?? widget.branch?['address'] as String? ?? '',
    );
    _phone = TextEditingController(
      text: widget.draft?['phone'] ?? widget.branch?['phone'] as String? ?? '',
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
                if (widget.error != null) ...[
                  Text(widget.error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                  const SizedBox(height: 12),
                ],
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
            child: Text(context.tr('取消', 'Cancel')),
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
            child: Text(context.tr('儲存', 'Save')),
          ),
        ],
      );
}


class _ShopCategory {
  const _ShopCategory(this.id, this.name, this.nameEn);

  final int id;
  final String name;
  final String nameEn;
}

class _ShopTextEditor extends StatefulWidget {
  const _ShopTextEditor({
    required this.api,
    required this.session,
    required this.shop,
    required this.draft,
    required this.error,
  });

  final ApiClient api;
  final int session;
  final Map<String, dynamic> shop;
  final Map<String, Object> draft;
  final String? error;

  @override
  State<_ShopTextEditor> createState() => _ShopTextEditorState();
}

class _ShopTextEditorState extends State<_ShopTextEditor> {
  static const fields = [
    ('name', 'shop_name', '店舖名稱', 'Shop name'),
    ('name_en', 'name_en', '英文名稱', 'English name'),
    ('address', 'address', '地址', 'Address'),
    ('phone', 'phone', '電話', 'Phone'),
    ('description', 'description', '簡介', 'Description'),
    ('announcement', 'announcement', '公告', 'Announcement'),
  ];

  final _form = GlobalKey<FormState>();
  late final Map<String, String> _original;
  late final Map<String, TextEditingController> _controllers;
  List<_ShopCategory>? _categories;
  int? _category;

  int? get _originalCategory {
    final value = widget.shop['category_id'];
    return value is int && value > 0 ? value : null;
  }

  Future<List<_ShopCategory>> _loadCategories() async {
    if (widget.api.sessionRevision != widget.session) return [];
    final data = await widget.api.get('/get-category-list');
    final rows = data['categories'];
    if (rows is! List) throw const FormatException('Invalid categories');
    final categories = <_ShopCategory>[];
    final ids = <int>{};
    for (final row in rows) {
      if (row is! Map) continue;
      final id = row['id'];
      final name = row['name'];
      if (id is! int || id <= 0 || name is! String ||
          name.trim().isEmpty || !ids.add(id)) {
        throw const FormatException('Invalid category');
      }
      final english = row['name_en'];
      categories.add(_ShopCategory(id, name,
          english is String && english.trim().isNotEmpty ? english : name));
    }
    return categories;
  }

  @override
  void initState() {
    super.initState();
    _original = {
      for (final (key, readKey, _, _) in fields)
        key: widget.shop[readKey] is String ? widget.shop[readKey] as String : '',
    };
    _controllers = {
      for (final key in _original.keys)
        key: TextEditingController(
          text: (widget.draft[key] as String?) ?? _original[key],
        ),
    };
    _category = (widget.draft['category_id'] as int?) ?? _originalCategory;
    widget.api.addListener(_checkSession);
    _checkSession();
    _loadCategories().then(_setCategoryOptions,
        onError: (Object _) => _setCategoryOptions(const []));
  }

  void _setCategoryOptions(List<_ShopCategory> categories) {
    if (!mounted || widget.api.sessionRevision != widget.session) return;
    setState(() => _categories = categories);
  }

  void _checkSession() {
    if (widget.api.sessionRevision == widget.session) return;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && ModalRoute.of(context)?.isCurrent == true) {
        Navigator.of(context).pop();
      }
    });
  }

  @override
  void dispose() {
    widget.api.removeListener(_checkSession);
    for (final controller in _controllers.values) {
      controller.dispose();
    }
    super.dispose();
  }

  String _value(String key) =>
      key == 'name' ? _controllers[key]!.text.trim() : _controllers[key]!.text;

  Map<String, Object> get _changed => {
        for (final key in _original.keys)
          if (_value(key) != _original[key]) key: _value(key),
        if (_categoryVisible && _category != _originalCategory)
          'category_id': _category!,
      };

  bool get _categoryVisible =>
      _categories?.any((option) => option.id == _category) == true;

  Widget _categoryField(BuildContext context) {
    final categories = _categories ?? const <_ShopCategory>[];
    final name = widget.shop['category_name'];
    final hint = name is String && name.trim().isNotEmpty
        ? name : context.tr('選擇分類', 'Choose a category');
    return Padding(
      padding: const EdgeInsets.only(top: 12),
      child: InputDecorator(
        decoration: InputDecoration(labelText: context.tr('分類', 'Category')),
        child: DropdownButtonHideUnderline(
          child: DropdownButton<int>(
            key: const ValueKey('edit-shop-category'),
            isExpanded: true,
            value: _categoryVisible ? _category : null,
            hint: Text(hint, overflow: TextOverflow.ellipsis),
            disabledHint: Text(
              _categories == null
                  ? context.tr('正在載入分類', 'Loading categories')
                  : context.tr('未能載入分類', 'Categories unavailable'),
            ),
            items: [
              for (final category in categories)
                DropdownMenuItem(
                  value: category.id,
                  child: Text(context.tr(category.name, category.nameEn),
                      overflow: TextOverflow.ellipsis),
                ),
            ],
            onChanged: categories.isEmpty ? null : (selected) {
              if (selected != null) setState(() => _category = selected);
            },
          ),
        ),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: Text(context.tr('編輯店舖資料', 'Edit shop details')),
        scrollable: true,
        content: Form(
          key: _form,
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              if (widget.error != null)
                Text(widget.error!,
                    style: TextStyle(color: Theme.of(context).colorScheme.error)),
              _categoryField(context),
              for (final (key, _, chinese, english) in fields)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: TextFormField(
                    key: ValueKey('edit-shop-$key'),
                    controller: _controllers[key],
                    decoration: InputDecoration(labelText: context.tr(chinese, english)),
                    keyboardType: key == 'phone' ? TextInputType.phone : null,
                    maxLines: const {'description', 'announcement'}.contains(key) ? 3 : 1,
                    onChanged: (_) => setState(() {}),
                    validator: key != 'name'
                        ? null
                        : (value) => value == null || value.trim().isEmpty
                            ? context.tr('請輸入店舖名稱。', 'Enter the shop name.')
                            : null,
                  ),
                ),
            ],
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: Text(context.tr('取消', 'Cancel')),
          ),
          FilledButton(
            key: const ValueKey('save-shop-details'),
            onPressed: _changed.isEmpty
                ? null
                : () {
                    if (widget.api.sessionRevision == widget.session &&
                        _form.currentState?.validate() == true) {
                      Navigator.of(context).pop(_changed);
                    }
                  },
            child: Text(context.tr('儲存', 'Save')),
          ),
        ],
      );
}
