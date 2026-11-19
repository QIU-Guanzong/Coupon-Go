import 'package:flutter/material.dart';

import '../api_client.dart';
import '../language.dart';
import '../theme.dart';

Future<bool> guestLoginPrompt(BuildContext context) async {
  return await showDialog<bool>(
        context: context,
        builder: (c) => Dialog(
          backgroundColor: Colors.white,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(16)),
          child: Stack(
              clipBehavior: Clip.none,
              alignment: Alignment.topCenter,
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(18, 27, 18, 14),
                  child: Column(mainAxisSize: MainAxisSize.min, children: [
                    Text(c.tr('請登錄以繼續', 'Please login to continue'),
                        style: Theme.of(c).textTheme.titleMedium),
                    Text(
                        c.tr('立即登入以獲取 Coupon 獎勵！',
                            'Login now to get Coupon rewards!'),
                        textAlign: TextAlign.center),
                    const SizedBox(height: 12),
                    Row(children: [
                      Expanded(
                          child: OutlinedButton(
                              onPressed: () => Navigator.pop(c, false),
                              child: Text(c.tr('取消', 'Cancel')))),
                      const SizedBox(width: 12),
                      Expanded(
                          child: FilledButton(
                              onPressed: () => Navigator.pop(c, true),
                              child: Text(c.tr('登錄', 'Login')))),
                    ]),
                  ]),
                ),
                Positioned(
                    top: -46,
                    child: Image.asset('assets/images/img_cornor_cat.png',
                        height: 60)),
              ]),
        ),
      ) ??
      false;
}

enum _Step { chooser, login, register, verify, forgot, reset }

class AuthScreen extends StatefulWidget {
  const AuthScreen(
      {super.key,
      required this.api,
      required this.onAuthed,
      this.vendor = false});
  final ApiClient api;
  final VoidCallback onAuthed;
  final bool vendor;
  @override
  State<AuthScreen> createState() => _AuthScreenState();
}

class _AuthScreenState extends State<AuthScreen> {
  late _Step step = widget.vendor ? _Step.login : _Step.chooser;
  final email = TextEditingController(),
      password = TextEditingController(),
      name = TextEditingController(),
      confirmation = TextEditingController(),
      code = TextEditingController();
  bool busy = false, remember = true, obscure = true;
  String? error;

  @override
  void dispose() {
    for (final controller in [email, password, name, confirmation, code]) {
      controller.dispose();
    }
    super.dispose();
  }

  void go(_Step next) {
    FocusScope.of(context).unfocus();
    password.clear();
    confirmation.clear();
    code.clear();
    setState(() {
      step = next;
      error = null;
      obscure = true;
    });
  }

  bool get first => step == (widget.vendor ? _Step.login : _Step.chooser);
  void back() {
    go(switch (step) {
      _Step.login => _Step.chooser,
      _Step.verify => _Step.register,
      _Step.reset => _Step.forgot,
      _ => _Step.login,
    });
  }

  Future<void> submit() async {
    if (busy) return;
    if ((step == _Step.register || step == _Step.reset) &&
        password.text != confirmation.text) {
      setState(
          () => error = context.tr('兩次輸入的密碼不一致', 'Passwords do not match'));
      return;
    }
    if (email.text.trim().isEmpty ||
        ([_Step.login, _Step.register, _Step.reset].contains(step) &&
            password.text.isEmpty) ||
        (step == _Step.register && name.text.trim().isEmpty) ||
        ([_Step.verify, _Step.reset].contains(step) && code.text.length != 6)) {
      setState(() =>
          error = context.tr('請填妥所需資料', 'Please complete the required fields'));
      return;
    }
    final api = widget.api, address = email.text.trim();
    setState(() {
      busy = true;
      error = null;
    });
    try {
      switch (step) {
        case _Step.chooser:
          return;
        case _Step.register:
          await api.register(address, password.text, name.text.trim(),
              widget.vendor ? 'vendor' : 'customer');
          if (mounted) go(_Step.verify);
        case _Step.forgot:
          await api.post('/password/forgot', body: {'email': address});
          if (mounted) go(_Step.reset);
        case _Step.reset:
          await api.post('/password/reset', body: {
            'email': address,
            'code': code.text,
            'new_password': password.text
          });
          if (mounted) go(_Step.login);
        case _Step.verify:
        case _Step.login:
          final result = step == _Step.verify
              ? await api.verifyOtp(address, code.text, 'register')
              : await api.post('/login',
                  body: {'email': address, 'password': password.text});
          if (!mounted) return;
          if (widget.vendor && !['vendor', 'admin'].contains(result['role'])) {
            setState(() => error =
                context.tr('請使用企業帳戶登錄', 'Please use a business account'));
            return;
          }
          await api.saveToken(result['access_token'] as String,
              persist: remember);
          if (mounted) widget.onAuthed();
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    } catch (_) {
      if (mounted) {
        setState(() => error =
            context.tr('無法連線，請重試', 'Unable to connect. Please try again.'));
      }
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = context.tr;
    final accent = widget.vendor ? CgColors.vendorGold : CgColors.primary;
    final title = switch (step) {
      _Step.chooser => t('請註冊或登入。', 'Please sign up or log in.'),
      _Step.login => t('歡迎回來', 'Welcome back'),
      _Step.register => t('創建帳戶', 'Create account'),
      _Step.verify => t('驗證電郵', 'Verify email'),
      _Step.forgot => t('忘記密碼？', 'Forgot password?'),
      _Step.reset => t('重設密碼', 'Reset password'),
    };
    Widget field(TextEditingController controller, String label,
            {TextInputType? type, bool secret = false}) =>
        Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: TextField(
                controller: controller,
                enabled: !busy,
                keyboardType: type,
                obscureText: secret && obscure,
                autocorrect: !secret,
                decoration: InputDecoration(
                    labelText: label,
                    filled: true,
                    fillColor: Colors.white,
                    suffixIcon: secret && controller == password
                        ? IconButton(
                            tooltip: t('顯示或隱藏密碼', 'Show or hide password'),
                            icon: Icon(obscure
                                ? Icons.visibility_off_outlined
                                : Icons.visibility_outlined),
                            onPressed: () => setState(() => obscure = !obscure))
                        : null)));
    return PopScope(
      canPop: first && !busy,
      onPopInvokedWithResult: (didPop, _) {
        if (!didPop && !busy && !first) back();
      },
      child: Scaffold(
        appBar: AppBar(
            backgroundColor: accent,
            title: const Text('COUPON GO!'),
            leading: BackButton(
                onPressed: busy
                    ? null
                    : () {
                        if (first) {
                          Navigator.maybePop(context);
                        } else {
                          back();
                        }
                      })),
        body: ListView(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 24),
            children: [
              Container(
                  color: accent,
                  width: double.infinity,
                  alignment: Alignment.center,
                  child: Image.asset('assets/images/img_logo_cropped.png',
                      height: 115)),
              const SizedBox(height: 16),
              Center(
                  child: Text(title,
                      style: Theme.of(context).textTheme.titleLarge)),
              const SizedBox(height: 24),
              if (step == _Step.chooser) ...[
                for (final provider in ['Google', 'Facebook'])
                  OutlinedButton(
                      onPressed: null,
                      child: Text(
                          t('使用 $provider 帳戶繼續', 'Continue with $provider'))),
                const SizedBox(height: 12),
                FilledButton(
                    onPressed: () => go(_Step.login),
                    child:
                        Text(t('使用電子郵件帳戶繼續', 'Continue with Email account'))),
              ] else ...[
                if (step == _Step.register) field(name, t('用戶名', 'Username')),
                if (step == _Step.forgot)
                  Text(t('輸入電子郵件以取得重設密碼的驗證碼。',
                      'Enter your email to request a password reset code.')),
                if ([_Step.login, _Step.register, _Step.forgot].contains(step))
                  field(email, t('電子郵件', 'Email'),
                      type: TextInputType.emailAddress),
                if ([_Step.verify, _Step.reset].contains(step))
                  field(code, t('驗證碼', 'Verification code'),
                      type: TextInputType.number),
                if ([_Step.login, _Step.register, _Step.reset].contains(step))
                  field(password, t('密碼', 'Password'), secret: true),
                if ([_Step.register, _Step.reset].contains(step))
                  field(confirmation, t('確認密碼', 'Confirm password'),
                      secret: true),
                if (step == _Step.login)
                  Wrap(
                      crossAxisAlignment: WrapCrossAlignment.center,
                      children: [
                        Checkbox(
                            value: remember,
                            onChanged: busy
                                ? null
                                : (v) => setState(() => remember = v!)),
                        Text(t('記住我', 'Remember me')),
                        TextButton(
                            onPressed: busy ? null : () => go(_Step.forgot),
                            child: Text(t('忘記密碼？', 'Forgot password?'))),
                      ]),
                if (error != null)
                  Semantics(
                      liveRegion: true,
                      child: Text(error!,
                          style: TextStyle(
                              color: Theme.of(context).colorScheme.error))),
                FilledButton(
                    style: FilledButton.styleFrom(
                        backgroundColor: accent,
                        foregroundColor:
                            widget.vendor ? Colors.black : Colors.white),
                    onPressed: busy ? null : submit,
                    child: Text(step == _Step.login
                        ? t('登錄', 'Login')
                        : step == _Step.register
                            ? t('確定', 'Sure')
                            : t('繼續', 'Continue'))),
                if (step == _Step.login)
                  OutlinedButton(
                      onPressed: busy ? null : () => go(_Step.register),
                      child: Text(t('創建帳戶', 'Create account'))),
              ],
            ]),
      ),
    );
  }
}
