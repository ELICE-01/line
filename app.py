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
from dotenv import load_dotenv # 導入 load_dotenv，用於從 .env 檔案載入環境變數

load_dotenv() # 載入 .env 檔案中的環境變數 (如果有的話)

app = Flask(__name__)

# 日誌設定：設定日誌記錄，方便追蹤程式執行狀況和錯誤
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s') # 加入時間戳記和層級資訊
logger = logging.getLogger(__name__)

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
        logger.error(f"環境變數 {var} 未設定，程式無法啟動！") # 使用 logger.error 記錄更明確的錯誤訊息
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

# 載入綁定關係：從 JSON 檔案載入 Line 用戶 ID 和 Trello 會員 ID 的綁定關係
def load_bindings():
    try:
        if os.path.exists(BINDING_FILE):
            with open(BINDING_FILE, 'r', encoding='utf-8') as f:
                bindings = json.load(f)
                logger.info(f"成功載入綁定檔案 {BINDING_FILE}，目前綁定數量：{len(bindings)}") # 記錄成功載入訊息，包含綁定數量
                return bindings
        logger.info(f"綁定檔案 {BINDING_FILE} 不存在，將返回空綁定。") # 使用 logger.info 記錄檔案不存在訊息
        return {}
    except FileNotFoundError:
        logger.warning(f"綁定檔案 {BINDING_FILE} 未找到，將返回空綁定。") # 使用 logger.warning 記錄檔案未找到訊息
        return {}
    except json.JSONDecodeError:
        logger.error(f"綁定檔案 {BINDING_FILE} JSON 格式錯誤，將返回空綁定。請檢查檔案內容！") # 使用 logger.error 並提示檢查檔案內容
        return {}
    except Exception as e:
        logger.error(f"讀取綁定檔案 {BINDING_FILE} 失敗: {e}，將返回空綁定。") # 使用 logger.error 記錄讀取失敗訊息，包含例外資訊
        return {}

# 儲存綁定關係：將 Line 用戶 ID 和 Trello 會員 ID 的綁定關係儲存到 JSON 檔案
def save_bindings(bindings):
    try:
        with open(BINDING_FILE, 'w', encoding='utf-8') as f:
            json.dump(bindings, f, ensure_ascii=False, indent=2)
        logger.info(f"成功儲存綁定檔案 {BINDING_FILE}，目前綁定數量：{len(bindings)}") # 記錄成功儲存訊息，包含綁定數量
    except Exception as e:
        logger.error(f"儲存綁定檔案 {BINDING_FILE} 失敗: {e}") # 使用 logger.error 記錄儲存失敗訊息，包含例外資訊

# 驗證 Line Signature：驗證 Line Webhook 請求的簽名，確保請求來自 Line 官方
def validate_signature(body, signature):
    hash_value = hmac.new(LINE_CHANNEL_SECRET.encode('utf-8'), body.encode('utf-8'), hashlib.sha256).digest()
    expected_signature = base64.b64encode(hash_value).decode('utf-8')
    if expected_signature != signature:
        logger.warning("Line signature 驗證失敗，請求可能不是來自 Line Server！") # 使用 logger.warning 記錄簽名驗證失敗
        raise ValueError('Invalid signature.')

# 處理綁定 Trello 帳號指令
def handle_binding_command(user_id, text, bindings):
    if text.startswith("綁定 trello@"):  # 處理 "綁定 trello@" 指令
        trello_id_input = text.replace("綁定 trello@", "").strip()  # 移除指令前綴並去除空白
        if not trello_id_input:  # 檢查 Trello ID 是否為空
            send_line_message(user_id, "Trello 帳號 ID 不得為空，請重新輸入正確格式：『綁定 trello@你的Trello會員ID』")
            return True # 已處理指令，返回 True
        trello_id = trello_id_input  # 取得 Trello ID
        bindings[user_id] = trello_id  # 將 Line 用戶 ID 和 Trello 會員 ID 進行綁定
        save_bindings(bindings)  # 儲存綁定關係
        send_line_message(user_id, f"綁定成功！您的 Trello 帳號 ID：{trello_id}")  # 回覆綁定成功訊息
        logger.info(f"用戶 {user_id} 成功綁定 Trello 帳號 ID: {trello_id}") # 記錄綁定成功訊息
        return True # 已處理指令，返回 True
    return False # 非綁定指令，返回 False

# 處理查詢任務狀態指令 (修改後，接收 text 參數)
def handle_status_query(user_id, trello_id, text):
    if "狀態" in text or "進度" in text:
        try:
            task_status = get_user_trello_tasks(trello_id)
            reply_message = get_chatgpt_response(f"我的任務狀態是：{task_status}")
            logger.info(f"用戶 {user_id} 查詢任務狀態，Trello ID: {trello_id}")
        except Exception as e:
            logger.error(f"取得任務狀態或 ChatGPT 回覆失敗 (User ID: {user_id}, Trello ID: {trello_id}): {e}")
            reply_message = "處理任務狀態查詢時發生錯誤，請稍後再試。"
        send_line_message(user_id, reply_message)
        return True
    return False

# 處理建立 Trello 卡片指令
def handle_create_task_command(user_id, text, bindings):
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
        logger.info(f"用戶 {user_id} 發送一般訊息，將作為卡片名稱處理: {task_name}") # 記錄一般訊息處理
    else:  # 如果訊息中有 "新增任務：" 關鍵字，則建立 Trello 卡片
        reply_message = get_chatgpt_response(text)  # 使用 ChatGPT 針對任務相關訊息產生回覆
        logger.info(f"用戶 {user_id} 嘗試建立 Trello 卡片，任務名稱: {task_name}, 成員: {member_name}, 開始日期: {start_date_str}, 截止日期: {due_date_str}")  # 記錄建立卡片嘗試訊息

        # NLP 日期解析：使用 dateutil.parser 解析自然語言日期描述
        try:
            if start_date_str:  # 如果有開始日期字串
                start_datetime = parser.parse(start_date_str, fuzzy=True)  # 使用 fuzzy=True 允許模糊日期解析
                start_date_str = start_datetime.strftime('%Y-%m-%d')  # 將解析後的 datetime 物件格式化為YYYY-MM-DD 字串
                logger.info(f"用戶 {user_id} NLP 解析開始日期成功: {start_date_str}")  # 記錄 NLP 解析成功訊息
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
                logger.info(f"用戶 {user_id} NLP 解析截止日期成功: {due_date_str}")  # 記錄 NLP 解析成功訊息
        except ValueError as e:  # 捕捉日期解析錯誤
            logger.warning(f"用戶 {user_id} NLP 日期解析失敗: {e}")  # 記錄 NLP 解析失敗訊息
            send_line_message(user_id, f"提醒：日期解析失敗，請嘗試更明確的日期描述，例如：YYYY-MM-DD 或 '下星期一'。")  # 回覆日期解析失敗提醒訊息

        create_trello_card(task_name, member_name, start_date_str, due_date_str, due_datetime)  # 呼叫函式建立 Trello 卡片，並傳遞解析出的任務資訊 (包含日期時間物件)

    send_line_message(user_id, reply_message)  # 發送 Line 回覆訊息
    return True # 已處理指令，返回 True


# Line Webhook Callback 路由：接收 Line Server 發送的訊息事件
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers.get('X-Line-Signature')  # 從 Header 中取得 signature
    body = request.get_data(as_text=True)  # 取得 request body (訊息內容)

    try:
        validate_signature(body, signature)  # 驗證簽名
    except ValueError:
        logger.warning("Line signature 驗證失敗，拒絕請求") # 使用 logger.warning 記錄簽名驗證失敗並拒絕請求
        abort(400)  # 驗證失敗回傳 400 錯誤

    events = request.json.get('events', [])  # 解析 JSON body 中的 events 陣列，取得所有事件
    if not events: # 檢查 events 是否為空
        logger.warning("接收到空的 events 陣列，可能為測試請求或異常狀況。") # 記錄收到空 events 警告
        return 'OK' # 直接返回 200 OK，避免後續處理錯誤

    bindings = load_bindings()  # 載入綁定關係

    for event in events:  # 迭代處理每一個事件
        if event['type'] == 'message' and event['message']['type'] == 'text':  # 判斷事件類型是否為文字訊息
            text = event['message']['text']  # 取得訊息文字
            user_id = event['source']['userId']  # 取得用戶 ID
            logger.info(f"接收到來自用戶 {user_id} 的訊息: {text}") # 記錄接收到的訊息內容

            if handle_binding_command(user_id, text, bindings): # 處理綁定指令
                continue # 指令已處理，繼續下一個事件
            if handle_status_query(user_id, bindings.get(user_id), text): # 處理狀態查詢指令  <-- 修正錯誤的地方，加入 text 參數
                continue # 指令已處理，繼續下一個事件
            if handle_create_task_command(user_id, text, bindings): # 處理建立任務指令
                continue # 指令已處理，繼續下一個事件

            # 如果以上指令都不是，則視為一般訊息，使用 ChatGPT 回覆
            reply_message = get_chatgpt_response(text)
            send_line_message(user_id, reply_message)
            logger.info(f"用戶 {user_id} 輸入一般訊息，使用 ChatGPT 回覆。") # 記錄一般訊息處理

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
        logger.info(f"開始建立 Trello 卡片：{card_name}") # 記錄開始建立卡片訊息

        # 處理成員分配：如果訊息中包含成員名稱，則嘗試將卡片分配給該成員
        if member_name:
            member_id = get_trello_member_id_by_name(member_name)  # 根據成員名稱查詢 Trello 會員 ID (需實作)
            if member_id:  # 如果找到對應的 Trello 會員 ID
                query['idMembers'] = member_id  # 將成員 ID 加入 query 參數，設定卡片成員
                logger.info(f"將卡片 '{card_name}' 分配給成員：{member_name} (ID: {member_id})")  # 記錄成員分配訊息
            else:  # 如果找不到對應的 Trello 會員 ID
                logger.warning(f"找不到名為 '{member_name}' 的 Trello 成員。卡片將不會被分配成員。")  # 使用 logger.warning 記錄找不到成員的警告訊息

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
        logger.info(f"已成功創建 Trello 卡片：{card_name}, 回應狀態碼: {response.status_code}")  # 記錄卡片建立成功訊息，包含 HTTP 狀態碼
    except requests.exceptions.RequestException as e:  # 捕捉 requests 模組的例外，例如連線錯誤、HTTP 錯誤等
        logger.error(f"創建 Trello 卡片失敗：{e}")  # 記錄卡片建立失敗訊息
        send_line_message(bindings.get(user_id), f"建立 Trello 卡片 '{card_name}' 失敗，請稍後再試。") # 回覆 Line 錯誤訊息

# 取得 Trello 列表名稱對應 ID 的 Map：方便後續查詢列表名稱
def get_list_map():
    try:
        url = f"https://api.trello.com/1/boards/{TRELLO_BOARD_ID}/lists"  # Trello API 取得看板列表 endpoint
        query = {'key': TRELLO_API_KEY, 'token': TRELLO_TOKEN}  # Trello API 請求參數
        response = requests.get(url, params=query)  # 發送 GET 請求到 Trello API 取得列表
        response.raise_for_status()  # 檢查 HTTP 狀態碼
        lists = response.json()  # 解析 JSON response
        logger.info(f"成功取得 Trello 列表，共 {len(lists)} 個列表。") # 記錄成功取得列表訊息，包含列表數量
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
            logger.info(f"Trello 用戶 ID {trello_member_id} 沒有任何指派的任務。") # 記錄沒有任務訊息
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
        logger.info(f"成功取得 Trello 用戶 ID {trello_member_id} 的任務狀態。") # 記錄成功取得任務狀態訊息
        return status_message  # 返回任務狀態訊息
    except requests.exceptions.RequestException as e:  # 捕捉 requests 模組的例外
        logger.error(f"獲取 Trello 任務失敗：{e}")  # 記錄獲取任務失敗訊息
        return "無法獲取任務狀態，請稍後再試。"  # 回覆無法獲取任務狀態訊息

# 取得 ChatGPT 回覆：呼叫 OpenAI ChatGPT API 取得自然語言回覆
def get_chatgpt_response(prompt):
    try:
        response = openai.ChatCompletion.create(  # 呼叫 OpenAI ChatGPT API
            model="gpt-4o",  # 使用的模型
            messages=[  # 訊息內容
                {"role": "system", "content": "你是貼心的助理。"},  # 設定 ChatGPT 角色為貼心的助理
                {"role": "user", "content": prompt}  # 使用者輸入的訊息
            ]
        )
        logger.info("成功呼叫 OpenAI API 並取得回覆。") # 記錄成功呼叫 OpenAI API
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
        logger.info(f"已發送 LINE 訊息給用戶 {user_id}, 訊息內容：{message[:20]}...")  # 記錄訊息發送成功訊息，只記錄前 20 字元避免敏感資訊外洩
    except requests.exceptions.RequestException as e:  # 捕捉 requests 模組的例外
        logger.error(f"發送 LINE 訊息失敗給用戶 {user_id}: {e}")  # 記錄訊息發送失敗訊息

# 定期檢查 Trello 卡片截止日期的排程任務函式：每天檢查一次 Trello 看板上的卡片，並在卡片即將到期時發送 Line 提醒訊息
def check_trello_cards():
    logger.info("排程任務開始：檢查 Trello 卡片截止日期...")  # 更明確的排程任務開始日誌
    bindings = load_bindings()  # 載入綁定關係
    try:
        url = f"https://api.trello.com/1/boards/{TRELLO_BOARD_ID}/cards"  # Trello API 取得看板卡片 endpoint
        query = {'key': TRELLO_API_KEY, 'token': TRELLO_TOKEN}  # Trello API 請求參數
        response = requests.get(url, params=query)  # 發送 GET 請求到 Trello API 取得卡片
        response.raise_for_status()  # 檢查 HTTP 狀態碼
        cards = response.json()  # 解析 JSON response
        logger.info(f"排程任務：成功取得 Trello 卡片，共 {len(cards)} 張卡片。") # 記錄成功取得卡片訊息，包含卡片數量

        now = datetime.datetime.now()  # 取得目前時間 (本地時間)

        for card in cards:  # 迭代處理每一張卡片
            due_date = card.get('due')  # 取得卡片的截止日期 (ISO 8601 格式字串)
            if not due_date:  # 如果卡片沒有設定截止日期
                logger.info(f"排程任務：卡片 {card['name']} 沒有截止日期，跳過檢查。")  # 更明確的跳過檢查日誌
                continue  # 跳過本次迴圈，繼續檢查下一張卡片

            try:
                due_date = datetime.datetime.fromisoformat(due_date)  # 將 ISO 8601 格式的日期字串轉換為 datetime 物件
            except ValueError:  # 捕捉日期格式錯誤
                logger.error(f"排程任務：卡片 {card['name']} 的截止日期格式無效: {card.get('due')}")  # 更明確的日期格式錯誤日誌
                continue  # 跳過本次迴圈，繼續檢查下一張卡片

            if (due_date - now).days == 1:  # 判斷卡片是否即將在一天後到期
                logger.info(f"排程任務：卡片 {card['name']} 即將截止，剩餘時間：一天。")  # 更明確的卡片即將截止日誌
                for trello_member_id in card.get('idMembers', []):  # 迭代處理卡片上的每一個成員
                    user_id = next((uid for uid, tid in bindings.items() if tid == trello_member_id), None)  # 根據 Trello 會員 ID 查找綁定的 Line 用戶 ID
                    if user_id:  # 如果找到綁定的 Line 用戶 ID
                        logger.info(f"排程任務：發送提醒訊息給用戶 {user_id} 關於卡片 {card['name']}")  # 更明確的發送提醒訊息日誌
                        send_line_message(user_id, f"提醒：任務『{card['name']}』明天截止，請注意。")  # 發送 Line 提醒訊息
    except requests.exceptions.RequestException as e:  # 捕捉 requests 模組的例外
        logger.error(f"排程任務：檢查 Trello 卡片截止日期失敗：{e}")  # 更明確的排程任務失敗日誌
    logger.info("排程任務結束：檢查 Trello 卡片截止日期完成。") # 更明確的排程任務結束日誌

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
        logger.info(f"成功取得 Trello 看板成員，共 {len(members)} 位成員。") # 記錄成功取得看板成員訊息

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
scheduler.add_job(check_trello_cards, 'interval', hours=24)  # 修改為每天檢查一次，更符合「定期」檢查的語意
scheduler.start()  # 啟動排程器
logger.info("排程任務已啟動，將每天檢查 Trello 卡片截止日期。")  # 更明確的排程任務啟動訊息

# Flask 應用程式啟動入口點
if __name__ == "__main__":
    from waitress import serve  # 導入 waitress，用於生產環境部署
    logger.info("Flask 應用程式即將啟動...") # 記錄 Flask 應用程式啟動訊息
    serve(app, host="0.0.0.0", port=5000)  # 使用 waitress 啟動 Flask 應用程式，監聽 5000 端口，host="0.0.0.0" 允許外部連線，方便 Render 部署