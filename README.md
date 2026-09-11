# Music Play

YouTube Music als selbst gehostete Docker-App auf Unraid – bedienbar im Safari auf dem
iPhone **und** über die Subsonic-Schnittstelle, an die sich [Amperfy](https://github.com/BLeeEZ/amperfy)
direkt anhängt. Optional kombiniert sie Entdecken von YouTube Music mit deiner
**Navidrome**-Bibliothek: Suche und Vorschläge von YouTube, Abspielen und Downloads von Navidrome.
Optional übergibst du einen Titel an **MediaSync** auf demselben Unraid – inkl. Auswahl einer
dortigen Playlist.

Die Funktionsliste orientiert sich an [SimpMusic](https://simpmusic.org). Wo eine native
Android-App etwas kann, das ein Browser nicht kann, steht das unten klar dabei.

## Warum nicht einfach SimpMusic im Browser?

[SimpMusic](https://simpmusic.org) ist eine native Android-App. Man kann sie nicht in einen
Browser packen. Was sie inhaltlich liefert, ist aber nachbaubar, denn sie ist im Kern ein
Client für die interne YouTube-Music-API.

Genau das macht diese App – nur eben als Webdienst, und mit einem zweiten Ausgang für
Amperfy. Beide Zugänge sprechen dieselbe Datenbasis: Was du im Browser als Favorit
markierst, taucht in Amperfy auf und umgekehrt.

```
                 ┌──────────────────────────────────────────┐
 iPhone Safari ──►  PWA  ─┐                                 │
                 │        ├─ FastAPI ─ ytmusicapi ──► YouTube Music (Suche, Start, Vorschläge)
 Amperfy ────────►  /rest ┘     │      yt-dlp   ──► YouTube (nur wenn der Titel nicht in Navidrome liegt)
   (Subsonic)                     │              ├─ SQLite (Favoriten, Verlauf, Zuordnung)
                 │              ├─ Navidrome (Bibliothek, Playlists, Download, Wiedergabe)
                 │              └─ MediaSync (optional: Download in Jellyfin-Playlist)
                 └──────────────────────────────────────────┘
```

## Funktionsabdeckung gegenüber SimpMusic

| SimpMusic | Hier | Anmerkung |
|---|---|---|
| Werbefreies Streaming | ja | YouTube-Music-Audio ohne Werbung |
| Hintergrund / Bildschirm aus | ja, mit Einschränkung | PWA + MediaSession; Equalizer aus lassen (siehe unten) |
| Offline-Modus | ja | Browser-Cache **oder** Download nach Navidrome (NAS) |
| Intelligentes Caching | ja | Server-Cache **und** Geräte-Cache |
| Synchronisierte Songtexte | ja | Zeile für Zeile, wenn YouTube Music Timestamps liefert |
| KI-Übersetzung | ja, optional | LibreTranslate oder beliebiger OpenAI-kompatibler Endpunkt |
| Charts | ja | Tab *Entdecken* |
| Stimmungen & Genres | ja | YouTube Musics Moods-&-Genres-Baum |
| Podcasts | ja | Suche, Abos, neue Episoden – auch in Amperfy |
| Personalisierte Empfehlungen | ja | Startseite von YouTube Music |
| 10-Band-Equalizer | ja | inkl. Profile und AutoEq-Import |
| Crossfade | ja | 0–12 Sekunden |
| Sleep-Timer | ja | Minuten oder „Ende des Titels“ |
| SponsorBlock | ja | Intros, Outros, Werbung, Nicht-Musik |
| ReturnYouTubeDislike | ja | Like-/Dislike-Zahlen am Now-Playing |
| Hörstatistiken | ja | lokal, keine Telemetrie |
| Jahresrückblick | ja | *Bibliothek → Statistiken → Rückblick* |
| Android Auto | **nein** | Browser können das nicht. Amperfy bringt **CarPlay** mit |
| Registrierungszwang / Tracking | nein | ein Passwort, alles bleibt auf dem NAS |

### Hintergrundwiedergabe auf dem iPhone

Safari spielt Audio im Hintergrund, wenn die Seite als PWA auf dem Home-Bildschirm
liegt. Sperrbildschirm und AirPods-Steuerung laufen über die MediaSession-API.

**Equalizer und Crossfade** leiten den Ton über die Web-Audio-API. Auf iOS kann das die
Wiedergabe bei gesperrtem Bildschirm stören. Deshalb sind beide standardmässig aus.
Wer sie braucht: einschalten und testen; bei Problemen ausschalten und die Seite neu laden.

### Offline

Mit Navidrome: *Nach Navidrome* speichert den Titel in deine Musikbibliothek auf dem NAS
(*⋯ → Nach Navidrome laden*). Danach spielt die App die lokale Datei. Zusätzlich kannst
du weiter eine Kopie *aufs Gerät* legen (Browser-Cache). Ohne Navidrome bleibt nur der
Geräte-Download wie bisher. Safari räumt Caches unter Speicherdruck auf – die NAS-Kopie
ist die dauerhafte Variante.

### KI-Übersetzung

Aus, solange du keinen Übersetzer konfigurierst. Zwei Varianten:

```env
# Selbst gehostetes LibreTranslate
TRANSLATE_PROVIDER=libretranslate
TRANSLATE_URL=http://libretranslate:5000

# Ollama / LM Studio / OpenAI
TRANSLATE_PROVIDER=openai
TRANSLATE_URL=http://ollama:11434/v1
TRANSLATE_MODEL=llama3.1
```

Ergebnisse werden lokal zwischengespeichert, dieselbe Übersetzung läuft also nur einmal.

### CarPlay statt Android Auto

Android Auto gibt es nur für native Android-Apps. Amperfy spricht Subsonic und bringt
CarPlay mit – das ist hier das Gegenstück. In Amperfy denselben Server eintragen wie in
der Weboberfläche unter *Mehr*.

## Wichtig: YouTube Music und Navidrome

Suche, Startseite, Charts und Radio kommen **live von YouTube Music**. Navidrome ist
die eigene Bibliothek: was schon dort liegt, wird von dort abgespielt. Download und
Playlists gehen nach Navidrome (Ordner `YouTube/` in deiner Musikbibliothek), danach
scannt Navidrome und der Titel ist dauerhaft auf dem NAS.

Ohne Navidrome verhält sich die App wie bisher: YouTube-Wiedergabe, Favoriten in
SQLite, optional Offline im Browser.

Amperfy hängt weiter an *dieser* App (Subsonic), nicht an Navidrome. Die Navidrome-
WebUI bleibt parallel nutzbar.

Direkt nach der Installation ist die lokale Favoritenliste leer – das ist kein Fehler.
Navidrome-Alben und -Playlists erscheinen in *Bibliothek*, sobald die Verbindung unter
*Mehr → Navidrome* steht.

## Installation auf Unraid

Wie bei Zoraxy Guard: Vorlage einmalig auf den USB-Stick legen, danach erscheint die App
unter **Docker → Container hinzufügen → Template**.

```bash
cd /mnt/user/appdata
git clone https://github.com/PaulG67/music-play.git
bash /mnt/user/appdata/music-play/unraid/install-template.sh
```

Dann in der Unraid-WebUI:

1. **Docker → Container hinzufügen**
2. Oben **Template** → **music-play** (User Templates)
3. **Passwort** setzen, Rest kann so bleiben
4. **Apply**

WebUI: `http://<unraid-ip>:5080`

Beim ersten Start ist **kein Passwort** nötig. Unter **Mehr → Zugang** kannst du
später Benutzername und Passwort setzen (gilt auch für Amperfy). Das Unraid-Feld
Passwort wird ignoriert.

Navidrome: unter **Mehr → Navidrome** URL, Benutzer und Passwort eintragen. In der
Unraid-Vorlage denselben **Musik**-Share wie Navidrome nach `/music` einhängen
(Standard `/mnt/user/music`). Imports landen in `/music/YouTube/<Interpret>/<Album>/`.

Das Image kommt von `ghcr.io/paulg67/music-play:latest`. Updates später über
**Docker → music-play → Force Update**.

Falls Unraid das Image nicht ziehen kann: [Package music-play](https://github.com/PaulG67/music-play/pkgs/container/music-play)
→ **Package settings → Change visibility → Public**.

### Optional: Docker Compose

```bash
cd /mnt/user/appdata/music-play
cp .env.example .env
docker compose up -d
```

### Speicherlimit nicht zu klein wählen

yt-dlp startet für YouTubes JavaScript-Challenges einen Deno-Prozess. Unterhalb von etwa
512 MB wird der vom Kernel abgeschossen, und im Log steht dann `Signature solving failed`.
Das mitgelieferte Compose-File und Template setzen deshalb 2 GB.

## iPhone: als App einrichten

1. `http://<unraid-ip>:5080` (oder die eigene Container-IP) in Safari öffnen und anmelden.
2. Teilen-Symbol → **Zum Home-Bildschirm**.

Ab dann startet sie im Vollbild ohne Safari-Leisten, mit Sperrbildschirm-Steuerung. Für
Zugriff von unterwegs die App hinter deinen Reverse Proxy hängen und `SERVER_URL` auf die
externe Adresse setzen – iOS installiert PWAs nur zuverlässig über HTTPS.

## Amperfy verbinden

In Amperfy → *Login*:

| Feld | Wert |
|---|---|
| Server-Typ | **Subsonic** |
| URL | `http://<unraid-ip>:5080` (oder eigene IP / HTTPS-Adresse) |
| Benutzername | unter *Mehr → Zugang*, sonst `SUBSONIC_USER` / `musicplay` |
| Passwort | leer, solange keins unter *Mehr* gesetzt ist |

Die gleichen Angaben stehen in der Weboberfläche unter *Mehr*. Andere Subsonic-Clients
(Symfonium, Feishin, play:Sub, Substreamer) funktionieren genauso. Amperfy bringt CarPlay,
Offline-Download und Equalizer nativ mit – fürs Auto also Amperfy, fürs Sofa die PWA.

## Konfiguration

Alle Werte als Umgebungsvariablen, siehe `.env.example`:

| Variable | Standard | Bedeutung |
|---|---|---|
| `PORT` | `5080` | Web- und Subsonic-Port. Nicht 5060 (Chrome: ERR_UNSAFE_PORT) |
| `SUBSONIC_USER` | `musicplay` | Vorschlag für den Benutzernamen, in der App änderbar |
| `SUBSONIC_PASSWORD` | leer | Wird ignoriert; Passwort in der App unter Mehr setzen |
| `SERVER_URL` | – | Externe Basis-URL hinter einem Reverse Proxy |
| `YTM_LANGUAGE` / `YTM_LOCATION` | `de` / `CH` | Sprache und Region der Ergebnisse |
| `STREAM_MODE` | `proxy` | `proxy` oder `redirect` (siehe unten) |
| `CACHE_ENABLED` / `CACHE_MAX_GB` | `true` / `10` | Zwischenspeicher für Audiodateien auf dem Server |
| `TRANSCODE_TO_AAC` | `true` | Opus nach AAC wandeln, falls nötig |
| `YTM_AUTH_FILE` | – | Optionale YouTube-Music-Anmeldung |
| `YTDLP_COOKIE_FILE` | – | Optionale Cookie-Datei für yt-dlp |
| `SPONSORBLOCK` | `true` | Intros/Outros überspringen |
| `RETURN_YOUTUBE_DISLIKE` | `true` | Like-/Dislike-Zahlen |
| `TRANSLATE_PROVIDER` | `none` | `none`, `libretranslate` oder `openai` |
| `TRANSLATE_URL` | – | Endpunkt des Übersetzers |
| `TRANSLATE_API_KEY` | – | Optional |
| `TRANSLATE_MODEL` | `gpt-4o-mini` | Nur bei `openai` |
| `TRANSLATE_TARGET` | – | Zielsprache, sonst `YTM_LANGUAGE` |
| `NAVIDROME_URL` | – | z.B. `http://192.168.0.188:4533` (auch in der App unter Mehr) |
| `NAVIDROME_USER` / `NAVIDROME_PASSWORD` | – | Navidrome-Login |
| `NAVIDROME_MUSIC_DIR` | `/music` | Schreibender Mount der Navidrome-Musikbibliothek |
| `NAVIDROME_IMPORT_FOLDER` | `YouTube` | Unterordner für Imports |
| `MEDIASYNC_URL` | – | z.B. `http://192.168.0.188:8090` (auch in der App unter Mehr) |
| `MEDIASYNC_USER` / `MEDIASYNC_PASSWORD` | – | nur wenn MediaSync Anmeldung verlangt |
| `LOG_LEVEL` | `INFO` | Protokollierung |

### Navidrome

1. In Unraid denselben Musikordner wie bei Navidrome als Pfad `/music` einhängen.
2. In der App *Mehr → Navidrome*: URL (die Navidrome-WebUI), Benutzer, Passwort.
3. Suche bleibt YouTube Music. Treffer, die schon in Navidrome liegen, zeigen das Badge
   **Navidrome** und werden von dort gestreamt.
4. *Nach Navidrome* lädt den Titel per yt-dlp in `/music/YouTube/...`, startet einen
   Scan und spielt danach die lokale Datei.
5. Eigene Playlists werden mitgespiegelt, sobald die Titel in Navidrome existieren.

Ohne beschreibbaren Musikordner geht Wiedergabe aus Navidrome trotzdem – nur der Import
nicht.

### MediaSync

Downloads laufen über MediaSync: Suche, Dateiablage in der Bibliothek und
Playlist-Zuordnung. Music Play schickt Interpret + Titel und die gewählte Playlist.

1. MediaSync unter *Mehr → MediaSync* verbinden (URL der MediaSync-WebUI).
2. Im Player **Download** tippen. Es erscheinen die **bestehenden Playlists** dort.
3. Eine Playlist wählen, *Neue Playlist* anlegen oder nur in die Bibliothek legen.

MediaSync lädt mit der bei ihm eingestellten Quelle (YouTube, Usenet, MusiKat …)
und legt die Datei in der Navidrome-/Jellyfin-Struktur ab.

### `STREAM_MODE`

`proxy` leitet das Audio durch den Container. Das kostet etwas Bandbreite auf dem Server,
ist aber die kompatible Variante: nur so funktionieren Amperfys Offline-Downloads und das
Zwischenspeichern zuverlässig, weil YouTubes Medien-URLs kurzlebig und teils an die
IP-Adresse gebunden sind. `redirect` schickt den Client direkt zu Google.

### Eigene YouTube-Music-Playlists

Optional. Mit `ytmusicapi` eine Anmeldung exportieren:

```bash
pip install ytmusicapi
ytmusicapi browser        # erzeugt browser.json
```

Die Datei nach `/mnt/user/appdata/music-play/config/ytmusic_auth.json` legen und
`YTM_AUTH_FILE=/config/ytmusic_auth.json` setzen. Danach erscheinen die Playlists deines
Kontos in der Bibliothek und in Amperfy.

### AutoEq-Profile

Die Kurven von [AutoEq](https://github.com/jaakkopasanen/AutoEq) lassen sich unter
*Equalizer → AutoEq-Profil importieren* einfügen. Sowohl `ParametricEQ.txt` als auch
`GraphicEQ.txt` werden erkannt.

## Fehlerbehebung

**„Titel konnte nicht geladen werden" / im Log `Signature solving failed`**
yt-dlp kommt nicht an die Formate. Meist zu wenig Container-Speicher (siehe oben), sonst
ein veraltetes yt-dlp. Image neu bauen:
`docker compose build --no-cache && docker compose up -d`.

**Wiedergabe stockt oder bleibt stumm auf dem iPhone**
iOS spielt kein Opus in WebM. Normalerweise liefert YouTube für Musik eine AAC-Spur; wenn
nicht, greift die Konvertierung über ffmpeg, was den ersten Start um einige Sekunden
verzögert. `TRANSCODE_TO_AAC=true` muss dafür gesetzt sein.

**Wiedergabe stirbt bei gesperrtem Bildschirm, sobald der Equalizer an ist**
Web Audio auf iOS. Equalizer und Crossfade ausschalten, Seite neu laden.

**Amperfy zeigt eine leere Bibliothek**
Erwartetes Verhalten für YouTube-Favoriten. Navidrome-Inhalte siehst du in der PWA unter
*Bibliothek*. In Amperfy weiter diese App eintragen, nicht Navidrome selbst.

**Navidrome-Import: „Musikordner nicht beschreibbar"**
Container und Navidrome müssen denselben Share sehen. Unraid: Pfad *Musikbibliothek*
auf `/mnt/user/music` (oder deinen Navidrome-Ordner), Target `/music`.

**Suche liefert nichts**
Region oder Sprache prüfen (`YTM_LOCATION`, `YTM_LANGUAGE`) und ob der Container ins
Internet kommt. Im Log stehen die Fehler von ytmusicapi im Klartext.

**Übersetzung tut nichts**
`TRANSLATE_PROVIDER` und `TRANSLATE_URL` müssen gesetzt sein. Unter *Mehr* steht der
aktuelle Status.

**MediaSync: „keine Ingest-API"**
Der laufende MediaSync-Container ist älter als 0.3. Image neu bauen und den Container
neu starten. Unter *Mehr* muss die URL erreichbar sein (`/health`).

## Projektstruktur

```
app/
  core/       Konfiguration, Logging, Icon-Erzeugung
  db/         SQLAlchemy-Modelle und Bibliotheks-Zugriffe
  ytm/        ytmusicapi-Wrapper, ID-Schema, Normalisierung, yt-dlp-Streaming
  navidrome/  Navidrome-Client, Matching, Import in die Musikbibliothek
  mediasync/  Übergabe an MediaSync (Ziele, Download-Auftrag)
  services/   Katalog, Übersetzung, Statistiken, SponsorBlock/RYD
  subsonic/   Subsonic-API: Envelope, Auth, Entitäten, Medienauslieferung
  api/        JSON-API und Sitzungen für die Weboberfläche
  static/     PWA (Player, Equalizer, Offline, Service Worker)
  templates/  index.html
unraid/       Unraid-Template
```

## Rechtliches

Die App greift auf öffentliche YouTube-Endpunkte zu. Das kann den Nutzungsbedingungen von
YouTube widersprechen. Für den privaten Gebrauch gedacht, ohne Gewähr – und nicht
öffentlich erreichbar betreiben. Hörstatistiken bleiben auf dem NAS, es gibt keine
Telemetrie.
