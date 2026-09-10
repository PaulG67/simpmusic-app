/* Audio engine: queue, crossfade, 10-band equaliser, sleep timer,
   SponsorBlock skipping and listening statistics.

   Two <audio> elements alternate so one track can fade into the next. The Web
   Audio graph is built lazily, because on iOS routing an element through Web
   Audio can interfere with background playback - users who never touch the
   equaliser or crossfade never pay that price. */

(() => {
  "use strict";

  const BANDS = window.SM.EQ.BANDS;
  const SCROBBLE_THRESHOLD = 15;

  const Player = {
    queue: [],
    index: -1,
    shuffle: false,
    repeat: "off",

    elements: [],
    active: 0,
    crossfading: false,

    audioCtx: null,
    sources: [],
    elementGains: [],
    filterNodes: [],
    preampNode: null,

    settings: {
      crossfade: 0,
      eqEnabled: false,
      eqPreset: "Neutral",
      gains: BANDS.map(() => 0),
      preamp: 0,
      mode: "graphic",
      filters: null,
      sponsorblock: true,
      skipSilence: false,
    },

    segments: [],
    listened: 0,
    lastTick: 0,
    sleep: { mode: null, deadline: 0, timer: null },
    handlers: {},

    // ------------------------------------------------------------ plumbing

    on(event, handler) {
      (this.handlers[event] = this.handlers[event] || []).push(handler);
    },

    emit(event, payload) {
      (this.handlers[event] || []).forEach((handler) => handler(payload));
    },

    init(elements) {
      this.elements = elements;
      elements.forEach((element, position) => {
        element.preload = "auto";
        element.addEventListener("timeupdate", () => this.onTimeUpdate(position));
        element.addEventListener("ended", () => this.onEnded(position));
        element.addEventListener("play", () => position === this.active && this.emit("state", true));
        element.addEventListener("pause", () => position === this.active && this.emit("state", false));
        element.addEventListener("error", () => {
          if (position === this.active && element.src) this.emit("error", "Titel konnte nicht geladen werden");
        });
      });

      window.addEventListener("pagehide", () => this.flushScrobble(true));
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "hidden") this.flushScrobble(true);
      });
    },

    el(offset = 0) {
      return this.elements[(this.active + offset) % this.elements.length];
    },

    current() {
      return this.queue[this.index] || null;
    },

    isPlaying() {
      return !this.el().paused;
    },

    // ----------------------------------------------------------- Web Audio

    needsGraph() {
      return this.settings.eqEnabled || this.settings.crossfade > 0;
    },

    ensureGraph() {
      if (this.audioCtx || !this.needsGraph()) return this.audioCtx;

      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return null;

      this.audioCtx = new AudioContextClass();
      this.preampNode = this.audioCtx.createGain();

      this.filterNodes = BANDS.map((frequency) => {
        const filter = this.audioCtx.createBiquadFilter();
        filter.type = "peaking";
        filter.frequency.value = frequency;
        filter.Q.value = 1.1;
        filter.gain.value = 0;
        return filter;
      });

      let node = this.preampNode;
      for (const filter of this.filterNodes) {
        node.connect(filter);
        node = filter;
      }
      node.connect(this.audioCtx.destination);

      this.elements.forEach((element) => {
        const source = this.audioCtx.createMediaElementSource(element);
        const gain = this.audioCtx.createGain();
        gain.gain.value = 1;
        source.connect(gain);
        gain.connect(this.preampNode);
        this.sources.push(source);
        this.elementGains.push(gain);
      });

      this.applyEqualizer();
      return this.audioCtx;
    },

    resumeGraph() {
      if (this.audioCtx && this.audioCtx.state === "suspended") this.audioCtx.resume();
    },

    applyEqualizer() {
      if (!this.audioCtx) return;

      const { eqEnabled, mode, gains, preamp, filters } = this.settings;
      const dbToGain = (db) => Math.pow(10, db / 20);

      this.preampNode.gain.value = eqEnabled ? dbToGain(preamp || 0) : 1;

      if (!eqEnabled) {
        this.filterNodes.forEach((filter) => {
          filter.type = "peaking";
          filter.gain.value = 0;
        });
        return;
      }

      if (mode === "parametric" && Array.isArray(filters)) {
        this.filterNodes.forEach((node, position) => {
          const definition = filters[position];
          if (definition) {
            node.type = definition.type;
            node.frequency.value = definition.frequency;
            node.Q.value = definition.q || 1;
            node.gain.value = definition.gain;
          } else {
            node.type = "peaking";
            node.gain.value = 0;
          }
        });
        return;
      }

      this.filterNodes.forEach((node, position) => {
        node.type = "peaking";
        node.frequency.value = BANDS[position];
        node.Q.value = 1.1;
        node.gain.value = gains[position] || 0;
      });
    },

    updateSettings(patch) {
      Object.assign(this.settings, patch);
      if (this.needsGraph()) {
        this.ensureGraph();
        this.resumeGraph();
      }
      this.applyEqualizer();
      this.emit("settings", this.settings);
    },

    setGain(elementIndex, value, seconds) {
      const gain = this.elementGains[elementIndex];
      if (!gain) {
        // Without Web Audio there is nothing to fade: iOS ignores
        // HTMLMediaElement.volume entirely.
        return;
      }
      const now = this.audioCtx.currentTime;
      gain.gain.cancelScheduledValues(now);
      gain.gain.setValueAtTime(gain.gain.value, now);
      gain.gain.linearRampToValueAtTime(value, now + (seconds || 0.01));
    },

    // ------------------------------------------------------------ playback

    load(element, track) {
      element.src = `/api/stream/${encodeURIComponent(track.id)}`;
      element.load();
    },

    play(tracks, position) {
      this.flushScrobble();
      this.queue = tracks.slice();
      this.index = position;
      this.startCurrent();
    },

    enqueueNext(track) {
      this.queue.splice(this.index + 1, 0, track);
      this.emit("queue", this.queue);
    },

    startCurrent() {
      const track = this.current();
      if (!track) return;

      this.crossfading = false;
      this.listened = 0;
      this.lastTick = 0;
      this.segments = [];

      if (this.needsGraph()) {
        this.ensureGraph();
        this.resumeGraph();
      }
      this.setGain(this.active, 1, 0.01);
      this.setGain((this.active + 1) % 2, 0, 0.01);

      const element = this.el();
      this.elements.forEach((other) => {
        if (other !== element) {
          other.pause();
          other.removeAttribute("src");
        }
      });

      this.load(element, track);
      element.play().catch((error) => this.emit("error", error.message));

      this.emit("track", track);
      this.loadSegments(track);
      this.updateMediaSession(track);
    },

    toggle() {
      if (!this.current()) return;
      const element = this.el();
      if (element.paused) {
        this.resumeGraph();
        element.play().catch((error) => this.emit("error", error.message));
      } else {
        element.pause();
      }
    },

    nextIndex() {
      if (this.shuffle && this.queue.length > 1) {
        let candidate = this.index;
        while (candidate === this.index) candidate = Math.floor(Math.random() * this.queue.length);
        return candidate;
      }
      if (this.index + 1 < this.queue.length) return this.index + 1;
      if (this.repeat === "all") return 0;
      return -1;
    },

    next(auto) {
      if (auto && this.sleep.mode === "track") {
        this.clearSleepTimer();
        this.el().pause();
        this.emit("sleep", null);
        return;
      }
      if (auto && this.repeat === "one") {
        this.el().currentTime = 0;
        this.el().play();
        return;
      }

      const target = this.nextIndex();
      if (target < 0) return;

      this.flushScrobble();
      this.index = target;
      this.startCurrent();
    },

    previous() {
      const element = this.el();
      if (element.currentTime > 4) {
        element.currentTime = 0;
        return;
      }
      if (this.index > 0) {
        this.flushScrobble();
        this.index -= 1;
        this.startCurrent();
      }
    },

    jumpTo(position) {
      if (position < 0 || position >= this.queue.length) return;
      this.flushScrobble();
      this.index = position;
      this.startCurrent();
    },

    duration() {
      const element = this.el();
      if (Number.isFinite(element.duration) && element.duration > 0) return element.duration;
      return (this.current() || {}).duration || 0;
    },

    seek(seconds) {
      const element = this.el();
      if (Number.isFinite(seconds)) element.currentTime = seconds;
    },

    // ----------------------------------------------------------- crossfade

    beginCrossfade() {
      const target = this.nextIndex();
      if (target < 0 || this.repeat === "one") return;

      const seconds = this.settings.crossfade;
      const graph = this.ensureGraph();
      if (!graph) return;

      this.crossfading = true;
      const outgoing = this.active;
      const incoming = (this.active + 1) % 2;
      const element = this.elements[incoming];

      this.load(element, this.queue[target]);
      this.setGain(incoming, 0, 0.01);
      element
        .play()
        .then(() => {
          this.setGain(outgoing, 0, seconds);
          this.setGain(incoming, 1, seconds);
        })
        .catch(() => {
          this.crossfading = false;
        });

      window.setTimeout(() => {
        this.elements[outgoing].pause();
        this.elements[outgoing].removeAttribute("src");
        this.flushScrobble();
        this.active = incoming;
        this.index = target;
        this.crossfading = false;
        const track = this.current();
        this.emit("track", track);
        this.loadSegments(track);
        this.updateMediaSession(track);
      }, seconds * 1000);
    },

    // -------------------------------------------------------------- events

    onTimeUpdate(position) {
      if (position !== this.active) return;

      const element = this.el();
      const duration = this.duration();

      if (this.lastTick) {
        const delta = element.currentTime - this.lastTick;
        if (delta > 0 && delta < 2) this.listened += delta;
      }
      this.lastTick = element.currentTime;

      for (const segment of this.segments) {
        if (element.currentTime >= segment.start && element.currentTime < segment.end - 0.5) {
          element.currentTime = segment.end;
          this.emit("skip", segment);
          break;
        }
      }

      if (
        this.settings.crossfade > 0 &&
        !this.crossfading &&
        duration > this.settings.crossfade * 2 &&
        duration - element.currentTime <= this.settings.crossfade
      ) {
        this.beginCrossfade();
      }

      this.emit("time", { position: element.currentTime, duration });
      this.updatePositionState(element.currentTime, duration);
    },

    onEnded(position) {
      if (position !== this.active || this.crossfading) return;
      this.next(true);
    },

    // ------------------------------------------------------- sleep timer

    setSleepTimer(mode, minutes) {
      this.clearSleepTimer();

      if (mode === "track") {
        this.sleep = { mode: "track", deadline: 0, timer: null };
        this.emit("sleep", this.sleep);
        return;
      }
      if (mode !== "minutes" || !minutes) return;

      const deadline = Date.now() + minutes * 60000;
      const timer = window.setTimeout(() => this.fadeOutAndPause(), minutes * 60000);
      this.sleep = { mode: "minutes", deadline, timer };
      this.emit("sleep", this.sleep);
    },

    clearSleepTimer() {
      if (this.sleep.timer) window.clearTimeout(this.sleep.timer);
      this.sleep = { mode: null, deadline: 0, timer: null };
      this.emit("sleep", null);
    },

    fadeOutAndPause() {
      const element = this.el();
      if (this.elementGains[this.active]) {
        this.setGain(this.active, 0, 8);
        window.setTimeout(() => {
          element.pause();
          this.setGain(this.active, 1, 0.01);
        }, 8200);
      } else {
        element.pause();
      }
      this.clearSleepTimer();
    },

    // -------------------------------------------------------------- extras

    async loadSegments(track) {
      this.segments = [];
      if (!this.settings.sponsorblock || !track) return;
      try {
        const response = await fetch(`/api/sponsorblock/${encodeURIComponent(track.id)}`, {
          credentials: "same-origin",
        });
        if (response.ok) this.segments = (await response.json()).segments || [];
      } catch (error) {
        this.segments = [];
      }
    },

    flushScrobble(useBeacon) {
      const track = this.current();
      const seconds = Math.round(this.listened);
      this.listened = 0;
      this.lastTick = 0;

      if (!track || seconds < SCROBBLE_THRESHOLD) return;

      const body = JSON.stringify({ videoId: track.id, seconds });
      if (useBeacon && navigator.sendBeacon) {
        navigator.sendBeacon("/api/scrobble", new Blob([body], { type: "application/json" }));
        return;
      }
      fetch("/api/scrobble", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body,
      }).catch(() => {});
    },

    updateMediaSession(track) {
      if (!("mediaSession" in navigator) || !track) return;

      const artwork = track.thumbnail
        ? [96, 192, 512].map((size) => ({
            src: `/api/cover?u=${encodeURIComponent(track.thumbnail)}&size=${size}`,
            sizes: `${size}x${size}`,
            type: "image/jpeg",
          }))
        : [];

      navigator.mediaSession.metadata = new MediaMetadata({
        title: track.title,
        artist: track.artist,
        album: track.album || "",
        artwork,
      });

      navigator.mediaSession.setActionHandler("play", () => this.toggle());
      navigator.mediaSession.setActionHandler("pause", () => this.toggle());
      navigator.mediaSession.setActionHandler("previoustrack", () => this.previous());
      navigator.mediaSession.setActionHandler("nexttrack", () => this.next(false));
      navigator.mediaSession.setActionHandler("seekto", (details) => this.seek(details.seekTime));
      navigator.mediaSession.setActionHandler("seekbackward", () =>
        this.seek(Math.max(0, this.el().currentTime - 10))
      );
      navigator.mediaSession.setActionHandler("seekforward", () =>
        this.seek(Math.min(this.duration(), this.el().currentTime + 30))
      );
    },

    updatePositionState(position, duration) {
      if (!("mediaSession" in navigator) || !navigator.mediaSession.setPositionState) return;
      if (!Number.isFinite(duration) || duration <= 0) return;
      try {
        navigator.mediaSession.setPositionState({
          duration,
          playbackRate: this.el().playbackRate || 1,
          position: Math.min(position, duration),
        });
      } catch (error) {
        /* Safari throws when the values briefly disagree; harmless. */
      }
    },
  };

  window.SM = window.SM || {};
  window.SM.Player = Player;
})();
