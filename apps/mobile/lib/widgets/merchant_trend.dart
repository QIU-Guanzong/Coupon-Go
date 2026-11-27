import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../language.dart';
import '../theme.dart';

enum _Metric { count, gross, net }

class MerchantTrend extends StatefulWidget {
  const MerchantTrend({super.key, required this.days});

  final List<Map> days;

  @override
  State<MerchantTrend> createState() => _MerchantTrendState();
}

class _MerchantTrendState extends State<MerchantTrend> {
  _Metric _metric = _Metric.count;
  int? _selected;

  @override
  void didUpdateWidget(covariant MerchantTrend oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.days.firstOrNull?['date'] !=
        widget.days.firstOrNull?['date']) {
      _selected = null;
    }
  }

  String get _field => switch (_metric) {
        _Metric.count => 'redemptions',
        _Metric.gross => 'gross_amount',
        _Metric.net => 'net_amount',
      };

  String _format(num? value) {
    if (value == null) return '—';
    return _metric == _Metric.count
        ? value.toInt().toString()
        : formatHkd(value.toDouble());
  }

  @override
  Widget build(BuildContext context) {
    if (widget.days.isEmpty) return const SizedBox.shrink();
    final t = context.tr;
    final values = [
      for (final day in widget.days)
        if (day[_field] case final num value) value.toDouble() else null,
    ];
    final finite = values.whereType<double>().where((value) => value.isFinite);
    final min = finite.fold<double>(0, math.min);
    final max = finite.fold<double>(0, math.max);
    final index = (_selected ?? values.length - 1).clamp(0, values.length - 1);
    final day = widget.days[index];
    final previous =
        index > 0 ? () => setState(() => _selected = index - 1) : null;
    final next = index < values.length - 1
        ? () => setState(() => _selected = index + 1)
        : null;
    final date = day['date'] as String? ?? '—';
    final value = _format(values[index]);
    final metricLabels = [
      t('核銷數', 'Redemptions'),
      t('已核銷金額', 'Redeemed amount'),
      t('扣佣後', 'After commission'),
    ];

    return Padding(
      padding: const EdgeInsets.only(top: 20),
      child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
        const Divider(height: 1),
        const SizedBox(height: 14),
        Text(t('每日經營趨勢', 'Daily performance'),
            style: Theme.of(context)
                .textTheme
                .titleMedium
                ?.copyWith(fontWeight: FontWeight.w800)),
        const SizedBox(height: 8),
        Wrap(spacing: 6, children: [
          for (final metric in _Metric.values)
            ChoiceChip(
              key: ValueKey('merchant-trend-${metric.name}'),
              label: Text(metricLabels[metric.index]),
              selected: _metric == metric,
              selectedColor: const Color(0xFFFFE3A1),
              onSelected: (_) => setState(() => _metric = metric),
            ),
        ]),
        Row(children: [
          Expanded(
              child: Semantics(
            liveRegion: true,
            child:
                Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
              Text('$date · UTC',
                  style: Theme.of(context).textTheme.labelMedium),
              Text(value, style: Theme.of(context).textTheme.titleLarge),
            ]),
          )),
          IconButton(
              tooltip: t('前一日', 'Previous day'),
              onPressed: previous,
              icon: const Icon(Icons.chevron_left)),
          IconButton(
              tooltip: t('後一日', 'Next day'),
              onPressed: next,
              icon: const Icon(Icons.chevron_right)),
        ]),
        const SizedBox(height: 8),
        if (finite.isEmpty)
          Text(t('暫未有此項數據。', 'This metric is unavailable.'))
        else if (min == 0 && max == 0)
          Text(t('這段期間未有此項活動。', 'No activity for this metric in this period.'))
        else
          Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
            Expanded(
              child: LayoutBuilder(builder: (context, constraints) {
                void select(double x) => setState(() => _selected =
                    (x / constraints.maxWidth * values.length)
                        .floor()
                        .clamp(0, values.length - 1));
                return Semantics(
                  label: t('逐日趨勢。可用前一日和後一日查看數字。',
                      'Daily trend. Use Previous day and Next day for exact values.'),
                  child: GestureDetector(
                    excludeFromSemantics: true,
                    behavior: HitTestBehavior.opaque,
                    onTapUp: (event) => select(event.localPosition.dx),
                    onHorizontalDragUpdate: (event) =>
                        select(event.localPosition.dx),
                    child: SizedBox(
                      height: 104,
                      width: double.infinity,
                      child: CustomPaint(
                        painter: _TrendBars(values, index, min, max),
                      ),
                    ),
                  ),
                );
              }),
            ),
            const SizedBox(width: 8),
            SizedBox(
              height: 106,
              child: Column(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  Text(_format(max),
                      style: Theme.of(context).textTheme.labelSmall),
                  Text(_format(min),
                      style: Theme.of(context).textTheme.labelSmall),
                ],
              ),
            ),
          ]),
        const SizedBox(height: 6),
        Wrap(alignment: WrapAlignment.spaceBetween, spacing: 12, children: [
          Text('${widget.days.first['date']}',
              style: Theme.of(context).textTheme.labelSmall),
          if (widget.days.length > 1)
            Text('${widget.days.last['date']}',
                style: Theme.of(context).textTheme.labelSmall),
        ]),
      ]),
    );
  }
}

class _TrendBars extends CustomPainter {
  _TrendBars(this.values, this.selected, this.min, this.max);

  final List<double?> values;
  final int selected;
  final double min, max;

  @override
  void paint(Canvas canvas, Size size) {
    final gap = size.width / values.length;
    final width = math.min(22.0, gap * 0.76);
    double y(double value) =>
        2 + (max - value) / (max - min) * (size.height - 4);
    final paint = Paint();
    final baseline = y(0);
    canvas.drawLine(Offset(0, baseline), Offset(size.width, baseline),
        paint..color = const Color(0xFFD3C6AB));
    for (var index = 0; index < values.length; index++) {
      final value = values[index];
      final start = (index + 0.5) * gap - width / 2;
      if (index == selected) {
        canvas.drawRect(Rect.fromLTWH(start, 0, width, size.height),
            paint..color = const Color(0xFFFFF1CE));
      }
      if (value == null || !value.isFinite || value == 0) continue;
      final top = math.min(y(value), baseline);
      final bottom = math.max(y(value), baseline);
      canvas.drawRect(
          Rect.fromLTRB(start, top, start + width, bottom),
          paint
            ..color = index == selected
                ? const Color(0xFF745008)
                : CgColors.vendorGold);
    }
  }

  @override
  bool shouldRepaint(covariant _TrendBars oldDelegate) =>
      oldDelegate.values != values || oldDelegate.selected != selected;
}
