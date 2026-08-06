import anvil.server
import anvil.users
import uuid
from datetime import datetime

from .db_config import get_connection

# ---------------------------------------------------------------------------
# Schema contract with titan-ai-service on Hermes (migration 020).
#
# This app is a thin client: it INSERTs the user's question as a `pending`
# row, and titan-ai-service (running on Hermes) reads pending rows, does the
# work against Nexus, and writes back `status`, `assistant`, and `error` rows.
# This app then polls for those rows. It never writes anything but questions
# and thumbs-up/down ratings -- strictly read-only with respect to Nexus data.
#
# The table/column names below MUST match dbo.tai_chat_messages as created by
# migration 020. If the live schema differs, adjust these constants only --
# the query text is built from them.
# ---------------------------------------------------------------------------
TABLE = "dbo.tai_chat_messages"

COL_ID = "message_id"        # BIGINT IDENTITY primary key
COL_SESSION = "session_id"   # VARCHAR: groups a conversation
COL_EMAIL = "user_email"     # VARCHAR: who asked
COL_ROLE = "role"            # VARCHAR: 'user' | 'assistant' | 'status' | 'error'
COL_CONTENT = "content"      # NVARCHAR(MAX): question / answer markdown / status / error text
COL_TRACE = "trace"          # NVARCHAR(MAX): tool trace markdown (assistant rows), nullable
COL_RATING = "rating"        # VARCHAR: 'up' | 'down', nullable
COL_STATUS = "status"        # VARCHAR: 'pending' | 'working' | 'answered' | 'error' (user rows)
COL_CREATED = "created_at"   # DATETIME2

# Columns returned to the client for each message row.
_SELECT_COLS = ", ".join([COL_ID, COL_ROLE, COL_CONTENT, COL_TRACE, COL_RATING, COL_STATUS])


# Suggested-question chips. A chip whose text ends in an ellipsis is a stem:
# clicking it drops the stem into the TextBox for the user to complete rather
# than sending it immediately.
_SUGGESTIONS = [
    "What work orders are open right now?",
    "Which items are below their min level?",
    "Show pending transfers between stores.",
    "What's on the production schedule today?",
    "Show the usage trend for a SKU…",
    "How many units of a product sold last week…",
]


def _current_email():
    user = anvil.users.get_user()
    return user["email"] if user else None


def _row_to_message(row):
    return {
        "message_id": row[COL_ID],
        "role": row[COL_ROLE],
        "content": row[COL_CONTENT],
        "trace": row.get(COL_TRACE),
        "rating": row.get(COL_RATING),
        "status": row.get(COL_STATUS),
    }


@anvil.server.callable(require_user=True)
def ta_start_session():
    """Begin a fresh conversation. Sessions are identified by this id on every
    message row, so old sessions remain in the database for question-log
    analytics. No row is written until the first question is asked."""
    return uuid.uuid4().hex


@anvil.server.callable(require_user=True)
def ta_get_suggestions():
    return list(_SUGGESTIONS)


@anvil.server.callable(require_user=True)
def ta_send_message(session_id, question):
    """Queue a question as a pending row and return it so the client can show
    it immediately. titan-ai-service picks it up and writes the answer."""
    question = (question or "").strip()
    if not question:
        return None
    email = _current_email()

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO " + TABLE
            + " (" + COL_SESSION + ", " + COL_EMAIL + ", " + COL_ROLE + ", "
            + COL_CONTENT + ", " + COL_STATUS + ", " + COL_CREATED + ")"
            + " OUTPUT INSERTED." + COL_ID + " AS " + COL_ID
            + " VALUES (%s, %s, 'user', %s, 'pending', %s)",
            (session_id, email, question, datetime.utcnow()),
        )
        new_id = cur.fetchone()[COL_ID]
        conn.commit()
        cur.close()
    finally:
        conn.close()

    return {
        "message_id": new_id,
        "role": "user",
        "content": question,
        "trace": None,
        "rating": None,
        "status": "pending",
    }


@anvil.server.callable(require_user=True)
def ta_get_updates(session_id, after_id):
    """Return every message in this session with an id greater than after_id,
    in order. Called by the client's 1s poll timer via call_s (silent)."""
    if not session_id:
        return []
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT " + _SELECT_COLS + " FROM " + TABLE
            + " WHERE " + COL_SESSION + " = %s AND " + COL_ID + " > %s"
            + " ORDER BY " + COL_ID + " ASC",
            (session_id, after_id or 0),
        )
        rows = cur.fetchall()
        cur.close()
    finally:
        conn.close()
    return [_row_to_message(r) for r in rows]


@anvil.server.callable(require_user=True)
def ta_rate_message(message_id, rating):
    """Record a thumbs up/down on an assistant answer."""
    if rating not in ("up", "down"):
        return {"ok": False, "message": "Invalid rating"}
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "UPDATE " + TABLE + " SET " + COL_RATING + " = %s"
            + " WHERE " + COL_ID + " = %s AND " + COL_ROLE + " = 'assistant'",
            (rating, message_id),
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
    return {"ok": True}
