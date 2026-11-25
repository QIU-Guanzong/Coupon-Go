import 'package:flutter/material.dart';

import '../theme.dart';

/// Shared merchant presentation; callers keep ownership of business states.
class MerchantStatusPill extends StatelessWidget {
  const MerchantStatusPill({
    super.key,
    required this.label,
    required this.active,
    this.labelKey,
    this.listingStatus,
  });

  final String label;
  final bool active;
  final Key? labelKey;
  final String? listingStatus;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final dark = theme.brightness == Brightness.dark;
    final scheme = theme.colorScheme;
    final (Color foreground, Color background) = switch (listingStatus) {
      'rejected' => (scheme.onErrorContainer, scheme.errorContainer),
      'draft' || 'paused' || 'scheduled' => (
          dark ? Colors.white : const Color(0xFF4B5059),
          dark ? const Color(0xFF3A3D44) : const Color(0xFFEEEFF1),
        ),
      'expired' || 'sold_out' => (Colors.white, const Color(0xFF565861)),
      _ => (
          active
              ? (dark ? CgColors.moneyPositiveDark : CgColors.statusActiveText)
              : (dark ? CgColors.moneyPendingDark : CgColors.statusPendingText),
          dark
              ? scheme.surfaceContainerHigh
              : (active ? CgColors.statusActiveBg : CgColors.statusPendingBg),
        ),
    };
    return DecoratedBox(
      decoration: BoxDecoration(
        color: background,
        borderRadius: const BorderRadius.all(Radius.circular(999)),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(
          horizontal: CgTheme.space12,
          vertical: CgTheme.space4,
        ),
        child: Text(
          label,
          key: labelKey,
          style: theme.textTheme.labelMedium?.copyWith(
            color: foreground,
            fontWeight: FontWeight.w600,
          ),
        ),
      ),
    );
  }
}

class MerchantAmountValue extends StatelessWidget {
  const MerchantAmountValue({
    super.key,
    required this.value,
    this.emphasized = false,
  });

  final String value;
  final bool emphasized;

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    return Text(
      value,
      textAlign: TextAlign.end,
      style: (emphasized ? text.titleLarge : text.titleMedium)?.copyWith(
        fontWeight: emphasized ? FontWeight.bold : FontWeight.w600,
        fontFeatures: tabularFigures.fontFeatures,
      ),
    );
  }
}

class MerchantAmountRow extends StatelessWidget {
  const MerchantAmountRow({
    super.key,
    required this.label,
    required this.value,
    this.emphasized = false,
  });

  final String label, value;
  final bool emphasized;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: CgTheme.space4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Expanded(child: Text(label)),
          const SizedBox(width: CgTheme.space12),
          Flexible(
            child: MerchantAmountValue(value: value, emphasized: emphasized),
          ),
        ],
      ),
    );
  }
}

class MerchantPrimaryAction extends StatelessWidget {
  const MerchantPrimaryAction({
    super.key,
    required this.label,
    required this.onPressed,
    this.icon,
  });

  final String label;
  final VoidCallback? onPressed;
  final IconData? icon;

  @override
  Widget build(BuildContext context) {
    final style = FilledButton.styleFrom(
      minimumSize: const Size(0, CgTheme.buttonHeight),
      shape: const RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(CgTheme.buttonRadius)),
      ),
    );
    return SizedBox(
      width: double.infinity,
      child: icon == null
          ? FilledButton(onPressed: onPressed, style: style, child: Text(label))
          : FilledButton.icon(
              onPressed: onPressed,
              style: style,
              icon: Icon(icon),
              label: Text(label),
            ),
    );
  }
}
