# 分階段建置與驗收

需求以 [project-contract.md](project-contract.md) 為準，數值與 ABI 見 [implementation.md](implementation.md)。P0–P7 各有實作及 validator；當前完成狀態由 `python3 tools/phase.py status` 讀取有效收據，不在文件手動維護 PASS。Gate ID 與相依關係以 [config/phases.json](../config/phases.json) 為準。

## Phase 與實際驗收

| Phase／Gate | 實作 | 驗證範圍 |
|---|---|---|
| P0 `p0-image`、`p0-boot` | Image header、EL1 entry、UART、受控 FDT parser、明確測試退出 | host 檢查 header 與壞 header；guest 回報 EL1、FDT 範圍及平台欄位，與實際 DTB 比對；FDT host 測試使用 sanitizer |
| P1 `p1-pte`、`p1-vm` | 4 KiB MMU、核心 identity mapping、高位址 alias、vectors、固定頁表 pool | host 檢查 PTE 編碼與拒絕條件；guest 檢查 MMU、text RO／data XN、頁表保留區、高位址 leaf fetch 與已知 BRK 恢復 |
| P2 `p2-isolation` | 三個固定 EL0 task、獨立 TTBR0、BootInfo、SVC、EL0 fault 隔離 | 六個個案分別觸發 MMIO、text write、stack guard、核心位址、XN、FP／SIMD trap；檢查 exception class／FAR、非法 syscall／pointer 拒絕及 root 繼續執行 |
| P3 `p3-state`、`p3-sched` | GICv3、physical timer、固定優先權、READY 集合掃描、有限等待與 idle | host 檢查等待狀態／deadline 邊界；guest 驗 timer 搶占不發 SVC 的 RTC busy loop、sleep 喚醒，以及全體阻塞時進入 idle |
| P4 `p4-ipc-model`、`p4-ipc` | 靜態 handle、四 word RPC、單筆 exchange、token／epoch、唯一完成 | host 以不同事件標籤重複呼叫共同完成函式，檢查只能成功一次、舊 token／epoch 與 sequence 用盡拒絕；guest 驗成功回覆、bad handle／rights、busy、client timeout、late reply 與完成計數 |
| P5 `p5-rtc` | PL031 `GET_TIME`／`PING`／`WAIT_ALARM`、READY 握手、IRQ notification／ACK | 四個個案涵蓋正常 IRQ、遮蔽裝置中斷造成有限 timeout、IRQ 先於 WAIT、client timeout 後 RTC 繼續等待及 late reply；另驗 alarm 秒數 0／4 拒絕及 pending 未清時 ACK 拒絕 |
| P6 `p6-restart` | RTC fault／EXIT 共用清理、root 監督、固定槽位重建 | 一般 fault 與主動 EXIT 各重啟三次；另驗裝置 pending 時 fault 與死亡事件未取走前拒絕重啟。驗 instance／epoch 更新、固定舊 token 1 遭拒與 SPI 遮蔽 |
| P7 `p7-regression`、`p7-demo` | 重啟回歸與端到端展示 | 100 次 fault／restart 後頁表頁數回到開機基線；demo 完成三次重啟後的 alarm RPC，並檢查 IPC 計數與 IRQ |

P2 為隔離測試使用合作式輪轉；P3 起採 root 0、RTC 1、client 2 的固定優先權，數字小者優先。三個角色優先權不同，沒有同優先權 round-robin 或 priority donation。P3 的 READY 集合是固定大小掃描，未配置 runqueue 節點。

## 操作與交付

```bash
make all
make check
make test-p0
make test-p1
# 依序執行至 test-p7；完整驗收使用 make test。
make phase-check PHASE=P7
```

各 Phase 測試可以單獨執行；收據要依相同來源與相依順序通過，`phase.py check` 才接受。文件或程式變動也會使舊收據過期，完整重跑後再判定狀態。

每個實際缺陷記錄重現條件、症狀、根因、修正與回歸，放在 [bugs.md](bugs.md)。故障注入是測試條件，不能寫成曾經發現的缺陷。各條路徑的閱讀順序與操作見 [教學目錄](tutorial/README.md)。

## 證據界線

- `make check` 驗工具；preflight 的 READY 驗環境，兩者都不代表 guest 功能通過。
- `run_qemu.py` 產生 `evidence_kind=qemu_transport`、`semantic_validation=false` 的報告；其 PASS 只支持程序、marker、失敗訊息與清理檢查。
- `tools/gates.py` 分析實際 guest 欄位與 host 測試，產生 `build/reports/gates/<gate_id>.json`；`phase.py` 再驗來源指紋、證據 hash 及前置順序。
- `resource_delta=0` 只表示頁表頁數無增長。task frame、user stack、kernel stack 採固定配置並重用；這不是 heap 或整體 RAM 使用量量測。
- host exchange 測試檢查共同完成函式的單次成功、舊識別資料與 sequence 用盡，不執行各事件的核心分支；guest 未遍歷所有指令交錯、所有 fault 位置或 64-bit wrap。128 次重啟上限有拒絕分支，尚無 guest 上限耗盡個案。
- QEMU TCG 的結果支持功能與順序分析，不支持真實 Cortex-A53 延遲、效能或 hard real-time 結論。

完整 CLI 與收據欄位見 [tooling.md](tooling.md)。互動 GDB 與自動 timeout 測試分開執行；host wall-clock、architected counter 與 RTC clock 分別解讀。
