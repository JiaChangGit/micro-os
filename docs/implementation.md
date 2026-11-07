# 實作契約

數值定義以 `include/uapi.h`、`include/trap.h`、`include/test_cases.h` 與 `config/qemu.json` 為準；此處整理核心實際採用的配置。

## 啟動與位址空間

| 項目 | 配置 |
|---|---|
| 平台 | QEMU virt、Cortex-A53、GICv3、TCG、單核心、512 MiB、RTC VM clock |
| Image | 64-byte arm64 header，LE／4 KiB，`text_offset=0x80000`，PA `0x40080000`；`image_size` 含 BSS 與 stack |
| 入口 | 保留 x0 FDT、遮蔽 DAIF、清 BSS、建立 32 KiB boot stack；只接受 EL1 |
| FDT | 上限 2 MiB；所選裝置只接受 root／空 ranges 祖先 bus，GIC 三個 interrupt cells，RTC／timer 必須參照同一 GIC；reserve map 不得與固定 task slot 或核心保守區重疊 |
| 核心 VA | 39-bit、4 KiB；EL1 identity mapping。TTBR1 同時提供 `0xffffff8000000000 + PA` alias；測試只以 leaf routine 驗 instruction fetch |
| 權限 | text RX、rodata R+XN、data/BSS RW+XN；MMIO Device-nGnRE RW+XN；核心映射不允許 EL0 |
| 頁表 | 固定 128-page pool；task 低位址頁表分開，核心 RAM 映射共用；切換 TTBR0 做完整 TLBI，不使用 ASID 最佳化 |

核心映像與頁表須位於 RAM 的前 2 MiB 區間 `[0x40000000, 0x40200000)`；保留區間由 linker symbol 與平台 RAM 範圍檢查。FDT、user 固定 frame 與核心映像不重疊。parser 在檢查 reserve map 前，boot entry 已清除 BSS；因此不能把對核心保守區的拒絕視為防止所有早期寫入。完整核心仍在低位址執行，不宣稱已重新連結到高位址。

## 固定 task 配置

| 項目 | 配置 |
|---|---|
| role | root 0、RTC 1、client 2；每個一條 thread；idle 為 EL1 context |
| physical slot | `0x41000000 + role * 0x20000`，每個 128 KiB／32 pages；不同 task 沒有共用 writable frame |
| BootInfo | slot 第一頁，user VA `0x003ff000`，read-only／XN；結構 64 bytes |
| ELF segments | user VA `[0x00400000, 0x00600000)`；各段按頁配置於 slot 的 BootInfo 後方，總計最多 27 pages；依 ELF flags 建立 R／W／X 權限 |
| user stack | VA `[0x007fc000, 0x00800000)`，四頁 RW／XN；`0x007fb000` 為 unmapped guard |
| RTC MMIO | 只有 RTC 映射 VA `0x20000000` 至 PL031 physical page |
| kernel stack | 每個 context 8 KiB，固定 BSS 配置；重啟保留槽位，不在目前 RTC stack 上清除 RTC stack |
| FP／SIMD | 不保存 FP／SIMD context；user 使用時 trap，P2 有實際指令個案 |

Host packer 驗證固定 AArch64 ELF64、entry、segment bounds／對齊、頁面重疊、W^X，拒絕 dynamic／interpreter／TLS，產生 `build/user_images.h`。核心只載入已驗證 descriptor，不實作任意 ELF loader；複製與清零後執行 D-cache／I-cache 同步。

BootInfo 的八個 64-bit 欄位依序為 `version, role, instance, test_case, rtc_va, endpoint, irq, control`。endpoint handle 為 1；IRQ handle 2 只給 RTC；control handle 3 只給 root。handle 是 task-local 名稱，型別錯誤／不存在回 `E_BAD_HANDLE`，已知物件缺 rights 回 `E_RIGHTS`。

## Trap 與 syscall ABI

Vectors 對齊 2 KiB，trap frame 為 288 bytes：x0–x30 佔 248 bytes；SP_EL0／ELR／SPSR／ESR／FAR offset 分別為 248／256／264／272／280。C／ASM 共用 offset，保存一般暫存器後由 dispatcher 回傳下一個 frame，再 `eret`。

SVC 編號在 x8，參數 x0–x5，回傳 x0 為 signed syscall status，x1–x5 為結果；user wrapper 宣告 `memory`／`cc`。除已知測試 BRK 外，EL1 fault 為失敗退出；EL0 client fault 在 P2 隔離，RTC fault 走服務清理。

FP／SIMD access trap 不以 FAR 定位；P2 彙總將此欄標為 0。Instruction／data abort 才比對其 fault address。

| 編號 | syscall | 參數／結果 |
|---|---|---|
| 0 | YIELD | 重新選擇可執行角色 |
| 1 | LOG | x0 pointer、x1 length；最多 256 bytes，逐頁驗 user readable range |
| 2 | EXIT | RTC 走同一清理路徑；root／client 結束視為固定角色失敗 |
| 3 | SLEEP | x0 相對 milliseconds |
| 4 | CALL | x0 endpoint、x1 timeout、x2–x5 四 word；回覆在 x1–x4 |
| 5 | RECV | x0 endpoint、x1 timeout；回 x1 token、x2–x5 四 word |
| 6 | REPLY | x0 token、x1–x4 四 word；只有 RTC 可用 |
| 7 | WAIT | x0 IRQ handle、x1 timeout；回 x1 notification bits |
| 8 | IRQ_ACK | x0 IRQ handle；驗 binding／instance 及裝置 pending 後解除 mask |
| 9 | READY | x0 endpoint；建立該 RTC instance 的就緒與 IRQ 綁定 |
| 10 | RESTART | x0 control；回 x1 新 instance |
| 11 | WAIT_DEATH | x0 control、x1 timeout；回 x1 死亡 instance、x2 新 epoch |
| 12 | REPORT | 測試用 x0 success、x1 case／失敗行號；核心檢查狀態、輸出計數後退出 |
| 13 | CLOCK | 回 x1 architected counter 的 milliseconds，與 RTC 時間分開 |

所有 timeout／sleep 參數接受 1–10000 ms，0、過大或計算溢位回 `E_INVAL`；沒有無限等待 ABI。未知編號回 `E_NOSYS`，非法 user pointer 回 `E_FAULT`。錯誤碼定義集中在 `uapi.h`；服務回覆錯誤放在 RPC 第一 word，與 x0 syscall status 分開。

## 排程與完成規則

P3 起優先權為 root 0、RTC 1、client 2；每次 bounded scan 選最小優先權的 READY task，無 READY 時執行 `wfi` idle。沒有動態 task、runqueue 節點、同優先權輪轉或 priority donation。P2 測試另用合作式輪轉，IRQ 維持遮蔽。

Physical timer 使用 `CNTPCT_EL0`／`CNTFRQ_EL0` 與 `CNTP_CVAL_EL0`；下一次中斷取 5 ms tick 與最早 deadline 的較早者。進入例外先處理 `now >= deadline`，再處理 syscall／IRQ，因此 deadline 相等時 timeout 優先。

每個 task 的 primary wait 與 deadline 存在同一個 `wait_state`，只經 helper 修改。`SLEEP`／`RECV`／`REPLY`／`NTFN`／`DEATH` 必須有非零 deadline；READY／RUNNING／DEAD 不可有 deadline。IRQ／syscall 處理期間 IRQ 遮蔽，不依賴 `volatile` 做同步。

Endpoint 只有一筆 active exchange，不設 send queue。RTC 已 READY 且在 RECV 才能 CALL；忙碌回 `E_BUSY`，未就緒回 `E_NOT_READY`，已死亡回 `E_PEER_DIED`。token 為不重用的遞增 sequence，exchange 另保存 epoch；完成先驗 active／token／epoch，reply、timeout 或 death 只有第一個能喚醒 client。sequence 用盡拒絕開啟新 exchange。

Client timeout 只完成 client 的 REPLY wait，不取消 RTC 裝置操作。RTC 的 IRQ wait、deadline 與 pending 獨立保留；RTC 完成清理後 late REPLY 回 `E_STALE`，再回到有限 RECV。

## RTC 與 IRQ

RTC 提供 `GET_TIME=1`、`PING=2`、`WAIT_ALARM=3`。alarm 秒數只接受 1–3，邊界 0／4 在 guest 拒絕。正常 IRQ 等待上限為 `(seconds + 2) * 1000 ms`；裝置 IRQ 抑制測試使用 100 ms 上限。

開機與重啟共用流程：SPI masked → RTC 關 IMSC、清 ICR → READY 驗 IMSC=0／RIS=0 → 綁定當前 instance、解除 mask、endpoint 可接請求。RTC 收到 alarm 後先清舊裝置狀態，再設 MR／IMSC，然後 WAIT。

核心 IRQ handler 只讀一次 IAR；RTC SPI 先 mask、EOI，再 OR notification bit。Notification 為可合併 bits，一次 WAIT 取走，不是計數 queue。裝置 MIS 尚為 1 時 ACK 回 `E_BUSY`；RTC 關 IMSC、清 ICR 並 barrier 後再 ACK。接受 ACK 時清 kernel pending、解除 mask，將 timeout 清理時晚到的 notification 一併丟棄。只有一條 RTC thread 操作裝置，沒有新 alarm 與這次 ACK 的並行操作。

MIS=0 不代表 RIS=0，因為關閉 IMSC 也會讓 MIS 歸零；ACK 的檢查不能單獨證明 ICR 已清。清理順序由 RTC 服務執行，重啟 READY 另驗 RIS=0。

測試分別觀察正常 IRQ、先到 notification、缺 IRQ timeout，以及 client 5 ms timeout 後 RTC 仍完成 2 秒 alarm 並得到 stale reply。RTC `timeout` 計數只計 `NTFN` 等待到期；client CALL timeout 在 IPC 計數。

## 死亡、重建與量測

RTC fault／EXIT 先 mask SPI；active client 以原 token／epoch 完成 `E_PEER_DIED`，再關閉 endpoint、解除 binding、增加 epoch、清 notification／deadline，進 DEAD，最後通知 root。這段在 IRQ 遮蔽的 exception context 完成，不暴露可接受新 CALL 的中間狀態。

Root 只有在 DEAD 且死亡事件已取走時能 RESTART；事件尚未取走回 `E_BUSY`。這時已切到 root stack，可清除 RTC user slot 與 kernel stack，保留原頁表 root、重設 frame／wait／pending、增加 instance，再進入共用 READY 流程。上限為 128 次；instance 用盡拒絕重建，epoch 用盡停止核心，避免 wrap 後重用。Root 等死亡有有限 timeout；目前沒有失敗重建後的自動重試或退避機制。

P6 驗一般 fault、裝置 pending 時 fault 與主動 EXIT；P7 驗 100 次一般 fault／restart，並檢查每次 instance／epoch、固定舊 token 1 遭拒、mask 與頁表頁數。固定 token 1 不是故障當下的 token，不能據此聲稱已重播每筆故障請求的 token。`resource_delta` 是 `vm_table_pages()` 相對開機基線的差值；固定 user slot／stack 重用由配置與載入路徑保證，不是總記憶體耗用量測。尚未在 guest 耗盡 128 次上限，也未遍歷所有 fault 指令位置或 64-bit wrap。
