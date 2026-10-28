/**
 * MySmartHome (Energetika a Přehled) — Unified Animated Interactive Cockpit Card
 * Optimized for 16:10 Widescreen Wall Tablet (<WALL_TABLET_HOST>) & Responsive Mobile View
 */
class SmartHomeCockpitCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._hass = null;
    this._config = {};
    this._rendered = false;
    this._alwaysOn = localStorage.getItem("mysmarthome_always_on_jeffrey") === "true";
    this._useNestSpeaker = localStorage.getItem("mysmarthome_use_nest_speaker") === "true";
    this._listeningCommand = false;
    this._recognition = null;
    this._lastReplyText = "Řekněte „Hey Jeffrey“ (při zapnutém Always-On) nebo klepněte na mikrofon a zeptejte se česky.";
    this._voiceStatus = this._alwaysOn ? "Always-On aktivní: Čekám na „Hey Jeffrey“…" : "Hlasový asistent připraven (Gemini 3.8 Live)";
    this._isProcessing = false;
    this._clockTimer = null;
  }

  setConfig(config) {
    this._config = config || {};
  }

  getCardSize() {
    return 10;
  }

  connectedCallback() {
    if (!this._clockTimer) {
      this._clockTimer = setInterval(() => this._updateClock(), 1000);
    }
    if (this._alwaysOn) {
      setTimeout(() => this._startAlwaysOnRecognition(), 1200);
    }
  }

  disconnectedCallback() {
    if (this._clockTimer) {
      clearInterval(this._clockTimer);
      this._clockTimer = null;
    }
    this._stopRecognition();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._rendered) {
      this._renderShell();
      this._rendered = true;
    }
    this._updateContent();
  }

  _openMoreInfo(entityId) {
    if (!entityId || !this._hass) return;
    const ev = new Event("hass-more-info", { bubbles: true, composed: true });
    ev.detail = { entityId };
    this.dispatchEvent(ev);
  }

  _playBeep(freq = 660, durationMs = 140) {
    try {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      const ctx = new Ctx();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.setValueAtTime(freq, ctx.currentTime);
      gain.gain.setValueAtTime(0.08, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + durationMs / 1000);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start();
      osc.stop(ctx.currentTime + durationMs / 1000);
    } catch (e) {
      // Ignore audio context restrictions
    }
  }

  _playGeminiWav() {
    const audioEl = this.shadowRoot.getElementById("gemini-audio-player");
    if (!audioEl) return;
    audioEl.src = `/local/gemini_live_reply.wav?t=${Date.now()}`;
    audioEl.play().catch(() => {});
  }

  async _sendToGemini(text) {
    const cleanText = (text || "").trim();
    if (!cleanText || !this._hass || this._isProcessing) return;
    this._isProcessing = true;
    this._voiceStatus = `Zpracovávám v Gemini 3.8 Live: „${cleanText}“…`;
    this._updateVoiceUI();

    const inputEl = this.shadowRoot.getElementById("gemini-input");
    if (inputEl) inputEl.value = cleanText;

    try {
      // Update input_text helper in HA for history visibility
      this._hass.callService("input_text", "set_value", {
        entity_id: "input_text.dotaz_pro_gemini",
        value: cleanText.slice(0, 250),
      }).catch(() => {});

      const resp = await this._hass.callWS({
        type: "call_service",
        domain: "conversation",
        service: "process",
        service_data: {
          text: cleanText,
          language: "cs",
          agent_id: "conversation.google_generative_ai",
        },
        return_response: true,
      });

      const speech =
        resp?.response?.response?.speech?.plain?.speech ||
        resp?.response?.speech?.plain?.speech ||
        "Příkaz byl proveden.";
      this._lastReplyText = speech;
      this._voiceStatus = this._alwaysOn
        ? "Odpověď přijata • Čekám na „Hey Jeffrey“…"
        : "Odpověď z Gemini 3.8 Live přijata";
      this._isProcessing = false;
      this._updateVoiceUI();

      // Play native 24kHz Gemini 3.8 Live audio on tablet/browser
      this._playGeminiWav();

      // Optionally also play on Google Nest speakers if toggled on
      if (this._useNestSpeaker) {
        this._hass.callService("media_player", "play_media", {
          entity_id: ["media_player.obyvak", "media_player.loznice"],
          media_content_id: `http://<HA_SERVER_HOST>:8123/local/gemini_live_reply.wav?t=${Date.now()}`,
          media_content_type: "audio/wav",
        }).catch(() => {});
      }
    } catch (err) {
      this._lastReplyText = `Chyba komunikace: ${err?.message || JSON.stringify(err)}`;
      this._voiceStatus = "Chyba při zpracování dotazu";
      this._isProcessing = false;
      this._updateVoiceUI();
    }
  }

  _toggleAlwaysOn() {
    this._alwaysOn = !this._alwaysOn;
    localStorage.setItem("mysmarthome_always_on_jeffrey", this._alwaysOn ? "true" : "false");
    if (this._alwaysOn) {
      this._voiceStatus = "Always-On aktivní: Řekněte „Hey Jeffrey“…";
      this._playBeep(740, 120);
      this._startAlwaysOnRecognition();
    } else {
      this._listeningCommand = false;
      this._voiceStatus = "Always-On vypnuto (klepněte na mikrofon pro dotaz)";
      this._stopRecognition();
    }
    this._updateVoiceUI();
  }

  _toggleNestSpeaker() {
    this._useNestSpeaker = !this._useNestSpeaker;
    localStorage.setItem("mysmarthome_use_nest_speaker", this._useNestSpeaker ? "true" : "false");
    this._updateVoiceUI();
  }

  _triggerPushToTalk() {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRec) {
      this._voiceStatus = "Prohlížeč nepodporuje Web Speech API (vyžaduje HTTPS nebo Chrome/Companion App).";
      this._updateVoiceUI();
      return;
    }
    this._listeningCommand = true;
    this._playBeep(880, 140);
    this._voiceStatus = "🟢 Poslouchám váš český příkaz…";
    this._updateVoiceUI();
    if (!this._recognition) {
      this._startAlwaysOnRecognition(true);
    }
  }

  _stopRecognition() {
    if (this._recognition) {
      try {
        this._recognition.onend = null;
        this._recognition.stop();
      } catch (e) {}
      this._recognition = null;
    }
  }

  _startAlwaysOnRecognition(singleShot = false) {
    const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRec) {
      this._voiceStatus = "Hlasové buzení v prohlížeči vyžaduje povolený mikrofon (nebo HA Companion App).";
      this._updateVoiceUI();
      return;
    }
    this._stopRecognition();

    try {
      const rec = new SpeechRec();
      rec.lang = "cs-CZ";
      rec.continuous = true;
      rec.interimResults = false;

      rec.onresult = (event) => {
        for (let i = event.resultIndex; i < event.results.length; i++) {
          if (!event.results[i].isFinal) continue;
          const raw = (event.results[i][0].transcript || "").trim();
          if (!raw) continue;
          const lower = raw.toLowerCase();

          const wakePatterns = [
            "hey jeffrey",
            "hej jeffrey",
            "hey jefrey",
            "hej jefrey",
            "hey džefry",
            "hej džefry",
            "jeffrey",
            "jefrey",
            "džefry",
            "hey jarvis",
            "hej jarvis",
          ];

          if (this._listeningCommand) {
            this._listeningCommand = false;
            this._playBeep(520, 100);
            this._sendToGemini(raw);
            return;
          }

          let matchedWake = null;
          for (const pat of wakePatterns) {
            if (lower.includes(pat)) {
              matchedWake = pat;
              break;
            }
          }

          if (matchedWake) {
            const idx = lower.indexOf(matchedWake);
            const afterCommand = raw.slice(idx + matchedWake.length).replace(/^[,.\s:-]+/, "").trim();
            if (afterCommand.length >= 3) {
              this._playBeep(880, 120);
              this._sendToGemini(afterCommand);
            } else {
              this._listeningCommand = true;
              this._playBeep(880, 150);
              this._voiceStatus = "🟢 Slyším „Hey Jeffrey“! Řekněte příkaz…";
              this._updateVoiceUI();
            }
          }
        }
      };

      rec.onerror = () => {};
      rec.onend = () => {
        if (this._alwaysOn && !singleShot) {
          setTimeout(() => {
            if (this._alwaysOn) this._startAlwaysOnRecognition();
          }, 600);
        }
      };

      rec.start();
      this._recognition = rec;
    } catch (e) {
      // Ignore start race conditions
    }
  }

  _updateClock() {
    if (!this.shadowRoot) return;
    const el = this.shadowRoot.getElementById("live-clock");
    if (!el) return;
    const now = new Date();
    const days = ["Neděle", "Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota"];
    const dName = days[now.getDay()];
    const timeStr = now.toLocaleTimeString("cs-CZ", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    const dateStr = now.toLocaleDateString("cs-CZ", { day: "numeric", month: "numeric", year: "numeric" });
    el.textContent = `${dName} ${dateStr} • ${timeStr}`;
  }

  _updateVoiceUI() {
    if (!this.shadowRoot) return;
    const statusEl = this.shadowRoot.getElementById("voice-status-text");
    const replyEl = this.shadowRoot.getElementById("voice-reply-text");
    const alwaysBtn = this.shadowRoot.getElementById("btn-always-on");
    const nestBtn = this.shadowRoot.getElementById("btn-nest-route");
    const micBtn = this.shadowRoot.getElementById("btn-ptt-mic");

    if (statusEl) statusEl.textContent = this._voiceStatus;
    if (replyEl) replyEl.textContent = this._lastReplyText;
    if (alwaysBtn) {
      alwaysBtn.className = `pill-btn ${this._alwaysOn ? "active-emerald" : ""}`;
      alwaysBtn.innerHTML = this._alwaysOn
        ? `<span class="pulse-dot"></span> Always-On „Hey Jeffrey“: ZAP`
        : `🎙️ Always-On „Hey Jeffrey“: VYP`;
    }
    if (nestBtn) {
      nestBtn.className = `pill-btn ${this._useNestSpeaker ? "active-cyan" : ""}`;
      nestBtn.innerHTML = this._useNestSpeaker
        ? `🔊 Google Nest: ZAP`
        : `🔈 Jen tento displej`;
    }
    if (micBtn) {
      micBtn.className = `mic-trigger-btn ${this._listeningCommand ? "listening" : ""}`;
    }
  }

  _weatherInfo(code) {
    const c = Number(code ?? 0);
    if (c === 0) return { label: "Jasno", icon: "☀️" };
    if (c === 1 || c === 2) return { label: "Polojasno", icon: "⛅" };
    if (c === 3) return { label: "Zataženo", icon: "☁️" };
    if (c === 45 || c === 48) return { label: "Mlha", icon: "🌫️" };
    if (c >= 51 && c <= 67) return { label: "Déšť / Mrholení", icon: "🌧️" };
    if (c >= 71 && c <= 77) return { label: "Sněžení", icon: "❄️" };
    if (c >= 80 && c <= 82) return { label: "Přeháňky", icon: "🌦️" };
    if (c >= 95) return { label: "Bouřky", icon: "⛈️" };
    return { label: "Proměnlivo", icon: "🌤️" };
  }

  _renderShell() {
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
          color: #f8fafc;
          box-sizing: border-box;
          padding: 12px 16px;
        }
        * {
          box-sizing: border-box;
        }
        .cockpit-shell {
          background: radial-gradient(circle at 18% 15%, rgba(16, 185, 129, 0.10), transparent 42%),
                      radial-gradient(circle at 85% 20%, rgba(56, 189, 248, 0.10), transparent 45%),
                      linear-gradient(155deg, #0b1120 0%, #0f172a 55%, #090d16 100%);
          border: 1px solid rgba(148, 163, 184, 0.18);
          border-radius: 26px;
          padding: 20px 24px;
          box-shadow: 0 24px 60px rgba(0, 0, 0, 0.55), inset 0 1px 0 rgba(255, 255, 255, 0.08);
          display: flex;
          flex-direction: column;
          gap: 18px;
          min-height: calc(100vh - 96px);
        }

        /* Header Bar */
        .cockpit-header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          flex-wrap: wrap;
          gap: 12px;
          padding-bottom: 12px;
          border-bottom: 1px solid rgba(148, 163, 184, 0.14);
        }
        .title-group {
          display: flex;
          align-items: center;
          gap: 14px;
        }
        .brand-orb {
          width: 44px;
          height: 44px;
          border-radius: 14px;
          background: linear-gradient(135deg, #10b981, #0ea5e9);
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 22px;
          box-shadow: 0 0 20px rgba(16, 185, 129, 0.4);
        }
        .title-text h1 {
          margin: 0;
          font-size: 1.35rem;
          font-weight: 800;
          letter-spacing: -0.02em;
          background: linear-gradient(90deg, #ffffff, #cbd5e1);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
        }
        .title-text .subtitle {
          font-size: 0.8rem;
          color: #94a3b8;
          margin-top: 2px;
        }
        .header-badges {
          display: flex;
          align-items: center;
          gap: 10px;
          flex-wrap: wrap;
        }
        .status-chip {
          background: rgba(30, 41, 59, 0.75);
          border: 1px solid rgba(148, 163, 184, 0.2);
          border-radius: 999px;
          padding: 6px 14px;
          font-size: 0.78rem;
          font-weight: 600;
          color: #e2e8f0;
          display: inline-flex;
          align-items: center;
          gap: 7px;
          cursor: pointer;
          transition: all 0.2s ease;
        }
        .status-chip:hover {
          border-color: rgba(56, 189, 248, 0.5);
          transform: translateY(-1px);
        }
        .dot-online {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: #10b981;
          box-shadow: 0 0 10px #10b981;
        }

        /* Main 16:10 Widescreen Grid */
        .cockpit-grid {
          display: grid;
          grid-template-columns: 1.35fr 1fr;
          gap: 20px;
          flex: 1;
          align-items: stretch;
        }

        .glass-panel {
          background: rgba(15, 23, 42, 0.68);
          border: 1px solid rgba(148, 163, 184, 0.15);
          border-radius: 22px;
          padding: 18px 20px;
          display: flex;
          flex-direction: column;
          justify-content: space-between;
          gap: 14px;
          backdrop-filter: blur(16px);
        }

        .panel-heading {
          display: flex;
          align-items: center;
          justify-content: space-between;
          font-size: 0.85rem;
          text-transform: uppercase;
          letter-spacing: 0.08em;
          color: #94a3b8;
          font-weight: 700;
        }
        .panel-heading span.highlight {
          color: #38bdf8;
          font-weight: 600;
          text-transform: none;
          letter-spacing: 0;
        }

        /* Animated SVG Energy Hub */
        .energy-svg-wrap {
          width: 100%;
          position: relative;
          display: flex;
          align-items: center;
          justify-content: center;
        }
        svg.energy-flow-svg {
          width: 100%;
          height: auto;
          max-height: 390px;
          overflow: visible;
        }
        .flow-track {
          fill: none;
          stroke: rgba(148, 163, 184, 0.18);
          stroke-width: 3.5;
          stroke-linecap: round;
        }
        .flow-active {
          fill: none;
          stroke-width: 4;
          stroke-linecap: round;
          stroke-dasharray: 10 14;
          animation: flowDash 1.4s linear infinite;
          opacity: 0;
          transition: opacity 0.4s ease;
        }
        .flow-active.active {
          opacity: 1;
        }
        .flow-active.reverse {
          animation-direction: reverse;
        }
        @keyframes flowDash {
          to {
            stroke-dashoffset: -48;
          }
        }
        .node-box {
          cursor: pointer;
          transition: transform 0.2s ease;
        }
        .node-box:hover {
          filter: brightness(1.15);
        }

        /* Integrated 3-Phase Matrix (from Eastron & Deye) */
        .phases-strip {
          background: rgba(9, 14, 26, 0.72);
          border: 1px solid rgba(148, 163, 184, 0.14);
          border-radius: 16px;
          padding: 12px 16px;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .phases-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          font-size: 0.78rem;
          color: #94a3b8;
          font-weight: 600;
        }
        .phases-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 12px;
        }
        .phase-card {
          background: rgba(30, 41, 59, 0.55);
          border: 1px solid rgba(148, 163, 184, 0.14);
          border-radius: 12px;
          padding: 9px 12px;
          cursor: pointer;
          transition: border-color 0.2s ease;
        }
        .phase-card:hover {
          border-color: rgba(56, 189, 248, 0.45);
        }
        .phase-top {
          display: flex;
          justify-content: space-between;
          align-items: baseline;
          margin-bottom: 4px;
        }
        .phase-name {
          font-size: 0.76rem;
          font-weight: 700;
          color: #cbd5e1;
        }
        .phase-volt {
          font-size: 0.72rem;
          color: #64748b;
          font-variant-numeric: tabular-nums;
        }
        .phase-watt {
          font-size: 1.05rem;
          font-weight: 800;
          color: #f8fafc;
          font-variant-numeric: tabular-nums;
          margin-bottom: 6px;
        }
        .phase-bar-bg {
          width: 100%;
          height: 6px;
          background: rgba(15, 23, 42, 0.9);
          border-radius: 999px;
          overflow: hidden;
        }
        .phase-bar-fill {
          height: 100%;
          border-radius: 999px;
          transition: width 0.5s ease;
        }

        /* Right Column Stack */
        .right-column {
          display: flex;
          flex-direction: column;
          gap: 16px;
        }

        /* Weather & Solar Forecast Card */
        .weather-hero {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 14px;
          padding: 4px 0;
        }
        .weather-main {
          display: flex;
          align-items: center;
          gap: 14px;
        }
        .weather-emoji {
          font-size: 2.9rem;
          line-height: 1;
          filter: drop-shadow(0 4px 12px rgba(251, 191, 36, 0.25));
        }
        .weather-temp {
          font-size: 2.35rem;
          font-weight: 800;
          letter-spacing: -0.03em;
          line-height: 1;
          font-variant-numeric: tabular-nums;
        }
        .weather-desc {
          font-size: 0.82rem;
          color: #94a3b8;
          margin-top: 4px;
        }
        .solar-forecast-box {
          background: linear-gradient(135deg, rgba(245, 158, 11, 0.16), rgba(234, 88, 12, 0.08));
          border: 1px solid rgba(245, 158, 11, 0.35);
          border-radius: 14px;
          padding: 10px 14px;
          text-align: right;
          cursor: pointer;
        }
        .solar-forecast-box .sf-label {
          font-size: 0.72rem;
          text-transform: uppercase;
          letter-spacing: 0.06em;
          color: #fbbf24;
          font-weight: 700;
        }
        .solar-forecast-box .sf-val {
          font-size: 1.15rem;
          font-weight: 800;
          color: #fef3c7;
          margin-top: 2px;
          font-variant-numeric: tabular-nums;
        }
        .weather-metrics-grid {
          display: grid;
          grid-template-columns: repeat(4, 1fr);
          gap: 8px;
        }
        .w-pill {
          background: rgba(30, 41, 59, 0.55);
          border: 1px solid rgba(148, 163, 184, 0.12);
          border-radius: 12px;
          padding: 8px 10px;
          cursor: pointer;
        }
        .w-pill .wp-lbl {
          font-size: 0.68rem;
          color: #94a3b8;
        }
        .w-pill .wp-val {
          font-size: 0.9rem;
          font-weight: 700;
          color: #f1f5f9;
          margin-top: 2px;
          font-variant-numeric: tabular-nums;
        }
        .forecast-days-row {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 8px;
          padding-top: 4px;
        }
        .fday-item {
          background: rgba(9, 14, 26, 0.6);
          border: 1px solid rgba(148, 163, 184, 0.12);
          border-radius: 11px;
          padding: 7px 10px;
          display: flex;
          align-items: center;
          justify-content: space-between;
          font-size: 0.76rem;
        }
        .fday-name {
          font-weight: 700;
          color: #cbd5e1;
        }
        .fday-vals {
          color: #94a3b8;
          font-variant-numeric: tabular-nums;
        }
        .fday-vals strong {
          color: #f8fafc;
        }

        /* Gemini 3.8 Live & Always-On Hey Jeffrey Panel */
        .voice-panel {
          background: linear-gradient(145deg, rgba(15, 23, 42, 0.82), rgba(17, 24, 39, 0.9));
          border: 1px solid rgba(56, 189, 248, 0.25);
          border-radius: 20px;
          padding: 15px 18px;
          display: flex;
          flex-direction: column;
          gap: 10px;
        }
        .voice-top-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          flex-wrap: wrap;
        }
        .pill-btn {
          background: rgba(30, 41, 59, 0.85);
          border: 1px solid rgba(148, 163, 184, 0.25);
          color: #e2e8f0;
          border-radius: 999px;
          padding: 6px 12px;
          font-size: 0.75rem;
          font-weight: 700;
          cursor: pointer;
          display: inline-flex;
          align-items: center;
          gap: 6px;
          transition: all 0.2s ease;
        }
        .pill-btn.active-emerald {
          background: rgba(16, 185, 129, 0.18);
          border-color: #10b981;
          color: #6ee7b7;
          box-shadow: 0 0 14px rgba(16, 185, 129, 0.25);
        }
        .pill-btn.active-cyan {
          background: rgba(14, 165, 233, 0.18);
          border-color: #38bdf8;
          color: #7dd3fc;
        }
        .pulse-dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          background: #10b981;
          animation: pulseGlow 1.5s infinite;
        }
        @keyframes pulseGlow {
          0%, 100% { transform: scale(1); opacity: 1; }
          50% { transform: scale(1.4); opacity: 0.5; }
        }
        .voice-input-row {
          display: flex;
          gap: 8px;
          align-items: center;
        }
        .mic-trigger-btn {
          width: 40px;
          height: 40px;
          border-radius: 12px;
          border: 1px solid rgba(56, 189, 248, 0.4);
          background: linear-gradient(135deg, #0284c7, #2563eb);
          color: #fff;
          font-size: 1.1rem;
          cursor: pointer;
          display: flex;
          align-items: center;
          justify-content: center;
          flex-shrink: 0;
        }
        .mic-trigger-btn.listening {
          background: linear-gradient(135deg, #10b981, #059669);
          box-shadow: 0 0 18px rgba(16, 185, 129, 0.6);
        }
        .voice-text-input {
          flex: 1;
          background: rgba(9, 14, 26, 0.8);
          border: 1px solid rgba(148, 163, 184, 0.22);
          border-radius: 12px;
          padding: 9px 12px;
          color: #f8fafc;
          font-size: 0.83rem;
          outline: none;
        }
        .voice-text-input:focus {
          border-color: #38bdf8;
        }
        .voice-send-btn {
          background: #38bdf8;
          color: #0f172a;
          border: none;
          border-radius: 12px;
          padding: 9px 14px;
          font-size: 0.8rem;
          font-weight: 800;
          cursor: pointer;
        }
        .voice-reply-box {
          background: rgba(9, 14, 26, 0.65);
          border-left: 3px solid #38bdf8;
          border-radius: 8px;
          padding: 8px 12px;
          font-size: 0.8rem;
          color: #e2e8f0;
          line-height: 1.4;
          display: flex;
          justify-content: space-between;
          align-items: center;
          gap: 10px;
        }
        .replay-btn {
          background: rgba(56, 189, 248, 0.15);
          border: 1px solid rgba(56, 189, 248, 0.35);
          color: #7dd3fc;
          border-radius: 8px;
          padding: 4px 9px;
          font-size: 0.72rem;
          cursor: pointer;
          flex-shrink: 0;
        }

        /* Quick Home Controls Strip */
        .quick-controls-grid {
          display: grid;
          grid-template-columns: repeat(3, 1fr);
          gap: 10px;
        }
        .qc-tile {
          background: rgba(30, 41, 59, 0.55);
          border: 1px solid rgba(148, 163, 184, 0.15);
          border-radius: 14px;
          padding: 10px 12px;
          cursor: pointer;
          transition: all 0.2s ease;
          display: flex;
          flex-direction: column;
          gap: 3px;
        }
        .qc-tile:hover {
          border-color: rgba(16, 185, 129, 0.45);
          transform: translateY(-1px);
        }
        .qc-top {
          display: flex;
          align-items: center;
          justify-content: space-between;
          font-size: 0.74rem;
          color: #94a3b8;
          font-weight: 600;
        }
        .qc-val {
          font-size: 0.95rem;
          font-weight: 800;
          color: #f8fafc;
          font-variant-numeric: tabular-nums;
        }
        .qc-sub {
          font-size: 0.68rem;
          color: #64748b;
        }

        /* Mobile & Portrait Tablet Responsive Breakpoints */
        @media (max-width: 1024px) {
          :host {
            padding: 6px 8px;
          }
          .cockpit-shell {
            padding: 14px;
            border-radius: 20px;
            min-height: auto;
          }
          .cockpit-grid {
            grid-template-columns: 1fr;
          }
          .weather-metrics-grid {
            grid-template-columns: repeat(2, 1fr);
          }
          .quick-controls-grid {
            grid-template-columns: repeat(2, 1fr);
          }
          .phases-grid {
            grid-template-columns: 1fr;
          }
        }
      </style>

      <div class="cockpit-shell">
        <!-- Top Header -->
        <div class="cockpit-header">
          <div class="title-group">
            <div class="brand-orb">⚡</div>
            <div class="title-text">
              <h1>MySmartHome (Energetika a Přehled)</h1>
              <div class="subtitle" id="live-clock">Načítání času…</div>
            </div>
          </div>
          <div class="header-badges">
            <div class="status-chip" id="chip-inverter">
              <span class="dot-online"></span>
              <span id="hdr-inverter-state">Deye 15kW: Online</span>
            </div>
            <div class="status-chip" id="chip-grid-mode">
              <span>🛡️ Zero Export To CT • Eastron SDM630MCT</span>
            </div>
            <div class="status-chip" id="chip-heatsink">
              <span id="hdr-temp-text">🌡️ Střídač: -- °C</span>
            </div>
          </div>
        </div>

        <!-- Main 16:10 Widescreen Interactive Grid -->
        <div class="cockpit-grid">
          <!-- LEFT COLUMN: Interactive Animated Energy Flow + 3-Phase Eastron Breakdown -->
          <div class="glass-panel">
            <div class="panel-heading">
              <span>⚡ Interaktivní Tok Energie v Reálném Čase</span>
              <span class="highlight" id="energy-summary-badge">Odběr ze sítě • Baterie 0 %</span>
            </div>

            <div class="energy-svg-wrap">
              <svg class="energy-flow-svg" viewBox="0 0 760 420" xmlns="http://www.w3.org/2000/svg">
                <defs>
                  <linearGradient id="gradSolar" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stop-color="#f59e0b" />
                    <stop offset="100%" stop-color="#ea580c" />
                  </linearGradient>
                  <linearGradient id="gradGrid" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stop-color="#38bdf8" />
                    <stop offset="100%" stop-color="#818cf8" />
                  </linearGradient>
                  <linearGradient id="gradHome" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stop-color="#10b981" />
                    <stop offset="100%" stop-color="#34d399" />
                  </linearGradient>
                  <linearGradient id="gradBat" x1="0%" y1="0%" x2="0%" y2="100%">
                    <stop offset="0%" stop-color="#a855f7" />
                    <stop offset="100%" stop-color="#ec4899" />
                  </linearGradient>
                  <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
                    <feGaussianBlur stdDeviation="4" result="blur" />
                    <feComposite in="SourceGraphic" in2="blur" operator="over" />
                  </filter>
                </defs>

                <!-- Base Tracks -->
                <!-- Solar (top 380,95) -> Inverter (380,210) -->
                <path class="flow-track" d="M 380 115 L 380 165" />
                <!-- Grid (left 140,210) -> Inverter (380,210) -->
                <path class="flow-track" d="M 235 210 L 315 210" />
                <!-- Inverter (380,210) -> Home (right 620,210) -->
                <path class="flow-track" d="M 445 210 L 525 210" />
                <!-- Inverter (380,210) -> Battery (bottom 380,335) -->
                <path class="flow-track" d="M 380 255 L 380 300" />

                <!-- Animated Active Flow Paths -->
                <path id="flow-solar" class="flow-active" stroke="#fbbf24" filter="url(#glow)" d="M 380 115 L 380 165" />
                <path id="flow-grid" class="flow-active active" stroke="#38bdf8" filter="url(#glow)" d="M 235 210 L 315 210" />
                <path id="flow-home" class="flow-active active" stroke="#10b981" filter="url(#glow)" d="M 445 210 L 525 210" />
                <path id="flow-bat" class="flow-active" stroke="#c084fc" filter="url(#glow)" d="M 380 255 L 380 300" />

                <!-- TOP NODE: SOLAR FVE 15kW -->
                <g class="node-box" id="svg-node-solar" transform="translate(245, 12)">
                  <rect width="270" height="102" rx="18" fill="rgba(15, 23, 42, 0.92)" stroke="rgba(245, 158, 11, 0.55)" stroke-width="2" />
                  <circle cx="42" cy="51" r="24" fill="rgba(245, 158, 11, 0.18)" />
                  <text x="42" y="59" text-anchor="middle" font-size="24">☀️</text>
                  <text x="80" y="30" fill="#fbbf24" font-size="11" font-weight="700" letter-spacing="0.8">FVE ELEKTRÁRNA DEYE 15 kW</text>
                  <text id="svg-pv-total" x="80" y="58" fill="#ffffff" font-size="24" font-weight="800">0 W</text>
                  <text id="svg-pv-strings" x="80" y="77" fill="#cbd5e1" font-size="11" font-weight="600">PV1: 0 W • PV2: 0 W</text>
                  <text id="svg-pv-kwh" x="80" y="93" fill="#94a3b8" font-size="10.5">Dnes: 0.0 kWh • Celkem: 0.0 kWh</text>
                </g>

                <!-- CENTER HUB: DEYE INVERTER -->
                <g class="node-box" id="svg-node-inverter" transform="translate(315, 165)">
                  <rect width="130" height="90" rx="20" fill="rgba(30, 41, 59, 0.95)" stroke="rgba(148, 163, 184, 0.45)" stroke-width="2" />
                  <text x="65" y="28" text-anchor="middle" fill="#38bdf8" font-size="10" font-weight="800" letter-spacing="1">STŘÍDAČ DEYE</text>
                  <text x="65" y="52" text-anchor="middle" fill="#ffffff" font-size="15" font-weight="800">15kW 3-Fáze</text>
                  <text id="svg-inv-sub" x="65" y="72" text-anchor="middle" fill="#94a3b8" font-size="10.5">50.0 Hz • 29.1 °C</text>
                </g>

                <!-- LEFT NODE: GRID & EASTRON -->
                <g class="node-box" id="svg-node-grid" transform="translate(8, 152)">
                  <rect width="226" height="116" rx="18" fill="rgba(15, 23, 42, 0.92)" stroke="rgba(56, 189, 248, 0.55)" stroke-width="2" />
                  <circle cx="38" cy="58" r="22" fill="rgba(56, 189, 248, 0.18)" />
                  <text x="38" y="66" text-anchor="middle" font-size="22">🗼</text>
                  <text id="svg-grid-dir" x="72" y="29" fill="#38bdf8" font-size="11" font-weight="700" letter-spacing="0.6">ODBĚR ZE SÍTĚ (EASTRON)</text>
                  <text id="svg-grid-w" x="72" y="57" fill="#ffffff" font-size="23" font-weight="800">394 W</text>
                  <text id="svg-grid-buy" x="72" y="78" fill="#cbd5e1" font-size="11">Odběr celkem: 11.2 kWh</text>
                  <text id="svg-grid-sell" x="72" y="96" fill="#94a3b8" font-size="10.5">Přetoky celkem: 1.7 kWh</text>
                </g>

                <!-- RIGHT NODE: HOUSE CONSUMPTION -->
                <g class="node-box" id="svg-node-home" transform="translate(526, 152)">
                  <rect width="226" height="116" rx="18" fill="rgba(15, 23, 42, 0.92)" stroke="rgba(16, 185, 129, 0.55)" stroke-width="2" />
                  <circle cx="38" cy="58" r="22" fill="rgba(16, 185, 129, 0.18)" />
                  <text x="38" y="66" text-anchor="middle" font-size="22">🏠</text>
                  <text x="72" y="29" fill="#34d399" font-size="11" font-weight="700" letter-spacing="0.6">SPOTŘEBA DOMU CELKEM</text>
                  <text id="svg-home-w" x="72" y="57" fill="#ffffff" font-size="23" font-weight="800">394 W</text>
                  <text id="svg-home-day" x="72" y="78" fill="#cbd5e1" font-size="11">Spotřeba dnes: 9.6 kWh</text>
                  <text id="svg-home-total" x="72" y="96" fill="#94a3b8" font-size="10.5">Spotřeba celkem: 10.8 kWh</text>
                </g>

                <!-- BOTTOM NODE: BATTERY STORAGE (0% READY FOR FUTURE EXPANSION) -->
                <g class="node-box" id="svg-node-battery" transform="translate(230, 302)">
                  <rect width="300" height="108" rx="18" fill="rgba(15, 23, 42, 0.92)" stroke="rgba(192, 132, 252, 0.5)" stroke-width="2" />
                  <!-- Circular SOC Gauge -->
                  <circle cx="50" cy="54" r="28" fill="none" stroke="rgba(148, 163, 184, 0.2)" stroke-width="6" />
                  <circle id="svg-bat-ring" cx="50" cy="54" r="28" fill="none" stroke="#c084fc" stroke-width="6"
                          stroke-linecap="round" stroke-dasharray="176" stroke-dashoffset="176"
                          transform="rotate(-90 50 54)" />
                  <text id="svg-bat-soc" x="50" y="59" text-anchor="middle" fill="#ffffff" font-size="14" font-weight="800">0 %</text>

                  <text x="94" y="30" fill="#c084fc" font-size="11" font-weight="700" letter-spacing="0.6">BATERIOVÉ ÚLOŽIŠTĚ DEYE</text>
                  <text id="svg-bat-w" x="94" y="56" fill="#ffffff" font-size="21" font-weight="800">0 W • 0 % SOC</text>
                  <text id="svg-bat-status" x="94" y="76" fill="#cbd5e1" font-size="11">Stav: Nepřipojena (0 % — připraveno)</text>
                  <text id="svg-bat-volt" x="94" y="93" fill="#94a3b8" font-size="10.5">Napětí: 0.0 V • Připraveno pro budoucí baterii</text>
                </g>
              </svg>
            </div>

            <!-- Integrated 3-Phase Load & Voltage Strip (replaces separate Eastron widget) -->
            <div class="phases-strip">
              <div class="phases-header">
                <span>⚡ Detail zatížení a napětí jednotlivých fází (Měřák Eastron SDM630MCT V2)</span>
                <span id="phases-sum-txt">Součet fází: 394 W</span>
              </div>
              <div class="phases-grid">
                <div class="phase-card" id="card-phase-l1">
                  <div class="phase-top">
                    <span class="phase-name">Fáze L1</span>
                    <span class="phase-volt" id="val-v-l1">239.8 V</span>
                  </div>
                  <div class="phase-watt" id="val-w-l1">146 W</div>
                  <div class="phase-bar-bg">
                    <div class="phase-bar-fill" id="bar-l1" style="width: 35%; background: linear-gradient(90deg, #38bdf8, #0284c7);"></div>
                  </div>
                </div>
                <div class="phase-card" id="card-phase-l2">
                  <div class="phase-top">
                    <span class="phase-name">Fáze L2</span>
                    <span class="phase-volt" id="val-v-l2">239.9 V</span>
                  </div>
                  <div class="phase-watt" id="val-w-l2">173 W</div>
                  <div class="phase-bar-bg">
                    <div class="phase-bar-fill" id="bar-l2" style="width: 42%; background: linear-gradient(90deg, #10b981, #059669);"></div>
                  </div>
                </div>
                <div class="phase-card" id="card-phase-l3">
                  <div class="phase-top">
                    <span class="phase-name">Fáze L3</span>
                    <span class="phase-volt" id="val-v-l3">238.2 V</span>
                  </div>
                  <div class="phase-watt" id="val-w-l3">75 W</div>
                  <div class="phase-bar-bg">
                    <div class="phase-bar-fill" id="bar-l3" style="width: 23%; background: linear-gradient(90deg, #f59e0b, #d97706);"></div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <!-- RIGHT COLUMN: Integrated Weather MySmartHome + Always-On Hey Jeffrey + Quick Home Controls -->
          <div class="right-column">
            <!-- 1. Integrated Weather & Solar Forecast MySmartHome (100 00) -->
            <div class="glass-panel" id="panel-weather">
              <div class="panel-heading">
                <span>🌤️ Počasí a Solární Predikce MySmartHome (PSČ 100 00)</span>
                <span class="highlight" id="weather-minmax-badge">Min/Max: -- / -- °C</span>
              </div>

              <div class="weather-hero">
                <div class="weather-main" id="weather-main-click" style="cursor:pointer;">
                  <div class="weather-emoji" id="w-icon">🌤️</div>
                  <div>
                    <div class="weather-temp" id="w-temp">-- °C</div>
                    <div class="weather-desc" id="w-desc">Pocitově -- °C • MySmartHome</div>
                  </div>
                </div>
                <div class="solar-forecast-box" id="solar-forecast-click">
                  <div class="sf-label">☀️ Solární predikce FVE</div>
                  <div class="sf-val" id="w-fve-forecast">Dnes: -- kWh</div>
                  <div style="font-size:0.73rem; color:#fde68a; margin-top:2px;" id="w-fve-tomorrow">Zítra: -- kWh</div>
                </div>
              </div>

              <div class="weather-metrics-grid">
                <div class="w-pill" id="wp-irradiance">
                  <div class="wp-lbl">☀️ Sluneční osvit</div>
                  <div class="wp-val" id="val-irradiance">0 W/m²</div>
                </div>
                <div class="w-pill" id="wp-rain">
                  <div class="wp-lbl">🌧️ Srážky dnes</div>
                  <div class="wp-val" id="val-rain">0 % (0 mm)</div>
                </div>
                <div class="w-pill" id="wp-wind">
                  <div class="wp-lbl">💨 Vítr / Nárazy</div>
                  <div class="wp-val" id="val-wind">0 km/h</div>
                </div>
                <div class="w-pill" id="wp-hum">
                  <div class="wp-lbl">💧 Vlhkost / Tlak</div>
                  <div class="wp-val" id="val-hum">0 % • 1013 hPa</div>
                </div>
              </div>

              <div class="forecast-days-row" id="forecast-days-strip">
                <div class="fday-item"><span class="fday-name">Dnes</span><span class="fday-vals">--° / --°</span></div>
                <div class="fday-item"><span class="fday-name">Zítra</span><span class="fday-vals">--° / --°</span></div>
                <div class="fday-item"><span class="fday-name">Pozítří</span><span class="fday-vals">--° / --°</span></div>
              </div>
            </div>

            <!-- 2. Always-On "Hey Jeffrey" & Gemini 3.8 Live Voice Bar -->
            <div class="voice-panel">
              <div class="voice-top-row">
                <div style="font-size:0.78rem; font-weight:800; color:#38bdf8; letter-spacing:0.04em;">
                  🤖 GEMINI 3.8 LIVE ASISTENT (<span id="voice-status-text">Připraven</span>)
                </div>
                <div style="display:flex; gap:6px; flex-wrap:wrap;">
                  <button class="pill-btn" id="btn-always-on">🎙️ Always-On „Hey Jeffrey“</button>
                  <button class="pill-btn" id="btn-nest-route">🔈 Jen tento displej</button>
                </div>
              </div>

              <div class="voice-input-row">
                <button class="mic-trigger-btn" id="btn-ptt-mic" title="Mluvit česky hned">🎤</button>
                <input type="text" class="voice-text-input" id="gemini-input"
                       placeholder="Řekněte „Hey Jeffrey“ nebo napište příkaz (např. Jaká je spotřeba domu?)…" />
                <button class="voice-send-btn" id="btn-gemini-send">Odeslat</button>
              </div>

              <div class="voice-reply-box">
                <div id="voice-reply-text" style="flex:1;">Řekněte „Hey Jeffrey“ (při zapnutém Always-On) nebo klepněte na mikrofon.</div>
                <button class="replay-btn" id="btn-replay-wav">▶️ Přehrát hlas</button>
              </div>
              <audio id="gemini-audio-player" style="display:none;"></audio>
            </div>

            <!-- 3. Quick Smart Home Status & Controls (Loxone, Vacuum X10+, TV) -->
            <div class="glass-panel" style="padding: 14px 18px;">
              <div class="panel-heading">
                <span>🏡 Rychlé Ovládání Domu (Loxone • Xiaomi X10+ • TV)</span>
                <span class="highlight">Klepnutím otevřete detail</span>
              </div>
              <div class="quick-controls-grid">
                <div class="qc-tile" id="qc-obyvak">
                  <div class="qc-top"><span>🏠 Vnitřní klima (Loxone)</span><span>🌡️</span></div>
                  <div class="qc-val" id="val-qc-obyvak">20.1 °C • 50 %</div>
                  <div class="qc-sub" id="sub-qc-obyvak">CO₂: 468 ppm</div>
                </div>
                <div class="qc-tile" id="qc-loznice">
                  <div class="qc-top"><span>💡 Osvětlení (Loxone)</span><span>✨</span></div>
                  <div class="qc-val" id="val-qc-loznice">1 zapnuto</div>
                  <div class="qc-sub" id="sub-qc-loznice">Kuchyně, koupelna a pokoje</div>
                </div>
                <div class="qc-tile" id="qc-vacuum">
                  <div class="qc-top"><span>🤖 Xiaomi X10+</span><span>🧹</span></div>
                  <div class="qc-val" id="val-qc-vacuum">V doku</div>
                  <div class="qc-sub">Klepněte pro ovládání</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    `;

    // Wire interactive event listeners once
    const sr = this.shadowRoot;
    sr.getElementById("chip-inverter")?.addEventListener("click", () => this._openMoreInfo("sensor.stav_stridace_deye"));
    sr.getElementById("chip-grid-mode")?.addEventListener("click", () => this._openMoreInfo("sensor.deye_15kw_telemetry"));
    sr.getElementById("chip-heatsink")?.addEventListener("click", () => this._openMoreInfo("sensor.teplota_chladice_stridace"));

    sr.getElementById("svg-node-solar")?.addEventListener("click", () => this._openMoreInfo("sensor.fve_celkovy_vykon"));
    sr.getElementById("svg-node-inverter")?.addEventListener("click", () => this._openMoreInfo("sensor.deye_15kw_telemetry"));
    sr.getElementById("svg-node-grid")?.addEventListener("click", () => this._openMoreInfo("sensor.sit_aktualni_odber_a_pretok"));
    sr.getElementById("svg-node-home")?.addEventListener("click", () => this._openMoreInfo("sensor.spotreba_domu_celkem"));
    sr.getElementById("svg-node-battery")?.addEventListener("click", () => this._openMoreInfo("sensor.baterie_stav_nabiti"));

    sr.getElementById("card-phase-l1")?.addEventListener("click", () => this._openMoreInfo("sensor.spotreba_domu_faze_l1"));
    sr.getElementById("card-phase-l2")?.addEventListener("click", () => this._openMoreInfo("sensor.spotreba_domu_faze_l2"));
    sr.getElementById("card-phase-l3")?.addEventListener("click", () => this._openMoreInfo("sensor.spotreba_domu_faze_l3"));

    sr.getElementById("weather-main-click")?.addEventListener("click", () => this._openMoreInfo("sensor.mysmarthome_venkovni_teplota"));
    sr.getElementById("solar-forecast-click")?.addEventListener("click", () => this._openMoreInfo("sensor.mysmarthome_predikce_fve_dnes"));
    sr.getElementById("wp-irradiance")?.addEventListener("click", () => this._openMoreInfo("sensor.mysmarthome_slunecni_osvit"));
    sr.getElementById("wp-rain")?.addEventListener("click", () => this._openMoreInfo("sensor.mysmarthome_pravdepodobnost_srazek_dnes"));
    sr.getElementById("wp-wind")?.addEventListener("click", () => this._openMoreInfo("sensor.mysmarthome_rychlost_vetru"));
    sr.getElementById("wp-hum")?.addEventListener("click", () => this._openMoreInfo("sensor.mysmarthome_venkovni_vlhkost"));

    sr.getElementById("qc-obyvak")?.addEventListener("click", () => this._openMoreInfo("sensor.teplota"));
    sr.getElementById("qc-loznice")?.addEventListener("click", () => this._openMoreInfo("light.ovladani_osvetleni_kuchyne"));
    sr.getElementById("qc-vacuum")?.addEventListener("click", () => this._openMoreInfo("vacuum.smart_robot_vacuum"));

    sr.getElementById("btn-always-on")?.addEventListener("click", () => this._toggleAlwaysOn());
    sr.getElementById("btn-nest-route")?.addEventListener("click", () => this._toggleNestSpeaker());
    sr.getElementById("btn-ptt-mic")?.addEventListener("click", () => this._triggerPushToTalk());
    sr.getElementById("btn-replay-wav")?.addEventListener("click", () => this._playGeminiWav());

    const inputEl = sr.getElementById("gemini-input");
    sr.getElementById("btn-gemini-send")?.addEventListener("click", () => {
      if (inputEl && inputEl.value.trim()) {
        this._sendToGemini(inputEl.value.trim());
      }
    });
    inputEl?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && inputEl.value.trim()) {
        this._sendToGemini(inputEl.value.trim());
      }
    });

    this._updateClock();
    this._updateVoiceUI();
  }

  _updateContent() {
    if (!this._hass || !this.shadowRoot) return;
    const sr = this.shadowRoot;
    const states = this._hass.states;

    const deyeAttr = states["sensor.deye_15kw_telemetry"]?.attributes || {};
    const num = (v, def = 0) => {
      const n = Number(v);
      return Number.isFinite(n) ? n : def;
    };

    const pvTotal = num(deyeAttr.pv_total_power_w, num(states["sensor.fve_celkovy_vykon"]?.state, 0));
    const pv1 = num(deyeAttr.pv1_power_w, num(states["sensor.fve_string_pv1_vykon"]?.state, 0));
    const pv2 = num(deyeAttr.pv2_power_w, num(states["sensor.fve_string_pv2_vykon"]?.state, 0));
    const dayPv = num(deyeAttr.day_pv_kwh, num(states["sensor.fve_vyroba_dnes"]?.state, 0));
    const totPv = num(deyeAttr.total_pv_kwh, num(states["sensor.fve_vyroba_celkem"]?.state, 0));

    const l1W = num(deyeAttr.load_l1_w, num(states["sensor.spotreba_domu_faze_l1"]?.state, 0));
    const l2W = num(deyeAttr.load_l2_w, num(states["sensor.spotreba_domu_faze_l2"]?.state, 0));
    const l3W = num(deyeAttr.load_l3_w, num(states["sensor.spotreba_domu_faze_l3"]?.state, 0));
    const homeW = num(deyeAttr.load_total_w, num(states["sensor.spotreba_domu_celkem"]?.state, l1W + l2W + l3W));
    const dayLoad = num(deyeAttr.day_load_kwh, 0);
    const totLoad = num(deyeAttr.total_load_kwh, 0);

    const gridNetW = num(deyeAttr.grid_power_total_w, l1W + l2W + l3W);
    const totBuy = num(deyeAttr.total_grid_buy_kwh, num(states["sensor.odber_ze_site_celkem"]?.state, 0));
    const totSell = num(deyeAttr.total_grid_sell_kwh, num(states["sensor.pretoky_do_site_celkem"]?.state, 0));
    const dayBuy = num(deyeAttr.day_grid_buy_kwh, 0);
    const daySell = num(deyeAttr.day_grid_sell_kwh, 0);

    const vL1 = num(deyeAttr.grid_v_l1, num(states["sensor.napeti_site_l1"]?.state, 230));
    const vL2 = num(deyeAttr.grid_v_l2, num(states["sensor.napeti_site_l2"]?.state, 230));
    const vL3 = num(deyeAttr.grid_v_l3, num(states["sensor.napeti_site_l3"]?.state, 230));
    const freqHz = num(deyeAttr.grid_freq_hz, 50.0);
    const hsTemp = num(deyeAttr.heatsink_temp_c, num(states["sensor.teplota_chladice_stridace"]?.state, 0));
    const runText = deyeAttr.run_state_text || states["sensor.stav_stridace_deye"]?.state || "Online";

    const batSoc = num(deyeAttr.battery_soc_pct, num(states["sensor.baterie_stav_nabiti"]?.state, 0));
    const batW = num(deyeAttr.battery_power_w, num(states["sensor.baterie_aktualni_vykon"]?.state, 0));
    const batV = num(deyeAttr.battery_voltage_v, num(states["sensor.baterie_napeti"]?.state, 0));
    const batStatus = deyeAttr.battery_status || states["sensor.baterie_stav"]?.state || "Nepřipojena (0 % — připraveno)";

    // Update Header
    const setTxt = (id, txt) => {
      const el = sr.getElementById(id);
      if (el) el.textContent = txt;
    };
    setTxt("hdr-inverter-state", `Deye 15kW: ${runText}`);
    setTxt("hdr-temp-text", `🌡️ Chladič: ${hsTemp.toFixed(1)} °C • ${freqHz.toFixed(2)} Hz`);

    const flowLabel = gridNetW > 20
      ? `Odběr ze sítě ${gridNetW} W`
      : (gridNetW < -20 ? `Přetok do sítě ${Math.abs(gridNetW)} W` : `Nulový odběr ze sítě`);
    setTxt("energy-summary-badge", `${flowLabel} • Baterie ${batSoc} %`);

    // Update SVG Nodes
    setTxt("svg-pv-total", `${pvTotal} W`);
    setTxt("svg-pv-strings", `PV1: ${pv1} W • PV2: ${pv2} W`);
    setTxt("svg-pv-kwh", `Dnes: ${dayPv.toFixed(1)} kWh • Celkem: ${totPv.toFixed(1)} kWh`);
    setTxt("svg-inv-sub", `${freqHz.toFixed(1)} Hz • ${hsTemp.toFixed(1)} °C`);

    if (gridNetW >= 0) {
      setTxt("svg-grid-dir", "ODBĚR ZE SÍTĚ (EASTRON)");
      setTxt("svg-grid-w", `${gridNetW} W`);
    } else {
      setTxt("svg-grid-dir", "PŘETOK DO SÍTĚ (EXPORT)");
      setTxt("svg-grid-w", `${Math.abs(gridNetW)} W`);
    }
    setTxt("svg-grid-buy", `Odběr: dnes ${dayBuy.toFixed(1)} / celk. ${totBuy.toFixed(1)} kWh`);
    setTxt("svg-grid-sell", `Přetok: dnes ${daySell.toFixed(1)} / celk. ${totSell.toFixed(1)} kWh`);

    setTxt("svg-home-w", `${homeW} W`);
    setTxt("svg-home-day", `Spotřeba dnes: ${dayLoad.toFixed(1)} kWh`);
    setTxt("svg-home-total", `Spotřeba celkem: ${totLoad.toFixed(1)} kWh`);

    setTxt("svg-bat-soc", `${batSoc} %`);
    setTxt("svg-bat-w", `${batW} W • ${batSoc} % SOC`);
    setTxt("svg-bat-status", `Stav: ${batStatus}`);
    setTxt("svg-bat-volt", batV > 0 ? `Napětí baterie: ${batV.toFixed(1)} V` : `Napětí: 0.0 V • Připraveno pro budoucí baterii`);

    const batRing = sr.getElementById("svg-bat-ring");
    if (batRing) {
      const circum = 176;
      const offset = circum - (Math.max(0, Math.min(100, batSoc)) / 100) * circum;
      batRing.setAttribute("stroke-dashoffset", String(offset));
    }

    // Update SVG Animated Flow Lines
    const fSolar = sr.getElementById("flow-solar");
    const fGrid = sr.getElementById("flow-grid");
    const fHome = sr.getElementById("flow-home");
    const fBat = sr.getElementById("flow-bat");

    if (fSolar) fSolar.setAttribute("class", `flow-active ${pvTotal > 15 ? "active" : ""}`);
    if (fGrid) {
      const gridActive = Math.abs(gridNetW) > 15;
      const gridRev = gridNetW < -15;
      fGrid.setAttribute("class", `flow-active ${gridActive ? "active" : ""} ${gridRev ? "reverse" : ""}`);
    }
    if (fHome) fHome.setAttribute("class", `flow-active ${homeW > 15 ? "active" : ""}`);
    if (fBat) {
      const batActive = Math.abs(batW) > 15;
      const batRev = batW > 15; // discharging to inverter
      fBat.setAttribute("class", `flow-active ${batActive ? "active" : ""} ${batRev ? "reverse" : ""}`);
    }

    // Update 3-Phase Strip
    const sumPhases = Math.max(1, Math.abs(l1W) + Math.abs(l2W) + Math.abs(l3W));
    setTxt("phases-sum-txt", `Součet fází: ${l1W + l2W + l3W} W (L1+L2+L3)`);
    setTxt("val-v-l1", `${vL1.toFixed(1)} V`);
    setTxt("val-v-l2", `${vL2.toFixed(1)} V`);
    setTxt("val-v-l3", `${vL3.toFixed(1)} V`);

    const p1Pct = Math.round((Math.abs(l1W) / sumPhases) * 100);
    const p2Pct = Math.round((Math.abs(l2W) / sumPhases) * 100);
    const p3Pct = Math.round((Math.abs(l3W) / sumPhases) * 100);
    setTxt("val-w-l1", `${l1W} W (${p1Pct} %)`);
    setTxt("val-w-l2", `${l2W} W (${p2Pct} %)`);
    setTxt("val-w-l3", `${l3W} W (${p3Pct} %)`);

    const b1 = sr.getElementById("bar-l1");
    const b2 = sr.getElementById("bar-l2");
    const b3 = sr.getElementById("bar-l3");
    if (b1) b1.style.width = `${Math.max(6, p1Pct)}%`;
    if (b2) b2.style.width = `${Math.max(6, p2Pct)}%`;
    if (b3) b3.style.width = `${Math.max(6, p3Pct)}%`;

    // Update Weather & Solar Forecast MySmartHome (PSČ 100 00)
    const meteoAttr = states["sensor.open_meteo_mysmarthome"]?.attributes || {};
    const cur = meteoAttr.current || {};
    const daily = meteoAttr.daily || {};

    const tempOut = num(cur.temperature_2m, num(states["sensor.mysmarthome_venkovni_teplota"]?.state, 0));
    const tempFeel = num(cur.apparent_temperature, num(states["sensor.mysmarthome_pocitova_teplota"]?.state, tempOut));
    const hum = num(cur.relative_humidity_2m, num(states["sensor.mysmarthome_venkovni_vlhkost"]?.state, 0));
    const press = num(cur.pressure_msl, num(states["sensor.mysmarthome_atmosfericky_tlak"]?.state, 1013));
    const wind = num(cur.wind_speed_10m, num(states["sensor.mysmarthome_rychlost_vetru"]?.state, 0));
    const gusts = num(cur.wind_gusts_10m, num(states["sensor.mysmarthome_narazy_vetru"]?.state, 0));
    const irrad = num(cur.shortwave_radiation, num(states["sensor.mysmarthome_slunecni_osvit"]?.state, 0));
    const wInfo = this._weatherInfo(cur.weather_code);

    const tMin0 = num((daily.temperature_2m_min || [])[0], num(states["sensor.mysmarthome_min_teplota_dnes"]?.state, 0));
    const tMax0 = num((daily.temperature_2m_max || [])[0], num(states["sensor.mysmarthome_max_teplota_dnes"]?.state, 0));
    const rainProb0 = num((daily.precipitation_probability_max || [])[0], num(states["sensor.mysmarthome_pravdepodobnost_srazek_dnes"]?.state, 0));
    const rainSum0 = num((daily.precipitation_sum || [])[0], num(states["sensor.mysmarthome_uhrn_srazek_dnes"]?.state, 0));
    const fveToday = num(states["sensor.mysmarthome_predikce_fve_dnes"]?.state, num((daily.shortwave_radiation_sum || [])[0], 0) * 3.1);
    const fveTomorrow = num(states["sensor.mysmarthome_predikce_fve_zitra"]?.state, num((daily.shortwave_radiation_sum || [])[1], 0) * 3.1);

    setTxt("w-icon", wInfo.icon);
    setTxt("w-temp", `${tempOut.toFixed(1)} °C`);
    setTxt("w-desc", `${wInfo.label} • Pocitově ${tempFeel.toFixed(1)} °C`);
    setTxt("weather-minmax-badge", `Dnes: ${tMin0.toFixed(1)}° / ${tMax0.toFixed(1)} °C`);
    setTxt("w-fve-forecast", `Dnes: ${fveToday.toFixed(1)} kWh`);
    setTxt("w-fve-tomorrow", `Zítra: ${fveTomorrow.toFixed(1)} kWh`);

    setTxt("val-irradiance", `${irrad.toFixed(0)} W/m²`);
    setTxt("val-rain", `${rainProb0.toFixed(0)} % (${rainSum0.toFixed(1)} mm)`);
    setTxt("val-wind", `${wind.toFixed(1)} / ${gusts.toFixed(0)} km/h`);
    setTxt("val-hum", `${hum.toFixed(0)} % • ${press.toFixed(0)} hPa`);

    const fStrip = sr.getElementById("forecast-days-strip");
    if (fStrip && Array.isArray(daily.temperature_2m_max) && daily.temperature_2m_max.length >= 3) {
      const labels = ["Dnes", "Zítra", "Pozítří"];
      fStrip.innerHTML = [0, 1, 2].map((idx) => {
        const wi = this._weatherInfo((daily.weather_code || [])[idx]);
        const mn = num((daily.temperature_2m_min || [])[idx], 0).toFixed(0);
        const mx = num((daily.temperature_2m_max || [])[idx], 0).toFixed(0);
        const fve = (num((daily.shortwave_radiation_sum || [])[idx], 0) * 3.1).toFixed(0);
        return `<div class="fday-item">
          <span class="fday-name">${wi.icon} ${labels[idx]}</span>
          <span class="fday-vals"><strong>${mx}°</strong>/${mn}° • ☀️${fve}kWh</span>
        </div>`;
      }).join("");
    }

    // Update Quick Home Controls (Loxone Indoor Climate, Loxone Lights & Xiaomi Vacuum)
    const loxTemp = num(states["sensor.teplota"]?.state, 20.1);
    const loxHum = num(states["sensor.vlhkost"]?.state, 49.6);
    const loxCo2 = num(states["sensor.co2"]?.state, 468);
    setTxt("val-qc-obyvak", `${loxTemp.toFixed(1)} °C • ${loxHum.toFixed(0)} %`);
    setTxt("sub-qc-obyvak", `Kvalita vzduchu CO₂: ${loxCo2.toFixed(0)} ppm`);

    const loxLights = Object.keys(states).filter((k) => k.startsWith("light.ovladani_osvetleni"));
    const onLights = loxLights.filter((k) => states[k]?.state === "on").length;
    setTxt("val-qc-loznice", `${onLights} z ${loxLights.length || 15} svítí`);
    setTxt("sub-qc-loznice", `Kuchyně: ${states["light.ovladani_osvetleni_kuchyne"]?.state === "on" ? "Zapnuto" : "Vypnuto"}`);
    const vac = states["vacuum.smart_robot_vacuum"];
    if (vac) {
      const vMap = {
        docked: "V doku (Nabito)",
        cleaning: "Uklízí dům…",
        returning: "Vrací se do doku",
        paused: "Pozastaven",
        idle: "Připraven",
        unavailable: "Režim spánku",
      };
      setTxt("val-qc-vacuum", vMap[vac.state] || vac.state);
    }
  }
}

const _registerMySmartHomeCockpit = () => {
  try {
    if (!customElements.get("smarthome-cockpit-card")) {
      customElements.define("smarthome-cockpit-card", class extends SmartHomeCockpitCard {});
      window.dispatchEvent(new Event("ll-rebuild"));
    }
  } catch (e) {}
};
_registerMySmartHomeCockpit();
if (window.customElements && customElements.whenDefined) {
  customElements.whenDefined("home-assistant").then(_registerMySmartHomeCockpit);
  customElements.whenDefined("hui-view").then(_registerMySmartHomeCockpit);
}
setInterval(_registerMySmartHomeCockpit, 250);
window.customCards = window.customCards || [];
if (!window.customCards.some((c) => c.type === "smarthome-cockpit-card")) {
  window.customCards.push({
    type: "smarthome-cockpit-card",
    name: "MySmartHome (Energetika a Přehled)",
    description: "Jednotný animovaný 16:10 a mobilní kokpit pro FVE Deye 15kW, Eastron, Baterii, Počasí MySmartHome a Always-On Hey Jeffrey.",
  });
}
