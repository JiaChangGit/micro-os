# 系統問答

先遮住回答，從程式找出資料擁有者、狀態轉移及可觀測的證據，再回來核對。範例以本專案的單核心 QEMU 設定為準。

## 啟動與記憶體

**問：EL1 使用 identity mapping，是否代表 MMU 沒有作用？**

答：不是。核心正常路徑中 VA 和 PA 數值相同，但存取仍須經過頁表轉譯與權限檢查。`vm_init()` 為 text、rodata、data 建立不同權限；`p1-vm` 檢查 MMU 開啟、text RO、data XN，並透過 leaf routine 檢查高位址 alias 取指。該測試不表示整個核心連結在高位址。

**問：三個 task 都有 `0x400000`，為什麼不會因此存取同一份 user 程式？**

答：每個 task 有自己的低位址 page-table root；相同的 VA 在不同 root 中對應不同固定 PA slot。`load_role()` 建立 mapping，`schedule()` 選好 task 後由 `vm_activate()` 切換 TTBR0 並進行 TLB 同步。只比較 VA 本身，無法判斷實體資料是否相同。

**問：FDT 的 reserve map 能防止所有啟動期覆寫嗎？**

答：不能。parser 會拒絕與核心保守區或固定 task slot 重疊的 reserve entry，避免繼續載入 task；但啟動組語在呼叫 parser 前已清除 BSS。若外部傳入的 FDT 保留了核心本身使用的區域，檢查時已經太晚，不能聲稱沒有任何早期寫入。

**問：為什麼 entry 指向可執行 segment 還不夠？**

答：AArch64 指令長 4 bytes。若 entry 位於最後 1–3 個檔案 bytes，剩餘 bytes 會來自載入時清零的記憶體，而非完整檔案指令。`pack_users.py` 因此同時檢查對齊與完整的 4 bytes；測試用短 segment 驗證拒絕條件。

## 例外與排程

**問：如何分辨 SVC、IRQ 和 client 的 data abort？**

答：vector 依同步例外或 IRQ 選入口，保存 frame 後傳入 `kind`；`task_exception()` 再檢查保存的 SPSR 與 ESR exception class。`p2-mmio` 中 EC=36、FAR 是 client 嘗試讀取的 `0x20000000`；SVC 的 syscall 編號則在保存的 x8。FAR 對不同 exception 不一定有相同意義。

**問：單核心為什麼仍需要處理事件先後？**

答：IRQ、syscall 和 deadline 雖不會同時在多個 CPU 執行，仍會在不同例外之間交錯。此實作先在 `task_exception()` 處理已到期等待，再處理當次 IRQ 或 syscall；在這個判斷點 `now >= deadline` 時，timeout 先完成。這是明確規定的次序，不是對所有硬體時序的保證。

**問：只有三個 task，是否仍有固定優先權？**

答：有。P3 起 `schedule()` 掃描固定 task 表中的 READY 項目，依 root 0、RTC 1、client 2 選最小優先權；沒有 user task READY 時選 idle。這裡沒有另建 runqueue 節點，同優先權輪轉也不在範圍內。P2 隔離案例使用另一條合作式輪轉路徑。

## IPC 與中斷

**問：client timeout 是否會停止 RTC alarm？**

答：不會。timeout 結束 client 的 `T_REPLY`，不會撤銷已設定的 MR。RTC 仍以自己的 `T_NTFN` deadline 等待，完成裝置清理後可能得到 `E_STALE`。`p5-client-timeout` 實際檢查 client timeout、服務忙碌、IRQ、晚回覆遭拒及後續 PING。

**問：IAR、EOI、ICR 與 IRQ_ACK 都是「確認中斷」嗎？**

答：它們處理不同層次。核心讀 IAR 取得 INTID 並影響 GIC active 狀態，遮蔽 SPI 後寫 EOI；RTC 寫 PL031 ICR 清除裝置來源，再呼叫 IRQ_ACK 讓核心檢查 binding／MIS 並解除 SPI mask。MIS 為 0 不能單獨證明 ICR 已清，因為關閉 IMSC 也可能讓 MIS 變成 0。

**問：host 的六種事件標籤排列，是否驗證了所有 reply／timeout／death 交錯？**

答：沒有。host 測試反覆呼叫同一個 `exchange_finish()`，再以不同標籤記錄哪一次取得完成權，驗證共同完成點只能成功一次，並檢查舊識別資料與 sequence 上限。它沒有執行三種事件各自的整條核心路徑；guest 個案也只覆蓋已執行的次序。

## 故障、重啟與證據

**問：為什麼要先取走死亡事件，才能重啟 RTC？**

答：事件目前以 `death_pending` 保留；取事件時才從 RTC 的 instance 讀出死亡者身分。如果先重啟並遞增 instance，未取走的事件會指向新 instance。核心現在在事件 pending 時拒絕 RESTART 並回 `E_BUSY`。`p6-event` 驗證先拒絕、取出 instance 1／epoch 2，再成功重建 instance 2。

**問：`resource_delta=0` 能證明沒有記憶體洩漏嗎？**

答：不能。它只比較 page-table pool 已使用頁數與初始基線；固定 task slot 和 stack 由程式重用，但沒有以此計數涵蓋所有 RAM 或其他資源。`p7-regression` 執行 100 次故障重啟；並未耗盡 128 次上限或遍歷 64-bit counter wrap。

**問：重啟後重播舊 token 的測試涵蓋了什麼？**

答：每個新 RTC instance 在 READY 前送固定 token 1；該時刻沒有 active exchange，因此得到 `E_STALE`。此 token 來自先前已完成的 PING；guest 測試沒有在新 exchange 已 active 時重播它，也沒有保存並重播每次 fault 當下的 token。`exchange_finish()` 的 token、epoch 與 active 檢查另有 host 測試，兩者證據範圍不同。

**問：所有 Gate 通過，能否說沒有任何 bug？**

答：不能。收據代表指定版本與案例的檢查通過；程式、文件或證據變動後必須重驗。真實硬體、所有輸入與事件交錯都不在這組結果的完整涵蓋範圍。找到不一致時，先保存實際輸出，再提出能區分原因的檢查。
