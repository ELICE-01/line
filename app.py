from flask import Flask, request, abort, jsonify
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage
from linebot import LineBotApi, WebhookHandler
import os
import openai
import logging
from datetime import datetime, timedelta
from dateutil import parser
from apscheduler.schedulers.background import BackgroundScheduler
import pytz
import requests
from dotenv import load_dotenv
import json  # 導入 json 模組

load_dotenv()

app = Flask(__name__)

# LINE Bot 設定
YOUR_CHANNEL_ACCESS_TOKEN = os.environ.get("YOUR_CHANNEL_ACCESS_TOKEN")
YOUR_CHANNEL_SECRET = os.environ.get("YOUR_CHANNEL_SECRET")
line_bot_api = LineBotApi(YOUR_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(YOUR_CHANNEL_SECRET)

# OpenAI API 設定
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
openai.api_key = OPENAI_API_KEY

# Trello API 相關設定 (請替換成您自己的金鑰、Token 和看板 ID)
TRELLO_API_KEY = os.environ.get("TRELLO_API_KEY")
TRELLO_API_TOKEN = os.environ.get("TRELLO_API_TOKEN")
TRELLO_BOARD_ID = os.environ.get("TRELLO_BOARD_ID")
TRELLO_LIST_ID = os.environ.get("TRELLO_LIST_ID") # 預設清單 ID


# 日誌設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 綁定關係儲存 (使用 JSON 檔案儲存)
BINDINGS_FILE = 'bindings.json' # 綁定關係儲存檔案名稱

def load_bindings():
    try:
        with open(BINDINGS_FILE, 'r') as f:
            bindings = json.load(f)
    except FileNotFoundError:
        bindings = {}
    return bindings

def save_bindings(bindings):
    with open(BINDINGS_FILE, 'w') as f:
        json.dump(bindings, f)

# ChatGPT 回覆函式
def get_chatgpt_response(prompt):
    try:
        logger.info(f"OpenAI 請求: {prompt}") # 記錄請求訊息
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "user", "content": prompt}
            ]
        )
        logger.info(f"OpenAI 回應: {response}") # 記錄完整回應
        if response.choices:
            text_response = response.choices[0].message.content.strip()
            logger.info(f"OpenAI 回覆訊息: {text_response}") # 記錄 ChatGPT 回覆訊息
            return text_response
        else:
            logger.warning("OpenAI 回應中沒有 choices") # 記錄警告訊息
            return "無法生成回應，請稍後再試。"
    except Exception as e:
        logger.error(f"OpenAI 請求失敗: {e}") # 記錄錯誤訊息
        return f"呼叫 OpenAI API 時發生錯誤: {e}"

# 發送 Line 訊息函式
def send_line_message(user_id, message):
    line_bot_api.push_message(user_id, TextSendMessage(text=message))
    logger.info(f"已發送 LINE 訊息給用戶 {user_id}: {message}") # 記錄發送訊息

# 建立 Trello 卡片函式
def create_trello_card(task_name, member_name=None, start_date=None, due_date=None, due_datetime=None):
    url = f"https://api.trello.com/1/cards"
    headers = {
       "Accept": "application/json"
    }
    query = {
       'key': TRELLO_API_KEY,
       'token': TRELLO_API_TOKEN,
       'idList': TRELLO_LIST_ID, # 使用預設清單 ID
       'name': task_name,
    }

    desc = ""
    if member_name:
        member_id = get_trello_member_id_by_name(member_name) # 根據成員名稱取得 Trello ID
        if member_id:
            query['idMembers'] = member_id # 指派成員
            desc += f"指派成員: {member_name}\n"
        else:
            desc += f"**[警告: 成員 {member_name} 不存在於 Trello 看板中，請檢查名稱是否正確]**\n"

    if start_date:
        query['start'] = start_date # 設定開始日期 (YYYY-MM-DD)
        desc += f"開始日期: {start_date}\n"

    if due_date:
        query['due'] = due_date # 設定截止日期 (YYYY-MM-DD)
        desc += f"截止日期: {due_date}\n"

    if desc:
        query['desc'] = desc # 將描述資訊加入 query 參數

    response = requests.post(url, headers=headers, params=query)

    if response.status_code == 200:
        card_details = response.json()
        card_url = card_details['shortUrl']
        logger.info(f"Trello 卡片建立成功: {task_name}，URL: {card_url}")
        return card_url
    else:
        logger.error(f"Trello 卡片建立失敗: {response.status_code} - {response.text}")
        return None

# 根據成員名稱取得 Trello 會員 ID
def get_trello_member_id_by_name(member_name):
    url = f"https://api.trello.com/1/boards/{TRELLO_BOARD_ID}/members"
    headers = {
       "Accept": "application/json"
    }
    query_params = {
       'key': TRELLO_API_KEY,
       'token': TRELLO_API_TOKEN
    }
    response = requests.get(url, headers=headers, params=query_params)
    if response.status_code == 200:
        members = response.json()
        for member in members:
            if member['fullName'] == member_name: # 比對全名 (Full Name)
                return member['id']
            elif member['username'] == member_name: # 比對使用者名稱 (Username)
                return member['id']
        return None # 找不到符合的成員名稱
    else:
        logger.error(f"Trello 取得看板成員列表失敗: {response.status_code} - {response.text}")
        return None

# 檢查 Trello 卡片到期日排程任務
def check_trello_cards():
    now_utc = datetime.now(pytz.utc) # 取得 UTC 現在時間
    url = f"https://api.trello.com/1/boards/{TRELLO_BOARD_ID}/cards"
    headers = {
       "Accept": "application/json"
    }
    query = {
       'key': TRELLO_API_KEY,
       'token': TRELLO_API_TOKEN,
       'fields': 'name,due,dueComplete', # 只需要卡片名稱、到期日和完成狀態欄位
       'idList': TRELLO_LIST_ID # 限制只抓取特定清單的卡片 (可選)
    }
    response = requests.get(url, headers=headers, params=query)
    if response.status_code == 200:
        cards = response.json()
        for card in cards:
            if card['due'] and not card['dueComplete']: # 檢查是否有到期日，且尚未完成
                due_date_utc = parser.parse(card['due']).astimezone(pytz.utc) # 將 Trello 提供的日期字串轉換成 UTC datetime 物件
                if due_date_utc <= now_utc: # 檢查是否已到期 (UTC 時間比較)
                    card_name = card['name']
                    logger.info(f"**[警告]** Trello 卡片 **{card_name}** 已到期！到期日: {due_date_utc.astimezone(pytz.timezone('Asia/Taipei')).strftime('%Y-%m-%d %H:%M %Z%z')}") # 記錄到期警告訊息 (轉換為台北時區顯示)
                    # 在這裡可以加入發送 Line 訊息通知的程式碼 (例如，通知看板負責人或卡片指派成員)
                    # ... (發送 Line 訊息程式碼) ...
        else:
            logger.error(f"Trello 取得卡片列表失敗: {response.status_code} - {response.text}")

# 啟動排程器
scheduler = BackgroundScheduler()
scheduler.add_job(check_trello_cards, 'interval', minutes=10) # 每 10 分鐘檢查一次
scheduler.start()
logger.info("排程任務已啟動")

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')  # 從 Header 中取得 signature
    body = request.get_data(as_text=True)  # 取得 request body (訊息內容)

    try:
        validate_signature(body, signature)  # 驗證簽名
    except ValueError:
        logger.warning("Line signature 驗證失敗")
        abort(400)  # 驗證失敗回傳 400 錯誤

    events = request.json.get('events', [])  # 解析 JSON body 中的 events 陣列，取得所有事件
    bindings = load_bindings()  # 載入綁定關係

    for event in events:  # 迭代處理每一個事件
        if event['type'] == 'message' and event['message']['type'] == 'text':  # 判斷事件類型是否為文字訊息
            text = event['message']['text']  # 取得訊息文字
            user_id = event['source']['userId']  # 取得用戶 ID

            if text.startswith("綁定 trello@"):  # 處理 "綁定 trello@" 指令
                trello_id_input = text.replace("綁定 trello@", "").strip()  # 移除指令前綴並去除空白
                if not trello_id_input:  # 檢查 Trello ID 是否為空
                    send_line_message(user_id, "Trello 帳號 ID 不得為空，請重新輸入正確格式：『綁定 trello@你的Trello會員ID』")
                    continue  # 跳過本次迴圈，繼續處理下一個事件
                trello_id = trello_id_input  # 取得 Trello ID
                bindings[user_id] = trello_id  # 將 Line 用戶 ID 和 Trello 會員 ID 進行綁定
                save_bindings(bindings)  # 儲存綁定關係
                send_line_message(user_id, f"綁定成功！您的 Trello 帳號 ID：{trello_id}")  # 回覆綁定成功訊息
                continue  # 跳過本次迴圈，繼續處理下一個事件

            trello_id = bindings.get(user_id)  # 根據用戶 ID 取得已綁定的 Trello 會員 ID
            if not trello_id:  # 如果用戶尚未綁定 Trello 帳號
                send_line_message(user_id, "請先綁定 Trello 帳號，輸入『綁定 trello@你的Trello會員ID』")  # 回覆請先綁定訊息
                continue  # 跳過本次迴圈，繼續處理下一個事件

            if "狀態" in text or "進度" in text:  # 判斷是否為查詢任務狀態或進度
                try:
                    task_status = get_user_trello_tasks(trello_id)  # 取得用戶的 Trello 任務狀態
                    reply_message = get_chatgpt_response(f"我的任務狀態是：{task_status}")  # 使用 ChatGPT 產生更口語化的回覆
                except Exception as e:  # 捕捉錯誤，避免程式中斷
                    logger.error(f"取得任務狀態或 ChatGPT 回覆失敗 (User ID: {user_id}, Trello ID: {trello_id}): {e}")
                    reply_message = "處理任務狀態查詢時發生錯誤，請稍後再試。"  # 回覆錯誤訊息
            else:  # 處理一般訊息 (建立 Trello 卡片)
                task_name = ""
                member_name = None
                start_date_str = None
                due_date_str = None
                start_datetime = None
                due_datetime = None

                # 嘗試從訊息中解析任務資訊 (使用逗號分隔)
                lines = text.split('，')
                for line in lines:
                    if line.startswith("新增任務："):
                        task_name = line[len("新增任務："):].strip()  # 提取任務名稱並去除前後空白
                    elif line.startswith("成員："):
                        member_name = line[len("成員："):].strip()  # 提取成員名稱並去除前後空白
                    elif line.startswith("開始日期："):
                        start_date_str = line[len("開始日期："):].strip()  # 提取開始日期字串並去除前後空白
                    elif line.startswith("截止日期：") or line.startswith("日期："):  # 同時處理 "截止日期" 和 "日期" 兩種關鍵字
                        due_date_str = line[len("截止日期："):].strip() if line.startswith("截止日期：") else line[len("日期："):].strip()  # 提取截止日期字串並去除前後空白

                if not task_name:  # 如果訊息中沒有 "新增任務：" 關鍵字，則視為一般訊息，直接使用訊息文字作為任務名稱
                    task_name = text  # 使用原始訊息文字作為卡片名稱
                    send_line_message(user_id, "提醒：請在訊息中包含 '新增任務：' 來建立任務，例如：新增任務：[任務名稱]，成員：[成員名稱]，日期：[週六前]")  # 發送提醒訊息，告知使用者正確的訊息格式
                    reply_message = get_chatgpt_response(text)  # 仍然使用 ChatGPT 回覆訊息 (但與日期無關)
                else:  # 如果訊息中有 "新增任務：" 關鍵字，則建立 Trello 卡片
                    reply_message = get_chatgpt_response(text)  # 使用 ChatGPT 針對任務相關訊息產生回覆
                    logger.info(f"解析到任務資訊 - 任務名稱: {task_name}, 成員: {member_name}, 開始日期: {start_date_str}, 截止日期: {due_date_str}")  # 記錄解析到的任務資訊

                # NLP 日期解析：使用 dateutil.parser 解析自然語言日期描述
                try:
                    if start_date_str:  # 如果有開始日期字串
                        start_datetime = parser.parse(start_date_str, fuzzy=True)  # 使用 fuzzy=True 允許模糊日期解析
                        start_date_str = start_datetime.strftime('%Y-%m-%d')  # 將解析後的 datetime 物件格式化為YYYY-MM-DD 字串
                        logger.info(f"NLP 解析開始日期成功: {start_date_str}")  # 記錄 NLP 解析成功訊息
                    if due_date_str:  # 如果有截止日期字串
                        # **字串預處理：移除 "前" 和 "之前"**
                        due_date_str_processed = due_date_str.replace("前", "").replace("之前", "")

                        # **星期幾轉換：將中文星期幾轉換成英文**
                        weekday_mapping = {
                            "下星期一": "next monday",
                            "週日": "sunday",
                            "星期日": "sunday",  # 增加星期日
                            "週一": "monday",
                            "星期一": "monday",  # 增加星期一
                            "週二": "tuesday",
                            "星期二": "tuesday",  # 增加星期二
                            "週三": "wednesday",
                            "星期三": "wednesday",  # 增加星期三
                            "週四": "thursday",
                            "星期四": "thursday",  # 增加星期四
                            "週五": "friday",
                            "星期五": "friday",  # 增加星期五
                            "週六": "saturday",
                            "星期六": "saturday",  # 增加星期六
                            "明天": "tomorrow",
                        }
                        for chinese_weekday, english_weekday in weekday_mapping.items():
                            due_date_str_processed = due_date_str_processed.replace(chinese_weekday, english_weekday)

                        # **時間詞彙初步處理：嘗試提取時間**
                        hour = 0  # 預設小時為 0
                        minute = 0 # 預設分鐘為 0
                        if "早上" in due_date_str_processed:
                            hour = 8  # 早上預設 8 點 (可調整)
                            due_date_str_processed = due_date_str_processed.replace("早上", "")
                        elif "中午" in due_date_str_processed:
                            hour = 12 # 中午預設 12 點
                            due_date_str_processed = due_date_str_processed.replace("中午", "")
                        elif "下午" in due_date_str_processed:
                            hour = 14 # 下午預設 2 點 (可調整)
                            due_date_str_processed = due_date_str_processed.replace("下午", "")
                        elif "晚上" in due_date_str_processed:
                            hour = 20 # 晚上預設 8 點 (可調整)
                            due_date_str_processed = due_date_str_processed.replace("晚上", "")

                        due_datetime = parser.parse(due_date_str_processed, fuzzy=True)  # 使用 fuzzy=True 允許模糊日期解析 (對處理後的字串)

                        if hour != 0: # 如果有提取到時間詞彙，則手動設定時間
                            due_datetime = due_datetime.replace(hour=hour, minute=minute, second=0, microsecond=0)


                        due_date_str = due_datetime.strftime('%Y-%m-%d')  # 將解析後的 datetime 物件格式化為YYYY-MM-DD 字串
                        logger.info(f"NLP 解析截止日期成功: {due_date_str}")  # 記錄 NLP 解析成功訊息
                except ValueError as e:  # 捕捉日期解析錯誤
                    logger.warning(f"NLP 日期解析失敗: {e}")  # 記錄 NLP 解析失敗訊息
                    send_line_message(user_id, f"提醒：日期解析失敗，請嘗試更明確的日期描述，例如：YYYY-MM-DD 或 '下星期一'。")  # 回覆日期解析失敗提醒訊息

                create_trello_card(task_name, member_name, start_date_str, due_date_str, due_datetime)  # 呼叫函式建立 Trello 卡片，並傳遞解析出的任務資訊 (包含日期時間物件)

            send_line_message(user_id, reply_message)  # 發送 Line 回覆訊息

    return 'OK'  # 回應 Line Server HTTP 狀態碼 200，表示已成功接收訊息

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    user_id = event.source.user_id
    message_text = event.message.text

    reply = get_chatgpt_response(message_text) # 取得 ChatGPT 回覆

    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=reply) # 使用 ChatGPT 回覆
    )
    logger.info(f"已發送 ChatGPT 回覆訊息給用戶 {user_id}: {reply}") # 記錄 ChatGPT 回覆訊息


@app.route("/check_cards", methods=['POST']) # 定義檢查卡片到期日的 Webhook 路徑
def webhook_check_cards():
    if request.headers.get('X-Render-Schedule') != 'true': # 驗證 Render 排程器 Header
        return jsonify({'message': 'Not a Render Scheduler'}), 403 # 如果不是 Render 排程器觸發，則回傳 403 錯誤
    check_trello_cards() # 執行檢查 Trello 卡片到期日函式
    return jsonify({'message': 'Trello card check completed'}), 200 # 回傳成功訊息


@app.route("/", methods=['GET'])
def home():
    return "<h1>Line Bot is running</h1>"

if __name__ == "__main__":
    app.run(debug=False, port=10000)