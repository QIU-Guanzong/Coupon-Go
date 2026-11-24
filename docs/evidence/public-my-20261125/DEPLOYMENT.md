### Mac 发布（本 PR 不执行）

1. 在干净的 checkout 选择已审的完整提交 SHA。使用仓库锁定的 Flutter 版本和依赖；从仓库根目录进入 `apps/mobile`。
   ```sh
   python3 tools/unpack_assets.py
   flutter pub get --enforce-lockfile
   flutter build web --release --no-pub --base-href=/coupongo/ \
     --dart-define=PUBLIC_TRIAL=true \
     --dart-define=API_BASE_URL=/coupongo-trial
   shasum -a 256 build/web/main.dart.js
   ```
   不加 `PUBLIC_DEMO`。静态入口 /coupongo/ 与 API 路径 /coupongo-trial/api/v3/ 是两个不同的前缀；不用把 API 暴露到 /coupongo/api/v3/。若同时保留 /coupongo-trial/ 的入口，为它独立打另一个 base-href 包。
2. 网关与前端需要采用同一候选（如果 PR 确实更新网关）。按 `infra/interactive-trial/README.md` 安排现有独立试用服务：一个 worker、合成数据模板、每浏览器独立数据库、Cookie 仅发往 /coupongo-trial/，闲置四小时或服务重启后重建。保持同源 HTTPS 与相机权限，仅将 /coupongo-trial/api/v3/ 反代到私有网关的 /api/v3/。禁止指向正式库、支付凭据、真实文件目录，不改正式应用的登录或 Stripe 配置。
3. 记录旧静态包指针和旧网关版本。先在批准的内测入口检查记录的产物哈希与同源会话，再走既有发布流程。用全新浏览器检查顾客进入「我的」、企业服务直达、角色切换，以及第二个浏览器不能读取第一个的券或订单。不向正式系统提交、不测试收费。检查旧页面刷新并重新取入口文件（不要对未带哈希的 index.html、flutter_bootstrap.js 与 main.dart.js 设置 immutable）。按原静态包和原网关版本一起回滚；保留旧制品。
