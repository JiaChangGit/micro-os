# 缺陷紀錄

## 001：DTB 探測觸發不需要的網卡 ROM

- 重現：初次 `make all` 的 QEMU DTB 匯出步驟。
- 症狀：`failed to find romfile "efi-virtio.rom"`，命令非零退出。
- 根因：探測命令未關閉預設網卡；目前套件安裝沒有該 option ROM。
- 修正：DTB 探測與正式 QEMU profile 一樣使用 `-nic none`。Make 加入 `.DELETE_ON_ERROR`，避免失敗留下的產物被當作成功結果。
- 回歸：重新匯出 DTB，並在 host parser 測試及 guest 平台欄位比對中使用。

## 002：核心 ELF 的 LOAD segment 同時可寫與可執行

- 症狀：連結器回報 `has a LOAD segment with RWX permissions`。
- 根因：linker script 未指定 program headers，text 與可寫資料合併成一個 LOAD segment。
- 修正：分為 RX text、R rodata、RW data/BSS；segment 對齊固定 4 KiB。
- 回歸：`readelf -lW build/kernel.elf` 檢查 flags；頁表權限另在 P1 驗證。

## 003：FDT bus／interrupt schema 檢核不足

- 重現一：把合成 bus 的 identity ranges 長度縮至不包含 UART，parser 仍接受其 `reg`。
- 重現二：把真 DTB 的 `#interrupt-cells` 從 3 改為 2，parser 仍用三個 cell 解讀中斷。
- 根因：檢查了數值與基本 bounds，卻未完整釘清所支援的 bus/controller schema。
- 修正：所選裝置只接受 root 或空 ranges 的祖先 bus；GIC 必須宣告三個 interrupt cells、有效 phandle 與 interrupt-controller；RTC／timer 必須參照同一個 GIC。
- 回歸：兩項負面 fixture、foreign interrupt-parent、真 DTB 與 ASan／UBSan 變異測試。

## 004：PTE 測試與 validator 的輸出格式不一致

- 症狀：`make test-p1` 中，host 程式退出 0、QEMU MMU 測試成功，`p1-pte` 卻失敗於 `pte_checks_executed`。
- 定位：host 輸出 `PTE PASS ...`，validator 只接受 `[HOST] pte=PASS assertions=N`；並非頁表錯誤。
- 修正：每項 assertion 實際成立後計數，再輸出一致格式；不放寬 validator。
- 回歸：25 項 PTE assertion 與 QEMU `p1-vm` 分別執行。

## 005：RTC 正常退出漏做死亡清理

- 重現：RTC 完成 READY、接到 client RPC 後執行 `SYS_EXIT`。修正前 `p6-exit` 以退出碼 1 結束，UART 為 `[PANIC] dead RTC exposed`。
- 定位：GDB 在 RTC 的 RECV 入口把保存的 syscall number 換成 EXIT，得到相同結果。當時 `SYS_EXIT` 只改成 `T_DEAD`，endpoint 與 IRQ binding 仍有效，等待 client 也沒完成。
- 修正：正常退出與 user fault 共用清理：mask SPI、以 `E_PEER_DIED` 完成尚存的 call、失效 epoch／binding、清 notification／wait，最後公布死亡事件。root 切換到自己的 kernel stack 後才能重設 RTC stack。
- 回歸：`p6-exit` 連續三次退出、重啟與 PING；`p6-pending` 另驗實際 IRQ 到達、MIS=1 時發生 fault 的清理。
- 修正前原始紀錄：[UART](evidence/rtc-exit-before.log)、[GDB](evidence/rtc-exit-gdb.log)。GDB 修改 syscall 是重現方法；缺陷是 EXIT 路徑缺少清理。

## 006：user header 修改未觸發重新編譯

- 症狀：新增共用測試編號後，`make all` 沒有重新編譯未直接修改的 root 程式。
- 根因：compiler 已產生 user `.d`，Makefile 卻只 include kernel object 的 `.d`；因此 header 改動可能讓三個固定映像使用不同定義。
- 修正：納入三個 user object 與 start 的依賴檔；保留中間 object，避免每次無條件重編。
- 回歸：更新共用 header 的時間後，`make -n all` 應列出三個 user compile、ELF pack 與 kernel relink；完整 `make rebuild` 再驗實際映像。

## 007：整合展示誤套 IPC 單項測試的計數規則

- 症狀：`p7-demo` 的 guest 已成功退出，validator 卻要求 BUSY／timeout／rights 錯誤，並以 `completed == replies + timeout` 檢查包含死亡事件的展示。
- 定位：原始展示為 calls=10、replies=7、peer_died=3、completed=10。三筆死亡完成被漏算；展示也沒有觸發上述負面個案。
- 修正：展示檢查 `calls == completed == replies + timeout + peer_died`；BUSY／timeout／rights 要求保留在 `p4-ipc`。不重設 runtime 計數來遷就驗證器。
- 回歸：validator 負面 fixture 與實際 P4／P6／P7；另核對每筆 DEATH／REBUILD 的次數、順序、instance、epoch 與頁表數。

## 008：組語共用常數的 include 順序錯誤

把 user entry 的 EXIT 編號改用 UAPI 定義時，header 放在使用位置後方，assembler 回報 `undefined symbol UAPI_SYS_EXIT used as an immediate value`。移到檔案開頭，再以完整重建檢查 C／ASM 共用定義。此為建置錯誤，不作為核心執行期除錯案例。

## 009：ELF 入口允許不完整的指令

- 重現：將 host 測試 ELF 的可執行 segment 檔案長度改為 1、2 或 3 bytes，入口仍指向 segment 開頭。
- 症狀：原驗證只檢查入口位址落在檔案資料內，因而接受不足 4 bytes 的 AArch64 指令；載入後缺少的 bytes 會被補零。
- 修正：要求入口對齊，且從入口起的完整 4 bytes 都位於可執行 segment 的檔案資料內。
- 回歸：對 1–3 bytes 的 fixture 驗證拒絕，4 bytes 仍可接受；既有固定 ELF 重新建置與 guest 測試另行驗證。此問題在 host 輸入邊界重現，不表示既有固定 ELF 曾發生執行錯誤。

## 010：FDT reserve map 與固定記憶體重疊仍被接受

- 重現：在自訂 DTB 加入 `/memreserve/ 0x41000000 0x60000;`，覆蓋三個 task 固定 slot。
- 症狀：修正前 parser 接受 DTB，guest 繼續建立 task 並寫入該區域；預設 QEMU DTB 未出現此衝突。
- 修正：parser 拒絕與固定 slot `[0x41000000, 0x41060000)` 或核心保守區 `[0x40080000, 0x40200000)` 重疊的 reserve entry。
- 回歸：host 測試檢查前後相鄰、單 byte 重疊、非重疊和溢位；QEMU 使用衝突 DTB 時非零退出，使用預設和相鄰 reservation 時正常啟動。parser 執行前 boot entry 已清 BSS，此修正無法防止所有早期寫入。

## 011：死亡事件未取走前重啟，事件 instance 改變

- 重現：在隔離副本讓 root 先睡眠，使 RTC fault 先留下死亡事件，再先執行 RESTART、後執行 WAIT_DEATH。
- 症狀：修正前重啟成功並將 instance 從 1 增為 2；之後 WAIT_DEATH 卻從目前 task 讀出 2，而事件應屬於 instance 1。該副本以 `[USER_FAIL]` 結束；[建置與輸出紀錄](evidence/death-event-before-source.log)的本機路徑已匿名化，其餘內容保留。
- 定位：事件只由 `death_pending` 表示，未另存 instance。另以 GDB 注入 pending 狀態，在 RESTART 前後觀察到同一現象；[GDB 紀錄](evidence/death-event-before-gdb.log)和 [UART](evidence/death-event-before-uart.log) 為該次注入實驗，與無注入的隔離副本重現分開。
- 修正：RTC 已 DEAD 但事件仍 pending 時，RESTART 回 `E_BUSY`；root 取走事件後才能重建。
- 回歸：`p6-event` 驗證先拒絕、取出死亡 instance 1／epoch 2，再重建 instance 2，並繼續兩次故障與重啟。
