import 'package:flutter/material.dart';

import '../api_client.dart';

const _roles = <String, String>{
  'cashier': '收銀員',
  'manager': '經理',
  'finance': '財務',
  'merchant-admin': '店舖管理員',
};

String _message(Object e) => e is ApiException ? e.message : '連線失敗，請稍後重試';

/// Owner-managed staff for a single shop. The server enforces shop ownership.
class MerchantStaffScreen extends StatefulWidget {
  const MerchantStaffScreen(
      {super.key, required this.api, required this.shopId});
  final ApiClient api;
  final int shopId;

  @override
  State<MerchantStaffScreen> createState() => _MerchantStaffScreenState();
}

class _MerchantStaffScreenState extends State<MerchantStaffScreen> {
  List<Map<String, dynamic>> _members = [];
  bool _loading = true;
  bool _busy = false;
  String? _error;
  late int _session;

  bool _current(int session) =>
      mounted && session == widget.api.sessionRevision;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    _load();
  }

  void _sessionChanged() {
    if (!mounted || _session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    setState(() {
      _members = [];
      _loading = false;
      _busy = false;
      _error = '登入狀態已變更，請返回後重新進入。';
    });
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    super.dispose();
  }

  Future<void> _load() async {
    final session = widget.api.sessionRevision;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data =
          await widget.api.get('/admin/shops/${widget.shopId}/members');
      final raw = data['members'];
      if (raw is! List) throw const FormatException();
      if (!_current(session)) return;
      setState(() {
        _members =
            raw.map((item) => Map<String, dynamic>.from(item as Map)).toList();
        _loading = false;
      });
    } catch (e) {
      if (_current(session)) {
        setState(() {
          _loading = false;
          _error = _message(e);
        });
      }
    }
  }

  Future<void> _mutate(
      Future<Map<String, dynamic>> Function() request, int session) async {
    if (!_current(session) || _busy) return;
    setState(() => _busy = true);
    try {
      final result = await request();
      if (!mounted) return;
      if (!_current(session)) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(result['message'] as String? ?? '已更新')));
      await _load();
    } catch (e) {
      if (!mounted) return;
      if (_current(session)) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(_message(e))));
      }
    } finally {
      if (_current(session)) setState(() => _busy = false);
    }
  }

  Future<void> _invite() async {
    final session = widget.api.sessionRevision;
    String email = '';
    String role = 'cashier';
    final selection = await showDialog<Map<String, String>>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
          builder: (context, update) => AlertDialog(
                title: const Text('邀請店員'),
                content: Column(mainAxisSize: MainAxisSize.min, children: [
                  TextField(
                      onChanged: (value) => email = value,
                      keyboardType: TextInputType.emailAddress,
                      decoration: const InputDecoration(labelText: '店員的註冊電郵')),
                  const SizedBox(height: 12),
                  DropdownButtonFormField<String>(
                      initialValue: role,
                      decoration: const InputDecoration(labelText: '店舖角色'),
                      items: _roles.entries
                          .map((e) => DropdownMenuItem(
                              value: e.key, child: Text(e.value)))
                          .toList(),
                      onChanged: (value) => update(() => role = value ?? role)),
                  const SizedBox(height: 8),
                  const Text('對方確認邀請後才可使用店舖功能。'),
                ]),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dialogContext),
                      child: const Text('取消')),
                  FilledButton(
                      onPressed: () {
                        if (email.trim().isEmpty) return;
                        Navigator.pop(dialogContext,
                            {'email': email.trim(), 'role': role});
                      },
                      child: const Text('發送邀請')),
                ],
              )),
    );
    if (selection == null || !_current(session)) return;
    await _mutate(
        () => widget.api
            .post('/admin/shops/${widget.shopId}/members', body: selection),
        session);
  }

  Future<void> _changeRole(Map<String, dynamic> member) async {
    final session = widget.api.sessionRevision;
    final selected = await showDialog<String>(
        context: context,
        builder: (context) => SimpleDialog(
              title: const Text('更改店員角色'),
              children: _roles.entries
                  .map((entry) => SimpleDialogOption(
                      onPressed: () => Navigator.pop(context, entry.key),
                      child: Text(entry.value)))
                  .toList(),
            ));
    if (selected == null || !_current(session)) return;
    await _mutate(
        () => widget.api.patch(
            '/admin/shops/${widget.shopId}/members/${member['user_id']}',
            body: {'role': selected}),
        session);
  }

  Future<void> _remove(Map<String, dynamic> member) async {
    final session = widget.api.sessionRevision;
    final ok = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
              title: const Text('移除店員？'),
              content: Text(
                  '移除後，${member['name'] ?? member['email'] ?? '此店員'}將無法使用本店功能。'),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('取消')),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('移除'))
              ],
            ));
    if (ok != true || !_current(session)) return;
    await _mutate(
        () => widget.api.delete(
            '/admin/shops/${widget.shopId}/members/${member['user_id']}'),
        session);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('店員管理')),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? Center(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Text(_error!),
                    TextButton(onPressed: _load, child: const Text('重試'))
                  ]))
                : RefreshIndicator(
                    onRefresh: _load,
                    child:
                        ListView(padding: const EdgeInsets.all(16), children: [
                      const Text('邀請已註冊的用戶，並按職責設定店舖權限。'),
                      const SizedBox(height: 12),
                      FilledButton.icon(
                          onPressed: _busy ? null : _invite,
                          icon: const Icon(Icons.person_add_alt),
                          label: const Text('邀請店員')),
                      const SizedBox(height: 12),
                      if (_members.isEmpty)
                        const Padding(
                            padding: EdgeInsets.symmetric(vertical: 24),
                            child: Text('暫無店員')),
                      for (final member in _members)
                        ListTile(
                          title: Text(
                              (member['name'] as String?)?.isNotEmpty == true
                                  ? member['name'] as String
                                  : (member['email'] as String? ?? '店員')),
                          subtitle: Text(
                              '${member['email'] ?? ''}\n${_roles[member['role']] ?? member['role']} · ${member['status'] == 'active' ? '已加入' : '待確認'}'),
                          isThreeLine: true,
                          trailing: PopupMenuButton<String>(
                              enabled: !_busy,
                              onSelected: (action) {
                                if (action == 'role') _changeRole(member);
                                if (action == 'remove') _remove(member);
                              },
                              itemBuilder: (_) => const [
                                    PopupMenuItem(
                                        value: 'role', child: Text('更改角色')),
                                    PopupMenuItem(
                                        value: 'remove', child: Text('移除店員'))
                                  ]),
                        ),
                    ])),
      );
}

/// Available to signed-in invitees, including ordinary customer accounts.
class ShopInvitationsScreen extends StatefulWidget {
  const ShopInvitationsScreen({super.key, required this.api});
  final ApiClient api;
  @override
  State<ShopInvitationsScreen> createState() => _ShopInvitationsScreenState();
}

class _ShopInvitationsScreenState extends State<ShopInvitationsScreen> {
  List<Map<String, dynamic>> _items = [];
  bool _loading = true;
  bool _busy = false;
  String? _error;
  late int _session;
  bool _current(int revision) =>
      mounted && revision == widget.api.sessionRevision;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    _load();
  }

  void _sessionChanged() {
    if (!mounted || _session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    setState(() {
      _items = [];
      _loading = false;
      _busy = false;
      _error = '登入狀態已變更，請返回後重新進入。';
    });
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    super.dispose();
  }

  Future<void> _load() async {
    final session = widget.api.sessionRevision;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await widget.api.get('/merchant/shop-invitations');
      final raw = data['invitations'];
      if (raw is! List) throw const FormatException();
      if (!_current(session)) return;
      setState(() {
        _items = raw.map((v) => Map<String, dynamic>.from(v as Map)).toList();
        _loading = false;
      });
    } catch (e) {
      if (_current(session)) {
        setState(() {
          _loading = false;
          _error = _message(e);
        });
      }
    }
  }

  Future<void> _confirm(Map<String, dynamic> item) async {
    final session = widget.api.sessionRevision;
    final accepted = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
              title: const Text('確認加入店舖？'),
              content: Text(
                  '加入「${item['shop_name']}」，角色：${_roles[item['role']] ?? item['role']}。'),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: const Text('取消')),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: const Text('確認加入'))
              ],
            ));
    if (accepted != true || !_current(session) || _busy) return;
    setState(() => _busy = true);
    try {
      await widget.api.post(
          '/admin/shops/${item['shop_id']}/members/${item['id']}/confirm');
      if (!mounted) return;
      if (!_current(session)) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('已確認加入')));
      await _load();
    } catch (e) {
      if (!mounted) return;
      if (_current(session)) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(_message(e))));
      }
    } finally {
      if (_current(session)) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: const Text('店員邀請')),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? Center(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Text(_error!),
                    TextButton(onPressed: _load, child: const Text('重試'))
                  ]))
                : RefreshIndicator(
                    onRefresh: _load,
                    child:
                        ListView(padding: const EdgeInsets.all(16), children: [
                      if (_items.isEmpty)
                        const Padding(
                            padding: EdgeInsets.symmetric(vertical: 24),
                            child: Text('暫無待確認邀請')),
                      for (final item in _items)
                        ListTile(
                          title: Text(item['shop_name'] as String? ?? '店舖'),
                          subtitle: Text(
                              '角色：${_roles[item['role']] ?? item['role']}'),
                          trailing: FilledButton(
                              onPressed: _busy ? null : () => _confirm(item),
                              child: const Text('查看邀請')),
                        ),
                    ])),
      );
}
