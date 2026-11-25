import 'package:file_picker/file_picker.dart';
import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import '../api_client.dart';
import '../language.dart';

const _trialMode = bool.fromEnvironment('PUBLIC_TRIAL');

/// Applicant journey backed by the merchant-onboarding API.
class MerchantOnboardingScreen extends StatelessWidget {
  const MerchantOnboardingScreen({
    super.key,
    required this.api,
    this.openConnectUrl,
  });
  final ApiClient api;
  final Future<bool> Function(Uri)? openConnectUrl;

  @override
  Widget build(BuildContext context) => _trialMode
      ? _PresetApplication(api: api)
      : _MerchantApplication(api: api, openConnectUrl: openConnectUrl);
}

class _PresetApplication extends StatefulWidget {
  const _PresetApplication({required this.api});

  final ApiClient api;

  @override
  State<_PresetApplication> createState() => _PresetApplicationState();
}

class _PresetApplicationState extends State<_PresetApplication> {
  static const _choices = ['海港小店', '山邊小館'];
  String? _choice;
  String? _status;
  bool _loading = true;
  bool _submitting = false;
  bool _loadFailed = false;
  bool _submitFailed = false;
  late int _session;

  @override
  void initState() {
    super.initState();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    _refresh();
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    super.dispose();
  }

  bool get _current => mounted && _session == widget.api.sessionRevision;

  void _sessionChanged() {
    if (_current || !mounted) return;
    setState(() {
      _choice = _status = null;
      _loading = _submitting = false;
    });
  }

  Future<void> _refresh() async {
    if (!_current || !widget.api.isLoggedIn) {
      if (mounted) setState(() => _loading = false);
      return;
    }
    setState(() {
      _loading = true;
      _loadFailed = false;
    });
    try {
      final response = await widget.api.myMerchantApplications();
      final rows = response['applications'] as List<dynamic>;
      if (_current) {
        setState(() {
          _status = rows.isEmpty ? null : (rows.first as Map)['status'] as String;
        });
      }
    } catch (_) {
      if (_current) setState(() => _loadFailed = true);
    } finally {
      if (_current) setState(() => _loading = false);
    }
  }

  Future<void> _submit() async {
    final label = _choice;
    if (!_current || _submitting || _status != null || !_choices.contains(label)) return;
    setState(() {
      _submitting = true;
      _submitFailed = false;
    });
    try {
      await widget.api.submitMerchantApplication({'shop_name': label});
      if (_current) await _refresh();
    } catch (_) {
      if (!_current) return;
      // An interrupted response may still have created the application.
      // Read its status before exposing another submit.
      await _refresh();
      if (_current) setState(() => _submitFailed = _status == null);
    } finally {
      if (_current) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    return Scaffold(
      appBar: AppBar(title: Text(t('商戶入駐申請', 'Merchant application'))),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (!_current || !widget.api.isLoggedIn)
            Text(t('請從企業服務重新開啟申請。', 'Open the application from Business again.'))
          else if (_loading)
            const LinearProgressIndicator()
          else if (_loadFailed) ...[
            Text(t('未能讀取申請進度。', 'Could not load your application status.')),
            TextButton(onPressed: _refresh, child: Text(t('重試', 'Try again'))),
          ] else if (_status != null)
            ListTile(
              leading: const Icon(Icons.receipt_long_outlined),
              title: Text(t('申請進度', 'Application status')),
              subtitle: Text(switch (_status) {
                'pending' => t('待審核', 'Pending review'),
                'approved' => t('已通過', 'Approved'),
                'rejected' => t('未通過', 'Not approved'),
                _ => t('待確認', 'Awaiting confirmation'),
              }),
            )
          else ...[
            Text(t('選擇要登記的店舖。此步驟不需提供聯絡資料或文件。',
                'Choose a shop to register. No contact details or documents are required.')),
            const SizedBox(height: 16),
            DropdownButtonFormField<String>(
              key: const ValueKey('preset-merchant-shop'),
              initialValue: _choice,
              decoration: InputDecoration(labelText: t('店舖', 'Shop')),
              items: [
                DropdownMenuItem(value: _choices[0], child: Text(t(_choices[0], 'Harbour Shop'))),
                DropdownMenuItem(value: _choices[1], child: Text(t(_choices[1], 'Hillside Kitchen'))),
              ],
              onChanged: _submitting ? null : (value) => setState(() => _choice = value),
            ),
            const SizedBox(height: 16),
            if (_submitFailed)
              Text(t('未能提交申請，請稍後重試。', 'Could not submit the application. Try again later.')),
            FilledButton(
              key: const ValueKey('preset-merchant-submit'),
              onPressed: _choice == null || _submitting ? null : _submit,
              child: Text(_submitting
                  ? t('提交中…', 'Submitting…')
                  : t('提交申請', 'Submit application')),
            ),
          ],
        ],
      ),
    );
  }
}

class _MerchantApplication extends StatefulWidget {
  const _MerchantApplication({required this.api, this.openConnectUrl});

  final ApiClient api;
  final Future<bool> Function(Uri)? openConnectUrl;

  @override
  State<_MerchantApplication> createState() => _MerchantOnboardingScreenState();
}

class _MerchantOnboardingScreenState extends State<_MerchantApplication>
    with WidgetsBindingObserver {
  final _form = GlobalKey<FormState>();
  final _shop = TextEditingController();
  final _contact = TextEditingController();
  final _phone = TextEditingController();
  final _address = TextEditingController();
  final _brNumber = TextEditingController();
  final Map<String, Map<String, dynamic>> _files = {};
  Map<String, dynamic>? _application;
  Map<String, dynamic>? _contract;
  bool _loading = true;
  bool _busy = false;
  bool _showForm = false;
  bool _accepted = false;
  bool _uncertain = false;
  bool _dirty = false;
  bool _allowExit = false;
  bool _awaitingConnectReturn = false;
  String? _connectMessage;
  String? _error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    for (final c in [_shop, _contact, _phone, _address, _brNumber]) {
      c.addListener(_markDirty);
    }
    _refresh();
  }

  void _markDirty() {
    if (!_dirty && mounted) {
      setState(() {
        _dirty = true;
      });
    }
  }

  Future<void> _confirmExit(bool didPop) async {
    if (didPop || _allowExit) return;
    final leave = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text('放棄未提交的資料？'),
        content: const Text('離開後，尚未提交的資料及所選文件不會保留在此頁。'),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(context, false),
            child: const Text('繼續填寫'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(context, true),
            child: const Text('離開'),
          ),
        ],
      ),
    );
    if (leave == true && mounted) {
      setState(() {
        _allowExit = true;
      });
      WidgetsBinding.instance.addPostFrameCallback((_) {
        if (mounted) Navigator.of(context).pop();
      });
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    for (final c in [_shop, _contact, _phone, _address, _brNumber]) {
      c.dispose();
    }
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed &&
        _awaitingConnectReturn &&
        !_busy) {
      _awaitingConnectReturn = false;
      _syncConnect();
    }
  }

  Future<void> _startConnect() async {
    if (_busy || _application == null) return;
    setState(() {
      _busy = true;
      _error = null;
      _connectMessage = null;
    });
    try {
      final result = await widget.api.startApplicationConnect(
        _application!['id'] as int,
      );
      if (result['simulated'] == true) {
        if (mounted) {
          setState(() {
            _application = {
              ..._application!,
              'stripe_status_source': 'simulated'
            };
            _connectMessage = context.tr('未有可開啟的正式收款頁面。', 'No live payments page is available.');
          });
        }
        return;
      }
      final uri = Uri.tryParse(result['onboarding_url'] as String? ?? '');
      if (uri == null || uri.scheme != 'https' || uri.host.isEmpty) {
        throw ApiException(502, '未能取得有效的收款設定連結');
      }
      _awaitingConnectReturn = true;
      final opened = widget.openConnectUrl != null
          ? await widget.openConnectUrl!(uri)
          : await launchUrl(uri, mode: LaunchMode.externalApplication);
      if (!opened) {
        _awaitingConnectReturn = false;
        throw ApiException(502, '無法開啟收款設定，請重試');
      }
      if (mounted) {
        setState(() {
          _application = {
            ..._application!,
            'stripe_status_source': 'stripe_test'
          };
          _connectMessage = context.tr('已開啟收款設定；返回後請刷新收款狀態。', 'Payments setup opened. Refresh the status when you return.');
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _connectMessage = _message(e);
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
        });
      }
    }
  }

  Future<void> _syncConnect() async {
    if (_busy || _application == null) return;
    setState(() {
      _busy = true;
      _connectMessage = null;
    });
    try {
      final result = await widget.api.syncApplicationConnect(
        _application!['id'] as int,
      );
      final active = result['onboarding_status'] == 'active' &&
          result['charges_enabled'] == true &&
          result['payouts_enabled'] == true &&
          result['transfers_enabled'] == true;
      final simulated = result['simulated'] == true;
      if (mounted) {
        setState(() {
          _application = {
            ..._application!,
            'stripe_status_source': simulated ? 'simulated' : 'stripe_test'
          };
          _connectMessage = active
              ? context.tr('已完成收款設定，正式收款尚未開通。',
                  'Payments setup is complete. Live payments are not enabled.')
              : context.tr('收款設定尚未完成。', 'Payments setup is incomplete.');
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _connectMessage = _message(e);
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
        });
      }
    }
  }

  String _message(Object error) =>
      error is ApiException ? error.message : '連線失敗，請稍後重試';

  Future<void> _refresh() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final result = await widget.api.myMerchantApplications();
      final rows = result['applications'] as List<dynamic>;
      if (!mounted) return;
      setState(() {
        _application =
            rows.isEmpty ? null : Map<String, dynamic>.from(rows.first as Map);
        _showForm = rows.isEmpty || _application?['status'] == 'rejected';
        _uncertain = false;
        _loading = false;
      });
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = _message(e);
          _loading = false;
        });
      }
    }
  }

  Future<void> _upload(String kind) async {
    try {
      final result = await FilePicker.platform.pickFiles(
        type: FileType.custom,
        allowedExtensions: ['pdf', 'jpg', 'jpeg', 'png'],
        withData: true,
      );
      if (result == null || result.files.isEmpty) return;
      final file = result.files.single;
      if (file.bytes == null) throw Exception('Cannot read file');
      setState(() {
        _busy = true;
        _error = null;
      });
      final response = await widget.api.uploadMerchantDocument(
        kind,
        file.name,
        file.bytes!,
      );
      if (mounted) {
        setState(() {
          _files[kind] = Map<String, dynamic>.from(response['file'] as Map);
          _dirty = true;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = _message(e);
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
        });
      }
    }
  }

  Future<void> _submit() async {
    if (!_form.currentState!.validate() || _busy || _uncertain) return;
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.submitMerchantApplication({
        'shop_name': _shop.text.trim(),
        'contact_name': _contact.text.trim(),
        'phone': _phone.text.trim(),
        'address': _address.text.trim(),
        'br_number': _brNumber.text.trim(),
        if (_files['br'] != null) 'br_file_id': _files['br']!['id'],
        if (_files['ci'] != null) 'ci_file_id': _files['ci']!['id'],
        if (_files['director_id'] != null)
          'director_id_file_id': _files['director_id']!['id'],
        if (_files['shop_photo'] != null)
          'shop_photo_file_ids': [_files['shop_photo']!['id']],
      });
      if (mounted) {
        setState(() {
          _dirty = false;
        });
      }
      await _refresh();
    } on ApiException catch (e) {
      if (mounted) {
        setState(() {
          _error = e.message;
        });
      }
    } catch (_) {
      if (mounted) {
        setState(() {
          _uncertain = true;
          _error = '提交結果未能確認，請先刷新申請進度。';
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
        });
      }
    }
  }

  Future<void> _loadContract() async {
    setState(() {
      _busy = true;
      _error = null;
      _accepted = false;
    });
    try {
      final contract = await widget.api.currentMerchantContract();
      if (mounted) {
        setState(() {
          _contract = contract;
        });
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = _message(e);
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
        });
      }
    }
  }

  Future<void> _sign() async {
    if (!_accepted || _busy || _contract == null || _application == null) {
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await widget.api.signMerchantContract(
        _application!['id'] as int,
        _contract!['id'] as int,
      );
      if (!mounted) return;
      setState(() {
        _contract = null;
        _accepted = false;
      });
      await _refresh();
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = _message(e);
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
        });
      }
    }
  }

  Future<void> _download() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final result = await widget.api.merchantContractPdf(
        _application!['id'] as int,
      );
      final uri = Uri.parse(
        widget.api.baseUrl,
      ).resolve(result['download_url'] as String);
      if (!await launchUrl(uri, mode: LaunchMode.externalApplication)) {
        throw Exception('Could not open PDF');
      }
    } catch (e) {
      if (mounted) {
        setState(() {
          _error = _message(e);
        });
      }
    } finally {
      if (mounted) {
        setState(() {
          _busy = false;
        });
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = _application;
    final status = app?['status'];
    final signed = app?['contract'] as Map?;
    final source = app?['stripe_status_source'];
    final statusText = switch (status) {
      'pending' => '待審核',
      'approved' => '審核已通過',
      'rejected' => '未獲批准',
      _ => '狀態待確認',
    };
    return PopScope(
      canPop: !_dirty || _allowExit,
      onPopInvokedWithResult: (didPop, _) => _confirmExit(didPop),
      child: Scaffold(
        appBar: AppBar(title: Text(context.tr('商戶入駐', 'Merchant application'))),
        body: _loading
            ? const Center(child: CircularProgressIndicator())
            : Center(
                child: ConstrainedBox(
                  constraints: const BoxConstraints(maxWidth: 680),
                  child: ListView(
                    padding: const EdgeInsets.all(20),
                    children: [
                      const Text(
                        '申請入駐',
                        style: TextStyle(
                          fontSize: 26,
                          fontWeight: FontWeight.bold,
                        ),
                      ),
                      const SizedBox(height: 8),
                      const Text('填寫商店資料、提交文件並簽署當前合約，可在此查看申請進度。'),
                      const SizedBox(height: 20),
                      if (_error != null) ...[
                        Semantics(
                          liveRegion: true,
                          child: Text(
                            _error!,
                            style: TextStyle(
                              color: Theme.of(context).colorScheme.error,
                            ),
                          ),
                        ),
                        const SizedBox(height: 8),
                        TextButton(
                          onPressed: _busy ? null : _refresh,
                          child: const Text('刷新申請進度'),
                        ),
                      ],
                      if (app != null) ...[
                        Text(
                          '${app['shop_name']} · $statusText',
                          style: const TextStyle(
                            fontSize: 19,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        if (status == 'rejected' &&
                            (app['reject_reason'] as String? ?? '').isNotEmpty)
                          Text('原因：${app['reject_reason']}'),
                        const SizedBox(height: 8),
                        Text(
                          signed == null
                              ? '合約：尚未簽署'
                              : '合約：已簽署版本 ${signed['version']}',
                        ),
                        Text(switch (source) {
                          'simulated' || 'stripe_test' => context.tr(
                              '收款帳戶：未開通正式收款', 'Payments account: live payments not enabled'),
                          'unverified' => context.tr(
                              '收款帳戶：狀態尚未驗證', 'Payments account: status not verified'),
                          'not_started' => context.tr(
                              '收款帳戶：尚未開始', 'Payments account: setup not started'),
                          _ => context.tr('收款帳戶：狀態待確認', 'Payments account: status unconfirmed'),
                        }),
                        const SizedBox(height: 12),
                        if (status == 'pending') ...[
                          Align(
                            alignment: Alignment.centerLeft,
                            child: OutlinedButton(
                              onPressed: _busy ? null : _startConnect,
                              child: Text(context.tr('開啟收款設定', 'Open payments setup')),
                            ),
                          ),
                          Align(
                            alignment: Alignment.centerLeft,
                            child: TextButton(
                              onPressed: _busy ? null : _syncConnect,
                              child: const Text('刷新收款狀態'),
                            ),
                          ),
                          if (_connectMessage != null)
                            Semantics(
                              liveRegion: true,
                              child: Text(_connectMessage!),
                            ),
                        ],
                        if (status == 'pending')
                          Align(
                            alignment: Alignment.centerLeft,
                            child: OutlinedButton(
                              onPressed: _busy ? null : _loadContract,
                              child: const Text('查看當前合約'),
                            ),
                          ),
                        if (signed != null)
                          Align(
                            alignment: Alignment.centerLeft,
                            child: TextButton.icon(
                              onPressed: _busy ? null : _download,
                              icon: const Icon(Icons.download),
                              label: const Text('下載已簽合約 PDF'),
                            ),
                          ),
                        TextButton(
                          onPressed: _busy ? null : _refresh,
                          child: const Text('刷新進度'),
                        ),
                        const Divider(height: 32),
                      ],
                      if (_contract != null) ...[
                        Text(
                          '${_contract!['title']} · 版本 ${_contract!['version']}',
                          style: const TextStyle(
                            fontSize: 19,
                            fontWeight: FontWeight.w600,
                          ),
                        ),
                        if (_contract!['test_only'] == true)
                          const Padding(
                            padding: EdgeInsets.symmetric(vertical: 8),
                            child: Text(
                              '合約內容尚待確認，未生效。',
                              style: TextStyle(fontWeight: FontWeight.bold),
                            ),
                          ),
                        const SizedBox(height: 8),
                        SelectableText('${_contract!['body_text']}'),
                        CheckboxListTile(
                          contentPadding: EdgeInsets.zero,
                          value: _accepted,
                          onChanged: _busy
                              ? null
                              : (v) => setState(() {
                                    _accepted = v ?? false;
                                  }),
                          title: const Text('我已閱讀並同意以上當前版本合約'),
                        ),
                        FilledButton(
                          onPressed: _accepted && !_busy ? _sign : null,
                          child: const Text('簽署此版本'),
                        ),
                        TextButton(
                          onPressed: _busy
                              ? null
                              : () => setState(() {
                                    _contract = null;
                                    _accepted = false;
                                  }),
                          child: const Text('收起合約'),
                        ),
                        const Divider(height: 32),
                      ],
                      if (_showForm)
                        Form(
                          key: _form,
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.stretch,
                            children: [
                              const Text(
                                '商店資料',
                                style: TextStyle(
                                  fontSize: 19,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              const SizedBox(height: 12),
                              TextFormField(
                                controller: _shop,
                                decoration: const InputDecoration(
                                  labelText: '商店名稱 *',
                                ),
                                validator: (v) => v == null || v.trim().isEmpty
                                    ? '請填寫商店名稱'
                                    : null,
                              ),
                              const SizedBox(height: 12),
                              TextFormField(
                                controller: _contact,
                                decoration: const InputDecoration(
                                  labelText: '聯絡人',
                                ),
                              ),
                              const SizedBox(height: 12),
                              TextFormField(
                                controller: _phone,
                                keyboardType: TextInputType.phone,
                                decoration: const InputDecoration(
                                  labelText: '聯絡電話',
                                ),
                              ),
                              const SizedBox(height: 12),
                              TextFormField(
                                controller: _address,
                                decoration: const InputDecoration(
                                  labelText: '商店地址',
                                ),
                              ),
                              const SizedBox(height: 12),
                              TextFormField(
                                controller: _brNumber,
                                keyboardType: TextInputType.number,
                                decoration: const InputDecoration(
                                  labelText: '商業登記號碼（選填）',
                                ),
                                validator: (v) {
                                  final value = (v ?? '').replaceAll(
                                    RegExp(r'[\s-]'),
                                    '',
                                  );
                                  return value.isNotEmpty &&
                                          !RegExp(r'^\d{8}$').hasMatch(value)
                                      ? '請輸入 8 位數字'
                                      : null;
                                },
                              ),
                              const SizedBox(height: 24),
                              const Text(
                                '入駐文件',
                                style: TextStyle(
                                  fontSize: 19,
                                  fontWeight: FontWeight.w600,
                                ),
                              ),
                              const Text('支援 PDF、JPG 或 PNG。可先提交現有文件。'),
                              for (final entry in const {
                                'br': '商業登記證',
                                'ci': '公司註冊證書',
                                'director_id': '董事身份證明',
                                'shop_photo': '店舖照片',
                              }.entries)
                                ListTile(
                                  contentPadding: EdgeInsets.zero,
                                  title: Text(entry.value),
                                  subtitle: Text(
                                    _files[entry.key]?['original_filename']
                                            ?.toString() ??
                                        '尚未上傳',
                                  ),
                                  trailing: OutlinedButton(
                                    onPressed:
                                        _busy ? null : () => _upload(entry.key),
                                    child: Text(
                                      _files.containsKey(entry.key)
                                          ? '更換'
                                          : '上傳',
                                    ),
                                  ),
                                ),
                              const SizedBox(height: 16),
                              FilledButton(
                                onPressed: _busy || _uncertain ? null : _submit,
                                child: const Text('提交申請'),
                              ),
                            ],
                          ),
                        ),
                    ],
                  ),
                ),
              ),
      ),
    );
  }
}
