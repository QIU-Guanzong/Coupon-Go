import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../theme.dart';

/// Q仔 uses the Coupon GO server for catalog-grounded answers.
class QBotScreen extends StatefulWidget {
  const QBotScreen({super.key, required this.api});
  final ApiClient api;

  @override
  State<QBotScreen> createState() => _QBotScreenState();
}

class _QBotScreenState extends State<QBotScreen> {
  final _ctrl = TextEditingController();
  final _scroll = ScrollController();
  final _messages = <Map<String, String>>[];
  bool _sending = false;
  bool _failed = false;
  bool _askingPermission = false;
  bool _allowedForThisVisit = false;
  int _conversation = 0;

  @override
  void dispose() {
    _ctrl.dispose();
    _scroll.dispose();
    super.dispose();
  }

  void _scrollDown() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted && _scroll.hasClients) {
        _scroll.jumpTo(_scroll.position.maxScrollExtent);
      }
    });
  }

  Future<bool> _confirmSharing() async {
    if (_allowedForThisVisit) return true;
    if (_askingPermission) return false;
    _askingPermission = true;
    final accepted = await showDialog<bool>(
      context: context,
      builder: (dialog) => AlertDialog(
        title: Text(dialog.tr('使用 Q仔', 'Use Q仔')),
        content: Text(dialog.tr(
          '你的問題及近期對話會傳送給 DeepSeek 以產生回覆。請勿輸入密碼、兌換碼或其他個人資料。',
          'Your question and recent chat messages are sent to DeepSeek to generate replies. Do not enter passwords, redemption codes or other personal information.',
        )),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(dialog, false),
            child: Text(dialog.tr('取消', 'Cancel')),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(dialog, true),
            child: Text(dialog.tr('同意並送出', 'Agree and send')),
          ),
        ],
      ),
    );
    _askingPermission = false;
    if (!mounted || accepted != true) return false;
    _allowedForThisVisit = true;
    return true;
  }

  Future<void> _ask(String input, {bool retry = false}) async {
    final q = input.trim();
    if (_sending || q.isEmpty || !await _confirmSharing()) return;
    if (!mounted || _sending) return;
    final id = _conversation;
    final history = _messages.take(_messages.length - (retry ? 1 : 0)).toList();
    final previous = history.skip(history.length > 6 ? history.length - 6 : 0);
    final english = LanguageScope.of(context).english;
    setState(() {
      if (!retry) _messages.add({'role': 'user', 'text': q});
      _sending = true;
      _failed = false;
      if (!retry) _ctrl.clear();
    });
    _scrollDown();
    try {
      final data = await widget.api.post('/qbot/chat', body: {
        'message': q,
        'language': english ? 'en' : 'zh-Hant',
        'history': [
          for (final m in previous)
            {
              'role': m['role'],
              'text': m['text']!.characters.take(1100).toString()
            }
        ],
      });
      if (!mounted || id != _conversation) return;
      setState(() => _messages.add({
            'role': 'assistant',
            'text': data['answer'] as String,
            if (data['points_balance'] != null)
              'balance': "${data['points_balance']}"
          }));
    } catch (_) {
      if (mounted && id == _conversation) setState(() => _failed = true);
    } finally {
      if (mounted && id == _conversation) {
        setState(() => _sending = false);
        _scrollDown();
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    return Scaffold(
      appBar: AppBar(title: const Text('Q仔'), actions: [
        IconButton(
            tooltip: t('清除對話', 'Clear chat'),
            icon: const Icon(Icons.delete_outline),
            onPressed: _messages.isEmpty
                ? null
                : () => setState(() {
                      _conversation++;
                      _messages.clear();
                      _sending = false;
                      _failed = false;
                    })),
      ]),
      body: SafeArea(
          child: Column(children: [
        Expanded(
            child: ListView(
                controller: _scroll,
                padding: const EdgeInsets.all(16),
                children: [
              Text(t('我係 Q仔，可以幫你搵優惠券、查積分，或者講解點用券。',
                  'I can help you find coupons, check points, and redeem offers.')),
              const SizedBox(height: 8),
              Text(
                  t('由 DeepSeek AI 回答。請勿輸入密碼或兌換碼。',
                      'Answered by DeepSeek AI. Keep passwords and redemption codes private.'),
                  style: Theme.of(context).textTheme.bodySmall),
              if (_messages.isEmpty) ...[
                const SizedBox(height: 12),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final question in [
                      t('附近有咩優惠', 'What offers are nearby?'),
                      t('我有幾多積分', 'How many points do I have?'),
                      t('點樣用券', 'How do I use a coupon?'),
                    ])
                      ActionChip(
                        label: Text(question),
                        onPressed: () => _ask(question),
                      ),
                  ],
                ),
              ],
              for (final m in _messages)
                Align(
                    alignment: m['role'] == 'user'
                        ? Alignment.centerRight
                        : Alignment.centerLeft,
                    child: Container(
                      margin: const EdgeInsets.symmetric(vertical: 6),
                      padding: const EdgeInsets.all(12),
                      constraints: BoxConstraints(
                          maxWidth: MediaQuery.sizeOf(context).width * .82),
                      decoration: BoxDecoration(
                          color: m['role'] == 'user'
                              ? CgColors.primary
                              : Colors.white,
                          borderRadius: BorderRadius.circular(12)),
                      child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(m['text']!,
                                style: TextStyle(
                                    color: m['role'] == 'user'
                                        ? Colors.white
                                        : CgColors.textPrimary)),
                            if (m['balance'] != null)
                              Text(t("你的積分：${m['balance']}",
                                  "Your points: ${m['balance']}")),
                          ]),
                    )),
              if (_sending)
                Semantics(
                    liveRegion: true,
                    child: Text(t('Q仔正在回覆…', 'Q仔 is answering…'))),
              if (_failed)
                Column(children: [
                  Text(t('暫時未能回答，請稍後再試。',
                      'Could not answer just now. Try again shortly.')),
                  TextButton(
                      onPressed: () =>
                          _ask(_messages.last['text']!, retry: true),
                      child: Text(t('重試', 'Retry'))),
                ]),
            ])),
        Padding(
            padding: const EdgeInsets.all(12),
            child: Row(children: [
              Expanded(
                  child: TextField(
                      controller: _ctrl,
                      maxLength: 1000,
                      textInputAction: TextInputAction.send,
                      decoration: InputDecoration(
                          hintText: t('問 Q仔…', 'Ask Q仔…'), counterText: ''),
                      onSubmitted: _ask)),
              IconButton(
                  tooltip: t('送出', 'Send'),
                  onPressed: _sending ? null : () => _ask(_ctrl.text),
                  icon: const Icon(Icons.send, color: CgColors.primary)),
            ])),
      ])),
    );
  }
}
