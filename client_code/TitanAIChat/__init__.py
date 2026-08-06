"""
TitanAIChat - main chat form (Titan AI app)
===========================================
Build the components in the Anvil designer per
Titan_AI_Form_Specification.docx, then paste this code.

Components referenced (names must match the designer):
    cp_main            ColumnPanel (root content)
    lbl_title          Label      "Titan AI"
    lbl_subtitle       Label      tagline / read-only notice
    lk_new_chat        Link       "New chat"
    fp_chips           FlowPanel  suggested-question chips (Links added in code)
    rp_messages        RepeatingPanel (item template: TitanAIMessageRow)
    lbl_busy           Label      "Titan AI is thinking…"
    txt_question       TextBox    (set PLACEHOLDER, not text!)
    btn_send           Button     "Ask"
    tmr_poll           Timer      interval 1 (seconds)
"""

import anvil.server
from anvil import Link, DropDown, TextBox, ColumnPanel, Label, alert

from ._anvil_designer import TitanAIChatTemplate


class TitanAIChat(TitanAIChatTemplate):

  def __init__(self, **properties):
    self.init_components(**properties)

    self.username = None
    self.session_id = None
    self.last_message_id = 0
    self.items = []          # rows currently shown in rp_messages

    # Colors set in code (house rule): grey #616161 for the busy line —
    # medium grey, clearly secondary to the black message text.
    self.lbl_busy.foreground = "#616161"
    self.lbl_busy.visible = False

    # No Anvil Users service — matches the rest of the app family.
    # Identity is a picked name (SE-kiosk pattern), used for the
    # question log, feedback attribution, and the rate limiter.
    self._pick_user()

    self._start_session()
    self._build_chips()

  # ── setup ────────────────────────────────────────────────────────

  def _pick_user(self):
    """Modal name picker: dropdown of known names + free-text fallback."""
    names = anvil.server.call('ta_get_users') or []
    dd = DropDown(items=names, include_placeholder=True,
                  placeholder="Select your name…")
    txt = TextBox(placeholder="…or type your name")
    panel = ColumnPanel()
    panel.add_component(Label(text="Who's asking?", bold=True))
    if names:
      panel.add_component(dd)
    panel.add_component(txt)
    while not self.username:
      alert(content=panel, title="Titan AI",
            buttons=[("Start", True)], dismissible=False)
      picked = (txt.text or '').strip() or dd.selected_value
      self.username = picked or None

  def _start_session(self):
    info = anvil.server.call('ta_start_session', self.username)
    self.session_id = info['session_id']
    self.last_message_id = 0
    self.items = []
    self.rp_messages.items = self.items
    self.tmr_poll.interval = 1

  def _build_chips(self):
    """Suggested questions as clickable Links (Labels are NOT clickable)."""
    self.fp_chips.clear()
    for question in anvil.server.call('ta_get_suggested_questions'):
      chip = Link(text=question, role='outlined-button')
      chip.tag.question = question
      chip.set_event_handler('click', self._chip_click)
      self.fp_chips.add_component(chip)

  def _chip_click(self, sender, **event_args):
    question = sender.tag.question
    if question.endswith('…'):
      # Template chip: put the stem in the box for the user to finish.
      self.txt_question.text = question.rstrip('…').strip() + ' '
      self.txt_question.focus()
    else:
      self._send(question)

  # ── sending ──────────────────────────────────────────────────────

  def btn_send_click(self, **event_args):
    self._send(self.txt_question.text)

  def txt_question_pressed_enter(self, **event_args):
    self._send(self.txt_question.text)

  def _send(self, text):
    text = (text or '').strip()
    if not text:
      return
    self.btn_send.enabled = False
    try:
      anvil.server.call('ta_send_message', self.session_id, text)
      self.txt_question.text = ''
      self.fp_chips.visible = False       # chips only on an empty chat
      self.lbl_busy.visible = True
    except Exception as e:
      alert(str(e), title="Couldn't send")
    finally:
      self.btn_send.enabled = True

  def lk_new_chat_click(self, **event_args):
    self._start_session()
    self.fp_chips.visible = True
    self.lbl_busy.visible = False

  # ── receiving ────────────────────────────────────────────────────

  def tmr_poll_tick(self, **event_args):
    if self.session_id is None:
      return
    try:
      update = anvil.server.call_s(
        'ta_get_updates', self.session_id, self.last_message_id
      )
    except Exception:
      return  # transient blip; next tick will retry

    new_rows = update['messages']
    if new_rows:
      for row in new_rows:
        self.last_message_id = max(self.last_message_id, row['message_id'])
        if row['role'] in ('assistant', 'error'):
          # The turn is over: clear its transient status lines.
          self.items = [i for i in self.items if i['role'] != 'status']
        self.items.append(row)
      self.rp_messages.items = list(self.items)
      # Keep the newest message in view.
      panels = self.rp_messages.get_components()
      if panels:
        panels[-1].scroll_into_view()

    self.lbl_busy.visible = update['busy']
