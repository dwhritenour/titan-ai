"""
TitanAIMessageRow - item template for rp_messages (Titan AI app)
================================================================
One chat-thread row. All four roles render through this template;
code shows/hides the right pieces. Build per
Titan_AI_Form_Specification.docx, then paste this code.

Components referenced (names must match the designer):
    cp_row          ColumnPanel (root of the template)
    cp_user         ColumnPanel  right-aligned user bubble
    lbl_user        Label        the user's question (in cp_user)
    cp_assistant    ColumnPanel  assistant answer block
    rt_answer       RichText     markdown answer (in cp_assistant)
    fp_actions      FlowPanel    (in cp_assistant)
    lk_thumbs_up    Link  "👍"   (in fp_actions)
    lk_thumbs_down  Link  "👎"   (in fp_actions)
    lk_trace        Link  "How I got this"  (in fp_actions)
    rt_trace        RichText     tool trace, hidden by default (in cp_assistant)
    lbl_status      Label        transient "Checking…" line
    lbl_error       Label        error line
"""

import anvil.server

from ._anvil_designer import TitanAIMessageRowTemplate


class TitanAIMessageRow(TitanAIMessageRowTemplate):

  def __init__(self, **properties):
    self.init_components(**properties)

    item = self.item
    role = item['role']

    # Colors set in code (house rule — hex + description):
    #   user bubble    #E8F0FE  very light blue, matches filled-button tint
    #   status line    #616161  medium grey, secondary
    #   error line     #B3261E  Material error red (dark enough on white)
    self.cp_user.background = "#E8F0FE"
    self.lbl_status.foreground = "#616161"
    self.lbl_error.foreground = "#B3261E"

    self.cp_user.visible = role == 'user'
    self.cp_assistant.visible = role == 'assistant'
    self.lbl_status.visible = role == 'status'
    self.lbl_error.visible = role == 'error'

    if role == 'user':
      self.lbl_user.text = item['content']
    elif role == 'assistant':
      self.rt_answer.content = item['content']   # markdown
      self.rt_trace.visible = False
      trace = item.get('tool_trace') or []
      self.lk_trace.visible = bool(trace)
      self._rated = False
    elif role == 'status':
      self.lbl_status.text = item['content']
    elif role == 'error':
      self.lbl_error.text = item['content']

  # ── "How I got this" expander ────────────────────────────────────

  def lk_trace_click(self, **event_args):
    if self.rt_trace.visible:
      self.rt_trace.visible = False
      return
    lines = []
    for i, step in enumerate(self.item.get('tool_trace') or [], start=1):
      lines.append(f"**{i}. {step.get('tool', '?')}**")
      step_input = step.get('input')
      if step_input:
        if isinstance(step_input, dict) and 'sql' in step_input:
          lines.append(f"```sql\n{step_input['sql']}\n```")
        else:
          lines.append(f"`{step_input}`")
      preview = (step.get('result_preview') or '').strip()
      if preview:
        if len(preview) > 400:
          preview = preview[:400] + ' …'
        lines.append(f"> {preview}")
      lines.append("")
    self.rt_trace.content = "\n".join(lines) or "_No tools were needed._"
    self.rt_trace.visible = True

  # ── feedback ─────────────────────────────────────────────────────

  def _rate(self, rating):
    if getattr(self, '_rated', False):
      return
    try:
      anvil.server.call('ta_submit_feedback', self.item['message_id'], rating)
      self._rated = True
      # Grey out both links; keep the chosen one's tooltip as confirmation.
      self.lk_thumbs_up.foreground = "#9E9E9E"    # light grey = done
      self.lk_thumbs_down.foreground = "#9E9E9E"
      chosen = self.lk_thumbs_up if rating == 1 else self.lk_thumbs_down
      chosen.tooltip = "Thanks — recorded"
    except Exception:
      pass  # feedback is best-effort

  def lk_thumbs_up_click(self, **event_args):
    self._rate(1)

  def lk_thumbs_down_click(self, **event_args):
    self._rate(-1)
