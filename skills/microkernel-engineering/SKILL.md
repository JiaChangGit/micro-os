---
name: microkernel-engineering
description: "Build, review, and debug the fixed-role AArch64 microkernel in micro-os using phase gates, QEMU evidence, and focused source reading. Use for this project's kernel, tooling, design diagrams, and interview evidence."
---

# Microkernel Engineering

在目前工作區找到 `config/phases.json` 與 `docs/project-contract.md`。若不是這個專案，不把本 skill 的架構套到其他 repository。

## 先取得小份上下文

執行 `python3 tools/context.py --phase P<N>`；只讀輸出列出的相關實作與參考。尚未指定 Phase 時先用 `python3 tools/phase.py status` 看現況，再依使用者要求決定工作單位。

`docs/project-contract.md` 是正式範圍，`docs/phases.md` 說明 Gate。本機若保留根目錄舊 HTML，可按需參考底層細節；該檔不在儲存庫內，不沿用它的 24 syscalls、多 thread RTC、priority donation 或動態 capability 授權。

## 實作前要釘清的事

- 對應 Phase 的 ABI、資料版面、擁有者與狀態轉移要能在文件／程式定義中找到。未凍結的數值先完成當期契約，勿從別章猜值。
- 保留固定 root／RTC／client、各一 thread、單核心 TCG。RTC `WAIT_ALARM` 是同步 RPC；忙碌回 E_BUSY，不擴成多 client queue。
- client reply wait 與 RTC IRQ wait 是不同等待記錄。client timeout 不會取消硬體 alarm；RTC 自己有有限期限與收尾，late reply 回 E_STALE。
- IRQ pending／GIC active／notification／thread state 分別觀測。GIC IAR 讀取有副作用，記錄 handler 的既有讀值。
- 重啟固定 RTC 槽位仍須 instance／epoch、清除舊通知與失效 token。清理完成才可重啟；current kernel stack 由安全的 scheduler context 處理。

## 依工作選參考

- MMU、例外、排程、IRQ、IPC、復原：讀專案的 `docs/debugging.md` 中相關段落。
- 系統分析與真實除錯案例：讀 `docs/interview.md`。故障注入與實際發現的缺陷分開表達。
- 教學依實際程式與可重現觀察撰寫，搭配資料流、狀態轉移與呼叫路徑；入口在 `docs/tutorial/README.md`。
- 工具介面與證據格式：讀 `docs/tooling.md`。Windows 是入口，真正 build／test 在 WSL 執行。

## 完成與證據

修改工具後執行 `make check`。修改核心時執行當期與前置 Gate 的實際 validator；host 工具測試不能替代 guest Gate。

`run_qemu.py` 只驗 process、marker 與 failure 條件。其 `evidence_kind=qemu_transport`、`semantic_validation=false`，不能當 Phase 完成證據。語意 validator 需產生 `docs/tooling.md` 定義的收據，含真實 assertions、原始證據及目前來源指紋，再執行 `tools/phase.py check`。

缺工具回報 BLOCKED，未執行是 NOT_RUN。不要產生成功 stub、預寫 PASS、補造 trace 或用增大 timeout 掩蓋原因。發現契約矛盾時修正影響最小的當期設計與測試；會改變使用者已選範圍時再對齊，不增加例行批准流程。

交付包含實際驗證與未驗證範圍。文件使用自然繁中，保留工程決策與證據，省略 AI 對話紀錄。
