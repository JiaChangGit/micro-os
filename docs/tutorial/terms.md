# 術語與縮寫

本頁只收錄閱讀本專案時常遇到的名稱。每項先說明意思，再指出它在程式中的用途；完整控制流程請回到各章和原始碼。

## 執行環境與建置

| 名稱 | 簡要說明 |
|---|---|
| **Host／Guest** | Host 是執行 QEMU 的 WSL 環境；guest 是 QEMU 中執行的 AArch64 系統。Host 測試與 guest 測試的執行位置不同。 |
| **WSL2** | Windows Subsystem for Linux 2，讓 Windows 上執行 Linux 環境；本專案的建置工具在該 Linux 環境執行。 |
| **QEMU／TCG** | QEMU 提供虛擬機器及裝置模擬；TCG（Tiny Code Generator）是 QEMU 的動態指令轉譯執行方式。本專案以 TCG 執行 AArch64 guest。 |
| **AArch64／EL0／EL1** | AArch64 是 Arm 的 64-bit 執行狀態；EL 是 Exception Level（例外層級）。本專案的 user task 在 EL0，核心在 EL1。 |
| **ELF／Image** | ELF（Executable and Linkable Format）是包含可執行內容及中繼資料的檔案格式；本專案的 `kernel.elf` 供連結資訊與 GDB 使用，`kernel.img` 則是 QEMU 載入的 raw Image。 |
| **ABI** | Application Binary Interface，規定二進位元件之間如何交換資料。本專案的 syscall ABI 例如指定 x8 為編號、x0–x5 為參數。 |
| **FDT／DTB** | FDT 是 Flattened Device Tree；DTB 是其二進位資料，描述裝置與記憶體等平台資訊。核心以 parser 讀取其中位址及 IRQ。 |

## 記憶體與例外

| 名稱 | 簡要說明 |
|---|---|
| **VA／PA** | VA（Virtual Address）是程式使用的虛擬位址；PA（Physical Address）是位址轉譯後的實體位址。不同 task 可用相同 VA 對應不同 PA。 |
| **MMU／page table／PTE** | MMU（Memory Management Unit）依頁表進行位址轉譯及權限檢查；PTE（Page Table Entry）是頁表中的一筆項目，記錄對應位置或下一級表及屬性。 |
| **TLB** | Translation Lookaside Buffer，快取位址轉譯結果。切換頁表或修改映射時，需依架構規則處理可能過期的轉譯；本專案在 `vm_activate()` 執行 TLB 維護。 |
| **W^X／XN** | W^X 指頁面不應同時可寫又可執行；XN（Execute-Never）表示禁止取指執行。本專案將 user data／stack 設為不可執行。 |
| **MMIO** | Memory-Mapped I/O，透過記憶體位址存取裝置暫存器。此專案僅將 PL031 的 user MMIO 頁映射給 RTC task。 |
| **Exception／fault／IRQ** | Exception 是處理器轉入例外處理的機制；fault 是其中一類與目前指令執行相關的例外，例如非法存取。IRQ（Interrupt Request）是裝置或 timer 等來源提出的中斷。 |
| **SVC／syscall** | SVC（Supervisor Call）是 AArch64 的例外指令；syscall（system call，系統呼叫）是 user 向核心要求服務的介面。本專案以 SVC 進入核心，再依 x8 選擇 syscall。 |
| **Vector／trap frame** | exception vector 決定例外入口；trap frame 是本專案保存的暫存器和例外資訊結構，供核心分析與恢復執行。 |
| **ESR／FAR／ELR／SPSR** | 分別是例外原因、fault 位址、例外返回位置與保存的程式狀態暫存器；例如 `ESR_EL1` 供核心分類例外。FAR 是否有效取決於例外種類。 |

## 通訊、等待與裝置

| 名稱 | 簡要說明 |
|---|---|
| **IPC／RPC** | IPC（Inter-Process Communication）是不同執行單元間的通訊；RPC（Remote Procedure Call）是以請求與回覆表達操作的方式。本專案使用固定長度的同步 request／reply。 |
| **Handle／rights** | Handle 是 task 用來指定核心物件的名稱；rights 是可執行的操作權限。本專案會檢查物件類型與 rights，沒有動態授權傳遞。 |
| **Token／epoch／instance** | Token 用來識別一筆 exchange；epoch 區分 endpoint 的生命週期；instance 識別 RTC 的不同執行個體。三者由核心在不同時點更新或檢查。 |
| **Timeout／deadline** | Timeout 是允許等待的時間長度；deadline 是換算後的截止時間。本專案以 architected counter ticks 保存各 task 的絕對 deadline。 |
| **Pending／notification** | Pending 表示某事件已記錄、尚待處理；notification 是核心提供給 task 的通知。此處以 bits 合併事件，不是逐筆計數佇列。 |
| **RTC／PL031** | RTC（Real-Time Clock）是時間裝置；PL031 是本專案使用的 RTC 裝置型號。RTC task 透過其暫存器讀時間和設定 alarm。 |
| **GIC／PPI／SPI** | GIC（Generic Interrupt Controller）負責中斷分派；PPI 是 Private Peripheral Interrupt，SPI 是 Shared Peripheral Interrupt。本專案分別以 timer PPI 和 RTC SPI 接收中斷。 |
| **Mask／EOI／ACK** | Mask 是遮蔽中斷；EOI（End of Interrupt）通知 GIC 結束目前中斷處理；本專案的 `IRQ_ACK` 是 RTC 清理裝置後向核心要求解除遮蔽的 syscall，三者作用不同。 |

例如，一次 `WAIT_ALARM` 會讓 client 等待 RPC 回覆，RTC 等待 IRQ notification。兩者各有 deadline；client timeout 不等於 RTC 已取消裝置操作。完整時序見 [RTC 中斷與重啟](03-rtc-recovery.md)。
