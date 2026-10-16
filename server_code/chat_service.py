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
    ta_submit_suggestion(session_id, text)    -> store an improvement idea
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
# is inconvenient, not blocking. Casing here is canonical: whatever
# a user types is matched case-insensitively and stored THIS way.
USERS = [
  "Anthony",
  "Blake",
  "Chuck",
  "Dave",
  "Gina",
  "Gustavo",
  "JR",
  "Kayla",
  "Kim",
  "Pratik",
  "Tara",
]

# Free-text spellings that map to a canonical name above (compared
# lowercase). Keeps one identity per person in the usage stats.
USER_ALIASES = {
  "kimberly": "Kim",
  "anthony cabral": "Anthony",
  "jr.": "JR",
  "junior": "JR",
}


def _canonical_username(raw):
  """Map any spelling/casing to the canonical roster name."""
  key = (raw or '').strip().lower()
  if not key:
    return None
  if key in USER_ALIASES:
    return USER_ALIASES[key]
  for name in USERS:
    if key == name.lower():
      return name
  return (raw or '').strip()[:100]        # unknown name: keep as typed


def _looks_like_question(name):
  """People sometimes type their QUESTION (or a SKU) into the name
  box — catch the obvious cases. Real names have no digits, no '?',
  no underscores, and at most ~3 words."""
  n = (name or '').strip()
  return ('?' in n) or (len(n) > 40) or (len(n.split()) > 3) \
  or any(ch.isdigit() for ch in n) or ('_' in n)

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
  if _looks_like_question(username):
    raise ValueError(
      "That looks like a question, not a name — pick your name first; "
      "you can ask your question once the chat opens."
    )
  username = _canonical_username(username)
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

    # Duplicate guard (2026-09-17): a held Enter key / double click can
    # fire several identical sends before the first returns (observed:
    # the same question queued 5x in one second — each duplicate burns
    # a full agent run). If the newest user message in this session has
    # the SAME text within the last 10 seconds, return ITS id instead
    # of inserting again — idempotent from the client's point of view.
    cursor.execute(
      """
      SELECT TOP 1 message_id, content,
             DATEDIFF(second, created_at, GETDATE()) AS age_seconds
      FROM dbo.tai_chat_messages
      WHERE session_id = %s AND role = 'user'
      ORDER BY message_id DESC
      """,
      (session_id,)
    )
    last = cursor.fetchone()
    if (last is not None and last['content'] == text
        and last['age_seconds'] <= 10):
      conn.commit()
      return last['message_id']

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
      SELECT message_id, role, content, tool_trace, file_id, created_at
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
        'file_id': r['file_id'],
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
# FILE DOWNLOADS (Excel exports)
# =============================================================================

@anvil.server.callable
def ta_get_file(file_id):
  """Return an export file as downloadable media."""
  import anvil
  conn = _get_connection()
  try:
    cursor = conn.cursor()
    cursor.execute(
      """
      SELECT filename, content_type, file_bytes
      FROM dbo.tai_chat_files
      WHERE file_id = %s
      """,
      (file_id,)
    )
    row = cursor.fetchone()
    if row is None:
      raise ValueError("That file no longer exists.")
    return anvil.BlobMedia(
      row['content_type'], bytes(row['file_bytes']), name=row['filename'])
  finally:
    conn.close()


# =============================================================================
# VOICE (voice-1.0.0) — queue-driven, keys stay on the service box
# =============================================================================

MAX_VOICE_BYTES = 10 * 1024 * 1024    # ~10MB ≈ several minutes of opus


@anvil.server.callable
def ta_request_speech(session_id, message_id):
  """Ask the service to speak an answer. If it was already synthesized
  for this session, return the existing audio file_id (free replay);
  otherwise queue a TTS job and return {'queued': True}."""
  conn = _get_connection()
  try:
    cursor = conn.cursor()
    if _session_username(cursor, session_id) is None:
      raise ValueError("Unknown chat session — reload the app and try again.")

    # Replay: a 'voice' row already announces audio for this message.
    cursor.execute(
      """
      SELECT TOP 1 file_id FROM dbo.tai_chat_messages
      WHERE session_id = %s AND role = 'voice' AND content = %s
        AND file_id IS NOT NULL
      ORDER BY message_id DESC
      """,
      (session_id, str(message_id))
    )
    row = cursor.fetchone()
    if row:
      return {'file_id': row['file_id']}

    # Dedup: a request for this message is already pending/processing.
    cursor.execute(
      """
      SELECT COUNT(*) AS n FROM dbo.tai_chat_messages
      WHERE session_id = %s AND role = 'voice_req' AND content = %s
        AND status IN ('pending', 'processing')
      """,
      (session_id, str(message_id))
    )
    if cursor.fetchone()['n'] == 0:
      cursor.execute(
        """
        INSERT INTO dbo.tai_chat_messages (session_id, role, content, status)
        VALUES (%s, 'voice_req', %s, 'pending')
        """,
        (session_id, str(message_id))
      )
    conn.commit()
    return {'queued': True}
  finally:
    conn.close()


@anvil.server.callable
def ta_submit_voice(session_id, audio_media):
  """Store a mic recording and queue it for transcription. The
  transcript comes back through ta_get_updates as a role='stt' row."""
  conn = _get_connection()
  try:
    cursor = conn.cursor()
    if _session_username(cursor, session_id) is None:
      raise ValueError("Unknown chat session — reload the app and try again.")
    blob = audio_media.get_bytes()
    if not blob:
      raise ValueError("The recording came through empty — try again.")
    if len(blob) > MAX_VOICE_BYTES:
      raise ValueError("That recording is too long — keep it under a "
                       "couple of minutes.")
    content_type = audio_media.content_type or 'audio/webm'
    cursor.execute(
      """
      INSERT INTO dbo.tai_chat_files
        (session_id, filename, content_type, file_bytes, byte_count)
      OUTPUT inserted.file_id
      VALUES (%s, %s, %s, %s, %s)
      """,
      (session_id, 'voice-question', content_type, blob, len(blob))
    )
    file_id = cursor.fetchone()['file_id']
    cursor.execute(
      """
      INSERT INTO dbo.tai_chat_messages
        (session_id, role, content, status, file_id)
      VALUES (%s, 'stt_req', '', 'pending', %s)
      """,
      (session_id, file_id)
    )
    conn.commit()
    return True
  finally:
    conn.close()


# =============================================================================
# SUGGESTIONS
# =============================================================================
# 2026-09-17: per-message thumbs (ta_submit_feedback) removed along with
# the thumbs UI — dbo.tai_chat_feedback keeps its historical rows. User
# feedback is now the app-level "Suggest an improvement" dialog below.

MAX_SUGGESTION_CHARS = 1000


@anvil.server.callable
def ta_submit_suggestion(session_id, text):
  """Store a free-text improvement suggestion in dbo.tai_suggestions
  (migration 070). Username comes from the session, same honor-system
  identity as everything else."""
  text = (text or '').strip()
  if not text:
    raise ValueError("Please type a suggestion first.")
  if len(text) > MAX_SUGGESTION_CHARS:
    raise ValueError(
      f"That's a bit long ({len(text)} characters, max "
      f"{MAX_SUGGESTION_CHARS}). Please shorten it."
    )

  conn = _get_connection()
  try:
    cursor = conn.cursor()
    username = _session_username(cursor, session_id) or 'unknown'
    cursor.execute(
      """
      INSERT INTO dbo.tai_suggestions (username, suggestion)
      VALUES (%s, %s)
      """,
      (username, text)
    )
    conn.commit()
    return True
  finally:
    conn.close()


@anvil.server.callable
def ta_get_suggested_questions():
  return SUGGESTED_QUESTIONS