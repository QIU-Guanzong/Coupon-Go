# Coupon GO! — Flutter App（開發版）

消費者版 + 商戶版，連 `apps/api` 的 FastAPI 後端（`/api/v3`）。

## 準備

```bash
python3 tools/unpack_assets.py   # 解包 APK 拆包素材到 assets/（112 個文件）
flutter pub get
flutter run --dart-define=API_BASE_URL=http://localhost:8000
```

真機調試：把 `API_BASE_URL` 改成電腦的局域網 IP（例如
`http://192.168.1.10:8000`）。

## 頁面

- 消費者：Splash、首頁七分區、搜尋三分支、分類頁、券/商户詳情、收藏、
  Q Wallet 三狀態、QR 出示（15:00 倒數）、通知、設置、Q仔助手、指南 FAQ
- 商戶：金色企業版首頁、發券兩步（預覽 → 提交審核）、掃碼核銷
  （`mobile_scanner` 相機 + 手動輸碼）、用量數據、訂閱計劃

## 素材

`assets-packed/` 是 APK 拆包素材的 base64 打包（發布通道只接受 UTF-8
文本，見根目錄 README）。`assets/` 由 `tools/unpack_assets.py` 生成，
不進版本控制。

## 注意

- 未在本機跑過 `flutter analyze` / 真機編譯（本機無 Flutter SDK）。
- 演示數據：後端種子數據，非真實商戶。
