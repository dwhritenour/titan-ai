"""
chat_service.py - Anvil Server Module (Titan AI app)
====================================================
Server callables for the Titan AI chat form.

This app is a THIN CLIENT: it writes user questions into the
tai_chat_messages queue and reads the thread back. All AI work is
done by titan-ai-service on Hermes. If that service is down,
questions queue up and are answered when it returns.

Auth note: no Anvil Users service — matches the rest of the app
family (no app records logins). Identity is a picked name per
session (SE-kiosk pattern), used for the question log, feedback
attribution, and the rate limiter. This is honor-system identity,
same trust level as the other in-plant apps.

Callables:
    ta_get_users()                            -> list of names for the picker
    ta_start_session(username)                -> session info
    ta_send_message(session_id, text)         -> message_id
    ta_get_updates(session_id, after_id)      -> new rows + busy flag
    ta_submit_feedback(message_id, rating, comment=None)
    ta_get_suggested_questions()              -> list of strings

Author: Dave Whritenour / Claude AI Assistant
Project: Titan Flow Control - Year of Automation
Created: August 2026
"""

import json

import anvil.server

from . import db_config

# Names shown in the "Who's asking?" picker. EDIT THIS LIST as the
# user group changes — free-text fallback is allowed, so an omission
# is inconvenient, not blocking.
USERS = [
  "Dave",
  "Veronica",
  "Anthony",
  "Tara",
  "Kim",
  "Chuck",
  # add the other ~4 users here
]

# Rate limit: questions per user per hour (5-user app; this is a
# guardrail against a runaway Timer bug, not against people).
MAX_QUESTIONS_PER_HOUR = 30
MAX_QUESTION_CHARS = 2000

SUGGESTED_QUESTIONS = [
  "What work orders are due this week?",
  "What transfers are pending right now?",
  "Which SKUs are below min at Starlite?",
  "What sales orders are late right now?",
  "Show the usage trend for a SKU…",
  "Why is a transfer being recommended?",
]


def _get_connection():
  return db_config.get_nexus_connection()


def _session_username(cursor, session_id):
  """Return the session's username, or None if the session doesn't exist."""
  cursor.execute(
    "SELECT username FROM dbo.tai_chat_sessions WHERE session_id = %s",
    (session_id,)
  )
  row = cursor.fetchone()
  return row['username'] if row else None


# =============================================================================
# SESSION
# =============================================================================

@anvil.server.callable
def ta_get_users():
  """Names for the 'Who's asking?' picker."""
  return USERS


@anvil.server.callable
def ta_start_session(username):
  """Create a new chat session for the given (picked) name."""
  username = (username or '').strip()[:100]
  if not username:
    raise ValueError("Please pick or type your name first.")
  conn = _get_connection()
  try:
    cursor = conn.cursor()
    cursor.execute(
      """
      INSERT INTO dbo.tai_chat_sessions (username)
      OUTPUT inserted.session_id
      VALUES (%s)
      """,
      (username,)
    )
    row = cursor.fetchone()
    conn.commit()
    return {'session_id': row['session_id'], 'username': username}
  finally:
    conn.close()


# =============================================================================
# SEND / RECEIVE
# =============================================================================

@anvil.server.callable
def ta_send_message(session_id, text):
  """Queue a user question. Returns the new message_id."""
  text = (text or '').strip()
  if not text:
    raise ValueError("Please type a question first.")
  if len(text) > MAX_QUESTION_CHARS:
    raise ValueError(
      f"That question is too long ({len(text)} characters, "
      f"max {MAX_QUESTION_CHARS}). Please shorten it."
    )

  conn = _get_connection()
  try:
    cursor = conn.cursor()

    username = _session_username(cursor, session_id)
    if username is None:
      raise ValueError("Unknown chat session — reload the app and try again.")

    # Rate limit
    cursor.execute(
      """
      SELECT COUNT(*) AS n
      FROM dbo.tai_chat_messages m
      JOIN dbo.tai_chat_sessions s ON s.session_id = m.session_id
      WHERE s.username = %s
        AND m.role = 'user'
        AND m.created_at > DATEADD(hour, -1, GETDATE())
      """,
      (username,)
    )
    if cursor.fetchone()['n'] >= MAX_QUESTIONS_PER_HOUR:
      raise ValueError(
        "You've hit the limit of questions for this hour. "
        "Give it a few minutes and try again."
      )

    cursor.execute(
      """
      INSERT INTO dbo.tai_chat_messages (session_id, role, content, status)
      OUTPUT inserted.message_id
      VALUES (%s, 'user', %s, 'pending')
      """,
      (session_id, text)
    )
    message_id = cursor.fetchone()['message_id']

    # First question becomes the session title (for the analytics view)
    cursor.execute(
      """
      UPDATE dbo.tai_chat_sessions
      SET last_activity_at = GETDATE(),
          title = COALESCE(title, LEFT(%s, 200))
      WHERE session_id = %s
      """,
      (text, session_id)
    )
    conn.commit()
    return message_id
  finally:
    conn.close()


@anvil.server.callable
def ta_get_updates(session_id, after_message_id):
  """New rows for this session after the given id, plus a busy flag.

  Called by the form's Timer (~1s). Returns:
      {'messages': [ {message_id, role, content, tool_trace, created_at}, ... ],
       'busy': bool}   # True while a question is pending/processing
  """
  conn = _get_connection()
  try:
    cursor = conn.cursor()
    if _session_username(cursor, session_id) is None:
      raise ValueError("Unknown chat session — reload the app and try again.")

    cursor.execute(
      """
      SELECT message_id, role, content, tool_trace, created_at
      FROM dbo.tai_chat_messages
      WHERE session_id = %s AND message_id > %s
      ORDER BY message_id
      """,
      (session_id, after_message_id or 0)
    )
    messages = []
    for r in cursor.fetchall():
      trace = None
      if r['tool_trace']:
        try:
          trace = json.loads(r['tool_trace'])
        except Exception:
          trace = None
      messages.append({
        'message_id': r['message_id'],
        'role': r['role'],
        'content': r['content'],
        'tool_trace': trace,
        'created_at': r['created_at'],
      })

    cursor.execute(
      """
      SELECT COUNT(*) AS n
      FROM dbo.tai_chat_messages
      WHERE session_id = %s
        AND role = 'user'
        AND status IN ('pending', 'processing')
      """,
      (session_id,)
    )
    busy = cursor.fetchone()['n'] > 0

    return {'messages': messages, 'busy': busy}
  finally:
    conn.close()


# =============================================================================
# FEEDBACK & SUGGESTIONS
# =============================================================================

@anvil.server.callable
def ta_submit_feedback(message_id, rating, comment=None):
  """Thumbs up (+1) / down (-1) on an assistant answer.
  Username is taken from the answer's own session."""
  if rating not in (1, -1):
    raise ValueError("rating must be 1 or -1")

  conn = _get_connection()
  try:
    cursor = conn.cursor()
    cursor.execute(
      """
      SELECT s.username
      FROM dbo.tai_chat_messages m
      JOIN dbo.tai_chat_sessions s ON s.session_id = m.session_id
      WHERE m.message_id = %s AND m.role = 'assistant'
      """,
      (message_id,)
    )
    row = cursor.fetchone()
    if row is None:
      raise ValueError("That message is not an assistant answer.")
    username = row['username']

    cursor.execute(
      """
      INSERT INTO dbo.tai_chat_feedback (message_id, username, rating, comment)
      VALUES (%s, %s, %s, %s)
      """,
      (message_id, username, rating, (comment or None))
    )
    conn.commit()
    return True
  finally:
    conn.close()


@anvil.server.callable
def ta_get_suggested_questions():
  return SUGGESTED_QUESTIONS
