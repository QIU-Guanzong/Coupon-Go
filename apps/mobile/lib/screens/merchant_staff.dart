import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../theme.dart';
import '../widgets/merchant_layout.dart';

const _trialMode = bool.fromEnvironment('PUBLIC_TRIAL');

const _roles = <String, String>{
  'cashier': '收銀員',
  'manager': '經理',
  'finance': '財務',
  'merchant-admin': '店舖管理員',
};

const _rolesEn = <String, String>{
  'cashier': 'Cashier',
  'manager': 'Manager',
  'finance': 'Finance',
  'merchant-admin': 'Shop admin',
};

String _roleLabel(BuildContext context, Object? role) => context.tr(
    _roles[role] ?? '${role ?? ''}', _rolesEn[role] ?? '${role ?? ''}');

const _roleDescriptionsEn = <String, String>{
  'cashier': 'Redeem coupons and view redemption records.',
  'manager': 'Redeem coupons, view redemption records and manage coupons.',
  'finance': 'View shop settlements and manage payments.',
  'merchant-admin':
      'Manage coupons and redemptions, and view shop settlements.',
};

String _roleDescription(BuildContext context, Object? role) =>
    context.tr(_roleDescriptions[role] ?? '', _roleDescriptionsEn[role] ?? '');

const _roleDescriptions = <String, String>{
  'cashier': '核銷優惠券及查看核銷記錄。',
  'manager': '核銷優惠券、查看核銷記錄及管理優惠券。',
  'finance': '查看本店結算及使用收款相關功能。',
  'merchant-admin': '管理優惠券、核銷及查看本店結算。',
};

String _message(BuildContext context, Object e) => e is ApiException
    ? e.message
    : context.tr('連線失敗，請稍後重試', 'Connection failed. Try again later.');

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
      _error = context.tr('登入狀態已變更，請返回後重新進入。',
          'Your sign-in changed. Go back and reopen this page.');
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
          _error = _message(context, e);
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
      ScaffoldMessenger.of(context).showSnackBar(SnackBar(
          content: Text(
              result['message'] as String? ?? context.tr('已更新', 'Updated'))));
      await _load();
    } catch (e) {
      if (!mounted) return;
      if (_current(session)) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(_message(context, e))));
      }
    } finally {
      if (_current(session)) setState(() => _busy = false);
    }
  }

  Future<void> _invite() async {
    final session = widget.api.sessionRevision;
    String email = '';
    String role = 'cashier';
    String? emailError;
    final selection = await showDialog<Map<String, String>>(
      context: context,
      builder: (dialogContext) => StatefulBuilder(
          builder: (context, update) => AlertDialog(
                title: Text(context.tr('邀請店員', 'Invite staff')),
                content: SingleChildScrollView(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                  TextField(
                      onChanged: (value) {
                        email = value;
                        if (emailError != null) update(() => emailError = null);
                      },
                      keyboardType: TextInputType.emailAddress,
                      decoration: InputDecoration(
                          labelText: context.tr('店員的註冊電郵', 'Staff login email'),
                          errorText: emailError)),
                  const SizedBox(height: CgTheme.space12),
                  DropdownButtonFormField<String>(
                      initialValue: role,
                      decoration: InputDecoration(
                          labelText: context.tr('店舖角色', 'Shop role')),
                      items: _roles.entries
                          .map((e) => DropdownMenuItem(
                              value: e.key,
                              child: Text(_roleLabel(context, e.key))))
                          .toList(),
                      onChanged: (value) => update(() => role = value ?? role)),
                  const SizedBox(height: CgTheme.space8),
                  Text(_roleDescription(context, role)),
                  const SizedBox(height: CgTheme.space4),
                  Text(context.tr('對方確認邀請後才可使用店舖功能。',
                      'They can access the shop after accepting the invitation.')),
                ])),
                actions: [
                  TextButton(
                      onPressed: () => Navigator.pop(dialogContext),
                      child: Text(context.tr('取消', 'Cancel'))),
                  FilledButton(
                      onPressed: () {
                        if (!email.trim().contains('@')) {
                          update(() => emailError = context.tr(
                              '請輸入已註冊的電郵', 'Enter a registered email address'));
                          return;
                        }
                        Navigator.pop(dialogContext,
                            {'email': email.trim(), 'role': role});
                      },
                      child: Text(context.tr('發送邀請', 'Send invitation'))),
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
              title: Text(context.tr('更改店員角色', 'Change staff role')),
              children: _roles.entries
                  .map((entry) => SimpleDialogOption(
                      onPressed: () => Navigator.pop(context, entry.key),
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(_roleLabel(context, entry.key)),
                            Text(_roleDescription(context, entry.key),
                                style: Theme.of(context).textTheme.bodySmall)
                          ])))
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
              title: Text(context.tr('移除店員？', 'Remove staff member?')),
              content: Text(context.tr(
                  '移除後，${member['name'] ?? member['email'] ?? '此店員'}將無法使用本店功能。',
                  '${member['name'] ?? member['email'] ?? 'This staff member'} will lose access to this shop.')),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: Text(context.tr('取消', 'Cancel'))),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: Text(context.tr('移除', 'Remove')))
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
        appBar: AppBar(title: Text(context.tr('店員管理', 'Staff management'))),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? Center(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Text(_error!),
                    TextButton(
                        onPressed: _load,
                        child: Text(context.tr('重試', 'Try again')))
                  ]))
                : Center(
                    child: ConstrainedBox(
                        constraints: const BoxConstraints(
                            maxWidth: CgTheme.listMaxWidth),
                        child: RefreshIndicator(
                            onRefresh: _load,
                            child: ListView(
                                physics: const AlwaysScrollableScrollPhysics(),
                                padding:
                                    const EdgeInsets.all(CgTheme.pageInset),
                                children: [
                                  if (!_trialMode) ...[
                                    Text(context.tr('邀請已註冊的用戶，並按職責設定店舖權限。',
                                        'Invite registered users and choose their shop permissions.')),
                                    const SizedBox(height: CgTheme.space12),
                                    MerchantPrimaryAction(
                                        onPressed: _busy ? null : _invite,
                                        icon: Icons.person_add_alt,
                                        label:
                                            context.tr('邀請店員', 'Invite staff')),
                                  ],
                                  const SizedBox(height: CgTheme.space12),
                                  if (_members.isEmpty)
                                    Padding(
                                        padding: const EdgeInsets.symmetric(
                                            vertical: CgTheme.space24),
                                        child: Text(context.tr(
                                            '暫無店員', 'No staff members yet'))),
                                  for (final member in _members)
                                    _memberTile(context, member),
                                ])))),
      );

  Widget _memberTile(BuildContext context, Map<String, dynamic> member) {
    final name = (member['name'] as String? ?? '').trim();
    final email = (member['email'] as String? ?? '').trim();
    final showEmail = name.isNotEmpty && email.isNotEmpty && name != email;
    final active = member['status'] == 'active';
    return ListTile(
      contentPadding: EdgeInsets.zero,
      title: Text(name.isNotEmpty
          ? name
          : email.isNotEmpty
              ? email
              : context.tr('店員', 'Staff member')),
      subtitle: Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (showEmail) Text(email),
          Wrap(
            spacing: CgTheme.space8,
            runSpacing: CgTheme.space4,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              Text(_roleLabel(context, member['role'])),
              MerchantStatusPill(
                label: active
                    ? context.tr('已加入', 'Active')
                    : context.tr('待確認', 'Pending'),
                active: active,
              ),
            ],
          ),
        ],
      ),
      trailing: _trialMode
          ? null
          : PopupMenuButton<String>(
              enabled: !_busy,
              onSelected: (action) {
                if (action == 'role') _changeRole(member);
                if (action == 'remove') _remove(member);
              },
              itemBuilder: (_) => [
                    PopupMenuItem(
                        value: 'role',
                        child: Text(context.tr('更改角色', 'Change role'))),
                    PopupMenuItem(
                        value: 'remove',
                        child: Text(context.tr('移除店員', 'Remove staff member')))
                  ]),
    );
  }
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
      _error = context.tr('登入狀態已變更，請返回後重新進入。',
          'Your sign-in changed. Go back and reopen this page.');
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
          _error = _message(context, e);
        });
      }
    }
  }

  Future<void> _confirm(Map<String, dynamic> item) async {
    final session = widget.api.sessionRevision;
    final accepted = await showDialog<bool>(
        context: context,
        builder: (context) => AlertDialog(
              title: Text(context.tr('確認加入店舖？', 'Join this shop?')),
              content: Text(context.tr(
                  '加入「${item['shop_name']}」，角色：${_roleLabel(context, item['role'])}。',
                  'Join ${item['shop_name']} as ${_roleLabel(context, item['role'])}.')),
              actions: [
                TextButton(
                    onPressed: () => Navigator.pop(context, false),
                    child: Text(context.tr('取消', 'Cancel'))),
                FilledButton(
                    onPressed: () => Navigator.pop(context, true),
                    child: Text(context.tr('確認加入', 'Join shop')))
              ],
            ));
    if (accepted != true || !_current(session) || _busy) return;
    setState(() => _busy = true);
    try {
      await widget.api.post(
          '/admin/shops/${item['shop_id']}/members/${item['id']}/confirm');
      if (!mounted) return;
      if (!_current(session)) return;
      ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(content: Text(context.tr('已確認加入', 'Invitation accepted'))));
      await _load();
    } catch (e) {
      if (!mounted) return;
      if (_current(session)) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(_message(context, e))));
      }
    } finally {
      if (_current(session)) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: Text(context.tr('店員邀請', 'Staff invitations'))),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : _error != null
                ? Center(
                    child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Text(_error!),
                    TextButton(
                        onPressed: _load,
                        child: Text(context.tr('重試', 'Try again')))
                  ]))
                : Center(
                    child: ConstrainedBox(
                        constraints: const BoxConstraints(
                            maxWidth: CgTheme.listMaxWidth),
                        child: RefreshIndicator(
                            onRefresh: _load,
                            child: ListView(
                                physics: const AlwaysScrollableScrollPhysics(),
                                padding:
                                    const EdgeInsets.all(CgTheme.pageInset),
                                children: [
                                  if (_items.isEmpty)
                                    Padding(
                                        padding: const EdgeInsets.symmetric(
                                            vertical: CgTheme.space24),
                                        child: Text(context.tr('暫無待確認邀請',
                                            'No pending invitations'))),
                                  for (final item in _items)
                                    ListTile(
                                      contentPadding: EdgeInsets.zero,
                                      title: Text(
                                          item['shop_name'] as String? ??
                                              context.tr('店舖', 'Shop')),
                                      subtitle: Text(context.tr(
                                          '角色：${_roleLabel(context, item['role'])}',
                                          'Role: ${_roleLabel(context, item['role'])}')),
                                      trailing: FilledButton(
                                          onPressed: _busy
                                              ? null
                                              : () => _confirm(item),
                                          child: Text(context.tr(
                                              '查看邀請', 'View invitation'))),
                                    ),
                                ])))),
      );
}
