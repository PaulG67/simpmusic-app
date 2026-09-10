/* SimpMusic web player - UI, routing and views.
   Playback itself lives in player.js, downloads in offline.js. */

(() => {
  "use strict";

  const { Player, Offline, EQ } = window.SM;
  const $ = (id) => document.getElementById(id);

  const state = {
    user: null,
    stack: [],
    route: null,
    playlists: [],
    lyrics: null,
    translation: null,
    lyricsTranslated: false,
    status: null,
    online: navigator.onLine,
  };

  const PLACEHOLDER =
    "data:image/svg+xml;utf8," +
    encodeURIComponent(
      '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120"><rect width="120" height="120" fill="#1c212b"/><text x="60" y="72" font-size="42" text-anchor="middle" fill="#4a5364">&#9835;</text></svg>'
    );

  // ---------------------------------------------------------------- helpers

  async function api(path, options = {}) {
    const response = await fetch(path, {
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    if (response.status === 401) {
      $("login").hidden = false;
      throw new Error("Nicht angemeldet");
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

  function coverUrl(url, size = 320) {
    if (!url) return PLACEHOLDER;
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
    $("sheet-body").replaceChildren(element("p", { class: "sheet-title", text: title }), ...nodes);
    $("sheet").hidden = false;
  }

  $("sheet").addEventListener("click", (event) => {
    if (event.target === $("sheet")) closeSheet();
  });

  // ------------------------------------------------------------------ login

  $("login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = $("login-error");
    error.hidden = true;
    try {
      await api("/api/login", { method: "POST", body: { password: $("login-password").value } });
      $("login").hidden = true;
      $("login-password").value = "";
      boot();
    } catch (exception) {
      error.textContent = "Passwort falsch.";
      error.hidden = false;
    }
  });

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

    if (Offline.has(track.id)) row.append(element("span", { class: "pill", text: "Offline" }));
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

  function itemCard(item) {
    const open = {
      song: () => Player.play([item], 0),
      album: () => navigate({ name: "album", id: item.id }),
      artist: () => navigate({ name: "artist", id: item.id }),
      playlist: () => navigate({ name: "ytplaylist", id: item.id, title: item.name }),
      podcast: () => navigate({ name: "podcast", id: item.id }),
    };
    return element(
      "button",
      { class: `card ${item.kind === "artist" ? "round" : ""}`, onclick: open[item.kind] || open.song },
      [
        cover(item.thumbnail, 142),
        element("strong", { text: item.title || item.name }),
        element("span", { text: item.artist || item.author || item.owner || "" }),
      ]
    );
  }

  function sectionsFragment(sections) {
    const fragment = document.createDocumentFragment();
    for (const section of sections) {
      const songs = section.items.filter((item) => item.kind === "song");
      const body =
        songs.length === section.items.length && songs.length > 3
          ? trackList(songs)
          : element("div", { class: "row" }, section.items.map(itemCard));
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
          text: "↓ Alle offline",
          onclick: (event) => downloadAll(tracks, event.currentTarget),
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

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      input.blur();
      const query = input.value.trim();
      if (!query) return;
      renderSearch.lastQuery = query;
      results.replaceChildren(element("div", { class: "spinner", text: "Sucht …" }));

      const [music, podcasts] = await Promise.all([
        api(`/api/search?q=${encodeURIComponent(query)}`),
        api(`/api/podcasts/search?q=${encodeURIComponent(query)}&limit=8`).catch(() => ({
          podcasts: [],
          episodes: [],
        })),
      ]);

      const fragment = document.createDocumentFragment();
      const blocks = [
        ["Interpreten", music.artists, "row"],
        ["Alben", music.albums, "row"],
        ["Titel", music.songs, "list"],
        ["Podcasts", podcasts.podcasts, "row"],
        ["Episoden", podcasts.episodes, "list"],
      ];

      for (const [title, items, layout] of blocks) {
        if (!items || !items.length) continue;
        fragment.append(
          element("div", { class: "section" }, [
            element("h2", { text: title }),
            layout === "row" ? element("div", { class: "row" }, items.map(itemCard)) : trackList(items),
          ])
        );
      }
      if (!fragment.childNodes.length) fragment.append(element("div", { class: "empty", text: "Nichts gefunden." }));
      results.replaceChildren(fragment);
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

    for (const section of moods.sections) {
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

    if (podcasts.subscribed.length) {
      fragment.append(
        element("div", { class: "section" }, [
          element("h2", { text: "Deine Podcasts" }),
          element("div", { class: "row" }, podcasts.subscribed.map(itemCard)),
        ])
      );
    }
    if (podcasts.newEpisodes.length) {
      fragment.append(
        element("div", { class: "section" }, [
          element("h2", { text: "Neue Episoden" }),
          trackList(podcasts.newEpisodes),
        ])
      );
    }

    fragment.append(sectionsFragment(charts.sections));

    if (!fragment.childNodes.length) {
      fragment.append(element("div", { class: "empty", text: "Nichts zu entdecken – prüfe die Verbindung." }));
    }
    $("view").replaceChildren(fragment);
  }

  async function renderMood(params, title) {
    setTitle(title || "Kategorie");
    loading();
    const data = await api(`/api/mood?params=${encodeURIComponent(params)}`);
    $("view").replaceChildren(
      data.playlists.length
        ? element("div", { class: "grid" }, data.playlists.map(itemCard))
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

    $("view").replaceChildren(
      playAllHeader(
        playlist.name,
        `${tracks.length} Titel · ${formatDuration(playlist.duration)}`,
        playlist.thumbnail,
        tracks,
        [remove]
      ),
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

    const [favorites, playlists] = await Promise.all([api("/api/favorites"), api("/api/playlists")]);
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

  async function renderSettings() {
    setTitle("Mehr");
    loading();
    const status = await api("/api/status");
    state.status = status;
    const usage = await Offline.usage();

    $("view").replaceChildren(
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
          element("dd", { text: "wie bei der Anmeldung hier" }),
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
        element("button", {
          class: "chip",
          text: "Abmelden",
          onclick: async () => {
            await api("/api/logout", { method: "POST" });
            location.reload();
          },
        }),
      ])
    );
  }

  // -------------------------------------------------------- track actions

  async function downloadAll(tracks, button) {
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
      const cached = Offline.has(row.dataset.videoId);
      const badge = row.querySelector(".pill");
      if (cached && !badge) row.insertBefore(element("span", { class: "pill", text: "Offline" }), row.lastChild);
      if (!cached && badge) badge.remove();
    });
  }

  function openTrackSheet(track) {
    const cached = Offline.has(track.id);
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
      element("button", {
        class: "action",
        text: cached ? "Offline-Kopie löschen" : "Offline speichern",
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
      }),
      element("button", {
        class: "action",
        text: "Radio starten",
        onclick: () => {
          closeSheet();
          startRadio(track);
        },
      }),
    ];

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
        saveSettings("player", {
          crossfade: Player.settings.crossfade,
          sponsorblock: Player.settings.sponsorblock,
        });
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
            saveSettings("player", {
              crossfade: Player.settings.crossfade,
              sponsorblock: Player.settings.sponsorblock,
            });
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
    setDownloadButton(track);
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

  function setDownloadButton(track) {
    const cached = Offline.has(track.id);
    $("np-download").textContent = cached ? "✓ Offline" : "↓ Offline";
    $("np-download").classList.toggle("active", cached);
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

  $("np-download").addEventListener("click", async () => {
    const track = Player.current();
    if (!track) return;
    if (Offline.has(track.id)) {
      await Offline.remove(track.id);
      toast("Offline-Kopie gelöscht");
    } else {
      $("np-download").textContent = "…";
      await Offline.requestPersistence();
      try {
        await Offline.download(track);
        toast("Offline gespeichert");
      } catch (error) {
        toast(error.message);
      }
    }
    setDownloadButton(track);
    refreshOfflineBadges();
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
    const session = await api("/api/session");
    if (!session.authenticated) {
      $("login").hidden = false;
      return;
    }

    state.user = session.user;
    $("login").hidden = true;
    $("offline-badge").hidden = navigator.onLine;

    try {
      const stored = await api("/api/settings");
      Player.updateSettings({ ...stored.player, ...stored.equalizer });
    } catch (error) {
      /* defaults are fine */
    }

    api("/api/playlists")
      .then((data) => (state.playlists = data.playlists))
      .catch(() => {});

    state.route = { name: "home" };
    render(state.route);
  }

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  boot().catch(() => ($("login").hidden = false));
})();
