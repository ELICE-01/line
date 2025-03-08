import json
import datetime
import hmac
import hashlib
import base64
import requests
import os
import logging
from flask import Flask, request, abort
from apscheduler.schedulers.background import BackgroundScheduler
import openai
from dateutil import parser  #  導入 dateutil parser

app = Flask(__name__)

# 環境變數檢查 (省略，與之前程式碼相同)
# 環境變數讀取 (省略，與之前程式碼相同)
# 設定 OpenAI API Key (省略，與之前程式碼相同)
# 綁定檔案名稱 (省略，與之前程式碼相同)
# 日誌設定 (省略，與之前程式碼相同)
# load_bindings 函式 (省略，與之前程式碼相同)
# save_bindings 函式 (省略，與之前程式碼相同)
# validate_signature 函式 (省略，與之前程式碼相同)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    try:
        validate_signature(body, signature)
    except ValueError:
        logger.warning("Line signature驗證失敗")
        abort(400)

    events = request.json.get('events', [])
    bindings = load_bindings()

    for event in events:
        if event['type'] == 'message' and event['message']['type'] == 'text':
            text = event['message']['text']
            user_id = event['source']['userId']

            if text.startswith("綁定 trello@"):
                # ... (綁定 trello@ 指令處理，與之前程式碼相同) ...
                continue

            trello_id = bindings.get(user_id)
            if not trello_id:
                # ... (未綁定 Trello 帳號提示，與之前程式碼相同) ...
                continue

            if "狀態" in text or "進度" in text:
                # ... (任務狀態查詢處理，與之前程式碼相同) ...
                pass #  注意這裡改為 pass，避免 else 區塊的程式碼被錯誤執行
            else: #  處理一般訊息 (建立 Trello 卡片)
                task_name = ""
                member_name = None
                start_date_str = None
                due_date_str = None
                start_datetime = None
                due_datetime = None

                # 嘗試從訊息中解析任務資訊
                lines = text.split('，')
                for line in lines:
                    if line.startswith("新增任務："):
                        task_name = line[len("新增任務："):].strip()
                    elif line.startswith("成員："):
                        member_name = line[len("成員："):].strip()
                    elif line.startswith("開始日期："):
                        start_date_str = line[len("開始日期："):].strip()
                    elif line.startswith("截止日期：") or line.startswith("日期："):
                        due_date_str = line[len("截止日期："):].strip() if line.startswith("截止日期：") else line[len("日期："):].strip()

                if not task_name:
                    task_name = text
                    send_line_message(user_id, "提醒：請在訊息中包含 '新增任務：' 來建立任務，例如：新增任務：[任務名稱]，成員：[成員名稱]，日期：[週六前]")
                    reply_message = get_chatgpt_response(text)
                else:
                    reply_message = get_chatgpt_response(text)
                    logger.info(f"解析到任務資訊 - 任務名稱: {task_name}, 成員: {member_name}, 開始日期: {start_date_str}, 截止日期: {due_date_str}")

                #  NLP 日期解析
                try:
                    if start_date_str:
                        start_datetime = parser.parse(start_date_str, fuzzy=True)
                        start_date_str = start_datetime.strftime('%Y-%m-%d')
                        logger.info(f"NLP 解析開始日期成功: {start_date_str}")
                    if due_date_str:
                        due_datetime = parser.parse(due_date_str, fuzzy=True)
                        due_date_str = due_datetime.strftime('%Y-%m-%d')
                        logger.info(f"NLP 解析截止日期成功: {due_date_str}")
                except ValueError as e:
                    logger.warning(f"NLP 日期解析失敗: {e}")
                    send_line_message(user_id, f"提醒：日期解析失敗，請嘗試更明確的日期描述，例如：YYYY-MM-DD 或 '下星期一'。")

                create_trello_card(task_name, member_name, start_date_str, due_date_str, due_datetime) #  傳遞 due_datetime


            send_line_message(user_id, reply_message)
    return 'OK'

@app.route('/trello-webhook', methods=['POST'])
def trello_webhook():
    # ... (Trello Webhook 處理，與之前程式碼相同，無需修改) ...
    return 'OK'

def create_trello_card(card_name, member_name=None, start_date_str=None, due_date_str=None, due_datetime=None): #  接收 due_datetime
    try:
        url = "https://api.trello.com/1/cards"
        query = {
            'key': TRELLO_API_KEY,
            'token': TRELLO_TOKEN,
            'idList': TRELLO_LIST_ID,
            'name': card_name,
        }

        #  處理成員分配 (與之前程式碼相同，無需修改)
        if member_name:
            member_id = get_trello_member_id_by_name(member_name)
            if member_id:
                query['idMembers'] = member_id
                logger.info(f"將卡片 '{card_name}' 分配給成員：{member_name} (ID: {member_id})")
            else:
                logger.warning(f"找不到名為 '{member_name}' 的 Trello 成員。")

        #  處理截止日期 (修改部分)
        if due_datetime: #  判斷 due_datetime 是否為 None
            try:
                # due_date = datetime.datetime.fromisoformat(due_date_str) #  移除原本的字串解析
                query['due'] = due_datetime.isoformat() #  直接使用 NLP 解析後的 datetime 物件
                query['dueReminder'] = '1440'
                logger.info(f"為卡片 '{card_name}' 設定截止日期：{due_datetime.strftime('%Y-%m-%d %H:%M')}")
            except ValueError as e: #  雖然這裡的 ValueError 理論上不會發生，但為了程式碼完整性，保留 try...except 區塊
                logger.error(f"設定截止日期失敗 (datetime 轉換錯誤): {e}")

        response = requests.post(url, params=query)
        response.raise_for_status()
        logger.info(f"已創建 Trello 卡片：{card_name}")
    except requests.exceptions.RequestException as e:
        logger.error(f"創建 Trello 卡片失敗：{e}")

def get_list_map():
    # ... (get_list_map 函式，與之前程式碼相同，無需修改) ...
    return {}

def get_user_trello_tasks(trello_member_id):
    # ... (get_user_trello_tasks 函式，與之前程式碼相同，無需修改) ...
    return "無法獲取任務狀態，請稍後再試。"

def get_chatgpt_response(prompt):
    # ... (get_chatgpt_response 函式，與之前程式碼相同，無需修改) ...
    return "無法生成回應，請稍後再試。"

def send_line_message(user_id, message):
    # ... (send_line_message 函式，與之前程式碼相同，無需修改) ...

def check_trello_cards():
    # ... (check_trello_cards 函式，與之前程式碼相同，無需修改) ...

def get_trello_member_id_by_name(member_name):
    # ... (get_trello_member_id_by_name 函式，與之前程式碼相同，需要您自行實作成員搜尋邏輯) ...
    return None


scheduler = BackgroundScheduler()
scheduler.add_job(check_trello_cards, 'interval', minutes=30)
scheduler.start()
logger.info("排程任務已啟動")

if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=5000)