# PR #21 歷史歸檔（2026-11-21）

這些文件保留當時的測試記錄、產品構想及協作背景，並非目前的產品承諾、法律意見、漏洞狀態或執行指令。合併順序、遷移編號、待辦、帳號與權責、審批和發布要求都可能過時；工作開始時應以現行程式、測試及產品決策為準。

來源為 [PR #21](https://github.com/QIU-Guanzong/Coupon-Go/pull/21) 的 `audit/roadmap-ci`，原始提交 `2469006a59cf46552869deac827f080481837287`。下表為原始文件內容的 SHA-256。歸檔版在每份文件頂部加入歷史提示；路線圖內指向 `FEATURE_GAP_20261120.md` 的相對路徑為適應新目錄作了更正，其餘原文保留。

| 原始文件 | 原文 SHA-256 |
|---|---|
| [MUSE_MERGE_QUEUE_20261121.md](MUSE_MERGE_QUEUE_20261121.md) | `c838ad7d017b229b17100e8bdb9684bf7e33873621ae20e8f67184d1d97acd24` |
| [QUALITY_SECURITY_BASELINE.md](QUALITY_SECURITY_BASELINE.md) | `39ee1eb57a28080aa062eed1720dd0ae2a6f47a9a7688c0ebd9e8e0ebd1816f2` |
| [REVIEW_PR11-20_20261121.md](REVIEW_PR11-20_20261121.md) | `5b957ab1ece6ab21521b67e47d90606b68c54aa4c5d948dab8bb23f3a640b78c` |
| [ROADMAP_20261121.md](ROADMAP_20261121.md) | `b5bed54aba3ef13f5742225c422cc3086dc8333b7471ab980a8e9e378f4c254a` |
| [ROUND2_DECISIONS_20261121.md](ROUND2_DECISIONS_20261121.md) | `b576d84fdc039c442c2d640ce941fd43622501896c3d6ef3889b756c39301d6e` |

舊 PR 的 Dependabot、依賴審計與分支保護要求沒有納入；此次只保留獨立的手動功能測試工作流，不建立自動審批或合併門檻。

## 手動功能測試

需要時可在 GitHub Actions 手動執行 `Manual functional tests`。它使用已鎖定依賴及測試資料，選擇性檢查 API 結算、商戶合約字體、模擬付款、公開預覽、互動試用和 Flutter 普通、只讀、互動試用與雙標記四種模式；不會因推送或 PR 自動執行，也不建立合併門檻。這是定向回歸而非全量測試：SQLite 不驗證 PostgreSQL 行鎖，模擬交易不代表真實 Stripe 付款。其他測試應按變更範圍另行運行。
