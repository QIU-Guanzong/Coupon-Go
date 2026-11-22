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
文本，見根目錄 README）。`tools/unpack_assets.py` 解包其中 112 個文件
到 `assets/`，並逐項校驗大小和 SHA-256。這些生成文件不進版本控制；
`assets/images/` 中另有已納入版本控制的示範商品圖片。解包腳本會覆寫
manifest 所列的目標文件，已有修改請先保存。

## 注意

- 2026-11-22 使用 Flutter 3.35.7 在乾淨工作面解包素材並完成 Web release
  構建；這不代表已驗證實體手機或相機掃碼。
- 演示數據：後端種子數據，非真實商戶。
