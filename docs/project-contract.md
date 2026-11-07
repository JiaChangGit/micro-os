# 專案契約

目標是在 WSL2 上，用 QEMU 執行自行實作的 AArch64 微核心，展示「同步 RPC → RTC 中斷 → 服務故障 → 清理與重啟」，並觀察事件順序、責任邊界與除錯證據。

本文件是現行需求依據；[phases.md](phases.md) 定義施工順序與驗收。本機若保留舊版 HTML，僅作歷史參考；其中的 24 支 syscall、雙 thread RTC、priority donation、動態 capability 與 P0–P8 施工表不適用於本版。不可從舊版規格自動恢復已移出的功能。

核心依 P0–P7 順序實作；完成狀態以 `tools/phase.py status` 的有效收據為準。工具自測通過、環境工具齊全與 guest 功能通過是三種不同證據。

## 平台與範圍

| 項目 | 本版契約 |
|---|---|
| Host | 16 GB RAM、WSL2 Ubuntu 26；原始碼與 build 放 Linux 檔案系統 |
| Guest | AArch64 `virt`、Cortex-A53、TCG、單核心、512 MiB；不依賴 KVM |
| Privilege | 核心在 EL1；root、RTC、client 在 EL0；不實作 EL2／EL3 |
| 裝置 | PL011 console、GICv3、architected physical timer、PL031 RTC；位置與 IRQ 從受控 FDT schema 取得 |
| 記憶體 | 4 KiB pages、task 獨立位址空間、EL0 W^X、stack guard；RTC MMIO 僅映給 RTC |
| Task | 固定 root、RTC、client，各一條 thread；idle 屬核心；同一映像依 bootargs 選擇測試情境 |
| 排程 | 單核心 fixed-priority、固定 READY 集合掃描、timer 搶占、sleep／wait／wake；root 阻塞等待死亡事件 |
| IPC | 四個 64-bit 純值；每個 client 最多一筆 outstanding call；無 send queue，服務忙碌回 `E_BUSY` |
| 權限 | 開機注入的 task-local handle、type／rights 檢查；沒有動態 delegation／revoke |
| 映像 | Host 驗證固定 ELF，產生已知 segment descriptor；核心只載入固定映像資料，不接受任意 ELF |
| 復原 | root 只可重啟固定 RTC role；endpoint 保留，instance／epoch 更新，重啟次數有上限 |

不納入 SMP、priority donation、通用 task spawn／VM API、demand paging、COW、shared memory、DMA、PCIe、網路、檔案系統或新 QEMU 裝置模型。功能驗證不支持真實 Cortex-A53 效能或 hard real-time 結論。

## 三條控制路徑

1. **開機：** Image entry 保留 FDT pointer → 初始化 stack／頁表 → MMU 與 exception vectors → 建立固定 task → `eret` 進 EL0。
2. **請求：** client CALL → 權限與服務狀態檢查 → RTC 收到 request／reply token → 裝置操作 → REPLY → client READY。
3. **復原：** fault 保存現場／RTC EXIT → mask IRQ → 完成 client 等待、關閉 endpoint 與失效舊 token → 清等待／notification、DEAD／通知 root → 切至 root stack 重建 RTC → 共用啟動握手 → READY。

## RTC 與等待語意

- RTC 提供 `GET_TIME`、`PING`、`WAIT_ALARM`；服務等待 alarm 時不處理另一筆控制 RPC。
- `WAIT_ALARM`：先初始化裝置狀態，再設定 alarm；秒數接受 1–3，RTC 的正常 IRQ 等待上限為秒數加 2 秒。醒來驗裝置狀態、清 device pending、`IRQ_ACK`，最後回覆 client。
- 核心處理 RTC IRQ 時 mask SPI、完成 GIC EOI，再通知 RTC；不等待 EL0 才 EOI。`IRQ_ACK` 驗權限、instance、binding 與 PL031 MIS；MIS 尚為 1 回 `E_BUSY`。接受 ACK 時清 kernel pending 後解除 mask，丟棄該次 timeout 清理期間晚到的 notification。
- client 的 `BLOCKED_REPLY` 與 RTC 的 `BLOCKED_NTFN` 使用獨立 wait record／deadline。RTC 持有的 reply token 也獨立於 RTC 自身的 IRQ 等待。
- client timeout 只結束 client 等待，不承諾撤銷裝置操作。RTC 仍須有限等待、清理並返回 RECV；晚 REPLY 回 `E_STALE`，不得影響新請求。
- Notification 是 pending bits 的 OR／一次取走，允許事件合併；不是無損計數佇列。檢查 pending 與進入等待必須原子化。
- syscall 錯誤與 service 回覆錯誤需分開定義。無 owner 的 CALL 回 `E_PEER_DIED`；無權限的操作不得產生副作用。非冪等請求不盲目重試。

## 啟動與重啟

開機與重啟共用同一套就緒流程：RTC 資源與 handle 就緒、IRQ 維持 masked → RTC 關 IMSC、清 ICR 並呼叫 READY → 核心檢查 IMSC=0／RIS=0，綁定該 instance → 解除 mask、endpoint 可接請求。所有等待接受 1–10000 ms，均有有限失敗出口。

RTC 故障時立即阻止新引用並遮蔽 IRQ，endpoint owner 解除且 epoch 只遞增一次。尚未完成的 client 收到 `E_PEER_DIED`；已先 timeout 或 reply 的請求不再完成第二次。當前 kernel stack 只能在 scheduler-safe context 回收或重設。清理完成才發布可重啟的死亡事件；不把「偵測到 fault」當作可重用資源的保證。

重建時清除舊 data／stack、IRQ notification、wait／deadline 與 binding，保留原頁表 root。死亡事件尚未取走時，RESTART 回 `E_BUSY`，以免新 instance 覆蓋事件內容。固定槽位重用仍驗 instance generation 與 endpoint epoch；token sequence 不重用。最多允許 128 次重啟；instance 用盡拒絕，epoch 用盡停止核心，sequence 用盡拒絕新 CALL。目前沒有重建失敗後的自動重試或退避機制。

## 必須維持的不變量

| ID | 條件 |
|---|---|
| INV-RUNQ | 固定 task 表中的 READY thread 恰為一個可選候選；RUNNING 不列入候選；不使用 runqueue 節點 |
| INV-WAIT | 每個 thread 只有一種 primary wait；有限等待有獨立 deadline 值，READY／RUNNING／DEAD 無 deadline |
| INV-ONCE | 每筆 CALL 最多完成一次；舊 token 不因槽位重用恢復有效 |
| INV-RIGHTS | 無效或缺權限的 handle 不改變任何受保護狀態 |
| INV-VM | EL0 無 W+X 頁、無跨 task writable alias；client 無 RTC MMIO |
| INV-IRQ | RTC 故障後，在新 instance 就緒前 SPI 維持 masked |
| INV-LIFE | DEAD 才可重建；使用中的 stack／context 不清除；頁表、固定 frame 與 stack 重用 |

## 實作前需凍結的細節

P0 凍結 boot Image／QEMU profile；P1 凍結 VA／PA、保留區與 PTE 編碼；P2 凍結 trap frame、當期 syscall 編號／錯誤碼、calling convention 與 BootInfo；P3 凍結優先權、READY 選擇／deadline 轉移；P4 凍結 IPC ABI、RPC word layout、token 與截止點相等時規則；P5 凍結裝置握手與等待上限；P6 凍結死亡事件及重啟政策。實際配置整理於 [implementation.md](implementation.md)。

數值與結構一旦凍結，放進單一機器可讀定義或 UAPI header，使用生成檔與編譯期檢查。不得把本文件尚未指定的數值當作既定 ABI，也不得偷偷沿用原 HTML 的數值。契約、設定與程式不一致時，先修正同一項設計的所有使用處。

## 證據

每個 Gate 保存來源指紋、執行 command、退出碼、原始證據與具體 assertions。QEMU transport 報告另有執行命令、timeout 與 QEMU 版本；Git revision 或 dirty 狀態無法取得時為 null。Host model／sanitizer 驗特定邊界；guest 驗 MMU、exception、SVC、IRQ 與復原，兩者不能互相替代。

QEMU harness 的 `evidence_kind=qemu_transport`、`semantic_validation=false`；即使 PASS，也只表示退出／marker／panic 等傳輸條件通過。Phase Gate 必須由個案專用 validator 驗證觀察值，產生 `semantic_validation=true` 且 assertions 非空、全部通過的收據。Gate 收據在 `build/reports/gates/<gate_id>.json`，ID 與前置關係以 `config/phases.json` 為準。`phase.py` 只檢查收據與階段順序，不以人工旗標證明功能。

執行 `tools/context.py` 取得當前工作需要的摘要，再按 [phases.md](phases.md) 只讀相關文件。深入除錯時讀 [debugging.md](debugging.md)，複習關鍵問題時讀 [系統問答](interview.md)。

重啟回歸的 `resource_delta` 只量測頁表頁數相對開機基線的差值，不是整體記憶體用量。guest 已有一般 fault、pending 時 fault、EXIT 與 100 次重啟情境；未遍歷所有 fault 指令位置、128 次上限耗盡或 64-bit wrap。完整測試界線見 [phases.md](phases.md)。

循序閱讀與追蹤範例見 [教學目錄](tutorial/README.md)；工具介面與收據欄位見 [tooling.md](tooling.md)。
