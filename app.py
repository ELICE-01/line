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

app = Flask(__name__)

# 環境變數檢查
required_env_vars = [
    'LINE_CHANNEL_ACCESS_TOKEN',
    'LINE_CHANNEL_SECRET',
    'TRELLO_API_KEY',
    'TRELLO_TOKEN',
    'TRELLO_BOARD_ID',
    'TRELLO_LIST_ID',
    'OPENAI_API_KEY'
]

for var in required_env_vars:
    if not os.getenv(var):
        raise ValueError(f"環境變數 {var} 未設定")

# 環境變數讀取
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
TRELLO_API_KEY = os.getenv('TRELLO_API_KEY')
TRELLO_TOKEN = os.getenv('TRELLO_TOKEN')
TRELLO_BOARD_ID = os.getenv('TRELLO_BOARD_ID')
TRELLO_LIST_ID = os.getenv('TRELLO_LIST_ID')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# 設定 OpenAI API Key
openai.api_key = OPENAI_API_KEY

# 綁定檔案名稱
BINDING_FILE = "line_trello_map.json"

# 日誌設定
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def load_bindings():
    try:
        if os.path.exists(BINDING_FILE):
            with open(BINDING_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}
    except FileNotFoundError:
        logger.warning(f"綁定檔案 {BINDING_FILE} 未找到，將返回空綁定。")
        return {}
    except json.JSONDecodeError:
        logger.error(f"綁定檔案 {BINDING_FILE} JSON格式錯誤，將返回空綁定。")
        return {}
    except Exception as e:
        logger.error(f"讀取綁定檔案 {BINDING_FILE} 失敗: {e}")
        return {}

def save_bindings(bindings):
    try:
        with open(BINDING_FILE, 'w', encoding='utf-8') as f:
            json.dump(bindings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"儲存綁定檔案 {BINDING_FILE} 失敗: {e}")

def validate_signature(body, signature):
    hash = hmac.new(LINE_CHANNEL_SECRET.encode('utf-8'), body.encode('utf-8'), hashlib.sha256).digest()
    expected_signature = base64.b64encode(hash).decode('utf-8')
    if expected_signature != signature:
        raise ValueError('Invalid signature.')

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)

    try:
        validate_signature(body, signature)
    except ValueError:
        logger.warning("Line signature驗證失敗")
        abort(400)

    events = request.json.get('events', []) # 使用 .get() 避免 KeyError, 並設定預設值為空列表
    bindings = load_bindings()

    for event in events:
        if event['type'] == 'message' and event['message']['type'] == 'text':
            text = event['message']['text']
            user_id = event['source']['userId']

            if text.startswith("綁定 trello@"):
                trello_id_input = text.replace("綁定 trello@", "").strip()
                if not trello_id_input: # 檢查 Trello ID 是否為空
                    send_line_message(user_id, "Trello 帳號ID不得為空，請重新輸入正確格式：『綁定 trello@你的Trello會員ID』")
                    continue
                trello_id = trello_id_input
                bindings[user_id] = trello_id
                save_bindings(bindings)
                send_line_message(user_id, f"綁定成功！你的Trello帳號ID：{trello_id}")
                continue

            trello_id = bindings.get(user_id)
            if not trello_id:
                send_line_message(user_id, "請先綁定Trello帳號，輸入『綁定 trello@你的Trello會員ID』")
                continue

            if "狀態" in text or "進度" in text:
                try:
                    task_status = get_user_trello_tasks(trello_id)
                    reply_message = get_chatgpt_response(f"我的任務狀態是：{task_status}")
                except Exception as e:
                    logger.error(f"取得任務狀態或ChatGPT回覆失敗 (User ID: {user_id}, Trello ID: {trello_id}): {e}")
                    reply_message = "處理任務狀態查詢時發生錯誤，請稍後再試。"
            else:
                try:
                    reply_message = get_chatgpt_response(text)
                except Exception as e:
                    logger.error(f"取得 ChatGPT 回覆失敗 (User ID: {user_id}): {e}")
                    reply_message = "AI回覆生成失敗，請稍後再試。"
                create_trello_card(text) # 即使 ChatGPT 失敗，仍嘗試建立 Trello 卡片 (根據原始碼邏輯)

            send_line_message(user_id, reply_message)

    return 'OK'

@app.route('/trello-webhook', methods=['POST'])
def trello_webhook():
    data = request.json
    logger.info(f"收到 Trello Webhook 請求：{data}")

    # 根據資料處理 Trello 卡片變更
    action_type = data.get('action', {}).get('type')
    if action_type == 'updateCard':
        card_id = data.get('action', {}).get('data', {}).get('card', {}).get('id')
        card_name = data.get('action', {}).get('data', {}).get('card', {}).get('name')
        logger.info(f"卡片更新：{card_name} (ID: {card_id})")

    return 'OK'

def create_trello_card(card_name):
    try:
        url = "https://api.trello.com/1/cards"
        query = {
            'key': TRELLO_API_KEY,
            'token': TRELLO_TOKEN,
            'idList': TRELLO_LIST_ID,
            'name': card_name
        }
        response = requests.post(url, params=query)
        response.raise_for_status()
        logger.info(f"已創建 Trello 卡片：{card_name}")
    except requests.exceptions.RequestException as e:
        logger.error(f"創建 Trello 卡片失敗：{e}")

def get_list_map():
    try:
        url = f"https://api.trello.com/1/boards/{TRELLO_BOARD_ID}/lists"
        query = {'key': TRELLO_API_KEY, 'token': TRELLO_TOKEN}
        response = requests.get(url, params=query)
        response.raise_for_status()
        lists = response.json()
        return {lst['id']: lst['name'] for lst in lists}
    except requests.exceptions.RequestException as e:
        logger.error(f"獲取 Trello 列表失敗：{e}")
        return {}

def get_user_trello_tasks(trello_member_id):
    try:
        url = f"https://api.trello.com/1/boards/{TRELLO_BOARD_ID}/cards"
        query = {'key': TRELLO_API_KEY, 'token': TRELLO_TOKEN}
        response = requests.get(url, params=query)
        response.raise_for_status()
        cards = response.json()

        list_map = get_list_map()
        user_cards = [card for card in cards if trello_member_id in card.get('idMembers', [])]

        if not user_cards:
            return "你目前沒有任何任務。"

        status_message = "你的任務狀態如下：\n"
        for card in user_cards:
            due = card.get('due', '無截止日期')
            try:
                if due:
                    due = datetime.datetime.fromisoformat(due).strftime('%Y-%m-%d %H:%M')
            except ValueError:
                logger.warning(f"卡片 {card['name']} 的截止日期格式無效: {card.get('due')}")
                due = '無效日期' # 修正: 即使日期無效，也設定為 '無效日期' 而非 return

            status_message += f"- {card['name']}\n 狀態：{list_map.get(card['idList'], '未知')}\n 截止：{due}\n\n"
        return status_message
    except requests.exceptions.RequestException as e:
        logger.error(f"獲取 Trello 任務失敗：{e}")
        return "無法獲取任務狀態，請稍後再試。"

def get_chatgpt_response(prompt):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "你是貼心的助理。"},
                {"role": "user", "content": prompt}
            ]
        )
        return response['choices'][0]['message']['content']
    except Exception as e:
        logger.error(f"OpenAI 請求失敗：{e}")
        return "無法生成回應，請稍後再試。"

def send_line_message(user_id, message):
    try:
        url = "https://api.line.me/v2/bot/message/push"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
        }
        data = {
            "to": user_id,
            "messages": [{"type": "text", "text": message}]
        }
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        logger.info(f"已發送 LINE 訊息給用戶 {user_id}")
    except requests.exceptions.RequestException as e:
        logger.error(f"發送 LINE 訊息失敗給用戶 {user_id}: {e}")

def check_trello_cards():
    logger.info("開始檢查 Trello 卡片...")
    bindings = load_bindings()
    try:
        url = f"https://api.trello.com/1/boards/{TRELLO_BOARD_ID}/cards"
        query = {'key': TRELLO_API_KEY, 'token': TRELLO_TOKEN}
        response = requests.get(url, params=query)
        response.raise_for_status()
        cards = response.json()

        now = datetime.datetime.now()

        for card in cards:
            due_date = card.get('due')
            if not due_date:
                logger.info(f"卡片 {card['name']} 沒有截止日期")
                continue

            try:
                due_date = datetime.datetime.fromisoformat(due_date)
            except ValueError:
                logger.error(f"卡片 {card['name']} 的截止日期格式無效: {card.get('due')}")
                continue

            if (due_date - now).days == 1:
                logger.info(f"卡片 {card['name']} 即將截止")
                for trello_member_id in card.get('idMembers', []):
                    user_id = next((uid for uid, tid in bindings.items() if tid == trello_member_id), None)
                    if user_id:
                        logger.info(f"發送提醒給用戶 {user_id} 關於卡片 {card['name']}")
                        send_line_message(user_id, f"提醒：任務『{card['name']}』明天截止，請注意。")
    except requests.exceptions.RequestException as e:
        logger.error(f"檢查 Trello 卡片失敗：{e}")

scheduler = BackgroundScheduler()
scheduler.add_job(check_trello_cards, 'interval', minutes=30)
scheduler.start()
logger.info("排程任務已啟動")

if __name__ == "__main__":
    from waitress import serve
    serve(app, host="0.0.0.0", port=5000)