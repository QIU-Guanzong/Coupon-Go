import 'package:flutter/widgets.dart';
import 'package:shared_preferences/shared_preferences.dart';

class AppLanguage extends ChangeNotifier {
  bool english = false;
  bool? draft;

  Future<void> load() async {
    english =
        (await SharedPreferences.getInstance()).getBool('english') ?? false;
    notifyListeners();
  }

  Future<void> commit(bool value) async {
    await (await SharedPreferences.getInstance()).setBool('english', value);
    english = value;
    draft = null;
    notifyListeners();
  }
}

class LanguageScope extends InheritedNotifier<AppLanguage> {
  const LanguageScope(
      {super.key, required AppLanguage language, required super.child})
      : super(notifier: language);

  static AppLanguage of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<LanguageScope>()!.notifier!;
}

extension GuestCopy on BuildContext {
  String tr(String chinese, String english) =>
      dependOnInheritedWidgetOfExactType<LanguageScope>()?.notifier?.english ==
              true
          ? english
          : chinese;
}
