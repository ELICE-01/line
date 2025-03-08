# LINE + Trello + ChatGPT 整合機器人

## 功能

- 綁定LINE帳號與Trello帳號
- LINE查詢自己的Trello任務進度
- LINE收到Trello即將到期的提醒
- Trello新卡自動建立
- ChatGPT自動回覆LINE訊息

## 使用說明

1. 建立 `.env`，填入你的LINE、Trello、OpenAI資訊。
2. 啟動服務：
    ```bash
    docker-compose up --build
    ```
3. 到LINE後台設定Webhook：
    ```
    https://你的IP或域名:5000/callback
    ```
4. 在LINE聊天輸入：
    ```
    綁定 trello@你的Trello帳號ID
    ```
5. 查詢進度：
    ```
    查詢進度
    ```

## 目錄結構

