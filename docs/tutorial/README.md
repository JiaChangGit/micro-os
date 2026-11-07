# 從啟動到服務重啟

本教材以這個目錄中的 AArch64 核心為對象。系統在 QEMU `virt`、單一 Cortex-A53 CPU 上執行：核心位於 EL1，root、RTC 與 client 各有一個 EL0 task。先依建置章準備環境，再從一次例外開始追蹤；各章編號是檔案識別，建議順序如下。

```mermaid
flowchart LR
    Z[05 建置與工具] --> A[00 執行與追蹤]
    A --> B[01 啟動和記憶體]
    B --> C[02 排程和 IPC]
    C --> X[07 RPC 程式碼精讀]
    X --> D[03 RTC 中斷與重啟]
    D --> Y[06 除錯案例]
    Y --> E[04 系統問答]
```

| 章節 | 閱讀目標 | 對應程式 |
|---|---|---|
| [05 建置、映像與執行工具](05-build-toolchain.md) | 準備環境、建置、執行與辨認各種測試結果 | `Makefile`、`tools/bootstrap.py`、`tools/run_qemu.py` |
| [00 執行與追蹤](00-tracing.md) | 由一筆 fault 找到觸發指令、例外入口與保存的位址 | `user/client.c`、`arch/aarch64/vectors.S`、`kernel/task.c` |
| [01 啟動與記憶體](01-boot-memory.md) | 從 Image entry 走到第一個 EL0 指令，辨認記憶體擁有者與頁面權限 | `arch/aarch64/boot.S`、`kernel/main.c`、`arch/aarch64/vm.c` |
| [02 排程與 IPC](02-scheduling-ipc.md) | 找出 task 狀態、request 四個 word 與完成結果的變化 | `kernel/task.c`、`kernel/state.c`、`include/uapi.h` |
| [07 RPC 程式碼精讀](07-rpc-code-walk.md) | 逐欄追蹤 SVC 暫存器、trap frame 與完成點 | `user/common.h`、`include/uapi.h`、`kernel/task.c` |
| [03 RTC 中斷與重啟](03-rtc-recovery.md) | 依時間順序追蹤裝置、IRQ、notification、故障與固定槽位重建 | `user/rtc.c`、`arch/aarch64/irq.c`、`kernel/task.c` |
| [06 從症狀到修正](06-debug-cases.md) | 對照實際缺陷的假設、定位、修正與回歸 | `docs/bugs.md`、`docs/evidence/`、`tests/` |
| [04 系統問答](04-questions.md) | 根據程式與測試回答設計、除錯及證據範圍問題 | 各章所列程式、測試與實際紀錄 |

操作前在 WSL 的專案根目錄執行 `make all`。[實作契約](../implementation.md) 有固定的位址、ABI 與限制；[缺陷紀錄](../bugs.md) 保存實際缺陷。各章的範例以原始碼為準，輸出中的位址、秒數與計數可能隨建置或執行時間改變。

每章先交代角色、資料與預期結果，再用完整路徑連接原始碼及觀察值；小題可先自行預測，再看解答。這種安排參考了 [worked examples](https://educationendowmentfoundation.org.uk/news/supporting-pupils-with-worked-examples)、[範例程式寫作](https://developers.google.com/tech-writing/two/sample-code)及[先備知識檢查](https://teaching.cornell.edu/teaching-resources/assessment-evaluation/assessing-prior-knowledge-addressing-learning-gaps)。指令語意以 [GDB 文件](https://sourceware.org/gdb/current/onlinedocs/gdb/) 為準。
