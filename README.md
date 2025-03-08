# LINE Trello Bot

這是一個整合 LINE、Trello 和 OpenAI 的任務管理與通知系統。

## 功能

1. **綁定 Trello 帳號**：
   - 發送 `綁定 trello@你的Trello會員ID` 到 LINE Bot。

2. **查詢任務狀態**：
   - 發送包含「狀態」或「進度」的訊息。

3. **創建 Trello 卡片**：
   - 發送普通訊息，系統會將訊息內容創建為 Trello 卡片。

4. **任務提醒**：
   - 系統會每 30 分鐘檢查一次 Trello 卡片，並在任務即將到期時發送提醒。

## 安裝與運行

1. 安裝依賴套件：
   ```bash
   pip install -r requirements.txt