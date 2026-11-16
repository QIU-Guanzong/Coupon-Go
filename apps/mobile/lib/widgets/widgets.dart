import 'package:flutter/material.dart';

import '../models.dart';
import '../theme.dart';

/// Two-column coupon card (home grid / lists). Photo on top, name, price row
/// (discount large, original struck through), orange CTA, heart top-right.
class CouponCard extends StatelessWidget {
  const CouponCard({super.key, required this.coupon, this.onTap, this.onFavorite});

  final Coupon coupon;
  final VoidCallback? onTap;
  final VoidCallback? onFavorite;

  @override
  Widget build(BuildContext context) {
    return Card(
      clipBehavior: Clip.antiAlias,
      child: InkWell(
        onTap: onTap,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Stack(
              children: [
                AspectRatio(
                  aspectRatio: 16 / 9,
                  child: Image.asset(
                    'assets/images/dummy_image_1.png',
                    fit: BoxFit.cover,
                    errorBuilder: (_, __, ___) => Container(color: CgColors.pageBg),
                  ),
                ),
                Positioned(
                  right: 6,
                  top: 6,
                  child: GestureDetector(
                    onTap: onFavorite,
                    child: Container(
                      padding: const EdgeInsets.all(6),
                      decoration: const BoxDecoration(
                        color: Colors.white,
                        shape: BoxShape.circle,
                      ),
                      child: Image.asset(
                        coupon.isFavorite
                            ? 'assets/images/ic_filled_heart.png'
                            : 'assets/images/ic_empty_heart.png',
                        width: 18,
                        height: 18,
                        errorBuilder: (_, __, ___) => Icon(
                          coupon.isFavorite ? Icons.favorite : Icons.favorite_border,
                          size: 18,
                          color: CgColors.primary,
                        ),
                      ),
                    ),
                  ),
                ),
              ],
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 8, 10, 4),
              child: Text(
                coupon.name,
                maxLines: 2,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(fontWeight: FontWeight.w600, fontSize: 14),
              ),
            ),
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 10),
              child: Text(
                coupon.shopName,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: const TextStyle(color: CgColors.textSecondary, fontSize: 12),
              ),
            ),
            Padding(
              padding: const EdgeInsets.fromLTRB(10, 6, 10, 10),
              child: Row(
                children: [
                  Text(
                    coupon.amountText,
                    style: const TextStyle(
                      color: CgColors.primary,
                      fontWeight: FontWeight.bold,
                      fontSize: 16,
                    ),
                  ),
                  if (coupon.regularPrice != null) ...[
                    const SizedBox(width: 6),
                    Text(
                      '\$${coupon.regularPrice!.toStringAsFixed(0)}',
                      style: const TextStyle(
                        color: CgColors.textSecondary,
                        fontSize: 12,
                        decoration: TextDecoration.lineThrough,
                      ),
                    ),
                  ],
                  const Spacer(),
                  Container(
                    padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 5),
                    decoration: BoxDecoration(
                      color: CgColors.orange,
                      borderRadius: BorderRadius.circular(16),
                    ),
                    child: const Text(
                      '領取',
                      style: TextStyle(color: Colors.white, fontSize: 12),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class SectionHeader extends StatelessWidget {
  const SectionHeader({super.key, required this.title, this.onViewAll});

  final String title;
  final VoidCallback? onViewAll;

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 18, 16, 8),
      child: Row(
        children: [
          Text(
            title,
            style: const TextStyle(fontSize: 18, fontWeight: FontWeight.bold),
          ),
          const Spacer(),
          if (onViewAll != null)
            GestureDetector(
              onTap: onViewAll,
              child: const Row(
                children: [
                  Text('睇全部', style: TextStyle(color: CgColors.primary)),
                  Icon(Icons.chevron_right, size: 18, color: CgColors.primary),
                ],
              ),
            ),
        ],
      ),
    );
  }
}

/// Skeleton placeholder with a gloss sweep (shimmer-like, no extra dep).
class SkeletonBox extends StatefulWidget {
  const SkeletonBox({super.key, required this.height, this.width});

  final double height;
  final double? width;

  @override
  State<SkeletonBox> createState() => _SkeletonBoxState();
}

class _SkeletonBoxState extends State<SkeletonBox>
    with SingleTickerProviderStateMixin {
  late final AnimationController _c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1200),
  )..repeat();

  @override
  void dispose() {
    _c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return AnimatedBuilder(
      animation: _c,
      builder: (_, __) {
        return Container(
          width: widget.width,
          height: widget.height,
          decoration: BoxDecoration(
            borderRadius: BorderRadius.circular(12),
            gradient: LinearGradient(
              colors: const [
                Color(0xFFE8E8E8),
                Color(0xFFF5F5F5),
                Color(0xFFE8E8E8),
              ],
              stops: [
                (_c.value - 0.3).clamp(0.0, 1.0),
                _c.value,
                (_c.value + 0.3).clamp(0.0, 1.0),
              ],
            ),
          ),
        );
      },
    );
  }
}

void showCgToast(BuildContext context, String message) {
  ScaffoldMessenger.of(context).showSnackBar(
    SnackBar(content: Text(message), duration: const Duration(seconds: 2)),
  );
}
