"""
TitanAIMessageRow - item template for rp_messages (Titan AI app)
================================================================
One chat-thread row. All roles render through this template;
code shows/hides the right pieces.

2026-09-17: per-message thumbs and the "How I got this" trace
expander are REMOVED from the UI (barely used; tool_trace is still
stored in the database for debugging). Feedback now happens through
the app-level "Suggest an improvement" link on the main form.
lk_trace remains ONLY as the Excel download chip on file rows.

Components referenced (names must match the designer):
    cp_row          ColumnPanel (root of the template)
    cp_user         ColumnPanel  right-aligned user bubble
    lbl_user        Label        the user's question (in cp_user)
    cp_assistant    ColumnPanel  assistant answer block
    rt_answer       RichText     markdown answer (in cp_assistant)
    fp_actions      FlowPanel    (in cp_assistant)
    lk_trace        Link         download chip on file rows (in fp_actions)
    lbl_status      Label        transient "Checking…" line
    lbl_error       Label        error line

Safe to DELETE in the designer (code no longer references them,
and hides them if still present): lk_thumbs_up, lk_thumbs_down,
rt_trace.
"""

import anvil.server
import anvil.media
from anvil import Link, get_open_form

from ._anvil_designer import TitanAIMessageRowTemplate


class TitanAIMessageRow(TitanAIMessageRowTemplate):

  def __init__(self, **properties):
    self.init_components(**properties)

    item = self.item
    role = item['role']

    # Claude-style skin: styling lives in theme.css via these roles.
    self.cp_user.role = 'ta-user-bubble'
    self.rt_answer.role = 'ta-answer'
    self.fp_actions.role = 'ta-actions'
    self.lk_trace.role = 'ta-action'
    self.lbl_status.role = 'ta-status'
    self.lbl_error.role = 'ta-error'

    # Retired components — hide them if they still exist in the
    # designer (deleting them there is the real cleanup).
    for name in ('lk_thumbs_up', 'lk_thumbs_down', 'rt_trace'):
      comp = getattr(self, name, None)
      if comp is not None:
        comp.visible = False

    self.cp_user.visible = role == 'user'
    self.cp_assistant.visible = role in ('assistant', 'file')
    self.lbl_status.visible = role == 'status'
    self.lbl_error.visible = role == 'error'

    # lk_trace is now ONLY the download chip on file rows.
    self.lk_trace.visible = False

    if role == 'file':
      self.rt_answer.content = f"📄 **{item['content']}** is ready."
      self.lk_trace.text = "⬇ Download Excel"
      self.lk_trace.visible = True
      self.lk_trace.set_event_handler('click', self._download_click)
    elif role == 'user':
      self.lbl_user.text = item['content']
    elif role == 'assistant':
      self.rt_answer.content = item['content']   # markdown
      # Voice (voice-1.0.0): tap-to-play speaker chip, code-only.
      self._speak_busy = False
      self.lk_speak = Link(text="🔊 Listen", role='ta-action')
      self.lk_speak.set_event_handler('click', self._speak_click)
      self.fp_actions.add_component(self.lk_speak)
    elif role == 'status':
      self.lbl_status.text = item['content']
    elif role == 'error':
      self.lbl_error.text = item['content']

  # ── voice (voice-1.0.0) ──────────────────────────────────────────

  def _speak_click(self, **event_args):
    # Links have no 'enabled' property — a flag ignores re-taps
    # while the audio is being prepared.
    if self._speak_busy:
      return
    form = get_open_form()
    if hasattr(form, 'request_speech'):
      form.request_speech(self.item['message_id'], self)

  def set_speak_busy(self):
    self._speak_busy = True
    self.lk_speak.text = "🔊 Preparing audio…"

  def set_speak_ready(self):
    self._speak_busy = False
    self.lk_speak.text = "🔊 Listen again"

  # ── file download ────────────────────────────────────────────────

  def _download_click(self, **event_args):
    try:
      media = anvil.server.call('ta_get_file', self.item['file_id'])
      anvil.media.download(media)
    except Exception as e:
      from anvil import alert
      alert(str(e), title="Download failed")