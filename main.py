import sqlite3
import os
import requests
from typing import List, Optional
from pydantic import BaseModel
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse

app = FastAPI(title="LAN Task Platform with Notion")
DB_FILE = "計網期末.db"

# ---------------------------------------------------------------------------
# 🔑 請在此處配置你個人的 Notion 憑證
# ---------------------------------------------------------------------------
# 1. TOKEN 必須是 ntn_ 開頭的那一長串金鑰
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "ntn_O1011713336948W156Dvn9hRJfuIqze4Qtlf9FQNUev0aQ")

# 2. DATABASE_ID 必須是 32 位元的純英數資料庫識別碼（你的網頁截圖中那串）
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "36d03c4d85388005a504cdb22ae0cc8f")

class TaskCreateInput(BaseModel):
    project_name: str
    title: str
    priority: str

class ClaimInput(BaseModel):
    assignee: str

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT NOT NULL,
                title TEXT NOT NULL,
                priority TEXT NOT NULL,
                assignee TEXT,
                is_completed INTEGER DEFAULT 0
            )
        """)
        conn.commit()

init_db()

# --- 💡 核心功能：向 Notion 官方 API 發送請求建立頁面 ---
def sync_single_task_to_notion(task_title: str, project_name: str, priority: str, assignee: str, is_done: bool):
    if "請替換成" in NOTION_TOKEN or "請替換成" in NOTION_DATABASE_ID:
        raise HTTPException(status_code=400, detail="後端尚未配置有效的 Notion 憑證")
        
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    
    # 校準符合 Notion 官方最新規格的屬性結構
        # 建立符合 Notion 欄位屬性規格的 JSON Payload
    payload = {
        "parent": { "database_id": NOTION_DATABASE_ID },
        "properties": {
            "Name": {  # 如果你上次 Notion 改成 Name，這裡就維持 "Name"；如果維持截圖的 "Aa"，這裡就用 "Aa"
                "title": [{ "text": { "content": task_title } }]
            },
            "專案": {
                "select": { "name": project_name }
            },
            "優先權": {
                "select": { "name": priority }
            },
            "認領人": {
                "select": { "name": assignee if assignee else "未認領" }
            },
            # 🎯 關鍵修正：把原本的 "狀態" 改成跟 Notion 一致的 "Status"
            "Status": {
                "status": { "name": "Done" if is_done else "Not started" }
            }
        }
    }
    
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=5)
        if res.status_code == 200:
            print(f"[Notion] 成功同步任務: {task_title}")
            return True
        else:
            # 核心修正：將 Notion 的錯誤代碼與詳細原因直接包裝成 HTTP 錯誤丟給前端網頁
            error_msg = f"Notion API 失敗 [{res.status_code}]: {res.text}"
            print(error_msg)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=error_msg)
    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=status.HTTP_504_TIMEOUT, detail=f"連線至 Notion 伺服器超時: {str(e)}")

# --- API 路由群組 ---

@app.get("/api/tasks")
def get_all_tasks():
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM tasks ORDER BY task_id DESC")
        return [dict(row) for row in cursor.fetchall()]

@app.post("/api/tasks", status_code=status.HTTP_201_CREATED)
def create_task(payload: TaskCreateInput):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO tasks (project_name, title, priority, assignee, is_completed)
            VALUES (?, ?, ?, NULL, 0)
        """, (payload.project_name, payload.title, payload.priority))
        conn.commit()
        
    # 同步推送到 Notion
    sync_single_task_to_notion(payload.title, payload.project_name, payload.priority, "未認領", False)
    return {"status": "success"}

@app.put("/api/tasks/{task_id}/claim")
def claim_task(task_id: int, payload: ClaimInput):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT project_name, title, priority, assignee, is_completed FROM tasks WHERE task_id = ?", (task_id,))
        result = cursor.fetchone()
        
        if not result:
            raise HTTPException(status_code=404, detail="Task not found")
        if result[3] is not None and result[3] != "":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Task already claimed")
            
        cursor.execute("UPDATE tasks SET assignee = ? WHERE task_id = ?", (payload.assignee, task_id))
        conn.commit()
        
        # 同步更新至 Notion
        sync_single_task_to_notion(result[1], result[0], result[2], payload.assignee, bool(result[4]))
        
    return {"status": "success"}

@app.put("/api/tasks/{task_id}/complete")
def complete_task(task_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT project_name, title, priority, assignee FROM tasks WHERE task_id = ?", (task_id,))
        result = cursor.fetchone()
        
        if result:
            cursor.execute("UPDATE tasks SET is_completed = 1 WHERE task_id = ?", (task_id,))
            conn.commit()
            # 同步更新至 Notion
            sync_single_task_to_notion(result[1], result[0], result[2], result[3], True)
            
    return {"status": "success"}

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        conn.commit()
    return {"status": "success"}

@app.get("/")
def read_index():
    return FileResponse("index.html")