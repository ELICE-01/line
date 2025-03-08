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
from dateutil import parser  # 導入 dateutil parser，用於自然語言日期解析

app = Flask(__name__)

# 環境變數檢查：確保必要的環境變數都已設定，否則程式無法正常運作
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
        raise ValueError(f"環境變數 {var} 未設定：{var}")

# 環境變數讀取：從環境變數中讀取設定值，方便在程式碼中使用
LINE_CHANNEL_ACCESS_TOKEN = os.getenv('LINE_CHANNEL_ACCESS_TOKEN')
LINE_CHANNEL_SECRET = os.getenv('LINE_CHANNEL_SECRET')
TRELLO_API_KEY = os.getenv('TRELLO_API_KEY')
TRELLO_TOKEN = os.getenv('TRELLO_TOKEN')
TRELLO_BOARD_ID = os.getenv('TRELLO_BOARD_ID')
TRELLO_LIST_ID = os.getenv('TRELLO_LIST_ID')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# 設定 OpenAI API Key：將 OpenAI API 金鑰設定到 openai 模組中
openai.api_key = OPENAI_API_KEY

# 綁定檔案名稱：設定儲存 Line 用戶 ID 和 Trello 會員 ID 綁定關係的檔案名稱
BINDING_FILE = "line_trello_map.json"

# 日誌設定：設定日誌記錄，方便追蹤程式執行狀況和錯誤
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 載入綁定關係：從 JSON 檔案載入 Line 用戶 ID 和 Trello 會員 ID 的綁定關係
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
        logger.error(f"綁定檔案 {BINDING_FILE} JSON 格式錯誤，將返回空綁定。")
        return {}
    except Exception as e:
        logger.error(f"讀取綁定檔案 {BINDING_FILE} 失敗: {e}")
        return {}

# 儲存綁定關係：將 Line 用戶 ID 和 Trello 會員 ID 的綁定關係儲存到 JSON 檔案
def save_bindings(bindings):
    try:
        with open(BINDING_FILE, 'w', encoding='utf-8') as f:
            json.dump(bindings, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"儲存綁定檔案 {BINDING_FILE} 失敗: {e}")

# 驗證 Line Signature：驗證 Line Webhook 請求的簽名，確保請求來自 Line 官方
def validate_signature(body, signature):
    hash_value = hmac.new(LINE_CHANNEL_SECRET.encode('utf-8'), body.encode('utf-8'), hashlib.sha256).digest()
    expected_signature = base64.b64encode(hash_value).decode('utf-8')
    if expected_signature != signature:
        raise ValueError('Invalid signature.')

# Line Webhook Callback 路由：接收 Line Server 發送的訊息事件
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
                        # **在這裡加入字串預處理：移除 "前" 和 "之前"**
                        due_date_str_processed = due_date_str.replace("前", "").replace("之前", "")
                        due_datetime = parser.parse(due_date_str_processed, fuzzy=True)  # 使用 fuzzy=True 允許模糊日期解析 (對處理後的字串)
                        due_date_str = due_datetime.strftime('%Y-%m-%d')  # 將解析後的 datetime 物件格式化為YYYY-MM-DD 字串
                        logger.info(f"NLP 解析截止日期成功: {due_date_str}")  # 記錄 NLP 解析成功訊息
                except ValueError as e:  # 捕捉日期解析錯誤
                    logger.warning(f"NLP 日期解析失敗: {e}")  # 記錄 NLP 解析失敗訊息
                    send_line_message(user_id, f"提醒：日期解析失敗，請嘗試更明確的日期描述，例如：YYYY-MM-DD 或 '下星期一'。")  # 回覆日期解析失敗提醒訊息

                create_trello_card(task_name, member_name, start_date_str, due_date_str, due_datetime)  # 呼叫函式建立 Trello 卡片，並傳遞解析出的任務資訊 (包含日期時間物件)

            send_line_message(user_id, reply_message)  # 發送 Line 回覆訊息

    return 'OK'  # 回應 Line Server HTTP 狀態碼 200，表示已成功接收訊息

# Trello Webhook 路由：接收 Trello 看板的 Webhook 事件 (目前僅記錄，您可根據需求擴充功能)
@app.route('/trello-webhook', methods=['POST'])
def trello_webhook():
    data = request.json  # 解析 JSON request body
    logger.info(f"收到 Trello Webhook 請求：{data}")  # 記錄 Webhook 請求內容

    # 在這裡您可以根據 Trello Webhook 事件類型 (action_type) 進行不同的處理
    # 例如：卡片更新、列表變更、成員異動等
    action_type = data.get('action', {}).get('type')  # 取得 action type
    if action_type == 'updateCard':  # 如果是卡片更新事件
        card_id = data.get('action', {}).get('data', {}).get('card', {}).get('id')  # 取得卡片 ID
        card_name = data.get('action', {}).get('data', {}).get('card', {}).get('name')  # 取得卡片名稱
        logger.info(f"Trello 卡片更新：{card_name} (ID: {card_id})")  # 記錄卡片更新事件

    return 'OK'  # 回應 Trello Server HTTP 狀態碼 200，表示已成功接收 Webhook

# 建立 Trello 卡片函式：呼叫 Trello API 建立卡片，並設定卡片屬性 (名稱、成員、截止日期和提醒)
def create_trello_card(card_name, member_name=None, start_date_str=None, due_date_str=None, due_datetime=None):  # 接收更多參數，包含成員名稱、日期字串和日期時間物件
    try:
        url = "https://api.trello.com/1/cards"  # Trello API 建立卡片 endpoint
        query = {  # Trello API 請求參數
            'key': TRELLO_API_KEY,
            'token': TRELLO_TOKEN,
            'idList': TRELLO_LIST_ID,  # 使用 TRELLO_LIST_ID 環境變數設定預設列表
            'name': card_name,  # 卡片名稱
        }

        # 處理成員分配：如果訊息中包含成員名稱，則嘗試將卡片分配給該成員
        if member_name:
            member_id = get_trello_member_id_by_name(member_name)  # 根據成員名稱查詢 Trello 會員 ID (需實作)
            if member_id:  # 如果找到對應的 Trello 會員 ID
                query['idMembers'] = member_id  # 將成員 ID 加入 query 參數，設定卡片成員
                logger.info(f"將卡片 '{card_name}' 分配給成員：{member_name} (ID: {member_id})")  # 記錄成員分配訊息
            else:  # 如果找不到對應的 Trello 會員 ID
                logger.warning(f"找不到名為 '{member_name}' 的 Trello 成員。")  # 記錄找不到成員的警告訊息

        # 處理截止日期：如果訊息中包含截止日期，則設定卡片的截止日期和到期前一天提醒
        if due_datetime:  # 判斷 due_datetime 是否為有效值 (NLP 日期解析是否成功)
            try:
                # due_date = datetime.datetime.fromisoformat(due_date_str)  #  不再需要從字串解析日期，直接使用 NLP 解析後的 datetime 物件
                query['due'] = due_datetime.isoformat()  # 設定卡片截止日期，使用 ISO 8601 格式
                query['dueReminder'] = '1440'  # 設定卡片到期前一天 (1440 分鐘 = 24 小時) 提醒
                logger.info(f"為卡片 '{card_name}' 設定截止日期：{due_datetime.strftime('%Y-%m-%d %H:%M')}")  # 記錄設定截止日期訊息，並格式化日期時間方便閱讀
            except ValueError as e:  # 捕捉日期轉換錯誤 (雖然理論上 NLP 解析已處理，但為了程式碼的完整性，保留 try...except)
                logger.error(f"設定截止日期失敗 (datetime 轉換錯誤): {e}")  # 記錄設定截止日期失敗訊息

        response = requests.post(url, params=query)  # 發送 POST 請求到 Trello API 建立卡片
        response.raise_for_status()  # 檢查 HTTP 狀態碼，如果失敗 (4xx 或 5xx) 則拋出例外
        logger.info(f"已創建 Trello 卡片：{card_name}")  # 記錄卡片建立成功訊息
    except requests.exceptions.RequestException as e:  # 捕捉 requests 模組的例外，例如連線錯誤、HTTP 錯誤等
        logger.error(f"創建 Trello 卡片失敗：{e}")  # 記錄卡片建立失敗訊息

# 取得 Trello 列表名稱對應 ID 的 Map：方便後續查詢列表名稱
def get_list_map():
    try:
        url = f"https://api.trello.com/1/boards/{TRELLO_BOARD_ID}/lists"  # Trello API 取得看板列表 endpoint
        query = {'key': TRELLO_API_KEY, 'token': TRELLO_TOKEN}  # Trello API 請求參數
        response = requests.get(url, params=query)  # 發送 GET 請求到 Trello API 取得列表
        response.raise_for_status()  # 檢查 HTTP 狀態碼
        lists = response.json()  # 解析 JSON response
        return {lst['id']: lst['name'] for lst in lists}  # 返回列表 ID 對應列表名稱的字典
    except requests.exceptions.RequestException as e:  # 捕捉 requests 模組的例外
        logger.error(f"獲取 Trello 列表失敗：{e}")  # 記錄獲取列表失敗訊息
        return {}  # 發生錯誤時返回空字典

# 取得用戶 Trello 任務狀態：根據 Trello 會員 ID，查詢該用戶在看板上的任務狀態
def get_user_trello_tasks(trello_member_id):
    try:
        url = f"https://api.trello.com/1/boards/{TRELLO_BOARD_ID}/cards"  # Trello API 取得看板卡片 endpoint
        query = {'key': TRELLO_API_KEY, 'token': TRELLO_TOKEN}  # Trello API 請求參數
        response = requests.get(url, params=query)  # 發送 GET 請求到 Trello API 取得卡片
        response.raise_for_status()  # 檢查 HTTP 狀態碼
        cards = response.json()  # 解析 JSON response

        list_map = get_list_map()  # 取得列表名稱對應 ID 的 Map，方便後續查詢列表名稱
        user_cards = [card for card in cards if trello_member_id in card.get('idMembers', [])]  # 篩選出指派給特定 Trello 會員 ID 的卡片

        if not user_cards:  # 如果沒有找到任何指派給該用戶的卡片
            return "您目前沒有任何任務。"  # 回覆沒有任務訊息

        status_message = "您的任務狀態如下：\n"  # 初始化任務狀態訊息
        for card in user_cards:  # 迭代處理每一張卡片
            due = card.get('due', '無截止日期')  # 取得卡片截止日期，如果沒有則顯示 "無截止日期"
            try:
                if due:  # 如果有截止日期
                    due = datetime.datetime.fromisoformat(due).strftime('%Y-%m-%d %H:%M')  # 將 ISO 8601 格式的日期字串轉換為YYYY-MM-DD HH:MM 格式
            except ValueError:  # 捕捉日期格式錯誤
                logger.warning(f"卡片 {card['name']} 的截止日期格式無效: {card.get('due')}")  # 記錄日期格式無效警告
                due = '無效日期'  # 如果日期格式無效，則顯示 "無效日期"

            status_message += f"- {card['name']}\n 狀態：{list_map.get(card['idList'], '未知')}\n 截止：{due}\n\n"  # 將卡片名稱、狀態和截止日期加入任務狀態訊息
        return status_message  # 返回任務狀態訊息
    except requests.exceptions.RequestException as e:  # 捕捉 requests 模組的例外
        logger.error(f"獲取 Trello 任務失敗：{e}")  # 記錄獲取任務失敗訊息
        return "無法獲取任務狀態，請稍後再試。"  # 回覆無法獲取任務狀態訊息

# 取得 ChatGPT 回覆：呼叫 OpenAI ChatGPT API 取得自然語言回覆
def get_chatgpt_response(prompt):
    try:
        response = openai.ChatCompletion.create(  # 呼叫 OpenAI ChatGPT API
            model="gpt-3.5-turbo",  # 使用的模型
            messages=[  # 訊息內容
                {"role": "system", "content": "你是貼心的助理。"},  # 設定 ChatGPT 角色為貼心的助理
                {"role": "user", "content": prompt}  # 使用者輸入的訊息
            ]
        )
        return response['choices'][0]['message']['content']  # 返回 ChatGPT 的回覆訊息
    except Exception as e:  # 捕捉 OpenAI API 請求錯誤
        logger.error(f"OpenAI 請求失敗：{e}")  # 記錄 OpenAI API 請求失敗訊息
        return "無法生成回應，請稍後再試。"  # 回覆無法生成回應訊息

# 發送 Line 訊息函式：封裝 Line Bot API 發送訊息功能
def send_line_message(user_id, message):
    try:
        url = "https://api.line.me/v2/bot/message/push"  # Line Bot API push message endpoint
        headers = {  # HTTP Header 設定
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"  # 使用 LINE_CHANNEL_ACCESS_TOKEN 環境變數設定 Authorization
        }
        data = {  # request body 內容
            "to": user_id,  # 接收訊息的 Line 用戶 ID
            "messages": [{"type": "text", "text": message}]  # 訊息內容，這裡設定為 text message
        }
        response = requests.post(url, headers=headers, json=data)  # 發送 POST 請求到 Line Bot API
        response.raise_for_status()  # 檢查 HTTP 狀態碼
        logger.info(f"已發送 LINE 訊息給用戶 {user_id}")  # 記錄訊息發送成功訊息
    except requests.exceptions.RequestException as e:  # 捕捉 requests 模組的例外
        logger.error(f"發送 LINE 訊息失敗給用戶 {user_id}: {e}")  # 記錄訊息發送失敗訊息

# 定期檢查 Trello 卡片截止日期的排程任務函式：每天檢查一次 Trello 看板上的卡片，並在卡片即將到期時發送 Line 提醒訊息
def check_trello_cards():
    logger.info("開始檢查 Trello 卡片截止日期...")  # 記錄排程任務開始訊息
    bindings = load_bindings()  # 載入綁定關係
    try:
        url = f"https://api.trello.com/1/boards/{TRELLO_BOARD_ID}/cards"  # Trello API 取得看板卡片 endpoint
        query = {'key': TRELLO_API_KEY, 'token': TRELLO_TOKEN}  # Trello API 請求參數
        response = requests.get(url, params=query)  # 發送 GET 請求到 Trello API 取得卡片
        response.raise_for_status()  # 檢查 HTTP 狀態碼
        cards = response.json()  # 解析 JSON response

        now = datetime.datetime.now()  # 取得目前時間 (本地時間)

        for card in cards:  # 迭代處理每一張卡片
            due_date = card.get('due')  # 取得卡片的截止日期 (ISO 8601 格式字串)
            if not due_date:  # 如果卡片沒有設定截止日期
                logger.info(f"卡片 {card['name']} 沒有截止日期，跳過檢查。")  # 記錄跳過檢查訊息
                continue  # 跳過本次迴圈，繼續檢查下一張卡片

            try:
                due_date = datetime.datetime.fromisoformat(due_date)  # 將 ISO 8601 格式的日期字串轉換為 datetime 物件
            except ValueError:  # 捕捉日期格式錯誤
                logger.error(f"卡片 {card['name']} 的截止日期格式無效: {card.get('due')}")  # 記錄日期格式無效錯誤
                continue  # 跳過本次迴圈，繼續檢查下一張卡片

            if (due_date - now).days == 1:  # 判斷卡片是否即將在一天後到期
                logger.info(f"卡片 {card['name']} 即將截止，剩餘時間：一天。")  # 記錄卡片即將截止訊息
                for trello_member_id in card.get('idMembers', []):  # 迭代處理卡片上的每一個成員
                    user_id = next((uid for uid, tid in bindings.items() if tid == trello_member_id), None)  # 根據 Trello 會員 ID 查找綁定的 Line 用戶 ID
                    if user_id:  # 如果找到綁定的 Line 用戶 ID
                        logger.info(f"發送提醒訊息給用戶 {user_id} 關於卡片 {card['name']}")  # 記錄發送提醒訊息
                        send_line_message(user_id, f"提醒：任務『{card['name']}』明天截止，請注意。")  # 發送 Line 提醒訊息
    except requests.exceptions.RequestException as e:  # 捕捉 requests 模組的例外
        logger.error(f"檢查 Trello 卡片截止日期失敗：{e}")  # 記錄排程任務失敗訊息

# 根據成員名稱取得 Trello 會員 ID 函式：呼叫 Trello API 根據成員名稱查找 Trello 會員 ID
def get_trello_member_id_by_name(member_name):
    """
    根據成員名稱 (member_name) 查找 Trello 成員 ID
    **請務必參考 Trello API 文件，實作呼叫 Trello API 搜尋成員的邏輯**
    **並根據您的 Trello Board 或 Organization 的實際情況修改程式碼**

    **以下僅為範例程式碼，可能需要大幅修改才能在您的環境中使用**
    """
    try:
        url = f"https://api.trello.com/1/boards/{TRELLO_BOARD_ID}/members"  # Trello API 取得看板成員 endpoint
        query = {'key': TRELLO_API_KEY, 'token': TRELLO_TOKEN, 'filter': 'all'}  # Trello API 請求參數，filter=all 取得所有成員
        response = requests.get(url, params=query)  # 發送 GET 請求到 Trello API 取得看板成員
        response.raise_for_status()  # 檢查 HTTP 狀態碼
        members = response.json()  # 解析 JSON response
        for member in members:  # 迭代處理每一個成員
            if member.get('fullName') == member_name or member.get('username') == member_name:  # 比對成員全名或使用者名稱 (擇一比對)
                logger.info(f"找到成員：{member['fullName']} (ID: {member['id']})")  # 記錄找到成員訊息
                return member['id']  # 找到成員，返回成員 ID
        logger.warning(f"找不到名為 '{member_name}' 的 Trello 成員。")  # 記錄找不到成員警告訊息
        return None  # 找不到成員，返回 None
    except requests.exceptions.RequestException as e:  # 捕捉 requests 模組的例外
        logger.error(f"搜尋 Trello 成員失敗：{e}")  # 記錄搜尋成員失敗訊息
        return None  # 發生錯誤，返回 None

# 初始化排程器：設定排程任務，定期檢查 Trello 卡片截止日期
scheduler = BackgroundScheduler()  # 建立背景排程器
scheduler.add_job(check_trello_cards, 'interval', minutes=30)  # 每 30 分鐘執行一次 check_trello_cards 函式
scheduler.start()  # 啟動排程器
logger.info("排程任務已啟動")  # 記錄排程任務啟動訊息

# Flask 應用程式啟動入口點
if __name__ == "__main__":
    from waitress import serve  # 導入 waitress，用於生產環境部署
    serve(app, host="0.0.0.0", port=5000)  # 使用 waitress 啟動 Flask 應用程式，監聽 5000 端口，host="0.0.0.0" 允許外部連線