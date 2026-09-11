/* Music Play web player - UI, routing and views.
   Playback itself lives in player.js, downloads in offline.js. */

(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TOKEN_KEY = "music_play_token";

  const state = {
    user: null,
    passwordRequired: false,
    stack: [],
    route: null,
    playlists: [],
    lyrics: null,
    translation: null,
    lyricsTranslated: false,
    status: null,
    navidrome: null,
    mediasync: null,
    online: navigator.onLine,
  };

  const PLACEHOLDER =
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120" fill="#1c212b"/><text x="60" y="72" font-size="42" text-anchor="middle" fill="#4a5364">&#9835;</text></svg>'
    );

  // ---------------------------------------------------------------- helpers

  async function api(path, options = {}) {
    const headers = { "Content-Type": "application/json" };
    const token = localStorage.getItem(TOKEN_KEY);
    if (token) headers.Authorization = "Bearer " + token;
    const response = await fetch(path, {
      credentials: "include",
      ...options,
      headers: { ...headers, ...(options.headers || {}) },
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (response.status === 401) {
      localStorage.removeItem(TOKEN_KEY);
      const payload = await response.json().catch(() => ({}));
      const message = typeof payload.detail === "string" ? payload.detail : "Nicht angemeldet";
      throw new Error(message);
    }
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}));
      throw new Error(detail.detail || `Fehler ${response.status}`);
    }
    return response.json();
  }

  function formatTime(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    const total = Math.floor(seconds);
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const rest = String(total % 60).padStart(2, "0");
    return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
  }

  function formatDuration(seconds) {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.round((seconds % 3600) / 60);
    if (hours) return `${hours} h ${minutes} min`;
    return `${minutes} min`;
  }

  function formatBytes(bytes) {
    if (!bytes) return "0 MB";
    const gigabytes = bytes / 1024 ** 3;
    return gigabytes >= 1 ? `${gigabytes.toFixed(2)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
  }

  function toast(message) {
    const node = $("toast");
    node.textContent = message;
    node.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => (node.hidden = true), 2800);
  }

  function element(tag, attributes = {}, children = []) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attributes)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value);
    }
    for (const child of [].concat(children)) if (child) node.append(child);
    return node;
  }

  function navidromeReady() {
    return Boolean(state.navidrome && state.navidrome.configured);
  }

  function mediasyncReady() {
    return Boolean(state.mediasync && state.mediasync.configured);
  }

  function isLocalTrack(track) {
    return Boolean(track && (track.inLibrary || track.navidromeId || String(track.id || "").startsWith("nd:")));
  }

  function coverUrl(url, size = 320) {
    if (!url) return PLACEHOLDER;
    if (url.startsWith("/")) {
      const join = url.includes("?") ? "&" : "?";
      return `${url}${join}size=${size}`;
    }
    return `/api/cover?u=${encodeURIComponent(url)}&size=${size}`;
  }

  function cover(url, size) {
    const image = element("img", { src: coverUrl(url, size ? size * 2 : 320), alt: "", loading: "lazy" });
    image.addEventListener("error", () => (image.src = PLACEHOLDER));
    if (size) {
      image.width = size;
      image.height = size;
    }
    return image;
  }

  function closeSheet() {
    $("sheet").hidden = true;
  }

  function openSheet(title, nodes) {
    $("sheet-body").replaceChildren(
      element("p", { class: "sheet-title", text: title }),
      ...[].concat(nodes).filter(Boolean)
    );
    $("sheet").hidden = false;
  }

  $("sheet").addEventListener("click", (event) => {
    if (event.target === $("sheet")) closeSheet();
  });

  const { Player, Offline, EQ } = window.SM;

  // ------------------------------------------------------------- navigation

  function setTitle(title) {
    $("topbar-title").textContent = title;
    $("back-button").hidden = state.stack.length === 0;
  }

  function loading() {
    $("view").replaceChildren(element("div", { class: "spinner", text: "Lädt …" }));
  }

  function navigate(route, options = {}) {
    if (state.route && !options.replace) state.stack.push(state.route);
    state.route = route;
    render(route);
  }

  $("back-button").addEventListener("click", () => {
    const previous = state.stack.pop();
    if (previous) {
      state.route = previous;
      render(previous);
    }
  });

  $("reload-button").addEventListener("click", () => state.route && render(state.route, true));

  document.querySelectorAll("#nav button").forEach((button) => {
    button.addEventListener("click", () => {
      document.querySelectorAll("#nav button").forEach((other) => other.classList.remove("active"));
      button.classList.add("active");
      state.stack = [];
      state.route = { name: button.dataset.route };
      render(state.route);
    });
  });

  const VIEWS = {
    home: renderHome,
    search: renderSearch,
    explore: renderExplore,
    library: renderLibrary,
    settings: renderSettings,
    album: (route) => renderAlbum(route.id),
    artist: (route) => renderArtist(route.id),
    playlist: (route) => renderPlaylist(route.id),
    nplaylist: (route) => renderNavidromePlaylist(route.id),
    ytplaylist: (route) => renderYtPlaylist(route.id, route.title),
    mood: (route) => renderMood(route.params, route.title),
    podcast: (route) => renderPodcast(route.id),
    offline: renderOffline,
    stats: renderStats,
    wrapped: (route) => renderWrapped(route.year),
  };

  async function render(route, forceReload) {
    const view = VIEWS[route.name] || renderHome;
    try {
      await view(route, forceReload);
    } catch (exception) {
      $("view").replaceChildren(
        element("div", { class: "empty" }, [
          element("p", { text: exception.message }),
          element("button", { class: "chip", text: "Erneut versuchen", onclick: () => render(route, true) }),
        ])
      );
    }
  }

  // ------------------------------------------------------------- components

  function trackRow(track, list, position, options = {}) {
    const row = element("button", { class: "track", onclick: () => Player.play(list, position) }, [
      cover(track.thumbnail, 48),
      element("div", { class: "track-text" }, [
        element("strong", { text: track.title }),
        element("span", {
          text: options.subtitle || `${track.artist}${track.album ? " · " + track.album : ""}`,
        }),
      ]),
    ]);

    if (isLocalTrack(track)) row.append(element("span", { class: "pill", text: "Navidrome" }));
    else if (Offline.has(track.id)) row.append(element("span", { class: "pill", text: "Offline" }));
    if (track.duration) row.append(element("span", { class: "track-time", text: formatTime(track.duration) }));

    const more = element("span", { class: "track-more", text: "⋯" });
    more.addEventListener("click", (event) => {
      event.stopPropagation();
      openTrackSheet(track);
    });
    row.append(more);

    row.dataset.videoId = track.id;
    if ((Player.current() || {}).id === track.id) row.classList.add("playing");
    return row;
  }

  function trackList(tracks, options = {}) {
    return element("div", {}, tracks.map((track, position) => trackRow(track, tracks, position, options)));
  }

  function openItem(item) {
    const kind = item.kind || "song";
    if (kind === "album") return navigate({ name: "album", id: item.id });
    if (kind === "artist") return navigate({ name: "artist", id: item.id });
    if (kind === "podcast") return navigate({ name: "podcast", id: item.id });
    if (kind === "playlist") {
      if (item.source === "navidrome" || String(item.id || "").startsWith("nd:")) {
        navigate({ name: "nplaylist", id: String(item.id).replace(/^nd:/, "") });
      } else {
        navigate({ name: "ytplaylist", id: item.id, title: item.name || item.title });
      }
      return;
    }
    Player.play([item], 0);
  }

  async function playSearchCollection(item, shuffle = false) {
    if (!item) return;
    if (item.kind === "playlist") {
      toast("Lädt Playlist …");
      try {
        const playlist = await api(`/api/ytplaylist/${encodeURIComponent(item.id)}`);
        const tracks = playlist.tracks || [];
        if (!tracks.length) {
          toast("Playlist ist leer");
          return;
        }
        if (shuffle) {
          Player.shuffle = true;
          Player.play(tracks, Math.floor(Math.random() * tracks.length));
        } else {
          Player.play(tracks, 0);
        }
      } catch (error) {
        toast(error.message || "Playlist nicht ladbar");
      }
      return;
    }
    if (item.kind === "album") return navigate({ name: "album", id: item.id });
    if (item.kind === "artist") return navigate({ name: "artist", id: item.id });
    Player.play([item], 0);
  }

  function searchSubtitle(item) {
    const kind = item.kind || "song";
    if (kind === "playlist") {
      const bits = ["Playlist", item.owner || "YouTube Music"];
      if (item.song_count) bits.push(`${item.song_count} Songs`);
      return bits.join(" • ");
    }
    if (kind === "artist") {
      const bits = ["Künstler"];
      if (item.subscribers) {
        const text = String(item.subscribers);
        bits.push(/abonn/i.test(text) ? text : `${text} Abonnenten`);
      }
      return bits.join(" • ");
    }
    if (kind === "album") return ["Album", item.artist, item.year].filter(Boolean).join(" • ");
    if (kind === "podcast") return ["Podcast", item.author].filter(Boolean).join(" • ");
    if (item.isEpisode) return ["Folge", item.artist || item.album].filter(Boolean).join(" • ");
    if (item.isVideo) return ["Video", item.artist, item.views].filter(Boolean).join(" • ");
    return [item.artist, item.album].filter(Boolean).join(" • ");
  }

  function searchHitRow(item) {
    return element("button", { class: "list-card", onclick: () => openItem(item) }, [
      cover(item.thumbnail, 48),
      element("div", {}, [
        element("strong", { text: item.title || item.name }),
        element("span", { text: searchSubtitle(item) }),
      ]),
    ]);
  }

  function searchTopCard(item) {
    if (!item) return null;
    const playable = item.kind === "playlist" || item.kind === "song" || item.isVideo;
    return element("div", { class: "search-top", onclick: () => openItem(item) }, [
      cover(item.thumbnail, 72),
      element("div", { class: "search-top-meta" }, [
        element("strong", { text: item.title || item.name }),
        element("span", { text: searchSubtitle(item) }),
        playable
          ? element("div", { class: "search-top-actions" }, [
              item.kind === "playlist"
                ? element("button", {
                    class: "chip",
                    text: "Zufallsmix",
                    onclick: (event) => {
                      event.stopPropagation();
                      playSearchCollection(item, true);
                    },
                  })
                : null,
              element("button", {
                class: "chip solid",
                text: "Wiedergeben",
                onclick: (event) => {
                  event.stopPropagation();
                  playSearchCollection(item, false);
                },
              }),
            ])
          : null,
      ]),
      element("button", {
        class: "icon-button search-top-open",
        "aria-label": "Öffnen",
        text: "›",
        onclick: () => openItem(item),
      }),
    ]);
  }

  function itemCard(item) {
    return element(
      "button",
      { class: `card ${item.kind === "artist" ? "round" : ""}`, onclick: () => openItem(item) },
      [
        cover(item.thumbnail, 142),
        element("strong", { text: item.title || item.name }),
        element("span", { text: item.artist || item.author || item.owner || "" }),
      ]
    );
  }

  function sectionsFragment(sections) {
    const fragment = document.createDocumentFragment();
    for (const section of sections || []) {
      const items = section.items || [];
      const songs = items.filter((item) => item.kind === "song");
      const body =
        songs.length === items.length && songs.length > 3
          ? trackList(songs)
          : element("div", { class: "row" }, items.map(itemCard));
      fragment.append(element("div", { class: "section" }, [element("h2", { text: section.title }), body]));
    }
    return fragment;
  }

  function playAllHeader(title, subtitle, thumbnail, tracks, extras = []) {
    return element("div", { class: "hero" }, [
      cover(thumbnail, 132),
      element("div", {}, [
        element("h1", { text: title }),
        element("p", { text: subtitle }),
        element("button", {
          class: "chip solid",
          text: "▶ Abspielen",
          onclick: () => tracks.length && Player.play(tracks, 0),
        }),
        element("button", {
          class: "chip",
          text: "⇄ Mischen",
          onclick: () => {
            if (!tracks.length) return;
            Player.shuffle = true;
            Player.play(tracks, Math.floor(Math.random() * tracks.length));
            toast("Zufallswiedergabe an");
          },
        }),
        element("button", {
          class: "chip",
          text: mediasyncReady() ? "↓ Download" : navidromeReady() ? "↓ Nach Navidrome" : "↓ Alle offline",
          onclick: (event) => {
            if (mediasyncReady()) {
              openDownloadSheet(tracks);
              return;
            }
            downloadAll(tracks, event.currentTarget);
          },
        }),
        ...extras,
      ]),
    ]);
  }

  // ------------------------------------------------------------------ views

  async function renderHome() {
    setTitle("Start");
    loading();
    const data = await api("/api/home");
    if (!data.sections.length) {
      $("view").replaceChildren(
        element("div", { class: "empty", text: "Keine Empfehlungen verfügbar. Nutze die Suche oder Entdecken." })
      );
      return;
    }
    $("view").replaceChildren(sectionsFragment(data.sections));
  }

  function renderSearch() {
    setTitle("Suche");

    const input = element("input", {
      type: "search",
      placeholder: "Titel, Album, Interpret oder Podcast",
      autocomplete: "off",
      enterkeyhint: "search",
      value: renderSearch.lastQuery || "",
    });
    const results = element("div", {});
    const form = element("form", { class: "search-bar" }, [input]);

    function uniqueById(items) {
      const seen = new Set();
      return (items || []).filter((item) => {
        const id = String(item.id || item.sid || "");
        if (!id || seen.has(id)) return false;
        seen.add(id);
        return true;
      });
    }

    function paintSearch(music, podcasts, filter) {
      const library = music.library || {};
      const extraPodcasts = uniqueById([...(music.podcasts || []), ...(podcasts.podcasts || [])]);
      const extraEpisodes = uniqueById([...(music.episodes || []), ...(podcasts.episodes || [])]);
      const buckets = {
        all: music.items || [],
        playlists: music.playlists || [],
        community: music.community_playlists || [],
        artists: music.artists || [],
        videos: music.videos || [],
        songs: music.songs || [],
        albums: music.albums || [],
        podcasts: extraPodcasts,
        episodes: extraEpisodes,
      };
      const chips = [
        ["all", "Alle"],
        ["playlists", "Angesagte Playlists"],
        ["community", "Community-Playlists"],
        ["artists", "Künstler"],
        ["videos", "Videos"],
        ["songs", "Titel"],
        ["albums", "Alben"],
        ["podcasts", "Podcasts"],
        ["episodes", "Folgen"],
      ].filter(([key]) => key === "all" || (buckets[key] && buckets[key].length));

      const active = chips.some(([key]) => key === filter) ? filter : "all";
      renderSearch.lastFilter = active;

      const fragment = document.createDocumentFragment();
      fragment.append(
        element(
          "div",
          { class: "search-filters" },
          chips.map(([key, label]) =>
            element("button", {
              class: `chip${key === active ? " active" : ""}`,
              text: label,
              onclick: () => paintSearch(music, podcasts, key),
            })
          )
        )
      );

      if ((library.songs || []).length) {
        fragment.append(
          element("div", { class: "section" }, [element("h2", { text: "In Navidrome" }), trackList(library.songs)])
        );
      }

      if (active === "all") {
        const top = music.top;
        if (top) fragment.append(searchTopCard(top));
        const rest = (buckets.all || []).filter((item) => !top || item.id !== top.id);
        rest.forEach((item) => {
          if (item.kind === "song" && !item.isVideo && !item.isEpisode) {
            const queue = buckets.songs.length ? buckets.songs : [item];
            const index = Math.max(0, queue.findIndex((song) => song.id === item.id));
            fragment.append(trackRow(item, queue, index));
          } else fragment.append(searchHitRow(item));
        });
        if (!top && !rest.length && !extraPodcasts.length) {
          fragment.append(element("div", { class: "empty", text: "Nichts gefunden." }));
        } else if (!top && !rest.length && extraPodcasts.length) {
          extraPodcasts.forEach((item) => fragment.append(searchHitRow(item)));
        }
      } else if (active === "songs") {
        fragment.append(trackList(buckets.songs));
      } else if (active === "episodes") {
        fragment.append(trackList(buckets.episodes));
      } else if (active === "artists" || active === "albums") {
        fragment.append(element("div", { class: "row" }, buckets[active].map(itemCard)));
      } else {
        buckets[active].forEach((item) => fragment.append(searchHitRow(item)));
      }

      if (!fragment.querySelector(".search-top, .list-card, .track, .card, .empty")) {
        fragment.append(element("div", { class: "empty", text: "Nichts gefunden." }));
      }
      results.replaceChildren(fragment);
    }

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      input.blur();
      const query = input.value.trim();
      if (!query) return;
      renderSearch.lastQuery = query;
      renderSearch.lastFilter = "all";
      results.replaceChildren(element("div", { class: "spinner", text: "Sucht …" }));

      const [music, podcasts] = await Promise.all([
        api(`/api/search?q=${encodeURIComponent(query)}`),
        api(`/api/podcasts/search?q=${encodeURIComponent(query)}&limit=8`).catch(() => ({
          podcasts: [],
          episodes: [],
        })),
      ]);
      paintSearch(music, podcasts, "all");
    });

    $("view").replaceChildren(form, results);
    if (renderSearch.lastQuery) form.requestSubmit();
  }

  async function renderExplore() {
    setTitle("Entdecken");
    loading();

    const [charts, moods, podcasts] = await Promise.all([
      api("/api/charts").catch(() => ({ sections: [] })),
      api("/api/moods").catch(() => ({ sections: [] })),
      api("/api/podcasts").catch(() => ({ subscribed: [], newEpisodes: [] })),
    ]);

    const fragment = document.createDocumentFragment();
    const moodSections = moods.sections || [];
    const chartSections = charts.sections || [];

    for (const section of moodSections) {
      fragment.append(
        element("div", { class: "section" }, [
          element("h2", { text: section.title }),
          element(
            "div",
            { class: "chips" },
            section.items.map((item) =>
              element("button", {
                class: "chip",
                text: item.title,
                onclick: () => navigate({ name: "mood", params: item.params, title: item.title }),
              })
            )
          ),
        ])
      );
    }

    if ((podcasts.subscribed || []).length) {
      fragment.append(
        element("div", { class: "section" }, [
          element("h2", { text: "Deine Podcasts" }),
          element("div", { class: "row" }, podcasts.subscribed.map(itemCard)),
        ])
      );
    }
    if ((podcasts.newEpisodes || []).length) {
      fragment.append(
        element("div", { class: "section" }, [
          element("h2", { text: "Neue Episoden" }),
          trackList(podcasts.newEpisodes),
        ])
      );
    }

    fragment.append(sectionsFragment(chartSections));

    if (!fragment.childNodes.length) {
      fragment.append(element("div", { class: "empty", text: "Nichts zu entdecken – prüfe die Verbindung." }));
    }
    $("view").replaceChildren(fragment);
  }

  async function renderMood(params, title) {
    setTitle(title || "Kategorie");
    loading();
    const data = await api("/api/mood", { method: "POST", body: { params: params || "" } });
    const items = data.playlists || [];
    $("view").replaceChildren(
      items.length
        ? element("div", { class: "grid" }, items.map(itemCard))
        : element("div", { class: "empty", text: "Keine Playlists in dieser Kategorie." })
    );
  }

  async function renderAlbum(albumId) {
    loading();
    const album = await api(`/api/album/${encodeURIComponent(albumId)}`);
    setTitle(album.name);
    const tracks = album.tracks || [];

    const favorite = element("button", {
      class: `chip ${album.starred ? "active" : ""}`,
      text: album.starred ? "♥ Favorit" : "♡ Favorit",
      onclick: (event) => toggleFavorite(album.sid, event.currentTarget),
    });

    $("view").replaceChildren(
      playAllHeader(
        album.name,
        [album.artist, album.year, `${tracks.length} Titel`].filter(Boolean).join(" · "),
        album.thumbnail,
        tracks,
        [favorite]
      ),
      trackList(tracks)
    );
  }

  async function renderArtist(artistId) {
    loading();
    const artist = await api(`/api/artist/${encodeURIComponent(artistId)}`);
    setTitle(artist.name);

    const top = artist.top_songs || [];
    const fragment = document.createDocumentFragment();

    const favorite = element("button", {
      class: `chip ${artist.starred ? "active" : ""}`,
      text: artist.starred ? "♥ Favorit" : "♡ Favorit",
      onclick: (event) => toggleFavorite(artist.sid, event.currentTarget),
    });

    const header = playAllHeader(
      artist.name,
      artist.subscribers ? `${artist.subscribers} Abonnenten` : "",
      artist.thumbnail,
      top,
      [favorite]
    );
    header.classList.add("round");
    fragment.append(header);

    if (top.length) {
      fragment.append(
        element("div", { class: "section" }, [element("h2", { text: "Beliebte Titel" }), trackList(top)])
      );
    }
    for (const [title, items] of [
      ["Alben", artist.albums],
      ["Ähnliche Interpreten", artist.related],
    ]) {
      if (!items || !items.length) continue;
      fragment.append(
        element("div", { class: "section" }, [
          element("h2", { text: title }),
          element("div", { class: "row" }, items.map(itemCard)),
        ])
      );
    }

    $("view").replaceChildren(fragment);
  }

  async function renderPlaylist(playlistId) {
    loading();
    const playlist = await api(`/api/playlist/${playlistId}`);
    setTitle(playlist.name);
    const tracks = playlist.tracks || [];

    const remove = element("button", {
      class: "chip",
      text: "Löschen",
      onclick: async () => {
        if (!confirm(`Playlist "${playlist.name}" löschen?`)) return;
        await api(`/api/playlist/${playlistId}`, { method: "DELETE" });
        state.stack.pop();
        navigate({ name: "library" }, { replace: true });
      },
    });

    const extras = [remove];
    if (mediasyncReady() || navidromeReady()) {
      extras.unshift(
        element("button", {
          class: "chip",
          text: mediasyncReady() ? "↓ Download" : "Nach Navidrome",
          onclick: async (event) => {
            if (mediasyncReady()) {
              openDownloadSheet(tracks);
              return;
            }
            const button = event.currentTarget;
            button.disabled = true;
            button.textContent = "Speichert …";
            try {
              const result = await api(`/api/playlist/${playlistId}/navidrome`, { method: "POST" });
              if (result.jobs && result.jobs.length) await waitForJobs(result.jobs, button);
              toast("In Navidrome gespeichert");
              render(state.route, true);
            } catch (error) {
              toast(error.message);
              button.disabled = false;
              button.textContent = "Nach Navidrome";
            }
          },
        })
      );
    }

    $("view").replaceChildren(
      playAllHeader(
        playlist.name,
        `${tracks.length} Titel · ${formatDuration(playlist.duration)}`,
        playlist.thumbnail,
        tracks,
        extras
      ),
      trackList(tracks)
    );
  }

  async function renderNavidromePlaylist(playlistId) {
    loading();
    const playlist = await api(`/api/navidrome/playlist/${encodeURIComponent(playlistId)}`);
    setTitle(playlist.name);
    const tracks = playlist.tracks || [];
    $("view").replaceChildren(
      playAllHeader(playlist.name, `${tracks.length} Titel`, playlist.thumbnail, tracks),
      trackList(tracks)
    );
  }

  async function renderYtPlaylist(playlistId, title) {
    loading();
    const playlist = await api(`/api/ytplaylist/${encodeURIComponent(playlistId)}`);
    setTitle(playlist.name || title || "Playlist");
    const tracks = playlist.tracks || [];

    const copy = element("button", {
      class: "chip",
      text: "In eigene Playlist kopieren",
      onclick: async () => {
        await api("/api/playlists", {
          method: "POST",
          body: { name: playlist.name, tracks: tracks.map((track) => track.sid) },
        });
        toast("Kopiert");
      },
    });

    $("view").replaceChildren(
      playAllHeader(playlist.name, `${tracks.length} Titel`, playlist.thumbnail, tracks, [copy]),
      trackList(tracks)
    );
  }

  async function renderPodcast(podcastId) {
    loading();
    const show = await api(`/api/podcast/${encodeURIComponent(podcastId)}`);
    setTitle(show.title);
    const episodes = show.episodes || [];

    const subscribe = element("button", {
      class: `chip ${show.subscribed ? "active" : ""}`,
      text: show.subscribed ? "✓ Abonniert" : "+ Abonnieren",
      onclick: async (event) => {
        const next = !event.currentTarget.classList.contains("active");
        await api(`/api/podcast/${encodeURIComponent(podcastId)}/subscribe`, {
          method: "POST",
          body: { subscribed: next },
        });
        event.currentTarget.classList.toggle("active", next);
        event.currentTarget.textContent = next ? "✓ Abonniert" : "+ Abonnieren";
        toast(next ? "Abonniert – auch in Amperfy sichtbar" : "Abo entfernt");
      },
    });

    $("view").replaceChildren(
      playAllHeader(show.title, `${show.author} · ${episodes.length} Episoden`, show.thumbnail, episodes, [subscribe]),
      show.description ? element("p", { class: "description", text: show.description }) : null,
      trackList(episodes, { subtitle: null })
    );
  }

  async function renderLibrary() {
    setTitle("Bibliothek");
    loading();

    const [favorites, playlists, navidromeLibrary] = await Promise.all([
      api("/api/favorites"),
      api("/api/playlists"),
      api("/api/navidrome/library").catch(() => ({ configured: false })),
    ]);
    state.playlists = playlists.playlists;

    const fragment = document.createDocumentFragment();

    fragment.append(
      element("div", { class: "chips" }, [
        element("button", {
          class: "chip solid",
          text: `↓ Offline (${Offline.tracks().length})`,
          onclick: () => navigate({ name: "offline" }),
        }),
        element("button", { class: "chip", text: "📊 Statistiken", onclick: () => navigate({ name: "stats" }) }),
        element("button", {
          class: "chip",
          text: "+ Neue Playlist",
          onclick: async () => {
            const name = prompt("Name der Playlist");
            if (!name) return;
            await api("/api/playlists", { method: "POST", body: { name, tracks: [] } });
            render(state.route, true);
          },
        }),
      ])
    );

    if (navidromeLibrary.configured && (navidromeLibrary.playlists || []).length) {
      fragment.append(
        element("div", { class: "section" }, [
          element("h2", { text: "Navidrome-Playlists" }),
          ...navidromeLibrary.playlists.map((playlist) =>
            element(
              "button",
              {
                class: "list-card",
                onclick: () => navigate({ name: "nplaylist", id: String(playlist.id).replace(/^nd:/, "") }),
              },
              [
                cover(playlist.thumbnail, 46),
                element("div", {}, [
                  element("strong", { text: playlist.name }),
                  element("span", { text: `${playlist.song_count || 0} Titel` }),
                ]),
              ]
            )
          ),
        ])
      );
    }
    if (navidromeLibrary.configured && (navidromeLibrary.albums || []).length) {
      fragment.append(
        element("div", { class: "section" }, [
          element("h2", { text: "Zuletzt in Navidrome" }),
          element("div", { class: "row" }, navidromeLibrary.albums.map(itemCard)),
        ])
      );
    }

    const playlistCards = playlists.playlists.map((playlist) =>
      element("button", { class: "list-card", onclick: () => navigate({ name: "playlist", id: playlist.id }) }, [
        cover(playlist.thumbnail, 46),
        element("div", {}, [
          element("strong", { text: playlist.name }),
          element("span", { text: `${playlist.song_count} Titel` }),
        ]),
      ])
    );
    if (playlistCards.length) {
      fragment.append(
        element("div", { class: "section" }, [element("h2", { text: "Playlists" }), ...playlistCards])
      );
    }

    if (playlists.account.length) {
      fragment.append(
        element("div", { class: "section" }, [
          element("h2", { text: "YouTube-Music-Konto" }),
          ...playlists.account.map((playlist) =>
            element(
              "button",
              {
                class: "list-card",
                onclick: () => navigate({ name: "ytplaylist", id: playlist.id, title: playlist.name }),
              },
              [
                cover(playlist.thumbnail, 46),
                element("div", {}, [
                  element("strong", { text: playlist.name }),
                  element("span", { text: `${playlist.song_count} Titel` }),
                ]),
              ]
            )
          ),
        ])
      );
    }

    for (const [title, tracks] of [
      ["Lieblingstitel", favorites.songs],
      ["Zuletzt gehört", favorites.recent],
      ["Am häufigsten gehört", favorites.most],
    ]) {
      if (!tracks.length) continue;
      fragment.append(element("div", { class: "section" }, [element("h2", { text: title }), trackList(tracks)]));
    }

    for (const [title, items] of [
      ["Lieblingsalben", favorites.albums],
      ["Lieblingsinterpreten", favorites.artists],
    ]) {
      if (!items.length) continue;
      fragment.append(
        element("div", { class: "section" }, [
          element("h2", { text: title }),
          element("div", { class: "row" }, items.map(itemCard)),
        ])
      );
    }

    $("view").replaceChildren(fragment);
  }

  async function renderOffline() {
    setTitle("Offline");
    const tracks = Offline.tracks();
    const usage = await Offline.usage();

    const fragment = document.createDocumentFragment();
    fragment.append(
      element("div", { class: "section" }, [
        element("dl", { class: "info" }, [
          element("dt", { text: "Gespeicherte Titel" }),
          element("dd", { text: String(tracks.length) }),
          element("dt", { text: "Belegt" }),
          element("dd", { text: usage.supported ? formatBytes(usage.usage) : "unbekannt" }),
          element("dt", { text: "Verfügbar" }),
          element("dd", { text: usage.supported ? formatBytes(usage.quota) : "unbekannt" }),
        ]),
        element("button", {
          class: "chip",
          text: "Alle Downloads löschen",
          onclick: async () => {
            if (!confirm("Alle heruntergeladenen Titel löschen?")) return;
            await Offline.clear();
            render(state.route, true);
          },
        }),
      ])
    );

    if (!tracks.length) {
      fragment.append(
        element("div", {
          class: "empty",
          text: "Noch nichts heruntergeladen. Tippe bei einem Titel auf ⋯ → Offline speichern.",
        })
      );
    } else {
      fragment.append(trackList(tracks));
    }

    $("view").replaceChildren(fragment);
  }

  function barChart(entries, valueKey = "plays") {
    const max = Math.max(1, ...entries.map((entry) => entry[valueKey]));
    return element(
      "div",
      { class: "chart" },
      entries.map((entry) =>
        element("div", { class: "chart-row" }, [
          element("span", { class: "chart-label", text: entry.label || entry.name }),
          element("span", { class: "chart-bar" }, [
            element("i", { style: `width:${Math.round((entry[valueKey] / max) * 100)}%` }),
          ]),
          element("span", { class: "chart-value", text: String(entry[valueKey]) }),
        ])
      )
    );
  }

  async function renderStats() {
    setTitle("Statistiken");
    loading();
    const data = await api("/api/stats?limit=10");

    if (!data.totalPlays) {
      $("view").replaceChildren(
        element("div", { class: "empty", text: "Noch keine Wiedergaben aufgezeichnet." })
      );
      return;
    }

    const fragment = document.createDocumentFragment();
    fragment.append(
      element("div", { class: "section" }, [
        element("div", { class: "stat-grid" }, [
          statTile(String(data.totalPlays), "Wiedergaben"),
          statTile(formatDuration(data.totalSeconds), "Hörzeit"),
          statTile(String(data.uniqueTracks), "Titel"),
          statTile(String(data.uniqueArtists), "Interpreten"),
        ]),
        element(
          "div",
          { class: "chips" },
          data.years.map((year) =>
            element("button", {
              class: "chip solid",
              text: `Rückblick ${year}`,
              onclick: () => navigate({ name: "wrapped", year }),
            })
          )
        ),
      ])
    );

    for (const [title, entries] of [
      ["Top-Interpreten", data.topArtists],
      ["Top-Titel", data.topTracks],
      ["Top-Alben", data.topAlbums],
      ["Nach Wochentag", data.byWeekday],
      ["Nach Uhrzeit", data.byHour],
      ["Nach Monat", data.byMonth],
    ]) {
      if (!entries || !entries.length) continue;
      fragment.append(
        element("div", { class: "section" }, [element("h2", { text: title }), barChart(entries)])
      );
    }

    $("view").replaceChildren(fragment);
  }

  function statTile(value, label) {
    return element("div", { class: "stat-tile" }, [
      element("strong", { text: value }),
      element("span", { text: label }),
    ]);
  }

  async function renderWrapped(year) {
    setTitle(`Rückblick ${year}`);
    loading();
    const data = await api(`/api/wrapped/${year}`);

    if (data.empty) {
      $("view").replaceChildren(element("div", { class: "empty", text: `Keine Daten für ${year}.` }));
      return;
    }

    $("view").replaceChildren(
      element("div", { class: "wrapped" }, [
        element("h1", { text: `Dein Jahr ${year}` }),
        element("p", { class: "wrapped-lead", text: `${data.totalHours} Stunden Musik in ${data.totalPlays} Wiedergaben.` }),
        element("div", { class: "stat-grid" }, [
          statTile(String(data.uniqueArtists), "Interpreten"),
          statTile(String(data.uniqueTracks), "Titel"),
          statTile(String(data.activeDays), "aktive Tage"),
          statTile(String(data.longestStreak), "Tage am Stück"),
        ]),
        data.topArtist
          ? element("p", { class: "wrapped-lead", text: `Am meisten gehört: ${data.topArtist}` })
          : null,
        element("div", { class: "section" }, [
          element("h2", { text: "Deine Top-Titel" }),
          element(
            "div",
            {},
            data.topTracks.map((entry, position) =>
              element("div", { class: "track" }, [
                element("span", { class: "rank", text: String(position + 1) }),
                cover(entry.thumbnail, 48),
                element("div", { class: "track-text" }, [
                  element("strong", { text: entry.name }),
                  element("span", { text: `${entry.plays}×` }),
                ]),
              ])
            )
          ),
        ]),
        element("div", { class: "section" }, [
          element("h2", { text: "Deine Top-Interpreten" }),
          barChart(data.topArtists),
        ]),
        element("p", {
          class: "muted",
          text: `Aktivster Tag: ${data.busiestDay.date} mit ${data.busiestDay.plays} Wiedergaben.`,
        }),
      ])
    );
  }

  function navidromeSettings(status) {
    const nd = status.navidrome || {};
    state.navidrome = nd;
    return element("div", { class: "section" }, [
      element("h2", { text: "Navidrome" }),
      element("p", {
        class: "muted",
        text: nd.configured
          ? nd.reachable
            ? "Suche und Vorschläge kommen von YouTube Music. Vorhandene Titel spielt Navidrome. Downloads laufen über MediaSync in deine Bibliothek."
            : "Gespeichert, aber Navidrome antwortet nicht. URL und Zugangsdaten prüfen."
          : "URL, Benutzer und Passwort deines Navidrome eintragen. Musikordner denselben Share wie Navidrome nach /music einhängen.",
      }),
      element("div", { class: "settings-form" }, [
        element("input", {
          type: "url",
          id: "nd-url",
          value: nd.url || "",
          placeholder: "http://192.168.0.188:4533",
          autocomplete: "off",
        }),
        element("input", {
          type: "text",
          id: "nd-user",
          value: nd.username || "",
          placeholder: "Navidrome-Benutzer",
          autocomplete: "username",
        }),
        element("input", {
          type: "password",
          id: "nd-pass",
          placeholder: nd.passwordSet ? "Passwort unverändert lassen" : "Navidrome-Passwort",
          autocomplete: "new-password",
        }),
        element("input", {
          type: "text",
          id: "nd-folder",
          value: nd.importFolder || "YouTube",
          placeholder: "Unterordner für Imports",
        }),
        nd.configured
          ? element("p", {
              class: "muted",
              text: nd.musicDirWritable
                ? `Imports nach ${nd.musicDir}/${nd.importFolder || "YouTube"}`
                : `Musikordner nicht beschreibbar (${nd.musicDir}). In Unraid denselben Pfad wie Navidrome als /music einhängen.`,
            })
          : null,
        element("button", {
          class: "chip solid",
          text: "Verbinden",
          onclick: async () => {
            try {
              const data = await api("/api/navidrome", {
                method: "PUT",
                body: {
                  url: $("nd-url").value.trim(),
                  username: $("nd-user").value.trim(),
                  password: $("nd-pass").value,
                  importFolder: $("nd-folder").value.trim() || "YouTube",
                  musicDir: nd.musicDir,
                },
              });
              state.navidrome = data;
              toast(data.reachable ? "Navidrome verbunden" : "Gespeichert");
              render(state.route, true);
            } catch (error) {
              toast(error.message);
            }
          },
        }),
      ]),
    ]);
  }

  function mediasyncSettings(status) {
    const ms = status.mediasync || {};
    state.mediasync = ms;
    return element("div", { class: "section" }, [
      element("h2", { text: "MediaSync" }),
      element("p", {
        class: "muted",
        text: ms.configured
          ? ms.reachable
            ? "Beim Download wählst du eine bestehende Playlist. MediaSync sucht den Titel, lädt ihn und legt ihn in der Bibliothek ab."
            : "Gespeichert, aber MediaSync antwortet nicht. URL prüfen – im LAN z.B. http://192.168.0.188:PORT."
          : "URL deiner MediaSync-App auf Unraid. Optional Benutzer/Passwort, falls MediaSync Anmeldung verlangt.",
      }),
      element("div", { class: "settings-form" }, [
        element("input", {
          type: "url",
          id: "ms-url",
          value: ms.url || "",
          placeholder: "http://192.168.0.188:8090",
          autocomplete: "off",
        }),
        element("input", {
          type: "text",
          id: "ms-user",
          value: ms.username || "",
          placeholder: "Benutzer (optional)",
          autocomplete: "username",
        }),
        element("input", {
          type: "password",
          id: "ms-pass",
          placeholder: ms.passwordSet ? "Passwort unverändert lassen" : "Passwort (optional)",
          autocomplete: "new-password",
        }),
        element("button", {
          class: "chip solid",
          text: "Verbinden",
          onclick: async () => {
            try {
              const data = await api("/api/mediasync", {
                method: "PUT",
                body: {
                  url: $("ms-url").value.trim(),
                  username: $("ms-user").value.trim(),
                  password: $("ms-pass").value,
                },
              });
              state.mediasync = data;
              toast(data.reachable ? "MediaSync verbunden" : "Gespeichert");
              render(state.route, true);
            } catch (error) {
              toast(error.message);
            }
          },
        }),
      ]),
    ]);
  }

  async function renderSettings() {
    setTitle("Mehr");
    loading();
    const status = await api("/api/status");
    state.status = status;
    const usage = await Offline.usage();

    $("view").replaceChildren(
      element("div", { class: "section" }, [
        element("h2", { text: "Zugang" }),
        element("p", {
          class: "muted",
          text: status.passwordRequired
            ? "Passwort ist gesetzt. Web und Amperfy verwenden dieselben Daten."
            : "Kein Passwort – die App ist im lokalen Netz offen. Du kannst hier eines setzen.",
        }),
        element("div", { class: "settings-form" }, [
          element("input", {
            type: "text",
            id: "acct-user",
            value: status.subsonicUser || "musicplay",
            autocomplete: "username",
            placeholder: "Benutzername für Amperfy",
          }),
          element("input", {
            type: "password",
            id: "acct-pass",
            autocomplete: "new-password",
            placeholder: status.passwordRequired ? "Neues Passwort" : "Passwort",
          }),
          element("input", {
            type: "password",
            id: "acct-pass2",
            autocomplete: "new-password",
            placeholder: "Passwort wiederholen",
          }),
          element("div", { class: "row-buttons" }, [
            element("button", {
              class: "chip solid",
              text: "Speichern",
              onclick: async () => {
                const username = $("acct-user").value.trim();
                const password = $("acct-pass").value;
                const passwordConfirm = $("acct-pass2").value;
                if (!password) {
                  toast("Passwort eingeben oder «Passwort entfernen» nutzen");
                  return;
                }
                try {
                  const data = await api("/api/account", {
                    method: "POST",
                    body: { username, password, passwordConfirm },
                  });
                  if (data.token) localStorage.setItem(TOKEN_KEY, data.token);
                  toast(data.passwordRequired ? "Passwort gespeichert" : "Bitte ein Passwort eingeben");
                  render(state.route, true);
                } catch (error) {
                  toast(error.message);
                }
              },
            }),
            status.passwordRequired
              ? element("button", {
                  class: "chip",
                  text: "Passwort entfernen",
                  onclick: async () => {
                    try {
                      const data = await api("/api/account", {
                        method: "POST",
                        body: {
                          username: $("acct-user").value.trim(),
                          password: "",
                          passwordConfirm: "",
                        },
                      });
                      if (data.token) localStorage.setItem(TOKEN_KEY, data.token);
                      toast("Passwort entfernt – Zugang wieder offen");
                      render(state.route, true);
                    } catch (error) {
                      toast(error.message);
                    }
                  },
                })
              : null,
          ]),
        ]),
      ]),

      navidromeSettings(status),

      mediasyncSettings(status),

      element("div", { class: "section" }, [
        element("h2", { text: "Wiedergabe" }),
        element("div", { class: "chips" }, [
          element("button", { class: "chip", text: "Equalizer & Crossfade", onclick: openEqualizerSheet }),
          element("button", { class: "chip", text: "Sleep-Timer", onclick: openSleepSheet }),
        ]),
      ]),

      element("div", { class: "section" }, [
        element("h2", { text: "Amperfy verbinden" }),
        element("dl", { class: "info" }, [
          element("dt", { text: "Servertyp" }),
          element("dd", { text: "Subsonic" }),
          element("dt", { text: "URL" }),
          element("dd", { text: window.location.origin }),
          element("dt", { text: "Benutzer" }),
          element("dd", { text: status.subsonicUser }),
          element("dt", { text: "Passwort" }),
          element("dd", { text: status.passwordRequired ? "wie unter Zugang gesetzt" : "kein Passwort nötig" }),
        ]),
        element("p", {
          class: "muted",
          text: "Amperfy bringt CarPlay mit – das ist hier das Gegenstück zu Android Auto.",
        }),
      ]),

      element("div", { class: "section" }, [
        element("h2", { text: "Status" }),
        element("dl", { class: "info" }, [
          element("dt", { text: "Version" }),
          element("dd", { text: status.version }),
          element("dt", { text: "Titel in Bibliothek" }),
          element("dd", { text: String(status.libraryTracks) }),
          element("dt", { text: "Server-Zwischenspeicher" }),
          element("dd", { text: `${status.cache.tracks} Titel · ${formatBytes(status.cache.bytes)}` }),
          element("dt", { text: "Offline auf diesem Gerät" }),
          element("dd", { text: `${Offline.tracks().length} Titel · ${formatBytes(usage.usage)}` }),
          element("dt", { text: "Songtext-Übersetzung" }),
          element("dd", {
            text: status.translation.enabled
              ? `${status.translation.provider} → ${status.translation.language}`
              : "nicht konfiguriert",
          }),
          element("dt", { text: "SponsorBlock" }),
          element("dd", { text: status.sponsorblock ? "aktiv" : "aus" }),
          element("dt", { text: "YouTube-Music-Konto" }),
          element("dd", { text: status.ytmAccount ? "verbunden" : "nicht verbunden" }),
          element("dt", { text: "Navidrome" }),
          element("dd", {
            text: status.navidrome && status.navidrome.configured
              ? status.navidrome.reachable
                ? `verbunden${status.navidrome.version ? " · " + status.navidrome.version : ""}`
                : "konfiguriert, nicht erreichbar"
              : "nicht konfiguriert",
          }),
          element("dt", { text: "MediaSync" }),
          element("dd", {
            text: status.mediasync && status.mediasync.configured
              ? status.mediasync.reachable
                ? `verbunden${status.mediasync.version ? " · " + status.mediasync.version : ""}`
                : "konfiguriert, nicht erreichbar"
              : "nicht konfiguriert",
          }),
        ]),
        element("button", {
          class: "chip",
          text: "Server-Zwischenspeicher leeren",
          onclick: async () => {
            await api("/api/cache/clear", { method: "POST" });
            toast("Geleert");
            render(state.route, true);
          },
        }),
        status.passwordRequired
          ? element("button", {
              class: "chip",
              text: "Abmelden",
              onclick: async () => {
                await api("/api/logout", { method: "POST" });
                localStorage.removeItem(TOKEN_KEY);
                location.reload();
              },
            })
          : null,
      ])
    );
  }

  // -------------------------------------------------------- track actions

  async function waitForJobs(jobs, button) {
    const pending = (jobs || []).filter((job) => job.status === "queued" || job.status === "running");
    let remaining = pending.slice();
    const deadline = Date.now() + 5 * 60 * 1000;
    while (remaining.length) {
      if (Date.now() > deadline) throw new Error("Navidrome-Import dauert zu lange");
      if (button) button.textContent = `↓ ${jobs.length - remaining.length}/${jobs.length}`;
      await new Promise((resolve) => setTimeout(resolve, 1200));
      const updates = await Promise.all(remaining.map((job) => api(`/api/navidrome/job/${job.id}`).catch(() => job)));
      remaining = updates.filter((job) => job.status === "queued" || job.status === "running");
      const failed = updates.find((job) => job.status === "error");
      if (failed) throw new Error(failed.error || "Import fehlgeschlagen");
    }
    return jobs;
  }

  async function importToNavidrome(tracks, button) {
    const pending = tracks.filter((track) => track.id && !isLocalTrack(track) && !String(track.id).startsWith("nd:"));
    if (!pending.length) {
      toast("Bereits in Navidrome");
      return;
    }
    if (button) button.disabled = true;
    const result = await api("/api/navidrome/import", {
      method: "POST",
      body: { videoIds: pending.map((track) => track.id) },
    });
    await waitForJobs(result.jobs, button);
    pending.forEach((track) => {
      track.inLibrary = true;
      track.source = "both";
    });
    if (button) {
      button.disabled = false;
      button.textContent = mediasyncReady() ? "↓ Download" : navidromeReady() ? "↓ Nach Navidrome" : "↓ Alle offline";
    }
    toast(pending.length === 1 ? "In Navidrome gespeichert" : `${pending.length} Titel in Navidrome`);
    refreshOfflineBadges();
  }

  async function downloadAll(tracks, button) {
    if (mediasyncReady()) {
      openDownloadSheet(tracks);
      return;
    }
    if (navidromeReady()) {
      try {
        await importToNavidrome(tracks, button);
      } catch (error) {
        if (button) {
          button.disabled = false;
          button.textContent = "↓ Nach Navidrome";
        }
        toast(error.message);
      }
      return;
    }
    const pending = tracks.filter((track) => !Offline.has(track.id));
    if (!pending.length) {
      toast("Bereits alle offline verfügbar");
      return;
    }
    await Offline.requestPersistence();
    if (button) button.disabled = true;

    const result = await Offline.downloadMany(pending, (done, total) => {
      if (button) button.textContent = `↓ ${done}/${total}`;
    });

    if (button) {
      button.disabled = false;
      button.textContent = "↓ Alle offline";
    }
    toast(result.failed ? `${result.failed} Titel fehlgeschlagen` : "Offline gespeichert");
    refreshOfflineBadges();
  }

  function refreshOfflineBadges() {
    document.querySelectorAll("#view .track").forEach((row) => {
      const videoId = row.dataset.videoId;
      const already = row.querySelector(".pill");
      const local = already && already.textContent === "Navidrome";
      const cached = Offline.has(videoId);
      if (local) return;
      if (cached && !already) row.insertBefore(element("span", { class: "pill", text: "Offline" }), row.lastChild);
      if (!cached && already && already.textContent === "Offline") already.remove();
    });
  }

  function openTrackSheet(track) {
    const cached = Offline.has(track.id);
    const local = isLocalTrack(track);
    const actions = [
      element("button", {
        class: "action",
        text: "Als Nächstes abspielen",
        onclick: () => {
          Player.enqueueNext(track);
          closeSheet();
          toast("Zur Warteschlange hinzugefügt");
        },
      }),
      element("button", {
        class: "action",
        text: track.starred ? "Aus Favoriten entfernen" : "Zu Favoriten hinzufügen",
        onclick: async () => {
          await toggleFavorite(track.sid, null, !track.starred);
          track.starred = !track.starred;
          closeSheet();
        },
      }),
    ];

    if (mediasyncReady()) {
      actions.push(
        element("button", {
          class: "action",
          text: "Herunterladen …",
          onclick: () => {
            closeSheet();
            openDownloadSheet(track);
          },
        })
      );
    } else if (navidromeReady() && !String(track.id || "").startsWith("nd:")) {
      actions.push(
        element("button", {
          class: "action",
          text: local ? "Bereits in Navidrome" : "Nach Navidrome laden",
          onclick: async (event) => {
            if (local) {
              closeSheet();
              return;
            }
            const button = event.currentTarget;
            button.textContent = "Wird geladen …";
            try {
              await importToNavidrome([track], button);
              closeSheet();
            } catch (error) {
              toast(error.message);
            }
          },
        })
      );
    }

    actions.push(
      element("button", {
        class: "action",
        text: cached ? "Offline-Kopie löschen" : "Aufs Gerät speichern",
        onclick: async (event) => {
          const button = event.currentTarget;
          if (cached) {
            await Offline.remove(track.id);
            toast("Gelöscht");
          } else {
            button.textContent = "Wird geladen …";
            await Offline.requestPersistence();
            try {
              await Offline.download(track);
              toast("Offline gespeichert");
            } catch (error) {
              toast(error.message);
            }
          }
          closeSheet();
          refreshOfflineBadges();
        },
      })
    );

    if (!String(track.id || "").startsWith("nd:")) {
      actions.push(
        element("button", {
          class: "action",
          text: "Radio starten",
          onclick: () => {
            closeSheet();
            startRadio(track);
          },
        })
      );
    }

    if (track.album_id) {
      actions.push(
        element("button", {
          class: "action",
          text: "Zum Album",
          onclick: () => {
            closeSheet();
            navigate({ name: "album", id: track.album_id });
          },
        })
      );
    }
    if (track.artist_id) {
      actions.push(
        element("button", {
          class: "action",
          text: "Zum Interpreten",
          onclick: () => {
            closeSheet();
            navigate({ name: "artist", id: track.artist_id });
          },
        })
      );
    }
    if (track.podcast_id) {
      actions.push(
        element("button", {
          class: "action",
          text: "Zum Podcast",
          onclick: () => {
            closeSheet();
            navigate({ name: "podcast", id: track.podcast_id });
          },
        })
      );
    }

    for (const playlist of state.playlists) {
      actions.push(
        element("button", {
          class: "action",
          text: `Zu "${playlist.name}" hinzufügen`,
          onclick: async () => {
            await api(`/api/playlist/${playlist.id}/tracks`, { method: "POST", body: { tracks: [track.sid] } });
            closeSheet();
            toast("Hinzugefügt");
          },
        })
      );
    }

    openSheet(`${track.title} · ${track.artist}`, actions);
  }

  async function toggleFavorite(sid, button, forced) {
    const starred = forced !== undefined ? forced : !(button && button.classList.contains("active"));
    await api("/api/favorite", { method: "POST", body: { sid, starred } });
    if (button) {
      button.classList.toggle("active", starred);
      button.textContent = starred ? "♥ Favorit" : "♡ Favorit";
    }
    toast(starred ? "Zu Favoriten hinzugefügt" : "Aus Favoriten entfernt");
  }

  async function startRadio(track) {
    toast("Radio wird geladen …");
    const data = await api(`/api/radio/${encodeURIComponent(track.id)}`);
    Player.play([track, ...data.tracks.filter((item) => item.id !== track.id)], 0);
  }

  // ------------------------------------------------- equaliser & sleep UI

  async function saveSettings(section, value) {
    await api(`/api/settings/${section}`, { method: "PUT", body: value });
  }

  function persistPlayer() {
    saveSettings("player", {
      crossfade: Player.settings.crossfade,
      sponsorblock: Player.settings.sponsorblock,
      volume: Player.settings.volume,
      muted: Player.settings.muted,
      rate: Player.settings.rate,
    });
  }

  function openEqualizerSheet() {
    const settings = Player.settings;
    const nodes = [];

    const enable = element("label", { class: "switch" }, [
      element("input", {
        type: "checkbox",
        checked: settings.eqEnabled ? "checked" : null,
        onchange: (event) => {
          Player.updateSettings({ eqEnabled: event.target.checked });
          persistEqualizer();
        },
      }),
      element("span", { text: "Equalizer aktiv" }),
    ]);
    nodes.push(enable);

    const presetSelect = element(
      "select",
      {
        class: "select",
        onchange: (event) => {
          const gains = EQ.PRESETS[event.target.value];
          if (!gains) return;
          Player.updateSettings({
            eqPreset: event.target.value,
            mode: "graphic",
            gains: gains.slice(),
            filters: null,
          });
          persistEqualizer();
          openEqualizerSheet();
        },
      },
      Object.keys(EQ.PRESETS).map((name) =>
        element("option", { value: name, text: name, selected: settings.eqPreset === name ? "selected" : null })
      )
    );
    nodes.push(element("div", { class: "field" }, [element("label", { text: "Profil" }), presetSelect]));

    if (settings.mode === "parametric") {
      nodes.push(
        element("p", {
          class: "muted",
          text: `AutoEq-Profil aktiv (${(settings.filters || []).length} Filter, Preamp ${settings.preamp} dB). Wähle ein Profil, um zurückzuwechseln.`,
        })
      );
    } else {
      const sliders = EQ.BANDS.map((band, position) =>
        element("div", { class: "eq-band" }, [
          element("span", { class: "eq-value", text: `${(settings.gains[position] || 0).toFixed(1)}` }),
          element("input", {
            type: "range",
            min: "-12",
            max: "12",
            step: "0.5",
            value: String(settings.gains[position] || 0),
            oninput: (event) => {
              const gains = Player.settings.gains.slice();
              gains[position] = parseFloat(event.target.value);
              Player.updateSettings({ gains, eqPreset: "Eigen" });
              event.target.parentElement.querySelector(".eq-value").textContent = gains[position].toFixed(1);
            },
            onchange: persistEqualizer,
          }),
          element("span", { class: "eq-label", text: band >= 1000 ? `${band / 1000}k` : String(band) }),
        ])
      );
      nodes.push(element("div", { class: "eq-grid" }, sliders));
    }

    const autoeq = element("textarea", {
      class: "textarea",
      rows: "4",
      placeholder: "AutoEq-Text einfügen (ParametricEQ.txt oder GraphicEQ.txt)",
    });
    nodes.push(
      element("div", { class: "field" }, [
        element("label", { text: "AutoEq-Profil importieren" }),
        autoeq,
        element("button", {
          class: "chip solid",
          text: "Importieren",
          onclick: () => {
            const parsed = EQ.parseAutoEq(autoeq.value);
            if (!parsed) {
              toast("Format nicht erkannt");
              return;
            }
            Player.updateSettings({
              eqEnabled: true,
              eqPreset: "AutoEq",
              mode: parsed.mode,
              preamp: parsed.preamp,
              gains: parsed.gains || Player.settings.gains,
              filters: parsed.filters || null,
            });
            persistEqualizer();
            toast(parsed.mode === "parametric" ? "AutoEq-Filter übernommen" : "AutoEq-Kurve übernommen");
            openEqualizerSheet();
          },
        }),
      ])
    );

    const crossfade = element("input", {
      type: "range",
      min: "0",
      max: "12",
      step: "1",
      value: String(settings.crossfade),
      oninput: (event) => {
        event.target.nextElementSibling.textContent = `${event.target.value} s`;
      },
      onchange: (event) => {
        Player.updateSettings({ crossfade: parseInt(event.target.value, 10) });
        persistPlayer();
      },
    });
    nodes.push(
      element("div", { class: "field" }, [
        element("label", { text: "Crossfade" }),
        crossfade,
        element("span", { class: "muted", text: `${settings.crossfade} s` }),
      ])
    );

    nodes.push(
      element("label", { class: "switch" }, [
        element("input", {
          type: "checkbox",
          checked: settings.sponsorblock ? "checked" : null,
          onchange: (event) => {
            Player.updateSettings({ sponsorblock: event.target.checked });
            persistPlayer();
          },
        }),
        element("span", { text: "SponsorBlock: Nicht-Musik überspringen" }),
      ])
    );

    nodes.push(
      element("p", {
        class: "muted",
        text: "Equalizer und Crossfade leiten den Ton über Web Audio. Auf dem iPhone kann das die Wiedergabe bei gesperrtem Bildschirm stören – bei Problemen beides ausschalten und die Seite neu laden.",
      })
    );

    openSheet("Equalizer & Crossfade", nodes);
  }

  function persistEqualizer() {
    const { eqEnabled, eqPreset, gains, preamp, mode, filters } = Player.settings;
    saveSettings("equalizer", { eqEnabled, eqPreset, gains, preamp, mode, filters });
  }

  function openSleepSheet() {
    const options = [15, 30, 45, 60, 90].map((minutes) =>
      element("button", {
        class: "action",
        text: `In ${minutes} Minuten`,
        onclick: () => {
          Player.setSleepTimer("minutes", minutes);
          closeSheet();
          toast(`Sleep-Timer: ${minutes} Minuten`);
        },
      })
    );

    options.push(
      element("button", {
        class: "action",
        text: "Am Ende des Titels",
        onclick: () => {
          Player.setSleepTimer("track");
          closeSheet();
          toast("Stoppt nach diesem Titel");
        },
      }),
      element("button", {
        class: "action",
        text: "Timer ausschalten",
        onclick: () => {
          Player.clearSleepTimer();
          closeSheet();
          toast("Sleep-Timer aus");
        },
      })
    );

    openSheet("Sleep-Timer", options);
  }

  // ---------------------------------------------------------- player wiring

  Player.init([$("audio-a"), $("audio-b")]);
  syncVolumeUi();
  syncRateButton();

  Player.on("track", async (track) => {
    $("mini-player").hidden = false;
    $("mini-cover").src = coverUrl(track.thumbnail, 128);
    $("mini-title").textContent = track.title;
    $("mini-artist").textContent = track.artist;

    $("np-cover").src = coverUrl(track.thumbnail, 640);
    $("np-title").textContent = track.title;
    $("np-artist").textContent = track.artist;
    $("np-duration").textContent = formatTime(track.duration);
    setFavoriteButton(track);
    setLibraryButtons(track);
    syncRateButton();
    renderQueue();
    highlightPlaying(track);

    state.lyrics = null;
    state.translation = null;
    state.lyricsTranslated = false;
    $("np-lyrics").replaceChildren();
    $("np-votes").hidden = true;

    loadVotes(track);
    if (!$("np-lyrics-panel").hidden) loadLyrics(track);
  });

  Player.on("state", (playing) => {
    const symbol = playing ? "❚❚" : "▶";
    $("mini-toggle").textContent = symbol;
    $("np-toggle").textContent = symbol;
  });

  Player.on("time", ({ position, duration }) => {
    if (!progressDragging && duration > 0) {
      $("np-progress").value = Math.round((position / duration) * 1000);
    }
    $("np-elapsed").textContent = formatTime(position);
    $("np-duration").textContent = formatTime(duration);
    syncLyrics(position * 1000);
  });

  Player.on("skip", () => toast("Nicht-Musik übersprungen"));
  Player.on("error", (message) => toast(message));

  Player.on("sleep", (sleep) => {
    const badge = $("np-sleep-info");
    if (!sleep || !sleep.mode) {
      badge.hidden = true;
      return;
    }
    badge.hidden = false;
    badge.textContent =
      sleep.mode === "track"
        ? "Stoppt nach dem Titel"
        : `Stoppt um ${new Date(sleep.deadline).toLocaleTimeString("de-CH", { hour: "2-digit", minute: "2-digit" })}`;
  });

  function highlightPlaying(track) {
    document.querySelectorAll("#view .track").forEach((row) => {
      row.classList.toggle("playing", row.dataset.videoId === track.id);
    });
  }

  function setFavoriteButton(track) {
    $("np-star").textContent = track.starred ? "♥ Favorit" : "♡ Favorit";
    $("np-star").classList.toggle("active", !!track.starred);
  }

  function setLibraryButtons(track) {
    const local = isLocalTrack(track);
    const cached = Offline.has(track.id);
    if (mediasyncReady()) {
      $("np-navidrome").textContent = "↓ Download";
      $("np-navidrome").classList.remove("active");
    } else {
      $("np-navidrome").textContent = local ? "✓ In Navidrome" : "↓ In Navidrome";
      $("np-navidrome").classList.toggle("active", local);
    }
    $("np-offline").textContent = cached ? "✓ Offline" : "↓ Offline";
    $("np-offline").classList.toggle("active", cached);
  }

  function formatRate(rate) {
    return rate === 1 ? "1×" : `${rate}×`;
  }

  function syncRateButton() {
    $("np-rate").textContent = formatRate(Player.settings.rate || 1);
    $("np-rate").classList.toggle("active", (Player.settings.rate || 1) !== 1);
  }

  function syncVolumeUi() {
    const stored = Math.round((Player.settings.volume ?? 1) * 100);
    $("np-volume").value = String(stored);
    $("np-volume-label").textContent = Player.settings.muted ? "Stumm" : `${stored}%`;
    $("np-mute").textContent = Player.settings.muted || stored === 0 ? "🔇" : "🔊";
    $("np-mute").classList.toggle("active", !!Player.settings.muted);
  }

  async function openAddToPlaylistSheet(track) {
    if (!state.playlists.length) {
      const data = await api("/api/playlists").catch(() => ({ playlists: [] }));
      state.playlists = data.playlists || [];
    }
    const actions = [
      element("button", {
        class: "action",
        text: "+ Neue Playlist",
        onclick: async () => {
          const name = prompt("Name der Playlist");
          if (!name) return;
          await api("/api/playlists", { method: "POST", body: { name, tracks: [track.sid] } });
          const data = await api("/api/playlists").catch(() => ({ playlists: [] }));
          state.playlists = data.playlists || [];
          closeSheet();
          toast(`In „${name}“ gespeichert`);
        },
      }),
      ...state.playlists.map((playlist) =>
        element("button", {
          class: "action",
          text: playlist.name,
          onclick: async () => {
            await api(`/api/playlist/${playlist.id}/tracks`, { method: "POST", body: { tracks: [track.sid] } });
            closeSheet();
            toast(`Zu „${playlist.name}“ hinzugefügt`);
          },
        })
      ),
    ];
    if (actions.length === 1) {
      actions.push(element("p", { class: "muted", text: "Noch keine Playlists – oben eine anlegen." }));
    }
    openSheet("Zur Playlist hinzufügen", actions);
  }

  const MEDIASYNC_TARGET_KEY = "mp.mediasync.target";
  const mediasyncTargetsCache = { at: 0, items: [] };

  function asTrackList(tracks) {
    return (Array.isArray(tracks) ? tracks : [tracks]).filter(Boolean);
  }

  async function loadMediaSyncTargets(force) {
    if (!force && Date.now() - mediasyncTargetsCache.at < 30000 && mediasyncTargetsCache.items.length) {
      return mediasyncTargetsCache.items;
    }
    const data = await api("/api/mediasync/targets");
    mediasyncTargetsCache.at = Date.now();
    mediasyncTargetsCache.items = data.targets || [];
    return mediasyncTargetsCache.items;
  }

  async function sendToMediaSync(tracks, playlistId, playlistName) {
    const list = asTrackList(tracks);
    const result = await api("/api/mediasync/send", {
      method: "POST",
      body: {
        tracks: list.map((track) => ({
          videoId: track.id,
          title: track.title,
          artist: track.artist,
          album: track.album || "",
        })),
        playlistId: playlistId || "",
        playlistName: playlistName || "",
      },
    });
    try {
      localStorage.setItem(
        MEDIASYNC_TARGET_KEY,
        JSON.stringify({ playlistId: playlistId || "", playlistName: playlistName || "" })
      );
    } catch (error) {
      /* ignore */
    }
    const dest = playlistName || (playlistId && playlistId !== "library" ? "Playlist" : "Bibliothek");
    const count = list.length;
    toast(
      count === 1
        ? `Download gestartet (${dest})`
        : `${count} Titel an MediaSync übergeben (${dest})`
    );
    return result;
  }

  function playlistButton(target, last, tracks) {
    const selected = last.playlistId === target.id;
    return element("button", {
      class: `action${selected ? " active" : ""}`,
      text: selected ? `✓ ${target.name}` : target.name,
      onclick: async () => {
        try {
          await sendToMediaSync(tracks, target.id);
          closeSheet();
        } catch (error) {
          toast(error.message);
        }
      },
    });
  }

  async function openDownloadSheet(tracks) {
    const list = asTrackList(tracks);
    if (!list.length) return;
    if (!mediasyncReady()) {
      toast("MediaSync unter Mehr verbinden");
      $("now-playing").hidden = true;
      navigate({ name: "settings" });
      return;
    }
    const heading = list.length === 1 ? "Download – Playlist wählen" : `Download – ${list.length} Titel`;
    openSheet(heading, [element("p", { class: "muted", text: "Lade Playlists …" })]);
    let targets = [];
    try {
      targets = await loadMediaSyncTargets(true);
    } catch (error) {
      openSheet(heading, [element("p", { class: "muted", text: error.message })]);
      return;
    }
    let last = {};
    try {
      last = JSON.parse(localStorage.getItem(MEDIASYNC_TARGET_KEY) || "{}");
    } catch (error) {
      last = {};
    }

    const playlists = targets
      .filter((target) => target.kind !== "library" && target.id)
      .sort((a, b) => {
        if (a.id === last.playlistId) return -1;
        if (b.id === last.playlistId) return 1;
        return String(a.name || "").localeCompare(String(b.name || ""), "de");
      });
    const library = targets.find((target) => target.kind === "library") || {
      id: "library",
      name: "Nur Bibliothek (ohne Playlist)",
      kind: "library",
    };

    const summary =
      list.length === 1
        ? `${list[0].artist} – ${list[0].title}. MediaSync lädt den Titel und legt ihn in der Bibliothek ab.`
        : `${list.length} Titel. MediaSync lädt und legt sie in der Bibliothek ab.`;

    const listBox = element("div", {});
    const paint = (query) => {
      const needle = (query || "").trim().toLowerCase();
      const shown = playlists.filter((item) => !needle || String(item.name || "").toLowerCase().includes(needle));
      const children = shown.map((item) => playlistButton(item, last, list));
      if (!shown.length) {
        children.push(
          element("p", {
            class: "muted",
            text: needle ? "Keine Playlist zu diesem Namen." : "Noch keine Playlists in MediaSync.",
          })
        );
      }
      listBox.replaceChildren(...children);
    };
    paint("");

    const nodes = [
      element("p", { class: "muted", text: summary }),
      playlists.length > 6
        ? element("input", {
            type: "search",
            placeholder: "Bestehende Playlist suchen",
            autocomplete: "off",
            oninput: (event) => paint(event.target.value),
          })
        : null,
      playlists.length ? element("p", { class: "sheet-title", text: "Bestehende Playlists" }) : null,
      listBox,
      element("p", { class: "sheet-title", text: "Weitere Ziele" }),
      element("button", {
        class: "action",
        text: "+ Neue Playlist",
        onclick: async () => {
          const name = prompt("Name der Playlist in MediaSync");
          if (!name) return;
          try {
            await sendToMediaSync(list, "", name);
            closeSheet();
          } catch (error) {
            toast(error.message);
          }
        },
      }),
      playlistButton({ ...library, name: library.name || "Nur Bibliothek (ohne Playlist)" }, last, list),
    ];
    openSheet(heading, nodes.filter(Boolean));
  }

  function renderQueue() {
    const upcoming = Player.queue.slice(Player.index + 1, Player.index + 21);
    $("np-queue").replaceChildren(
      element("h3", { text: upcoming.length ? "Als Nächstes" : "Warteschlange leer" }),
      ...upcoming.map((track, offset) =>
        element("button", { class: "track", onclick: () => Player.jumpTo(Player.index + 1 + offset) }, [
          cover(track.thumbnail, 48),
          element("div", { class: "track-text" }, [
            element("strong", { text: track.title }),
            element("span", { text: track.artist }),
          ]),
        ])
      )
    );
  }

  async function loadVotes(track) {
    try {
      const votes = await api(`/api/votes/${encodeURIComponent(track.id)}`);
      if (!votes || votes.likes === undefined) return;
      $("np-votes").textContent = `👍 ${votes.likes.toLocaleString("de-CH")} · 👎 ${votes.dislikes.toLocaleString(
        "de-CH"
      )}`;
      $("np-votes").hidden = false;
    } catch (error) {
      /* optional */
    }
  }

  async function loadLyrics(track, translate) {
    const container = $("np-lyrics");
    container.replaceChildren(element("p", { class: "muted", text: "Songtext wird geladen …" }));

    const query = translate ? `?translate=true` : "";
    const data = await api(`/api/lyrics/${encodeURIComponent(track.id)}${query}`).catch(() => null);

    if (!data || !data.text) {
      container.replaceChildren(element("p", { class: "muted", text: "Kein Songtext gefunden." }));
      $("lyrics-translate").hidden = true;
      return;
    }

    state.lyrics = data;
    state.translation = data.translation;
    $("lyrics-source").textContent = data.source || "";
    $("lyrics-translate").hidden = !data.translationAvailable && !data.translation;
    renderLyrics();
  }

  function renderLyrics() {
    const container = $("np-lyrics");
    const data = state.lyrics;
    if (!data) return;

    const translated = state.lyricsTranslated && state.translation ? state.translation.lines : null;

    if (data.synced) {
      container.replaceChildren(
        ...data.synced.map((line, position) =>
          element("div", { class: "line", "data-start": line.start ?? 0 }, [
            element("span", { text: line.text }),
            translated && translated[position]
              ? element("em", { class: "line-translation", text: translated[position] })
              : null,
          ])
        )
      );
      return;
    }

    const originals = data.text.split("\n");
    container.replaceChildren(
      ...originals.map((line, position) =>
        element("div", { class: "line" }, [
          element("span", { text: line }),
          translated && translated[position]
            ? element("em", { class: "line-translation", text: translated[position] })
            : null,
        ])
      )
    );
  }

  function syncLyrics(currentMs) {
    if (!state.lyrics || !state.lyrics.synced || $("np-lyrics-panel").hidden) return;
    const lines = $("np-lyrics").querySelectorAll(".line");
    let activeIndex = -1;
    lines.forEach((line, position) => {
      if (Number(line.dataset.start) <= currentMs) activeIndex = position;
      line.classList.remove("active");
    });
    if (activeIndex >= 0) {
      lines[activeIndex].classList.add("active");
      lines[activeIndex].scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }

  // ------------------------------------------------------------- controls

  let progressDragging = false;
  $("np-progress").addEventListener("input", () => (progressDragging = true));
  $("np-progress").addEventListener("change", (event) => {
    progressDragging = false;
    const duration = Player.duration();
    if (duration > 0) Player.seek((event.target.value / 1000) * duration);
  });

  $("mini-toggle").addEventListener("click", (event) => {
    event.stopPropagation();
    Player.toggle();
  });
  $("mini-prev").addEventListener("click", (event) => {
    event.stopPropagation();
    Player.previous();
  });
  $("mini-next").addEventListener("click", (event) => {
    event.stopPropagation();
    Player.next(false);
  });
  $("mini-player").addEventListener("click", () => ($("now-playing").hidden = false));
  $("collapse-player").addEventListener("click", () => ($("now-playing").hidden = true));
  $("np-more").addEventListener("click", () => Player.current() && openTrackSheet(Player.current()));

  $("np-toggle").addEventListener("click", () => Player.toggle());
  $("np-next").addEventListener("click", () => Player.next(false));
  $("np-prev").addEventListener("click", () => Player.previous());

  $("np-shuffle").addEventListener("click", (event) => {
    Player.shuffle = !Player.shuffle;
    event.currentTarget.classList.toggle("active", Player.shuffle);
    toast(Player.shuffle ? "Zufallswiedergabe an" : "Zufallswiedergabe aus");
  });

  $("np-repeat").addEventListener("click", (event) => {
    Player.repeat = Player.repeat === "off" ? "all" : Player.repeat === "all" ? "one" : "off";
    event.currentTarget.classList.toggle("active", Player.repeat !== "off");
    event.currentTarget.textContent = Player.repeat === "one" ? "🔂" : "🔁";
    toast({ off: "Wiederholung aus", all: "Alle wiederholen", one: "Titel wiederholen" }[Player.repeat]);
  });

  $("np-star").addEventListener("click", async () => {
    const track = Player.current();
    if (!track) return;
    await toggleFavorite(track.sid, null, !track.starred);
    track.starred = !track.starred;
    setFavoriteButton(track);
  });

  $("np-navidrome").addEventListener("click", () => {
    const track = Player.current();
    if (!track) return;
    if (mediasyncReady()) {
      openDownloadSheet(track);
      return;
    }
    if (!navidromeReady()) {
      toast("MediaSync unter Mehr verbinden");
      navigate({ name: "settings" });
      $("now-playing").hidden = true;
      return;
    }
    if (isLocalTrack(track)) {
      toast("Bereits in der Navidrome-Bibliothek");
      return;
    }
    $("np-navidrome").textContent = "…";
    importToNavidrome([track])
      .then(() => setLibraryButtons(track))
      .catch((error) => {
        toast(error.message);
        setLibraryButtons(track);
      });
  });

  $("np-offline").addEventListener("click", async () => {
    const track = Player.current();
    if (!track) return;
    if (Offline.has(track.id)) {
      await Offline.remove(track.id);
      toast("Offline-Kopie gelöscht");
    } else {
      $("np-offline").textContent = "…";
      await Offline.requestPersistence();
      try {
        await Offline.download(track);
        toast("Offline auf diesem Gerät gespeichert");
      } catch (error) {
        toast(error.message);
      }
    }
    setLibraryButtons(track);
    refreshOfflineBadges();
  });

  $("np-playlist").addEventListener("click", () => {
    const track = Player.current();
    if (track) openAddToPlaylistSheet(track);
  });

  $("np-skip-back").addEventListener("click", () => Player.skipBy(-10));
  $("np-skip-fwd").addEventListener("click", () => Player.skipBy(10));

  $("np-mute").addEventListener("click", () => {
    Player.toggleMute();
    syncVolumeUi();
    persistPlayer();
  });
  $("np-volume").addEventListener("input", (event) => {
    Player.setVolume(Number(event.target.value) / 100);
    syncVolumeUi();
  });
  $("np-volume").addEventListener("change", () => persistPlayer());

  const PLAYBACK_RATES = [1, 1.25, 1.5, 0.75];
  $("np-rate").addEventListener("click", () => {
    const current = Player.settings.rate || 1;
    const next = PLAYBACK_RATES[(PLAYBACK_RATES.indexOf(current) + 1) % PLAYBACK_RATES.length] || 1;
    Player.setRate(next);
    syncRateButton();
    persistPlayer();
    toast(`Tempo ${formatRate(next)}`);
  });

  $("np-radio").addEventListener("click", () => Player.current() && startRadio(Player.current()));
  $("np-sleep").addEventListener("click", openSleepSheet);
  $("np-eq").addEventListener("click", openEqualizerSheet);

  $("np-lyrics-toggle").addEventListener("click", () => {
    const panel = $("np-lyrics-panel");
    panel.hidden = !panel.hidden;
    if (!panel.hidden && Player.current()) loadLyrics(Player.current());
  });

  $("lyrics-translate").addEventListener("click", async () => {
    const track = Player.current();
    if (!track) return;

    if (state.translation) {
      state.lyricsTranslated = !state.lyricsTranslated;
      renderLyrics();
      return;
    }

    $("lyrics-translate").textContent = "Übersetzt …";
    await loadLyrics(track, true);
    $("lyrics-translate").textContent = "Übersetzen";

    if (state.translation) {
      state.lyricsTranslated = true;
      renderLyrics();
    } else {
      toast("Übersetzung nicht konfiguriert (TRANSLATE_PROVIDER)");
    }
  });

  // ------------------------------------------------------------------- boot

  window.addEventListener("online", () => {
    state.online = true;
    $("offline-badge").hidden = true;
  });
  window.addEventListener("offline", () => {
    state.online = false;
    $("offline-badge").hidden = false;
    toast("Offline – gespeicherte Titel bleiben abspielbar");
  });

  async function boot() {
    document.getElementById("login")?.remove();
    const session = await api("/api/session").catch(() => ({ user: null, passwordRequired: false }));
    state.passwordRequired = !!session.passwordRequired;
    state.user = session.user;
    $("offline-badge").hidden = navigator.onLine;

    try {
      const stored = await api("/api/settings");
      Player.updateSettings({ ...stored.player, ...stored.equalizer });
      syncVolumeUi();
      syncRateButton();
    } catch (error) {
      /* defaults are fine */
    }

    api("/api/playlists")
      .then((data) => (state.playlists = data.playlists))
      .catch(() => {});
    api("/api/navidrome")
      .then((data) => (state.navidrome = data))
      .catch(() => {});
    api("/api/mediasync")
      .then((data) => {
        state.mediasync = data;
        if (data && data.configured) loadMediaSyncTargets().catch(() => {});
      })
      .catch(() => {});

    state.route = { name: "home" };
    render(state.route);
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker
      .getRegistrations()
      .then((regs) => Promise.all(regs.map((reg) => reg.unregister())))
      .then(() => navigator.serviceWorker.register("/sw.js"))
      .catch(() => {});
  }

  boot().catch(() => {
    document.getElementById("login")?.remove();
    state.route = { name: "home" };
    render(state.route);
  });
})();
