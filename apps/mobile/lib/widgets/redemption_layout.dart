import 'package:flutter/material.dart';

import '../theme.dart';

const _width = 1120.0;

Color redemptionPageColor(BuildContext context) =>
    Theme.of(context).brightness == Brightness.dark
        ? Theme.of(context).scaffoldBackgroundColor
        : CgColors.vendorPage;

/// The form stays adjacent to its evidence on desktop and first in reading
/// order on a phone. Both sections scroll together.
class RedemptionWorkbench extends StatelessWidget {
  const RedemptionWorkbench({
    super.key,
    required this.entry,
    required this.result,
  });

  final Widget entry;
  final Widget result;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
        builder: (context, viewport) {
          final wide = viewport.maxWidth >= 760 &&
              MediaQuery.textScalerOf(context).scale(1) <= 1.25;
          return SingleChildScrollView(
            padding: EdgeInsets.all(wide ? 24 : CgTheme.pageInset),
            child: Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: _width),
                child: wide
                    ? Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(flex: 4, child: entry),
                          const SizedBox(width: 20),
                          Expanded(flex: 6, child: result),
                        ],
                      )
                    : Column(
                        crossAxisAlignment: CrossAxisAlignment.stretch,
                        children: [
                          entry,
                          const SizedBox(height: 16),
                          result,
                        ],
                      ),
              ),
            ),
          );
        },
      );
}

class RedemptionPanel extends StatelessWidget {
  const RedemptionPanel({
    super.key,
    required this.icon,
    required this.title,
    required this.child,
    this.description,
  });

  final IconData icon;
  final String title;
  final String? description;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    return DecoratedBox(
      decoration: BoxDecoration(
        color: colors.surface,
        border: Border.all(color: colors.outlineVariant),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            Row(
              children: [
                DecoratedBox(
                  decoration: BoxDecoration(
                    color: colors.secondaryContainer,
                    borderRadius: BorderRadius.circular(8),
                  ),
                  child: Padding(
                    padding: const EdgeInsets.all(8),
                    child: Icon(icon,
                        size: 20, color: colors.onSecondaryContainer),
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Semantics(
                    header: true,
                    child: Text(title, style: theme.textTheme.titleMedium),
                  ),
                ),
              ],
            ),
            if (description != null) ...[
              const SizedBox(height: 8),
              Text(
                description!,
                style: theme.textTheme.bodyMedium
                    ?.copyWith(color: colors.onSurfaceVariant),
              ),
            ],
            const SizedBox(height: 12),
            child,
          ],
        ),
      ),
    );
  }
}

enum RedemptionTone { neutral, attention, error, success }

class RedemptionNotice extends StatelessWidget {
  const RedemptionNotice({
    super.key,
    required this.message,
    this.tone = RedemptionTone.neutral,
  });

  final String message;
  final RedemptionTone tone;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final colors = theme.colorScheme;
    final dark = theme.brightness == Brightness.dark;
    final (background, foreground, icon) = switch (tone) {
      RedemptionTone.neutral => (
          colors.surfaceContainerLow,
          colors.onSurfaceVariant,
          Icons.info_outline,
        ),
      RedemptionTone.attention => (
          dark ? colors.surfaceContainerHigh : CgColors.statusPendingBg,
          dark ? CgColors.moneyPendingDark : CgColors.statusPendingText,
          Icons.schedule_outlined,
        ),
      RedemptionTone.error => (
          colors.errorContainer,
          colors.onErrorContainer,
          Icons.error_outline,
        ),
      RedemptionTone.success => (
          dark ? colors.surfaceContainerHigh : CgColors.statusActiveBg,
          dark ? CgColors.moneyPositiveDark : CgColors.statusActiveText,
          Icons.check_circle_outline,
        ),
    };
    return Semantics(
      liveRegion: tone != RedemptionTone.neutral,
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: background,
          borderRadius: BorderRadius.circular(8),
        ),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              ExcludeSemantics(child: Icon(icon, color: foreground, size: 18)),
              const SizedBox(width: 8),
              Expanded(
                child: Text(
                  message,
                  style:
                      theme.textTheme.bodyMedium?.copyWith(color: foreground),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
