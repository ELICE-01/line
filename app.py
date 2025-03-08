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