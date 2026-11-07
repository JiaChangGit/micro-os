# micro-os

在 WSL2 的 QEMU TCG 上執行的單核心 AArch64 微核心。三個固定 EL0 task 分別負責監督、PL031 RTC 服務與 client；核心提供位址隔離、timer 搶占、同步 RPC、IRQ notification 與固定服務重啟。

P0–P7 已有實作與個案 validator。完成狀態由目前來源對應的 Gate 收據判定：

在 WSL 的專案根目錄執行：

```bash
make all
make test
make phase-status
make demo
```

`make test` 執行工具檢查、host 測試及 P0–P7 Gate。`make demo` 執行三次 RTC 故障與重啟，再完成一次 alarm RPC。預設映像含固定測試情境；目前沒有供應用程式長期使用的獨立 release 映像。

## 環境

使用 WSL Ubuntu 26、AArch64 GCC／binutils、QEMU、GDB 與 dtc；不需要 KVM，也不需要安裝 Windows Python 套件。

```bash
python3 tools/bootstrap.py
python3 tools/bootstrap.py --install
make toolcheck-target
```

不帶 `--install` 時只列出需求；安裝在 WSL 內透過 apt 與互動 sudo 進行。Windows 入口可用 `wsl.exe -d Ubuntu-26.04 -- ...`，原始碼與建置目錄保留在 WSL 的 Linux 檔案系統。

## 建置與驗證

| 指令 | 用途 |
|---|---|
| `make all` | 建置三個 EL0 ELF、驗證並打包固定 segments、連結核心 Image、匯出平台 DTB |
| `make check` | 檢查來源、設定、文件連結與 Python 工具測試 |
| `make test-p0` 至 `make test-p7` | 重跑個別 Phase 的 host／guest validator |
| `make phase-status` | 顯示收據狀態；來源改動後舊結果會標為 STALE |
| `make phase-check PHASE=P7` | 驗證 P7 及全部前置收據與原始證據 |
| `make context PHASE=P5` | 取得該 Phase 的契約、前置條件與相關檔案 |

建置輸出為 `build/kernel.elf`、`build/kernel.img`、`build/platform.dtb` 與 `build/user/*.elf`。Gate 收據在 `build/reports/gates/`；QEMU 原始輸出與命令在 `build/reports/qemu/`。`resource_delta` 只計算頁表頁數差異，不代表全部記憶體用量。

單獨執行個案時，case 名稱與 guest bootargs 必須一致：

```bash
python3 tools/run_qemu.py --image build/kernel.img --case p5-client-timeout \
    --boot-args case=p5-client-timeout --timeout 15
```

Harness PASS 只驗程序退出、marker、panic 與清理條件；Phase validator 另驗 exception、IPC 計數、IRQ、instance／epoch 與重啟順序。Host sanitizer 測試不能替代 guest 硬體路徑。

## 原始碼與文件

| 路徑 | 內容 |
|---|---|
| `arch/aarch64/` | entry、vectors、頁表、GICv3 與 physical timer |
| `kernel/task.c`、`kernel/state.c` | task、權限、等待、RPC、notification 與重啟 |
| `user/`、`include/uapi.h` | root／RTC／client 與共用 syscall ABI |
| [專案契約](docs/project-contract.md) | 範圍、角色、等待語意與不變量 |
| [實作契約](docs/implementation.md) | 位址配置、ABI、排程、IRQ 與復原規則 |
| [Phase 與驗收](docs/phases.md) | Gate 與目前測試涵蓋範圍 |
| [工具與證據](docs/tooling.md) | CLI、報告、收據與驗證界線 |
| [缺陷紀錄](docs/bugs.md) | 實際發現的問題、定位與回歸 |
| [除錯參考](docs/debugging.md) | register、trace 與狀態檢查方式 |
| [循序教學](docs/tutorial/README.md) | 從一次例外追蹤到啟動、IPC、IRQ 與重啟 |

專用 skill 來源在 [skills/microkernel-engineering](skills/microkernel-engineering/SKILL.md)；可用 `make install-skill SKILLS_DIR=/path/to/codex/skills` 同步，執行前將範例路徑替換成實際 skills 目錄。功能與 ABI 以現行契約及程式定義為準；本機保留的舊版 HTML 不納入儲存庫，也不影響建置。
