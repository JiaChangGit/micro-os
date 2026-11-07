# micro-os

目標：在 WSL2 的 QEMU TCG 上建立單核心 AArch64 微核心，練習 Linux kernel／驅動面試需要的系統分析與除錯。正式範圍在 `docs/project-contract.md`；本機若保留根目錄舊 HTML，僅供參考，不再決定 Phase、API 或功能清單。

## 開始工作

- 在 WSL 的專案根目錄執行工具；Windows 端可用 `wsl.exe -d Ubuntu-26.04 -- ...`。套件裝在 WSL，不裝 Windows Python 相依套件。
- 先執行 `python3 tools/context.py --phase P<N>`。只讀目前 Phase 與相關參考，毋須每次重讀整份 HTML。
- 專用 skill 原始檔在 `skills/microkernel-engineering/SKILL.md`。以這份及正式契約為準；安裝副本由 `tools/install_skill.py` 同步。
- 使用者指定的範圍優先。一次完成指定的工作單位；工具準備不代表已授權提前實作後續核心功能。

## 實作與驗證

- P0–P7 相依與 Gate 在 `config/phases.json`、`docs/phases.md`。前置 Gate 失敗先定位，不靠略過或成功 stub 繼續。
- 修改前釘清資料擁有者、狀態轉移、失敗清理與可觀測點；ABI／記憶體 layout 在對應 Phase 先凍結，不能沿用舊 HTML 猜值。
- 共用狀態經集中 helpers 修改。單核心仍有 IRQ／syscall／timeout 的事件交錯；不要把 `volatile` 當同步。
- Host 測試與 QEMU 測試分別說明證明範圍。`make test-tools` 只驗開發工具，不證明 MMU／IRQ／EL0 正確。
- `run_qemu.py` 的 PASS 只驗傳輸與執行條件。Phase Gate 另需實際語意檢查、原始證據與同版本來源指紋。
- 執行 `make check` 檢查工具。核心功能的測試依 Phase 增加，不能為尚未實作的功能建立 PASS 收據。
- 程式註解與文件使用自然繁中，runtime log 使用英文。文件留下設計與證據，不記錄 AI 對話、宣傳詞或虛構成果。

## 除錯與交付

- 依症狀提出可區分的假設，以 register／事件 trace／狀態不變量縮小範圍；參考 `docs/debugging.md`。
- 故障注入是驗證方法；真正找到的缺陷才整理成除錯案例。面試依據見 `docs/interview.md`。
- 回報修改、實際指令、結果與未驗證範圍。缺工具標 BLOCKED，未執行標 NOT_RUN，不能推定成功。
