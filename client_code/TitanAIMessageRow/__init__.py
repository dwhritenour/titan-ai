from ._anvil_designer import TitanAIMessageRowTemplate
from anvil import *
import anvil.server

# Colours (spec section 5). Set in code, not the designer picker.
USER_BG = "#E8F0FE"    # user question bubble
STATUS_FG = "#616161"  # transient status lines
ERROR_FG = "#B3261E"   # error lines
RATED_FG = "#9E9E9E"   # feedback links after rating ("done" state)


class TitanAIMessageRow(TitanAIMessageRowTemplate):
    def __init__(self, **properties):
        super().__init__(**properties)
        self._render()

    @handle("", "refreshing_data_bindings")
    def form_refreshing_data_bindings(self, **event_args):
        self._render()

    def _render(self):
        item = self.item or {}
        role = item.get("role")

        self.cp_user.visible = role == "user"
        self.cp_assistant.visible = role == "assistant"
        self.lbl_status.visible = role == "status"
        self.lbl_error.visible = role == "error"

        if role == "user":
            self.cp_user.background = USER_BG
            self.lbl_user.text = item.get("content", "")
        elif role == "assistant":
            self.rt_answer.content = item.get("content", "")
            trace = item.get("trace")
            self.lk_trace.visible = bool(trace)
            self.rt_trace.content = trace or ""
            self.rt_trace.visible = False
            self._apply_rating(item.get("rating"))
        elif role == "status":
            self.lbl_status.text = item.get("content", "")
            self.lbl_status.foreground = STATUS_FG
        elif role == "error":
            self.lbl_error.text = item.get("content", "")
            self.lbl_error.foreground = ERROR_FG

    def _apply_rating(self, rating):
        # One rating per answer: once rated, grey both links out.
        rated = rating in ("up", "down")
        self.lk_thumbs_up.foreground = RATED_FG if rated else ""
        self.lk_thumbs_down.foreground = RATED_FG if rated else ""

    def _rate(self, rating):
        item = self.item or {}
        if item.get("rating"):
            return
        message_id = item.get("message_id")
        if message_id is None:
            return
        anvil.server.call("ta_rate_message", message_id, rating)
        item["rating"] = rating
        self._apply_rating(rating)

    @handle("lk_thumbs_up", "click")
    def lk_thumbs_up_click(self, **event_args):
        self._rate("up")

    @handle("lk_thumbs_down", "click")
    def lk_thumbs_down_click(self, **event_args):
        self._rate("down")

    @handle("lk_trace", "click")
    def lk_trace_click(self, **event_args):
        self.rt_trace.visible = not self.rt_trace.visible
