/* Offline downloads.

   Audio is stored in the Cache API under the very same URL the player
   requests, so the service worker can serve it transparently - including byte
   ranges, which the Cache API does not handle on its own. Track metadata lives
   in localStorage so the offline library renders without the server. */

(() => {
  "use strict";

  const AUDIO_CACHE = "simpmusic-audio-v1";
  const META_KEY = "sm.offline.tracks";

  const Offline = {
    meta: {},
    listeners: new Set(),

    load() {
      try {
        this.meta = JSON.parse(localStorage.getItem(META_KEY) || "{}");
      } catch (error) {
        this.meta = {};
      }
      return this.meta;
    },

    persist() {
      localStorage.setItem(META_KEY, JSON.stringify(this.meta));
      this.listeners.forEach((listener) => listener(this.meta));
    },

    onChange(listener) {
      this.listeners.add(listener);
    },

    url(videoId) {
      return `/api/stream/${encodeURIComponent(videoId)}`;
    },

    has(videoId) {
      return Boolean(this.meta[videoId]);
    },

    tracks() {
      return Object.values(this.meta).sort((a, b) => (b.savedAt || 0) - (a.savedAt || 0));
    },

    async download(track, onProgress) {
      if (this.has(track.id)) return true;

      const cache = await caches.open(AUDIO_CACHE);
      const response = await fetch(this.url(track.id));
      if (!response.ok) throw new Error(`Download fehlgeschlagen (${response.status})`);

      const total = Number(response.headers.get("content-length") || 0);
      const reader = response.body ? response.body.getReader() : null;

      let stored;
      if (reader) {
        const chunks = [];
        let received = 0;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value);
          received += value.length;
          if (onProgress && total) onProgress(received / total);
        }
        const body = new Blob(chunks, { type: response.headers.get("content-type") || "audio/mp4" });
        stored = new Response(body, {
          status: 200,
          headers: {
            "Content-Type": response.headers.get("content-type") || "audio/mp4",
            "Content-Length": String(received),
          },
        });
      } else {
        stored = response;
      }

      await cache.put(this.url(track.id), stored);

      if (track.thumbnail) {
        // Same-origin proxy, so the artwork is cacheable too.
        caches.open("simpmusic-covers-v1").then((covers) =>
          covers.add(`/api/cover?u=${encodeURIComponent(track.thumbnail)}&size=544`).catch(() => {})
        );
      }

      this.meta[track.id] = {
        id: track.id,
        sid: track.sid,
        title: track.title,
        artist: track.artist,
        album: track.album,
        duration: track.duration,
        thumbnail: track.thumbnail,
        savedAt: Date.now(),
      };
      this.persist();
      return true;
    },

    async downloadMany(tracks, onProgress) {
      let done = 0;
      let failed = 0;
      for (const track of tracks) {
        try {
          await this.download(track);
        } catch (error) {
          failed += 1;
        }
        done += 1;
        if (onProgress) onProgress(done, tracks.length, failed);
      }
      return { done, failed };
    },

    async remove(videoId) {
      const cache = await caches.open(AUDIO_CACHE);
      await cache.delete(this.url(videoId));
      delete this.meta[videoId];
      this.persist();
    },

    async clear() {
      await caches.delete(AUDIO_CACHE);
      await caches.delete("simpmusic-covers-v1");
      this.meta = {};
      this.persist();
    },

    async usage() {
      if (!navigator.storage || !navigator.storage.estimate) {
        return { usage: 0, quota: 0, supported: false };
      }
      const estimate = await navigator.storage.estimate();
      return { usage: estimate.usage || 0, quota: estimate.quota || 0, supported: true };
    },

    /* Safari evicts caches under storage pressure unless the origin is
       persisted; asking is free and silently ignored where unsupported. */
    async requestPersistence() {
      if (navigator.storage && navigator.storage.persist) {
        try {
          return await navigator.storage.persist();
        } catch (error) {
          return false;
        }
      }
      return false;
    },
  };

  Offline.load();

  window.SM = window.SM || {};
  window.SM.Offline = Offline;
})();
