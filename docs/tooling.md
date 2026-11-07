# 工具與證據

工具在 WSL 的 Linux 檔案系統執行，使用 Python 標準函式庫。從專案根目錄執行下列指令；核心與 Gate 對應見 [phases.md](phases.md)。

## 常用入口

| 指令 | 行為與結果 |
|---|---|
| `make all` | 建置 user ELF、打包 segments、核心 ELF／Image 與平台 DTB |
| `make rebuild` | 強制重建全部產物，保留既有 reports |
| `make test` | 依序執行工具、host 與 P0–P7 validator，最後檢查 P7 前置鏈 |
| `make test-p0` 至 `make test-p7` | 執行個別 Phase 的 validator；不自動替其他 Phase 更新收據 |
| `make demo` | 執行 p7-demo 並顯示 guest 原始輸出；產生 transport report |
| `make check` | 靜態檢查與工具自測；不要求 target 工具，不產生 guest Gate |
| `make toolcheck` | host 工具與資源檢查 |
| `make toolcheck-target` | 加上 AArch64 工具鏈與 QEMU 能力檢查 |
| `make context PHASE=P0` | 當期契約摘要、freeze 項、Gate 狀態、相關檔案與指令 |
| `make phase-status` | 由收據衍生 P0–P7 狀態 |
| `make phase-check PHASE=P0` | 檢查當期及前置 Gate 收據；不足時非零退出 |
| `make qemu TEST=p6-pending IMAGE=build/kernel.img` | 建置後傳入相同 case bootargs，執行一個 test profile 個案並保存 transport report |

`make all` 產生 `build/kernel.img`。可用 `python3 tools/run_qemu.py --case p0-boot --image build/kernel.img --boot-args case=p0-boot --dry-run` 檢查 argv，不啟動 guest 或產生證據。Harness 的 `--case` 命名報告與預期 marker；`--boot-args` 才決定 guest 執行情境，兩者必須一致。

工具測試預設只輸出摘要與失敗細節；要列出每個案例時用 `make test-tools TEST_ARGS=-v`。`context.py` 將環境／前置 Gate 指令與當期完成後驗收分開列出；自己的 Gate 尚未通過，不是開始實作的阻擋條件。

## 環境與依賴

```bash
python3 tools/bootstrap.py
python3 tools/bootstrap.py --install
python3 tools/preflight.py --profile host
python3 tools/preflight.py --profile target --json --output build/reports/environment.json
```

Bootstrap 預設只顯示需求；`--install` 執行 WSL 的 apt 安裝並由 sudo 互動認證，不保存密碼。Preflight 檢查版本、資源、QEMU machine／CPU／必要屬性與 cross compiler target。檢查預設只讀，指定 `--output` 才寫報告。

`READY` 表示檢查到的工具可用，不代表 guest boot 成功。缺工具或必要能力未確認時回 `BLOCKED`／非零退出；完整原因在報告中。

## Image 與 QEMU

```bash
python3 tools/inspect_image.py build/kernel.img --json
python3 tools/run_qemu.py --image build/kernel.img --case p0-boot --boot-args case=p0-boot --timeout 15
```

Image 檢查固定的 raw arm64 Image header、branch 入口、LE／4 KiB flags、大小與溢位等條件。`VALID` 只支持格式檢查，不證明指令或開機流程正確。`tools/pack_users.py` 在建置時另外檢查三個固定 ELF 的 segments、權限、對齊、entry 與範圍；產生 `build/user_images.h` 供核心載入。

QEMU 參數由 [config/qemu.json](../config/qemu.json) 與 `tools/qemu_command.py` 共用：TCG、單核心、Cortex-A53、GICv3、512 MiB、明確的 RTC VM clock。改變 memory／machine／版本後重新核對 DTB 與保留區，不以舊位址推定新配置。

Test 與 debug profile 都只允許 privileged semihosting，作為目前測試映像的退出通路。Debug 另外綁定 loopback GDB 並暫停 CPU。沒有獨立 release profile。

互動除錯分兩個 terminal 執行，不建立 Gate 收據：

```bash
python3 tools/debug_qemu.py --case p6-pending
gdb-multiarch -q -x tools/gdbinit
```

預設 GDB port 1234；`debug_qemu.py --port` 可修改，GDB 的 target 必須跟著調整。`gdbinit` 載入 `build/kernel.elf` 並先設 panic breakpoint；user ELF 使用相同 VA，檢查 user symbol 時須配合當前 role／TTBR0。互動停機時間不納入自動 timeout 測試。

Harness 的選項：

| 選項 | 用途 |
|---|---|
| `--image PATH`、`--case NAME` | 必填；個案名稱限制為安全的簡單名稱 |
| `--boot-args case=NAME` | 傳入 guest；最多 63 個 printable ASCII 字元 |
| `--timeout SECONDS` | 有限正數的 host wall-clock 上限 |
| `--expect-marker LINE` | 可重複；要求原始輸出中出現完整相同的一行 |
| `--report-dir PATH` | 預設 `build/reports/qemu`，限制在 repository 內 |
| `--config PATH` | 使用另一份符合平台契約的設定 |
| `--show-output` | 完成後顯示已保存的 stdout／stderr |
| `--dry-run` | 只顯示 argv，不寫報告或執行 |

每次執行建立 `<report-dir>/<case>/<UTC timestamp-unique-id>/`，包含 `stdout.log`、`stderr.log`、`report.json`。輸出直接寫檔，避免 pipe 滿而卡住。解析時每行上限 4096 bytes，過長行使個案失敗並以有界讀取略過，避免故障 guest 的連續輸出耗盡解析記憶體。Timeout／中斷後終止整個 process group；殘留子程序、非零退出、缺 marker 或 panic／invariant failure 都不能 PASS。`--executable` 是測試替身入口；以 fake process 跑過 harness 自測不能支持 guest 功能結論。

Transport report 使用 `evidence_kind=qemu_transport`、`semantic_validation=false`，保存命令、工具版本、映像 digest、來源資訊、時間、退出碼、實際 marker 與 host assertions。它不分析裝置暫存器、頁表、請求或重啟語意。

## Phase 收據

個案專用 validator 讀取真實輸出，檢查當期觀察值與不變量，再寫入 `build/reports/gates/<gate_id>.json`。Gate ID 與前置關係來自 [config/phases.json](../config/phases.json)。

| 欄位 | 必要內容 |
|---|---|
| `schema_version` | 整數 `1` |
| `gate_id`、`phase` | 與設定中的 Gate、Phase 相符 |
| `status` | 實際判定結果；檢查通過要求 `PASS` |
| `evidence_kind` | `phase_gate`；transport report 不適用 |
| `semantic_validation` | 布林 `true`，代表此 validator 已做案例語意檢查 |
| `source_fingerprint` | 目前來源的 SHA-256 指紋，64 個小寫十六進位字元 |
| `command`、`exit_code` | 真正執行的非空 argv，退出碼需為整數零 |
| `assertions` | 非空的具名檢查；每項有 `name` 與布林 `passed`，全部通過 |
| `timestamp_utc` | ISO 8601 UTC 時間，使用 `Z` 或 `+00:00` |
| `evidence` | 非空的原始檔案清單；每項有 repository 相對、使用 `/` 的 `path` 與 64 位小寫十六進位 `sha256` |

`phase.py` 檢查收據、來源指紋、檔案 digest 與前置順序；不替 validator 分析 guest，也不以手動狀態旗標證明功能。收據需連到當次真實 raw logs／觀察資料，不能只放預期文字或自行寫入成功值。

來源指紋排除 build、cache 與第三方下載，涵蓋專案來源及維護中的契約。文件或程式改動後可能使既有收據過期；重新執行對應驗證，不直接改寫收據中的 hash。`source_fingerprint()` 的實際範圍以 `tools/project.py` 為準。

收據不存在時顯示 `NOT_RUN`；來源指紋不同為 `STALE`，原始證據 hash 不符或收據失敗為 `INVALID`。失敗收據保留 FAIL 與原始資料。工具單元測試使用暫存 fixture 驗證拒絕條件，不把 fixture 放進真實 reports 目錄。重跑會更新 Gate 收據，QEMU 原始執行目錄仍逐次保留。

## Skill 同步

先將下列範例路徑替換成自己的 Codex skills 目錄：

```bash
SKILLS_DIR=/path/to/codex/skills
python3 tools/install_skill.py --target "$SKILLS_DIR"
python3 tools/install_skill.py --target "$SKILLS_DIR" --check
```

來源是 [skills/microkernel-engineering](../skills/microkernel-engineering/SKILL.md)。安裝工具同步到目標 skills root 下的同名目錄，以 manifest 限制可管理檔案；`--check` 檢查是否與來源一致。更新專案來源後再同步，避免全域副本與 repository 漂移。

## 官方參考

- [QEMU virt](https://www.qemu.org/docs/master/system/arm/virt)：DTB 與裝置配置。
- [QEMU invocation](https://www.qemu.org/docs/master/system/invocation.html)：RTC clock、GDB、trace 與 semihosting。
- [arm64 boot protocol](https://kernel.org/doc/html/latest/arch/arm64/booting.html)：Image header、DTB 與入口條件。

這些文件說明底層契約；實際工具版本、命令與結果由本機報告保存。
