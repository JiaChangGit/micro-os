# GIC 與 timer

範圍固定為 QEMU `virt` 單 CPU、GICv3、Group 1、architected physical timer。GIC base 與 IRQ ID 取自已驗證的 FDT；不掃描多 CPU redistributor、不支援 SGI／LPI／ITS。

`irq_init()` 關閉 distributor，將 SPI 設為 Group 1、level、priority `0xa0`，啟用 affinity routing，全部路由至目前 CPU。喚醒第一個 redistributor 後設定 PPI bank；初始化 `ICC_SRE_EL1`、`ICC_PMR_EL1`、`ICC_BPR1_EL1` 與 Group 1 enable。RWP 與 ChildrenAsleep 等待都有上限，失敗會 panic。這些 register offset 與初始化責任可對照 [Linux v6.6 GICv3 定義](https://github.com/torvalds/linux/blob/v6.6/include/linux/irqchip/arm-gic-v3.h)及 [GICv3 初始化](https://github.com/torvalds/linux/blob/v6.6/drivers/irqchip/irq-gic-v3.c)。

初始化後 timer PPI 已啟用，RTC SPI 尚未啟用，CPU 的 DAIF 維持原值。`irq_enable()`／`irq_disable()` 完成對應 RWP 與 barrier 才返回。

handler 只呼叫一次 `irq_acknowledge()`，保存其 INTID 供分派與 trace 使用。`irq_eoi()` 採 EOImode 0；寫 EOIR 同時完成 priority drop 與 deactivate，不再寫 DIR。1020–1023 的特殊 INTID 不寫 EOI。IAR 讀取後使用 DSB，對照 [Linux arm64 GICv3 register accessor](https://github.com/torvalds/linux/blob/v6.6/arch/arm64/include/asm/arch_gicv3.h)。

`timer_now()` 使用 CNTPCT，`timer_frequency()` 使用 CNTFRQ。`timer_set()` 的 deadline 是此時間域的絕對 ticks，設定 CNTP_CVAL 並啟用 CNTP_CTL；到期後 handler 必須停用或改寫 compare，才能解除 level source。此介面不換算毫秒，也不判定 scheduler deadline。

RTC IRQ 的順序由上層維持：mask SPI → EOI → notification；RTC 清除 PL031 pending 後，經權限與 instance 檢查才重新 enable。
