# 系統分析與查證

固定配置、ABI 與測試範圍見 [實作契約](implementation.md) 和 [驗收說明](phases.md)。從具體問題複習可讀 [系統問答](tutorial/04-questions.md)；逐步追蹤可從 [教學目錄](tutorial/README.md) 開始。真實缺陷的症狀、定位與回歸見 [缺陷紀錄](bugs.md)。

比較其他系統時，應先確認各自的角色和介面。例如這裡的 RTC 服務在 EL0；[Linux v6.6 PL031 程式](https://github.com/torvalds/linux/blob/v6.6/drivers/rtc/rtc-pl031.c) 則是核心中的裝置驅動。兩者可比較中斷、裝置清除和等待責任，但不能直接把其中一者的錯誤處理保證套到另一者。
