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

from datetime import datetime

import anvil.js
import anvil.media
import anvil.server
from anvil import (Link, DropDown, TextBox, TextArea, ColumnPanel, Label,
alert, Notification)

from ._anvil_designer import TitanAIChatTemplate


class TitanAIChat(TitanAIChatTemplate):

  def __init__(self, **properties):
    self.init_components(**properties)

    self.username = None
    self.session_id = None
    self.last_message_id = 0
    self.items = []          # rows currently shown in rp_messages

    # Claude-style skin: all styling lives in theme.css via these
    # roles (defined in theme parameters). No colors hardcoded here.
    self.cp_main.role = 'ta-app'
    self.lbl_title.role = 'ta-brand'          # CSS adds the ✦ spark
    self.lbl_subtitle.role = 'ta-tagline'
    self.lbl_subtitle.text = "Read-only assistant · decisions stay in the apps"
    self.lk_new_chat.role = 'ta-newchat'
    self.lk_new_chat.text = "+ New chat"
    self.lbl_busy.role = 'ta-status'          # animated pulse via CSS
    self.lbl_busy.text = "Titan AI is thinking…"
    self.lbl_busy.visible = False
    self.btn_send.role = 'ta-send'
    self.btn_send.text = "↑"
    self.fp_input.role = 'ta-inputbar'

    # Wire events IN CODE so designer wiring is not required.
    # (set_event_handler replaces any designer-set handler — no double fire.)
    self.btn_send.set_event_handler('click', self.btn_send_click)
    self.txt_question.set_event_handler('pressed_enter',
                                        self.txt_question_pressed_enter)
    self.lk_new_chat.set_event_handler('click', self.lk_new_chat_click)
    self.tmr_poll.set_event_handler('tick', self.tmr_poll_tick)

    # Voice (voice-1.0.0): mic button in the input bar, code-only.
    self._recorder = None          # active JS MediaRecorder, else None
    self._rec_chunks = []
    self._rec_stream = None
    self._rec_started = 0
    self._speak_pending = {}       # message_id -> row awaiting audio
    self._temp_url = None          # last TempUrl, revoked on next play
    self._audio = None             # the one playing js Audio, else None
    self._playing_row = None       # row whose chip shows ⏹ Stop
    self.lk_mic = Link(text="🎤", role='ta-action')
    self.lk_mic.set_event_handler('click', self._mic_click)
    self.fp_input.add_component(self.lk_mic)
    self._style_mic(recording=False)   # match the 38px send circle

    # No Anvil Users service — matches the rest of the app family.
    # Identity is a picked name (SE-kiosk pattern), used for the
    # question log, feedback attribution, and the rate limiter.
    self._pick_user()

    # Greeting (empty state) — inserted in code, no designer change.
    hour = datetime.now().hour
    part = "Morning" if hour < 12 else ("Afternoon" if hour < 17 else "Evening")
    self.lbl_greeting = Label(text=f"{part}, {self.username}.",
                              role='ta-greeting')
    self.cp_main.add_component(self.lbl_greeting, index=2)  # before fp_chips

    # Hint line under the input — also code-only.
    self.lbl_hint = Label(
      text="Titan AI reads live Nexus data · answers may lag the last e21 sync by ~15 min",
      role='ta-hint')
    self.cp_main.add_component(self.lbl_hint)

    # "Suggest an improvement" — app-level feedback (replaced the
    # per-message thumbs, 2026-09-17). Code-only, no designer change.
    self.lk_suggest = Link(text="💡 Suggest an improvement", role='ta-hint')
    self.lk_suggest.set_event_handler('click', self._suggest_click)
    self.cp_main.add_component(self.lk_suggest)

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
      # Guard: people sometimes type their QUESTION (or a SKU) into
      # the name box — real names have no '?', digits or underscores.
      if picked and ('?' in picked or len(picked) > 40
                     or len(picked.split()) > 3
                     or any(ch.isdigit() for ch in picked)
                     or '_' in picked):
        Notification("That looks like a question — pick your name first; "
                     "you can ask it once the chat opens.",
                     style="warning", timeout=4).show()
        txt.text = ''
        picked = None
      self.username = picked or None

  # ── suggestions ──────────────────────────────────────────────────

  def _suggest_click(self, **event_args):
    """App-level feedback dialog → dbo.tai_suggestions."""
    box = TextArea(
      placeholder="What should Titan AI do better, or learn next?",
      height=120)
    panel = ColumnPanel()
    panel.add_component(Label(text="Suggest an improvement", bold=True))
    panel.add_component(box)
    if alert(content=panel, title="Titan AI",
             buttons=[("Send", True), ("Cancel", False)]):
      text = (box.text or '').strip()
      if not text:
        return
      try:
        anvil.server.call('ta_submit_suggestion', self.session_id, text)
        Notification("Thanks — sent to Dave.",
                     style="success", timeout=3).show()
      except Exception as e:
        alert(str(e), title="Could not send")

  def _start_session(self):
    info = anvil.server.call('ta_start_session', self.username)
    self.session_id = info['session_id']
    # Adopt the server's canonical spelling (kayla/KAYLA -> Kayla) so
    # the greeting and the usage log agree.
    self.username = info['username']
    if getattr(self, 'lbl_greeting', None) is not None:
      hour = datetime.now().hour
      part = "Morning" if hour < 12 else ("Afternoon" if hour < 17 else "Evening")
      self.lbl_greeting.text = f"{part}, {self.username}."
    self.last_message_id = 0
    self.items = []
    self.rp_messages.items = self.items
    self.tmr_poll.interval = 1

  def _build_chips(self):
    """Suggested questions as clickable Links (Labels are NOT clickable)."""
    self.fp_chips.clear()
    for question in anvil.server.call('ta_get_suggested_questions'):
      chip = Link(text=question, role='ta-chip')
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
    # Debounce (2026-09-17): pressed_enter fires _send directly, so a
    # held/re-tapped Enter key could queue the same question several
    # times while the first server call was still in flight (observed
    # 5x in one second). One in-flight send at a time, full stop.
    if getattr(self, '_sending', False):
      return
    self._sending = True
    self.btn_send.enabled = False
    try:
      anvil.server.call('ta_send_message', self.session_id, text)
      self.txt_question.text = ''
      self.fp_chips.visible = False       # chips only on an empty chat
      self.lbl_greeting.visible = False
      self.lbl_busy.visible = True
    except Exception as e:
      alert(str(e), title="Couldn't send")
    finally:
      self._sending = False
      self.btn_send.enabled = True

  def lk_new_chat_click(self, **event_args):
    self._start_session()
    self.fp_chips.visible = True
    self.lbl_greeting.visible = True
    self.lbl_busy.visible = False

  # ── voice (voice-1.0.0) ──────────────────────────────────────────

  def _style_mic(self, recording):
    """Size the 🎤 link to sit beside the 38×38 ta-send circle.
    At rest: a 38px circle (soft beige — ta-send's palette, one shade
    lighter so the orange ↑ stays the primary action). While recording
    the text is '⏹ Stop', so the circle relaxes into a pill."""
    node = anvil.js.get_dom_node(self.lk_mic)
    st = node.style
    st.height = '38px'
    st.background = '#eee7dc'
    st.display = 'inline-flex'
    st.alignItems = 'center'
    st.justifyContent = 'center'
    st.marginLeft = '4px'
    st.textDecoration = 'none'
    st.boxSizing = 'border-box'
    if recording:
      st.width = 'auto'
      st.minWidth = '38px'
      st.padding = '0 12px'
      st.borderRadius = '19px'
      st.fontSize = '14px'
      inner_px = '14px'
    else:
      st.width = '38px'
      st.minWidth = '38px'
      st.padding = '0'
      st.borderRadius = '50%'
      st.fontSize = '18px'
      inner_px = '18px'
    # Anvil wraps Link text in inner spans — size those too, or the
    # emoji keeps the theme's 13px chip font.
    try:
      spans = node.querySelectorAll('span')
      for i in range(spans.length):
        spans.item(i).style.fontSize = inner_px
    except Exception:
      pass

  def _mic_click(self, **event_args):
    """Toggle recording. Tap: record; tap again: stop + transcribe."""
    if self._recorder is None:
      try:
        stream = anvil.js.window.navigator.mediaDevices.getUserMedia(
          {'audio': True})
        rec = anvil.js.window.MediaRecorder(stream)
      except Exception as e:
        alert(f"Couldn't open the microphone: {e}\n\n"
              "Check the browser's mic permission for this site.",
              title="Microphone")
        return
      self._rec_chunks = []
      self._rec_stream = stream          # keep the stream alive
      self._rec_started = anvil.js.window.Date.now()
      rec.ondataavailable = self._rec_chunk
      rec.onstop = self._rec_done
      rec.start(250)                     # chunk every 250ms — reliable
      self._recorder = rec               # delivery on every browser
      self.lk_mic.text = "⏹ Stop"
      self._style_mic(recording=True)
      self.txt_question.placeholder = "Listening… tap ⏹ when done"
    else:
      self._recorder.stop()        # _rec_done fires next

  def _rec_chunk(self, event):
    self._rec_chunks.append(event.data)

  def _rec_done(self, event):
    rec, self._recorder = self._recorder, None
    self.lk_mic.text = "🎤"
    self._style_mic(recording=False)
    self.txt_question.placeholder = "Ask about stock, orders, transfers…"
    secs = (anvil.js.window.Date.now() - self._rec_started) / 1000.0
    try:
      for track in self._rec_stream.getTracks():
        track.stop()               # release the mic (red-dot indicator)
    except Exception:
      pass
    self._rec_stream = None
    if secs < 0.7:
      Notification("Too short — tap 🎤 once to start, speak, then tap "
                   "⏹ to stop.", style="warning", timeout=4).show()
      return
    if not self._rec_chunks:
      Notification("No audio came back from the recorder — try again.",
                   style="warning", timeout=4).show()
      return
    mime = rec.mimeType or 'audio/webm'
    blob = anvil.js.window.Blob(self._rec_chunks, {'type': mime})
    # Sanity: N seconds of real speech is tens of KB. A near-empty blob
    # means the browser captured SILENCE — almost always the wrong
    # input device selected in the browser (not the one Windows uses).
    if blob.size < max(3000, int(secs * 800)):
      alert(f"The recording was almost empty ({int(blob.size)} bytes "
            f"from {secs:.1f}s in {len(self._rec_chunks)} chunks) — the "
            "browser is capturing silence.\n\n"
            "Click the padlock icon by the address bar → Site settings "
            "→ Microphone, and check the browser is using the mic you "
            "tested (browsers keep their own device choice, separate "
            "from the Windows default).",
            title="Microphone captured silence")
      return
    media = anvil.js.to_media(blob)
    try:
      anvil.server.call('ta_submit_voice', self.session_id, media)
      Notification("Transcribing…", timeout=3).show()
    except Exception as e:
      alert(str(e), title="Couldn't send the recording")

  def request_speech(self, message_id, row):
    """Called by a message row's 🔊 chip. Tapping the row that is
    currently PLAYING stops it; tapping any other row stops the old
    audio and plays the new one — only one voice at a time."""
    if self._playing_row is row:
      self._stop_audio()
      return
    self._stop_audio()
    try:
      res = anvil.server.call('ta_request_speech',
                              self.session_id, message_id)
    except Exception as e:
      alert(str(e), title="Voice")
      return
    if res.get('file_id'):
      self._play_file(res['file_id'], row)
    else:
      self._speak_pending[int(message_id)] = row
      row.set_speak_busy()

  def _stop_audio(self):
    audio, self._audio = self._audio, None
    if audio is not None:
      try:
        audio.pause()
      except Exception:
        pass
    row, self._playing_row = self._playing_row, None
    if row is not None:
      try:
        row.set_speak_ready()
      except Exception:
        pass

  def _audio_ended(self, event):
    self._stop_audio()

  def _play_file(self, file_id, row=None):
    try:
      media = anvil.server.call('ta_get_file', file_id)
      self._stop_audio()
      if self._temp_url is not None:
        try:
          self._temp_url.revoke()
        except Exception:
          pass
      self._temp_url = anvil.media.TempUrl(media)
      audio = anvil.js.window.Audio(self._temp_url.url)
      audio.onended = self._audio_ended
      audio.play()
      self._audio = audio
      self._playing_row = row
      if row is not None:
        row.set_speak_playing()
    except Exception as e:
      Notification(f"Couldn't play the audio: {e}",
                   style="warning", timeout=4).show()

  def _handle_voice_row(self, row):
    """Intercept voice-protocol rows from the poll; return True when
    the row is internal and must NOT render in the thread."""
    role = row['role']
    if role in ('voice_req', 'stt_req'):
      return True
    if role == 'stt':
      self.txt_question.text = row['content']
      self.txt_question.focus()
      Notification("Transcribed — check it and tap ↑ to send.",
                   timeout=4).show()
      return True
    if role == 'voice':
      try:
        target = int(row['content'])
      except (TypeError, ValueError):
        target = None
      pending = self._speak_pending.pop(target, None)
      if pending is not None:
        pending.set_speak_ready()
      if row['file_id']:
        self._play_file(row['file_id'], pending)
      return True
    return False

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
      appended = False
      for row in new_rows:
        self.last_message_id = max(self.last_message_id, row['message_id'])
        if self._handle_voice_row(row):
          continue
        if row['role'] in ('assistant', 'error'):
          # The turn is over: clear its transient status lines.
          self.items = [i for i in self.items if i['role'] != 'status']
        self.items.append(row)
        appended = True
      if appended:
        self.rp_messages.items = list(self.items)
        # Keep the newest message in view.
        panels = self.rp_messages.get_components()
        if panels:
          panels[-1].scroll_into_view()

    self.lbl_busy.visible = update['busy']