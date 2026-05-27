# uvicorn main:app --host 0.0.0.0 --port 8000
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
# 🔑 請在此處配置你個人的 Notion 憑證 (任務大表同步仍保留)
# ---------------------------------------------------------------------------
NOTION_TOKEN = os.getenv("NOTION_TOKEN", "ntn_O1011713336948W156Dvn9hRJfuIqze4Qtlf9FQNUev0aQ")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID", "36d03c4d85388005a504cdb22ae0cc8f")

class TaskCreateInput(BaseModel):
    project_name: str
    title: str
    priority: str

class ClaimInput(BaseModel):
    assignee: str

# 🎯 核心修正：留言規格改為強烈綁定純數字 task_id，徹底防範爆版
class DiscussionInput(BaseModel):
    task_id: int
    user_name: str
    content: str

def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        # 任務表維持不變
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
        # 🎯 這裡會因為剛才執行了 DROP TABLE，所以會重建帶有 task_id 的正確表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS discussions (
                msg_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id INTEGER NOT NULL,
                user_name TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

init_db()

# --- 💡 任務大表同步至 Notion 函式 ---
def sync_single_task_to_notion(task_title: str, project_name: str, priority: str, assignee: str, is_done: bool):
    url = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    
    title_property_name = "Name"
    try:
        query_url = f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}"
        check_res = requests.get(query_url, headers={"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28"}, timeout=5)
        if check_res.status_code == 200:
            props = check_res.json().get("properties", {})
            for k, v in props.items():
                if v.get("type") == "title":
                    title_property_name = k
                    break
    except:
        pass

    payload = {
        "parent": { "database_id": NOTION_DATABASE_ID },
        "properties": {
            title_property_name: { "title": [{ "text": { "content": task_title } }] },
            "專案": { "select": { "name": project_name } },
            "優先權": { "select": { "name": priority } },
            "認領人": { "select": { "name": assignee if assignee else "未認領" } },
            "Status": { "status": { "name": "Done" if is_done else "Not started" } }
        }
    }
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=5)
        return res.status_code == 200
    except:
        return False

# --- ⚙️ API 路由群組 (任務看板核心) ---

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
            sync_single_task_to_notion(result[1], result[0], result[2], result[3], True)
    return {"status": "success"}

@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        conn.commit()
    return {"status": "success"}


# --- 🎯 討論區一對一實體對齊路由群組 ---

@app.get("/api/discussions")
def get_discussions_from_table(task_id: int):
    """【精確讀取】強制轉為純整數過濾，確保一筆都漏不掉，徹底解決空白問題"""
    with sqlite3.connect(DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM discussions WHERE task_id = ? ORDER BY msg_id DESC", (int(task_id),))
        return [dict(row) for row in cursor.fetchall()]

@app.post("/api/discussions", status_code=status.HTTP_201_CREATED)
def post_discussion_to_table(payload: DiscussionInput):
    """【強固寫入】將留言與唯一的數字 task_id 牢牢鎖定在資料表中"""
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO discussions (task_id, user_name, content) VALUES (?, ?, ?)
        """, (int(payload.task_id), payload.user_name, payload.content))
        conn.commit()
    return {"status": "success"}

@app.get("/")
def read_index():
    return FileResponse("index.html")