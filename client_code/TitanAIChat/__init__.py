from ._anvil_designer import TitanAIChatTemplate
from anvil import *
import anvil.server
import anvil.users

STATUS_FG = "#616161"  # busy line (spec section 5)


class TitanAIChat(TitanAIChatTemplate):
    def __init__(self, **properties):
        super().__init__(**properties)

        self.session_id = None
        self.messages = []     # ordered list of message dicts shown in the thread
        self.last_id = 0       # highest message_id seen from the server
        self.pending = False   # True while waiting for an answer

        self.lbl_busy.visible = False
        self.lbl_busy.foreground = STATUS_FG
        self.rp_messages.items = []

        # Any logged-in Titan user. login_with_form blocks until logged in.
        if not anvil.users.get_user():
            anvil.users.login_with_form()

        self._start_session()
        self._load_suggestions()

    # -- Session -------------------------------------------------------------

    def _start_session(self):
        self.session_id = anvil.server.call("ta_start_session")
        self.messages = []
        self.last_id = 0
        self.pending = False
        self.lbl_busy.visible = False
        self._show_messages()

    @handle("lk_new_chat", "click")
    def lk_new_chat_click(self, **event_args):
        # Old sessions stay in the database for the question log; just start fresh.
        self._start_session()

    # -- Suggestion chips ----------------------------------------------------

    def _load_suggestions(self):
        self.fp_chips.clear()
        for text in anvil.server.call("ta_get_suggestions"):
            chip = Link(text=text, role="secondary-button")
            chip.set_event_handler("click", self._chip_click)
            self.fp_chips.add_component(chip)

    def _chip_click(self, **event_args):
        text = event_args["sender"].text
        if text.rstrip().endswith("…"):
            # A stem: drop it into the box for the user to complete.
            self.txt_question.text = text.rstrip()[:-1].rstrip()
            self.txt_question.focus()
        else:
            self._send(text)

    # -- Sending -------------------------------------------------------------

    @handle("btn_send", "click")
    def btn_send_click(self, **event_args):
        self._send(self.txt_question.text)

    @handle("txt_question", "pressed_enter")
    def txt_question_pressed_enter(self, **event_args):
        self._send(self.txt_question.text)

    def _send(self, question):
        question = (question or "").strip()
        if not question:
            return
        self.txt_question.text = ""
        self.btn_send.enabled = False
        try:
            row = anvil.server.call("ta_send_message", self.session_id, question)
        finally:
            self.btn_send.enabled = True
        if row:
            self.messages.append(row)
            self.last_id = max(self.last_id, row.get("message_id", self.last_id))
        self.pending = True
        self.lbl_busy.visible = True
        self._show_messages()
        self._poll()  # pull any immediate status line without waiting for the tick

    # -- Polling -------------------------------------------------------------

    @handle("tmr_poll", "tick")
    def tmr_poll_tick(self, **event_args):
        if self.pending:
            self._poll()

    def _poll(self):
        if not self.session_id:
            return
        # call_s keeps the loading spinner quiet during background polling.
        rows = anvil.server.call_s("ta_get_updates", self.session_id, self.last_id)
        if not rows:
            return
        got_answer = False
        for row in rows:
            self.last_id = max(self.last_id, row.get("message_id", self.last_id))
            if row.get("role") in ("assistant", "error"):
                got_answer = True
            self.messages.append(row)
        if got_answer:
            # Clear transient status lines and stop the busy indicator.
            self.messages = [m for m in self.messages if m.get("role") != "status"]
            self.pending = False
            self.lbl_busy.visible = False
        self._show_messages()

    # -- Rendering -----------------------------------------------------------

    def _show_messages(self):
        self.rp_messages.items = list(self.messages)
