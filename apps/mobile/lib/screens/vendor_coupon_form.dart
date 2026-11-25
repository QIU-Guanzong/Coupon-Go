import 'package:flutter/foundation.dart' show listEquals;
import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';

import '../api_client.dart';
import '../language.dart';
import '../theme.dart';
import '../vendor_coupon_lifecycle.dart';
import '../widgets/merchant_layout.dart';
import '../widgets/widgets.dart';
import 'coupon_branch_scope.dart';

class IssueCouponScreen extends StatefulWidget {
  const IssueCouponScreen({
    super.key,
    required this.api,
    this.now,
    this.editCoupon,
    this.copyCoupon,
    this.pickCouponImage,
  }) : assert(editCoupon == null || copyCoupon == null);

  final ApiClient api;
  final DateTime Function()? now;

  /// A freshly read, merchant-authorized coupon, never a public listing row.
  final Map<String, dynamic>? editCoupon;

  /// Reuses only offer fields. A copy still requires fresh stock and dates.
  final Map<String, dynamic>? copyCoupon;
  final Future<XFile?> Function()? pickCouponImage;

  @override
  State<IssueCouponScreen> createState() => _IssueCouponScreenState();
}

class _IssueCouponScreenState extends State<IssueCouponScreen> {
  final _name = TextEditingController();
  final _nameEn = TextEditingController();
  final _perUserLimit = TextEditingController(text: '1');
  List<Map<String, dynamic>>? _categories;
  int? _categoryId;
  bool _categoriesFailed = false;

  int? get _validatedLimit {
    final text = _perUserLimit.text.trim();
    if (!RegExp(r'^[0-9]+$').hasMatch(text)) return null;
    final value = int.tryParse(text);
    return value != null && value > 0 ? value : null;
  }

  String _type = 'Discount';
  final _percentage = TextEditingController(text: '85');
  final _cash = TextEditingController();
  final _salePrice = TextEditingController();
  final _regularPrice = TextEditingController();
  final _discountPrice = TextEditingController();
  String _image = '';
  String _refundPolicy = 'anytime';
  String? _imageError;
  bool _uploading = false;
  String? _amountError;
  String? _typeChanged;
  bool _editReadOnly = false;
  Map<String, dynamic>? _copyDraft;
  String? _editConflict;
  bool _invalidEdit = false;
  bool _editSessionInvalid = false;
  Map<String, dynamic> _editBaseline = const {};

  bool get _editing => widget.editCoupon != null;
  bool get _preservesLegacyFreePrice =>
      _editing &&
      ((widget.editCoupon?['sale_price'] as num?) ?? 0) == 0 &&
      (_salePrice.text.trim().isEmpty || _parseAmount(_salePrice) == 0);
  bool get _controlsLocked =>
      _submitting ||
      _uploading ||
      _createUncertain ||
      _editReadOnly ||
      _editSessionInvalid;
  int? get _editCouponId =>
      (widget.editCoupon?['coupon_id'] ?? widget.editCoupon?['id']) as int?;
  String get _listingStatus => widget.editCoupon?['status'] as String? ?? '';
  static const _refundPolicies = ['anytime', 'expiry_only', 'non_refundable'];
  static const _reviewStates = [
    'active',
    'paused',
    'scheduled',
    'pending_review'
  ];
  final _gift = TextEditingController();
  final _total = TextEditingController(text: '100');
  final _minSpend = TextEditingController(text: '0');
  final _terms = TextEditingController();
  int? _shopId;
  List<Map<String, dynamic>>? _shops;
  List<int>? _branchIds;
  String? _shopsError;
  bool _submitting = false;
  bool _createUncertain = false;
  bool _dirty = false;
  late int _session;
  DateTime? _validFrom;
  late DateTime _validTo;
  String? _salePriceError;
  String? _dateError;
  static const _hkOffset = Duration(hours: 8);

  DateTime _nowUtc() => (widget.now?.call() ?? DateTime.now()).toUtc();

  DateTime _hongKongClock(DateTime instant) => instant.toUtc().add(_hkOffset);

  DateTime _fromHongKong(DateTime day, TimeOfDay clock) =>
      DateTime.utc(day.year, day.month, day.day, clock.hour, clock.minute)
          .subtract(_hkOffset);

  String _formatHongKong(DateTime instant) => _hongKongClock(instant)
      .toIso8601String()
      .substring(0, 16)
      .replaceFirst('T', ' ');

  double? _parseAmount(TextEditingController field, {bool positive = false}) {
    final text = field.text.trim();
    if (!RegExp(r'^\d+(?:\.\d{1,2})?$').hasMatch(text)) return null;
    final price = double.tryParse(text);
    return price != null &&
            price.isFinite &&
            (!positive || price > 0) &&
            price <= 99999999.99
        ? price
        : null;
  }

  double? _parseSalePrice() => _parseAmount(_salePrice, positive: true);

  DateTime? _serverDate(Object? raw) {
    if (raw is! String || raw.isEmpty) return null;
    final hasOffset = RegExp(r'(?:[zZ]|[+-]\d\d:?\d\d)$').hasMatch(raw);
    return DateTime.tryParse(hasOffset ? raw : '${raw}Z')?.toUtc();
  }

  void _seed(Map<String, dynamic> source) {
    void set(TextEditingController c, String field) {
      if (source[field] case final Object value) c.text = '$value';
    }

    set(_name, 'coupon_name');
    set(_nameEn, 'name_en');
    set(_perUserLimit, 'per_user_limit');
    set(_percentage, 'percentage');
    set(_cash, 'cash_amount');
    set(_salePrice, 'sale_price');
    set(_regularPrice, 'regular_price');
    set(_discountPrice, 'discount_price');
    set(_gift, 'gift');
    set(_minSpend, 'min_spend');
    set(_terms, 'additional_terms');
    final kind = source['coupon_type'];
    if (kind is String &&
        ['Discount', 'Cash', 'Gift', 'Price'].contains(kind)) {
      _type = kind;
    }
    if (source['category_id'] is int) {
      _categoryId = source['category_id'] as int;
    }
    if (source['image'] is String) _image = source['image'] as String;
    final refund = source['refund_policy'];
    if (refund is String && _refundPolicies.contains(refund)) {
      _refundPolicy = refund;
    }
    if (_editing) {
      if (source['shop_id'] is int) _shopId = source['shop_id'] as int;
      set(_total, 'total');
      _validFrom = _serverDate(source['valid_from']);
      final end = _serverDate(source['valid_to']);
      if (end != null) _validTo = end;
      _invalidEdit = _validFrom == null ||
          end == null ||
          _shopId == null ||
          (_editCouponId ?? 0) < 1 ||
          !VendorCouponAction.canEditListing(_listingStatus);
      if (!_invalidEdit) {
        _editBaseline = Map.unmodifiable(_body(_validFrom!, _parseSalePrice()));
      }
    }
  }

  @override
  void initState() {
    super.initState();
    _validTo = _nowUtc().add(const Duration(days: 30));
    if ((widget.editCoupon ?? widget.copyCoupon) case final seed?) _seed(seed);
    if (widget.copyCoupon != null) _total.clear();
    _session = widget.api.sessionRevision;
    widget.api.addListener(_sessionChanged);
    for (final controller in [
      _name,
      _nameEn,
      _perUserLimit,
      _percentage,
      _cash,
      _salePrice,
      _regularPrice,
      _discountPrice,
      _gift,
      _total,
      _minSpend,
      _terms,
    ]) {
      controller.addListener(_markDirty);
    }
    if (!_editing) _loadShops();
    if (!_invalidEdit) _loadCategories();
  }

  String _categoryLabel(Map<String, dynamic> row) => context.tr(
        row['name'] as String,
        row['name_en'] is String && (row['name_en'] as String).trim().isNotEmpty
            ? row['name_en'] as String
            : row['name'] as String,
      );

  Future<void> _loadCategories() async {
    final session = _session;
    setState(() => _categoriesFailed = false);
    try {
      final payload = await widget.api.get('/get-category-list');
      final rows = payload['categories'];
      if (rows is! List) throw const FormatException();
      final ids = <int>{};
      final categories = <Map<String, dynamic>>[];
      for (final row in rows) {
        if (row is! Map ||
            row['id'] is! int ||
            row['name'] is! String ||
            (row['name'] as String).trim().isEmpty ||
            !ids.add(row['id'] as int)) {
          throw const FormatException();
        }
        categories.add(Map<String, dynamic>.from(row));
      }
      if (!mounted || session != _session) return;
      setState(() => _categories = categories);
    } catch (_) {
      if (!mounted || session != _session) return;
      setState(() => _categoriesFailed = true);
    }
  }

  void _markDirty() {
    if (mounted && (_editing || !_dirty)) setState(() => _dirty = true);
  }

  Future<bool> _canLeave() async {
    if (_submitting || _uploading) return false;
    if (!_dirty) return true;
    return await showDialog<bool>(
          context: context,
          builder: (dialogContext) => AlertDialog(
            title: Text(context.tr('放棄尚未儲存的內容？', 'Discard unsaved changes?')),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(dialogContext, false),
                  child: Text(context.tr('繼續編輯', 'Keep editing'))),
              TextButton(
                  onPressed: () => Navigator.pop(dialogContext, true),
                  child: Text(context.tr('放棄', 'Discard'))),
            ],
          ),
        ) ??
        false;
  }

  void _sessionChanged() {
    if (_session == widget.api.sessionRevision) return;
    _session = widget.api.sessionRevision;
    if (mounted && _editing) {
      setState(() {
        _editSessionInvalid = true;
        _dirty = false;
        _submitting = false;
        _uploading = false;
      });
      return;
    }
    if (mounted) {
      setState(() {
        _shops = null;
        _shopId = null;
        _branchIds = null;
        _submitting = false;
        _createUncertain = false;
        _uploading = false;
        _shopsError = context.tr(
            '登入狀態已變更，請重新讀取商店。', 'Your session changed. Reload shops.');
      });
    }
  }

  Future<void> _loadShops() async {
    final session = _session;
    setState(() {
      _shops = null;
      _shopsError = null;
      _shopId = null;
      _branchIds = null;
    });
    try {
      final data = await widget.api.get('/merchant/shops');
      final rows = data['shops'];
      if (rows is! List ||
          rows.any(
              (v) => v is! Map || v['id'] is! int || v['name'] is! String)) {
        throw const FormatException();
      }
      if (!mounted || session != _session) return;
      setState(() {
        _shops = rows
            .where((v) => (v as Map)['can_manage_coupons'] == true)
            .map((v) => Map<String, dynamic>.from(v as Map))
            .toList();
        if (widget.copyCoupon case final copied?) {
          final originalShop = copied['shop_id'];
          if (_shops!.any((shop) => shop['id'] == originalShop)) {
            _shopId = originalShop as int;
          }
        } else if (_shops!.isNotEmpty) {
          _shopId = _shops!.first['id'] as int;
        }
      });
    } catch (e) {
      if (mounted && session == _session) {
        setState(() => _shopsError = e is ApiException
            ? e.message
            : context.tr('未能讀取商店，請重試', 'Could not load shops. Try again.'));
      }
    }
  }

  @override
  void dispose() {
    widget.api.removeListener(_sessionChanged);
    for (final controller in [
      _name,
      _nameEn,
      _perUserLimit,
      _percentage,
      _cash,
      _salePrice,
      _regularPrice,
      _discountPrice,
      _gift,
      _total,
      _minSpend,
      _terms,
    ]) {
      controller.dispose();
    }
    super.dispose();
  }

  Future<void> _pickValidFrom() async {
    final session = _session;
    final today = _hongKongClock(_nowUtc());
    final previous = _validFrom == null ? today : _hongKongClock(_validFrom!);
    final last = today.add(const Duration(days: 365));
    final initial = previous.isBefore(today)
        ? today
        : previous.isAfter(last)
            ? last
            : previous;
    final day = await showDatePicker(
      context: context,
      initialDate: initial,
      firstDate: today,
      currentDate: today,
      lastDate: last,
    );
    if (day == null || !mounted || session != _session) return;
    final time = await showTimePicker(
      context: context,
      initialTime: TimeOfDay.fromDateTime(initial),
    );
    if (time == null || !mounted || session != _session) return;
    setState(() {
      _validFrom = _fromHongKong(day, time);
      _dateError = null;
      _dirty = true;
    });
  }

  Map<String, dynamic> _body(DateTime validFrom, double? salePrice) => {
        'shop_id': _shopId,
        if (!_editing) 'branch_ids': List<int>.unmodifiable(_branchIds!),
        'coupon_type': _type,
        'coupon_name': _name.text,
        'name_en': _nameEn.text.trim(),
        if (_categoryId != null) 'category_id': _categoryId,
        'per_user_limit': _validatedLimit,
        'percentage':
            _type == 'Discount' ? double.tryParse(_percentage.text) : null,
        'cash_amount': _type == 'Cash' ? double.tryParse(_cash.text) : null,
        if (_type == 'Price') 'sale_price': salePrice,
        if (_type == 'Price' && _parseAmount(_regularPrice) != null)
          'regular_price': _parseAmount(_regularPrice),
        if (_type == 'Price' && _parseAmount(_discountPrice) != null)
          'discount_price': _parseAmount(_discountPrice),
        if (_type == 'Price') 'refund_policy': _refundPolicy,
        'image': _image,
        if (_type == 'Gift') 'gift': _gift.text.trim(),
        'min_spend': double.tryParse(_minSpend.text) ?? 0,
        'total': int.parse(_total.text.trim()),
        'additional_terms': _terms.text,
        'valid_from': validFrom.toIso8601String(),
        'valid_to': _validTo.toIso8601String(),
      };

  void _clearTypeFields(String next) {
    final other = <TextEditingController>[
      if (next != 'Discount') _percentage,
      if (next != 'Cash') _cash,
      if (next != 'Gift') _gift,
      if (next != 'Price') ...[_salePrice, _regularPrice, _discountPrice],
    ];
    final cleared = other.any((field) => field.text.isNotEmpty);
    for (final field in other) {
      field.clear();
    }
    setState(() {
      _type = next;
      _dirty = true;
      _salePriceError = null;
      _amountError = null;
      if (next != 'Price') _refundPolicy = 'anytime';
      _typeChanged = cleared
          ? context.tr('已清除上一種券類型的資料。',
              'Fields for the previous coupon type were cleared.')
          : null;
    });
  }

  Future<void> _chooseImage() async {
    if (_controlsLocked) return;
    final session = _session;
    setState(() {
      _uploading = true;
      _imageError = null;
    });
    try {
      final file = await (widget.pickCouponImage?.call() ??
          ImagePicker().pickImage(source: ImageSource.gallery));
      if (!mounted || session != _session || file == null) return;
      if (await file.length() > 5 * 1024 * 1024) {
        throw const FormatException('Image exceeds 5 MB');
      }
      final bytes = await file.readAsBytes();
      if (!mounted || session != _session) return;
      final url = await widget.api.uploadCouponImage(file.name, bytes);
      if (!mounted || session != _session) return;
      setState(() {
        _image = url;
        _dirty = true;
      });
    } on ApiException catch (e) {
      if (mounted && session == _session) {
        setState(() => _imageError = e.message);
      }
    } catch (_) {
      if (mounted && session == _session) {
        setState(() => _imageError = context.tr(
            '未能加入圖片。請選擇 5 MB 內的 JPG、PNG 或 WebP。',
            'Choose a JPG, PNG or WebP image under 5 MB and try again.'));
      }
    } finally {
      if (mounted && session == _session) setState(() => _uploading = false);
    }
  }

  Widget _statusNote() => Text(switch (_listingStatus) {
        'active' || 'paused' || 'scheduled' => context.tr(
            '儲存後會重新提交審核，審核期間不會在公開列表顯示。',
            'Saving sends the coupon back for review and removes it from public lists until approved.'),
        'pending_review' =>
          context.tr('儲存後仍然待審核。', 'The coupon will remain in review.'),
        _ => context.tr('儲存後為草稿，須另行提交審核。',
            'The coupon will remain a draft until you submit it for review.'),
      });

  String _refundLabel(String policy) => switch (policy) {
        'non_refundable' => context.tr('不可退款', 'Non-refundable'),
        'expiry_only' => context.tr('到期可退', 'Refundable on expiry'),
        _ => context.tr('未用可退', 'Refundable before use'),
      };

  String _fieldLabel(String key) => switch (key) {
        'coupon_name' => context.tr('優惠券名稱', 'Coupon name'),
        'name_en' => context.tr('優惠券英文名稱', 'English coupon name'),
        'category_id' => context.tr('分類', 'Category'),
        'image' => context.tr('優惠券圖片', 'Coupon image'),
        'percentage' => context.tr('付款比例', 'Payment percentage'),
        'cash_amount' => context.tr('現金金額', 'Cash amount'),
        'gift' => context.tr('贈品內容', 'Gift details'),
        'sale_price' => context.tr('售價', 'Sale price'),
        'regular_price' => context.tr('原價', 'Regular price'),
        'discount_price' => context.tr('券面優惠價', 'Coupon redemption price'),
        'per_user_limit' => context.tr('每人限領', 'Claim limit per person'),
        'min_spend' => context.tr('需消費金額', 'Minimum spend'),
        'total' => context.tr('數量', 'Quantity'),
        'additional_terms' => context.tr('使用條款', 'Terms of use'),
        'valid_from' => context.tr('生效時間', 'Start time'),
        'valid_to' => context.tr('到期時間', 'Expiry'),
        'refund_policy' => context.tr('退款政策', 'Refund policy'),
        _ => key,
      };

  String _reviewValue(String key, Object? value) {
    if (value == null || value == '') return '—';
    if (key == 'image') return context.tr('已有圖片', 'Image selected');
    if (key == 'refund_policy') return _refundLabel(value as String);
    if ([
          'cash_amount',
          'sale_price',
          'regular_price',
          'discount_price',
          'min_spend'
        ].contains(key) &&
        value is num) {
      return formatHkd(value.toDouble());
    }
    if (key == 'valid_from' || key == 'valid_to') {
      if (_serverDate(value) case final date?) return _formatHongKong(date);
    }
    if (key == 'category_id') {
      final cats = _categories?.where((row) => row['id'] == value);
      if (cats != null && cats.isNotEmpty) return _categoryLabel(cats.first);
    }
    return '$value';
  }

  Widget? _changedMarker(String field, Object? current) {
    if (!_editing || _editBaseline[field] == current) return null;
    return Icon(Icons.circle,
        color: Colors.blue.shade700,
        size: 9,
        semanticLabel: context.tr('已修改', 'Changed'));
  }

  Future<bool> _showEditReview(Map<String, dynamic> changes) async {
    return await showDialog<bool>(
          context: context,
          builder: (dialog) => AlertDialog(
            title: Text(dialog.tr('核對修改', 'Review changes')),
            content: SingleChildScrollView(
              child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    for (final entry in changes.entries)
                      ListTile(
                        contentPadding: EdgeInsets.zero,
                        title: Text(_fieldLabel(entry.key)),
                        subtitle: Text(
                            '${_reviewValue(entry.key, _editBaseline[entry.key])} → ${_reviewValue(entry.key, entry.value)}'),
                      ),
                  ]),
            ),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(dialog, false),
                  child: Text(dialog.tr('返回編輯', 'Keep editing'))),
              FilledButton(
                  onPressed: () => Navigator.pop(dialog, true),
                  child: Text(dialog.tr('確認儲存', 'Confirm save'))),
            ],
          ),
        ) ==
        true;
  }

  Future<void> _createCopy(Map<String, dynamic> draft) async {
    if (!mounted || _editSessionInvalid) return;
    await Navigator.of(context).push(MaterialPageRoute(
        builder: (_) => IssueCouponScreen(
            api: widget.api,
            now: widget.now,
            copyCoupon: draft,
            pickCouponImage: widget.pickCouponImage)));
  }

  Future<void> _saveEdit(Map<String, dynamic> draft, int session) async {
    final id = _editCouponId;
    if (id == null || _invalidEdit || _controlsLocked) return;
    // Omitting nulls is the server contract; they do not clear optional values.
    final patch = <String, dynamic>{};
    for (final entry in draft.entries) {
      if (entry.key == 'shop_id' ||
          entry.key == 'coupon_type' ||
          entry.key == 'branch_ids') {
        continue;
      }
      if (entry.value != null && entry.value != _editBaseline[entry.key]) {
        patch[entry.key] = entry.value;
      }
    }
    final clearingOptional = [
      'category_id',
      'regular_price',
      'discount_price',
      'cash_amount',
      'percentage'
    ].any((key) => _editBaseline[key] != null && draft[key] == null);
    if (clearingOptional) {
      showCgToast(
          context,
          context.tr('這個欄位不能清空，請輸入新值。',
              'This field cannot be cleared. Enter a new value.'));
      return;
    }
    if (patch.isEmpty) {
      showCgToast(
          context, context.tr('沒有需要儲存的修改。', 'There are no changes to save.'));
      return;
    }
    setState(() => _submitting = true);
    try {
      final confirmed = await _showEditReview(patch);
      if (!confirmed || !mounted || session != _session) return;
      final changedStart = _serverDate(patch['valid_from']);
      if (changedStart != null && !changedStart.isAfter(_nowUtc())) {
        setState(() => _dateError = context.tr('定時上架時間已過，請重新選擇',
            'The scheduled start has passed. Choose another time.'));
        return;
      }
      final saved = await widget.api.editVendorCoupon(id, patch);
      if (!mounted || session != _session) return;
      final parsed = saved['coupon'];
      final status = parsed is Map ? parsed['status'] : null;
      final allowedStatus =
          _reviewStates.contains(_listingStatus) ? 'pending_review' : 'draft';
      if (parsed is! Map ||
          (parsed['coupon_id'] ?? parsed['id']) != id ||
          parsed['shop_id'] != _shopId ||
          status != allowedStatus) {
        throw const FormatException('Unconfirmed coupon edit receipt');
      }
      setState(() {
        _dirty = false;
        _submitting = false;
      });
      Navigator.of(context).pop(Map<String, dynamic>.from(parsed));
    } on ApiException catch (error) {
      if (!mounted || session != _session) return;
      if (error.status == 409) {
        setState(() {
          _editReadOnly = true;
          _copyDraft = draft;
          _editConflict = error.message;
          _submitting = false;
        });
        final copy = await showDialog<bool>(
          context: context,
          builder: (dialog) => AlertDialog(
            title: Text(dialog.tr('需要建立新券', 'Create a new coupon')),
            content: Text(error.message),
            actions: [
              TextButton(
                  onPressed: () => Navigator.pop(dialog, false),
                  child: Text(dialog.tr('返回', 'Back'))),
              FilledButton(
                  onPressed: () => Navigator.pop(dialog, true),
                  child: Text(dialog.tr('建立新券', 'Create new coupon'))),
            ],
          ),
        );
        if (copy == true && mounted && session == _session) {
          await _createCopy(draft);
        }
      } else if (error.status >= 500) {
        setState(() => _createUncertain = true);
      } else {
        showCgToast(context, error.message);
      }
    } catch (_) {
      if (mounted && session == _session) {
        setState(() => _createUncertain = true);
      }
    } finally {
      if (mounted && session == _session) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return PopScope(
        canPop: !_dirty && !_submitting && !_uploading,
        onPopInvokedWithResult: (didPop, _) async {
          if (!didPop && await _canLeave() && context.mounted) {
            Navigator.of(context).pop();
          }
        },
        child: Scaffold(
          appBar: AppBar(
            title: Text(_editing
                ? context.tr('編輯優惠券', 'Edit coupon')
                : context.tr('發券', 'Issue coupon')),
          ),
          body: _invalidEdit || _editSessionInvalid
              ? Center(
                  child: Text(context.tr('無法編輯此優惠券。請返回詳情。',
                      'This coupon cannot be edited. Return to details.')))
              : Center(
                  child: ConstrainedBox(
                    constraints:
                        const BoxConstraints(maxWidth: CgTheme.formMaxWidth),
                    child: ListView(
                      padding: const EdgeInsets.all(CgTheme.pageInset),
                      children: [
                        Text(context.tr('券基本資料', 'Coupon details'),
                            style: Theme.of(context).textTheme.titleMedium),
                        const SizedBox(height: CgTheme.space12),
                        if (_editing) ...[
                          _statusNote(),
                          if (_editConflict != null)
                            Text(_editConflict!,
                                style: TextStyle(
                                    color:
                                        Theme.of(context).colorScheme.error)),
                          const SizedBox(height: CgTheme.space12),
                          Text(context.tr(
                              '商店：${widget.editCoupon?['shop_name'] ?? ''}',
                              'Shop: ${widget.editCoupon?['shop_name'] ?? ''}')),
                        ],
                        if (_shopsError != null) ...[
                          Text(_shopsError!),
                          TextButton(
                              onPressed: _loadShops,
                              child: Text(context.tr('重試', 'Retry')))
                        ],
                        if (!_editing && _shops == null && _shopsError == null)
                          const LinearProgressIndicator(),
                        if (_shops != null && _shops!.isEmpty)
                          Text(context.tr(
                              '此帳戶尚未有可管理的商店。', 'No shops to manage yet.')),
                        if (_shops != null && _shops!.isNotEmpty)
                          DropdownButtonFormField<int>(
                            isExpanded: true,
                            initialValue: _shopId,
                            decoration: InputDecoration(
                                labelText: context.tr('商店', 'Shop')),
                            items: _shops!
                                .map((shop) => DropdownMenuItem<int>(
                                    value: shop['id'] as int,
                                    child: Text(shop['name'] as String)))
                                .toList(),
                            onChanged: (id) => setState(() {
                              if (id != _shopId) _dirty = true;
                              _shopId = id;
                              _branchIds = null;
                            }),
                          ),
                        const SizedBox(height: CgTheme.space12),
                        TextField(
                            enabled: !_editing || !_controlsLocked,
                            controller: _name,
                            decoration: InputDecoration(
                                prefixIcon:
                                    _changedMarker('coupon_name', _name.text),
                                labelText: context.tr('優惠券名稱', 'Coupon name'))),
                        const SizedBox(height: CgTheme.space12),
                        TextField(
                          enabled: !_editing || !_controlsLocked,
                          controller: _nameEn,
                          maxLength: 160,
                          decoration: InputDecoration(
                              prefixIcon: _changedMarker(
                                  'name_en', _nameEn.text.trim()),
                              labelText: context.tr('優惠券英文名稱（選填）',
                                  'English coupon name (optional)')),
                        ),
                        if (_categoriesFailed)
                          TextButton(
                            onPressed: _loadCategories,
                            child:
                                Text(context.tr('重新讀取分類', 'Reload categories')),
                          ),
                        if (_categories?.isNotEmpty == true) ...[
                          const SizedBox(height: CgTheme.space12),
                          DropdownButtonFormField<int>(
                            isExpanded: true,
                            key: const ValueKey('issue-category'),
                            initialValue: _categories!
                                    .any((row) => row['id'] == _categoryId)
                                ? _categoryId
                                : null,
                            decoration: InputDecoration(
                                labelText: context.tr(
                                    '優惠券分類（選填）', 'Coupon category (optional)')),
                            items: [
                              DropdownMenuItem<int>(
                                child: Text(context.tr('不設定分類', 'No category')),
                              ),
                              for (final row in _categories!)
                                DropdownMenuItem<int>(
                                  value: row['id'] as int,
                                  child: Text(_categoryLabel(row)),
                                ),
                            ],
                            onChanged: (value) {
                              if (value == _categoryId) return;
                              setState(() {
                                _categoryId = value;
                                _dirty = true;
                              });
                            },
                          ),
                        ],
                        const SizedBox(height: CgTheme.space12),
                        if (_image.isNotEmpty)
                          CatalogPhoto(
                              url: _image,
                              height: 100,
                              apiBaseUrl: widget.api.baseUrl),
                        OutlinedButton.icon(
                          key: const ValueKey('issue-coupon-image'),
                          onPressed: _controlsLocked ? null : _chooseImage,
                          icon: const Icon(Icons.image_outlined),
                          label: Text(_uploading
                              ? context.tr('正在加入圖片…', 'Adding image…')
                              : _image.isEmpty
                                  ? context.tr('加入優惠券圖片', 'Add coupon image')
                                  : context.tr(
                                      '更換優惠券圖片', 'Replace coupon image')),
                        ),
                        if (_imageError != null)
                          Text(_imageError!,
                              style: TextStyle(
                                  color: Theme.of(context).colorScheme.error)),
                        const SizedBox(height: CgTheme.space24),
                        Text(context.tr('價格與數量', 'Price and quantity'),
                            style: Theme.of(context).textTheme.titleMedium),
                        const SizedBox(height: CgTheme.space12),
                        TextField(
                          enabled: !_editing || !_controlsLocked,
                          controller: _perUserLimit,
                          keyboardType: TextInputType.number,
                          onChanged: (_) => setState(() {}),
                          decoration: InputDecoration(
                            prefixIcon: _changedMarker(
                                'per_user_limit', _validatedLimit),
                            labelText:
                                context.tr('每人限領', 'Claim limit per person'),
                            errorText: _validatedLimit == null
                                ? context.tr('請輸入大於 0 的整數',
                                    'Enter a positive whole number')
                                : null,
                          ),
                        ),
                        const SizedBox(height: CgTheme.space12),
                        DropdownButtonFormField<String>(
                          isExpanded: true,
                          key: const ValueKey('issue-coupon-type'),
                          initialValue: _type,
                          decoration: InputDecoration(
                              labelText: context.tr('券類型', 'Coupon type')),
                          items: [
                            DropdownMenuItem(
                                value: 'Cash',
                                child:
                                    Text(context.tr('現金優惠券', 'Cash coupon'))),
                            DropdownMenuItem(
                                value: 'Discount',
                                child: Text(
                                    context.tr('折扣優惠券', 'Discount coupon'))),
                            DropdownMenuItem(
                                value: 'Gift',
                                child:
                                    Text(context.tr('贈品優惠券', 'Gift coupon'))),
                            DropdownMenuItem(
                                value: 'Price',
                                child:
                                    Text(context.tr('付費優惠券', 'Paid coupon'))),
                          ],
                          onChanged: _editing || _controlsLocked
                              ? null
                              : (next) {
                                  if (next != null && next != _type) {
                                    _clearTypeFields(next);
                                  }
                                },
                        ),
                        const SizedBox(height: CgTheme.space12),
                        if (_type == 'Discount')
                          TextField(
                              enabled: !_editing || !_controlsLocked,
                              controller: _percentage,
                              keyboardType: TextInputType.number,
                              decoration: InputDecoration(
                                  prefixIcon: _changedMarker('percentage',
                                      double.tryParse(_percentage.text)),
                                  labelText: context.tr('付款比例（85 即 8.5 折）',
                                      'Percent of regular price (85 = 15% off)'))),
                        if (_type == 'Cash')
                          TextField(
                              enabled: !_editing || !_controlsLocked,
                              controller: _cash,
                              keyboardType: TextInputType.number,
                              decoration: InputDecoration(
                                  prefixIcon: _changedMarker('cash_amount',
                                      double.tryParse(_cash.text)),
                                  labelText:
                                      context.tr('現金金額', 'Cash amount'))),
                        if (_type == 'Price')
                          TextField(
                            key: const ValueKey('issue-sale-price'),
                            enabled: !_editing || !_controlsLocked,
                            controller: _salePrice,
                            keyboardType: const TextInputType.numberWithOptions(
                                decimal: true),
                            decoration: InputDecoration(
                              prefixIcon: _changedMarker(
                                  'sale_price', _parseSalePrice()),
                              labelText:
                                  context.tr('售價（港幣）', 'Sale price (HKD)'),
                              errorText: _salePriceError,
                            ),
                            onChanged: (_) {
                              if (_salePriceError != null) {
                                setState(() => _salePriceError = null);
                              }
                            },
                          ),
                        if (_typeChanged != null) Text(_typeChanged!),
                        if (_type == 'Price') ...[
                          const SizedBox(height: CgTheme.space12),
                          TextField(
                            key: const ValueKey('issue-regular-price'),
                            controller: _regularPrice,
                            enabled: !_editing || !_controlsLocked,
                            keyboardType: const TextInputType.numberWithOptions(
                                decimal: true),
                            decoration: InputDecoration(
                                prefixIcon: _changedMarker('regular_price',
                                    _parseAmount(_regularPrice)),
                                labelText: context.tr(
                                    '原價（選填）', 'Regular price (optional)')),
                          ),
                          const SizedBox(height: CgTheme.space12),
                          TextField(
                            key: const ValueKey('issue-discount-price'),
                            controller: _discountPrice,
                            enabled: !_editing || !_controlsLocked,
                            keyboardType: const TextInputType.numberWithOptions(
                                decimal: true),
                            decoration: InputDecoration(
                                prefixIcon: _changedMarker('discount_price',
                                    _parseAmount(_discountPrice)),
                                labelText: context.tr('券面優惠價（選填）',
                                    'Coupon redemption price (optional)')),
                          ),
                        ],
                        if (_amountError != null)
                          Text(_amountError!,
                              style: TextStyle(
                                  color: Theme.of(context).colorScheme.error)),
                        if (_type == 'Gift')
                          TextField(
                            key: const ValueKey('issue-gift'),
                            enabled: !_editing || !_controlsLocked,
                            controller: _gift,
                            decoration: InputDecoration(
                              prefixIcon:
                                  _changedMarker('gift', _gift.text.trim()),
                              labelText: context.tr('贈品內容', 'Gift details'),
                            ),
                          ),
                        const SizedBox(height: CgTheme.space12),
                        TextField(
                            enabled: !_editing || !_controlsLocked,
                            controller: _total,
                            keyboardType: TextInputType.number,
                            decoration: InputDecoration(
                                prefixIcon: _changedMarker(
                                    'total', int.tryParse(_total.text)),
                                labelText: context.tr('數量', 'Quantity'))),
                        const SizedBox(height: CgTheme.space12),
                        TextField(
                            enabled: !_editing || !_controlsLocked,
                            controller: _minSpend,
                            keyboardType: TextInputType.number,
                            decoration: InputDecoration(
                                prefixIcon: _changedMarker('min_spend',
                                    double.tryParse(_minSpend.text) ?? 0),
                                labelText:
                                    context.tr('需消費金額', 'Minimum spend'))),
                        const SizedBox(height: CgTheme.space24),
                        Text(context.tr('有效期與使用條款', 'Validity and terms'),
                            style: Theme.of(context).textTheme.titleMedium),
                        const SizedBox(height: CgTheme.space12),
                        TextField(
                            enabled: !_editing || !_controlsLocked,
                            controller: _terms,
                            decoration: InputDecoration(
                                prefixIcon: _changedMarker(
                                    'additional_terms', _terms.text),
                                labelText: context.tr(
                                    '條款和條件', 'Terms and conditions'))),
                        if (!_editing && _shopId != null)
                          const SizedBox(height: CgTheme.space12),
                        if (!_editing && _shopId != null)
                          BranchScopePicker(
                            key: ValueKey(_shopId),
                            api: widget.api,
                            shopId: _shopId!,
                            initial: const [],
                            onChanged: (ids) => setState(() {
                              if (_branchIds != null &&
                                  !listEquals(_branchIds, ids)) {
                                _dirty = true;
                              }
                              _branchIds = ids;
                            }),
                          ),
                        const SizedBox(height: CgTheme.space12),
                        ListTile(
                          contentPadding: EdgeInsets.zero,
                          key: const ValueKey('issue-valid-from'),
                          title: Text(context.tr(
                              '生效時間（香港時間）', 'Start time (Hong Kong time)')),
                          subtitle: Text(_validFrom == null
                              ? context.tr('審核通過後上架', 'List after approval')
                              : _formatHongKong(_validFrom!)),
                          trailing: _validFrom == null || _editing
                              ? const Icon(Icons.schedule)
                              : IconButton(
                                  tooltip: context.tr(
                                      '取消定時上架', 'Remove scheduled start'),
                                  onPressed: _submitting
                                      ? null
                                      : () => setState(() {
                                            _validFrom = null;
                                            _dateError = null;
                                            _dirty = true;
                                          }),
                                  icon: const Icon(Icons.clear),
                                ),
                          onTap: _controlsLocked ? null : _pickValidFrom,
                        ),
                        const SizedBox(height: CgTheme.space12),
                        ListTile(
                          contentPadding: EdgeInsets.zero,
                          title: Text(context.tr(
                              '到期日期（香港時間）', 'Expiry date (Hong Kong time)')),
                          subtitle: Text(_formatHongKong(_validTo)),
                          trailing: const Icon(Icons.calendar_today),
                          onTap: _controlsLocked
                              ? null
                              : () async {
                                  final session = _session;
                                  final today = _hongKongClock(_nowUtc());
                                  final previous = _hongKongClock(_validTo);
                                  final last =
                                      today.add(const Duration(days: 365));
                                  final picked = await showDatePicker(
                                    context: context,
                                    initialDate: previous.isBefore(today)
                                        ? today
                                        : previous.isAfter(last)
                                            ? last
                                            : previous,
                                    firstDate: today,
                                    currentDate: today,
                                    lastDate: last,
                                  );
                                  if (picked != null &&
                                      mounted &&
                                      session == _session) {
                                    setState(() {
                                      _validTo = _fromHongKong(
                                          picked,
                                          _editing
                                              ? TimeOfDay.fromDateTime(previous)
                                              : const TimeOfDay(
                                                  hour: 0, minute: 0));
                                      _dateError = null;
                                      _dirty = true;
                                    });
                                  }
                                },
                        ),
                        if (_type == 'Price') ...[
                          const SizedBox(height: CgTheme.space12),
                          DropdownButtonFormField<String>(
                            isExpanded: true,
                            key: const ValueKey('issue-refund-policy'),
                            initialValue: _refundPolicy,
                            decoration: InputDecoration(
                                labelText: context.tr('退款政策', 'Refund policy')),
                            items: [
                              for (final value in _refundPolicies)
                                DropdownMenuItem(
                                    value: value,
                                    child: Text(_refundLabel(value)))
                            ],
                            onChanged: _controlsLocked
                                ? null
                                : (value) {
                                    if (value != null) {
                                      setState(() {
                                        _refundPolicy = value;
                                        _dirty = true;
                                      });
                                    }
                                  },
                          ),
                        ],
                        if (_dateError != null)
                          Text(_dateError!,
                              style: TextStyle(
                                  color: Theme.of(context).colorScheme.error)),
                        const SizedBox(height: CgTheme.space16),
                        if (_createUncertain)
                          Padding(
                            padding:
                                const EdgeInsets.only(bottom: CgTheme.space12),
                            child: Text(context.tr(
                              _editing
                                  ? '儲存結果未確認，請返回詳情重新載入核對。'
                                  : '提交結果未確認，請重新載入優惠券列表核對。',
                              _editing
                                  ? 'Reload coupon details to check whether your changes were saved.'
                                  : 'Refresh the coupon list to check whether the coupon was submitted.',
                            )),
                          ),
                        if (_copyDraft case final copy?)
                          OutlinedButton(
                            onPressed: () => _createCopy(copy),
                            child:
                                Text(context.tr('建立新券', 'Create new coupon')),
                          ),
                        MerchantPrimaryAction(
                          onPressed: _shopId == null ||
                                  (!_editing && _branchIds == null) ||
                                  _controlsLocked
                              ? null
                              : () async {
                                  final session = _session;
                                  if (_validatedLimit == null) {
                                    showCgToast(
                                        context,
                                        context.tr('請輸入每人限領數量',
                                            'Enter the claim limit per person'));
                                    return;
                                  }
                                  final quantity =
                                      int.tryParse(_total.text.trim());
                                  if (quantity == null ||
                                      quantity < 0 ||
                                      (!_editing && quantity == 0)) {
                                    showCgToast(
                                        context,
                                        context.tr('請輸入有效數量。',
                                            'Enter a valid quantity.'));
                                    return;
                                  }
                                  final now = _nowUtc();
                                  final scheduledStart = _validFrom;
                                  final validFrom = scheduledStart ?? now;
                                  final price = _type == 'Price'
                                      ? _parseSalePrice()
                                      : null;
                                  final priceError = _type == 'Price' &&
                                          price == null &&
                                          !_preservesLegacyFreePrice
                                      ? context.tr(
                                          '請輸入有效售價（最少 HK\$0.01，最多兩位小數）',
                                          'Enter a valid sale price (at least HK\$0.01, up to two decimals).')
                                      : null;
                                  final changedStart = !_editing ||
                                      validFrom.toIso8601String() !=
                                          _editBaseline['valid_from'];
                                  final dateError = scheduledStart != null &&
                                          changedStart &&
                                          !validFrom.isAfter(now)
                                      ? context.tr('定時上架時間必須晚於現在',
                                          'Choose a start time in the future.')
                                      : !_validTo.isAfter(validFrom)
                                          ? context.tr('到期日期必須晚於生效時間',
                                              'The expiry must be after the start time.')
                                          : null;
                                  final optionalInvalid = _type == 'Price' &&
                                      [_regularPrice, _discountPrice].any(
                                          (field) =>
                                              field.text.trim().isNotEmpty &&
                                              _parseAmount(field) == null);
                                  final emptyGift = _type == 'Gift' &&
                                      _gift.text.trim().isEmpty;
                                  final amountError = optionalInvalid
                                      ? context.tr('請輸入有效港幣金額，最多兩位小數。',
                                          'Enter a valid HKD amount with up to two decimals.')
                                      : emptyGift
                                          ? context.tr('請填寫贈品內容。',
                                              'Enter the gift details.')
                                          : null;
                                  setState(() {
                                    _salePriceError = priceError;
                                    _dateError = dateError;
                                    _amountError = amountError;
                                  });
                                  if (priceError != null ||
                                      dateError != null ||
                                      amountError != null) {
                                    return;
                                  }
                                  final draft = _body(validFrom, price);
                                  if (_editing) {
                                    await _saveEdit(
                                        Map.unmodifiable(draft), session);
                                    return;
                                  }
                                  final englishName = _nameEn.text.trim();
                                  final category = _categories
                                      ?.where((row) => row['id'] == _categoryId)
                                      .map(_categoryLabel)
                                      .firstOrNull;
                                  final branches = _branchIds!.length;
                                  final scope = branches == 0
                                      ? context.tr('所有分店', 'All branches')
                                      : context.tr('指定 $branches 間分店',
                                          '$branches selected branches');
                                  var creating = false;
                                  setState(() => _submitting = true);
                                  try {
                                    final preview =
                                        await widget.api.couponPreview(draft);
                                    if (!context.mounted ||
                                        session != _session) {
                                      return;
                                    }
                                    final previewAmount = price == null
                                        ? preview['amount_text']
                                        : context.tr('售價：${formatHkd(price)}',
                                            'Sale price: ${formatHkd(price)}');
                                    final ok = await showDialog<bool>(
                                      context: context,
                                      builder: (c) => AlertDialog(
                                        title: Text(context.tr(
                                            '預覽優惠券', 'Review coupon')),
                                        content: Text([
                                          '${preview['coupon_name']}',
                                          '$previewAmount',
                                          for (final key in [
                                            'regular_price',
                                            'discount_price',
                                            'refund_policy'
                                          ])
                                            if (draft.containsKey(key))
                                              '${_fieldLabel(key)}：${_reviewValue(key, draft[key])}',
                                          if ((draft['image'] as String)
                                              .isNotEmpty)
                                            context.tr('已加入優惠券圖片',
                                                'Coupon image selected'),
                                          context.tr(
                                              '商店：${preview['shop_name']}',
                                              'Shop: ${preview['shop_name']}'),
                                          context.tr('適用：$scope',
                                              'Available at: $scope'),
                                          context.tr('數量：${preview['total']}',
                                              'Quantity: ${preview['total']}'),
                                          '${context.tr('每人限領', 'Claim limit per person')}：${draft['per_user_limit']}',
                                          if (englishName.isNotEmpty)
                                            '${context.tr('英文名稱', 'English name')}：$englishName',
                                          if (category != null)
                                            '${context.tr('分類', 'Category')}：$category',
                                          if (scheduledStart != null)
                                            context.tr(
                                                '生效時間：${_formatHongKong(validFrom)}（香港時間）',
                                                'Start: ${_formatHongKong(validFrom)} (Hong Kong time)'),
                                        ].join('\n')),
                                        actions: [
                                          TextButton(
                                              onPressed: () =>
                                                  Navigator.pop(c, false),
                                              child: Text(
                                                  context.tr('取消', 'Cancel'))),
                                          TextButton(
                                              onPressed: () =>
                                                  Navigator.pop(c, true),
                                              child: Text(context.tr('提交審核',
                                                  'Submit for review'))),
                                        ],
                                      ),
                                    );
                                    if (ok == true &&
                                        context.mounted &&
                                        session == _session) {
                                      if (scheduledStart != null &&
                                          !scheduledStart.isAfter(_nowUtc())) {
                                        setState(() => _dateError = context.tr(
                                            '定時上架時間已過，請重新選擇',
                                            'The scheduled start has passed. Choose another time.'));
                                        return;
                                      }
                                      creating = true;
                                      final res =
                                          await widget.api.couponCreate(draft);
                                      if (context.mounted &&
                                          session == _session) {
                                        showCgToast(
                                            context,
                                            res['message'] as String? ??
                                                context.tr('已提交審核',
                                                    'Submitted for review'));
                                        Navigator.of(context).pop();
                                      }
                                    }
                                  } on ApiException catch (e) {
                                    if (context.mounted &&
                                        session == _session) {
                                      if (creating && e.status >= 500) {
                                        setState(() => _createUncertain = true);
                                      } else {
                                        showCgToast(context, e.message);
                                      }
                                    }
                                  } catch (_) {
                                    if (context.mounted &&
                                        session == _session) {
                                      if (creating) {
                                        setState(() => _createUncertain = true);
                                      } else {
                                        showCgToast(
                                            context,
                                            context.tr('未能連線，請重試',
                                                'Connection failed. Try again.'));
                                      }
                                    }
                                  } finally {
                                    if (mounted && session == _session) {
                                      setState(() => _submitting = false);
                                    }
                                  }
                                },
                          label: _editing
                              ? context.tr('檢查並儲存', 'Review and save')
                              : context.tr('檢查並提交', 'Review and submit'),
                        ),
                      ],
                    ),
                  ),
                ),
        ));
  }
}
