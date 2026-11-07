# 除錯

工具在 WSL 的專案根目錄執行。先保留失敗 case 的 UART、QEMU command、退出碼與來源指紋；實際缺陷見 [bugs.md](bugs.md)。以下列出目前程式可用的符號與操作。

## 啟動 GDB

在第一個 terminal 執行：

```sh
make all
python3 tools/debug_qemu.py --case p6-pending
```

第二個 terminal 在同一個專案根目錄執行：

```sh
gdb-multiarch -q -x tools/gdbinit
```

`debug_qemu.py` 使用 `config/qemu.json`，預設映像為 `build/kernel.img`、case 為 `p7-demo`、GDB port 為 1234。`--port` 換埠後，GDB 也須改用相同 port。case 名稱見 `include/test_cases.h` 與 `tasks_start()`。

`tools/gdbinit` 載入同次 build 的 `kernel.elf`，連接 localhost 1234，啟用 `panic` breakpoint。`continue` 從 QEMU reset 狀態開始執行；需要看 exception 時，再設 `break *task_exception`，避免預設每次 timer IRQ 都停住。

Debug profile 保留 test profile 的 EL1 semihost exit。`run_qemu.py` 是有 host timeout 的自動驗證器，不用來等待人工單步。GDB 暫停與單步會改變時序，取得的 elapsed time 不作效能證據；debug launcher 不建立 Gate 收據。

## Trap 與 task

在 `*task_exception` 第一條指令停住時，x0 是 `struct trap *`、x1 是 kind：0 為 synchronous、1 為 IRQ、2 為其他 vector。

```gdb
set $tf = (struct trap *)$x0
p current
p $x1
p/x $tf->esr
p/x $tf->far
p/x $tf->elr
p/x $tf->spsr
p/x $tf->sp
p task[current].wait
p task[current].instance
p/x task[current].root
```

`include/trap.h` 與 `arch/aarch64/vectors.S` 共用 offsets。判斷 exception origin 使用保存的 `spsr`；SVC number 在 `$tf->x[8]`，參數在 x0–x5。不要把進入 EL1 後的目前 PSTATE 當成 EL0 的保存狀態。

目前 state 為 `T_READY`、`T_RUNNING`、`T_SLEEP`、`T_RECV`、`T_REPLY`、`T_NTFN`、`T_DEATH`、`T_DEAD`，定義在 `include/state.h`。正式角色各一個固定優先權，scheduler 掃描 READY 集合，沒有另外配置 runqueue 節點。

## IPC 與 deadline

```gdb
p rpc
p epoch
p task[0].wait
p task[1].wait
p task[2].wait
p stats
```

`rpc` 保存唯一 outstanding call 的 token／epoch／client。client 的 `T_REPLY` 與 RTC 的 `T_NTFN` 各自帶 deadline；`expire()` 在處理當次 syscall／IRQ 前執行，`now >= deadline` 時 timeout 優先。`complete()` 經 `exchange_finish()` 取得唯一完成權，再設定結果與 READY；晚 reply 回 `E_STALE`。

只列印本次已保存的值，不在觀測時呼叫 `complete()`、`expire()` 或其他會改狀態的 C 函式。Host exchange 測試驗完成順序；QEMU case 才驗實際 trap／切換與裝置路徑。

## RTC 與 IRQ

```gdb
p rtc_masked
p irq_instance
p/x task[1].pending
p endpoint_ready
x/wx plat.rtc_base
x/wx plat.rtc_base + 0x10
x/wx plat.rtc_base + 0x14
x/wx plat.rtc_base + 0x18
```

上面四個 MMIO 分別為 DR、IMSC、RIS、MIS。GIC IAR 讀取會改變 active state，不為了看 INTID 再讀一次；handler 在 `irq_acknowledge()` 讀取後，以原有局部變數 `id` 分派。`irq_disable()`、`irq_eoi()`、PL031 ICR 與 `SYS_IRQ_ACK` 各有不同責任，不能互相替代。

RTC fault／exit 清理須先遮蔽 SPI，結束仍存在的 client call、失效 endpoint epoch、清 notification／binding／wait，才讓 root 得到死亡事件。`load_role(..., false)` 在 root 的 kernel stack 重建固定 RTC frame 與 data；重用已建立的頁表。用 `stats.restarts`、`stats.masked` 與重建前後 trace 比對資源，避免在 GDB 呼叫函式改變現場。

`p6-pending` 在實際 IRQ 到達、MIS 已為 1、device 尚未 clear 時注入 user fault；這是清理回歸測試。實際發現的缺陷與修正前輸出另記在 `docs/bugs.md`。
