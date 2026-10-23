# Voice input

Instructions can be dictated instead of typed. Recognition runs in the browser through the Web
Speech API; nothing is sent to the Arcviz server until you press **Senden**.

## Controls

The composer has a microphone button, a **Dauerhören** checkbox, a language selector and a small
state label next to them.

| Mode | How |
|---|---|
| **Push-to-talk** (default) | Hold the microphone button (mouse, touch, or ++space++ / ++enter++ while it has focus) and speak; release to stop. |
| **Dauerhören** (continuous) | Tick the checkbox, then click the button once to start and once to stop. The browser ends a session every so often; Arcviz restarts it automatically while the mode is on. Silence is fine, but a real error (no network, no microphone, unsupported language, permission denied) ends the mode and is shown in the status line. |

While listening the button pulses red and the label says *hört zu …* or *aufnehmen …*. The
language selector offers `de-DE` (default), `en-US` and `en-GB`; both settings are remembered in
the browser.

## Where the text goes

Recognised text lands in the **same input box** as typed instructions:

- interim results appear in grey italics below the box while you speak;
- each finished phrase is appended to the box.

Nothing is sent automatically. Correct the text, add to it, and press **Senden** (or ++enter++)
as usual. Sending in push-to-talk mode also stops the recording.

## Voice commands without the LLM

Short phrases are executed directly by the UI; they are not sent to the model and not written
into the input box. The **?** button next to the microphone lists them. German and English work:

| Say | Effect |
|---|---|
| *Zoom rein* / *Zoom raus* / *Hundert Prozent* / *Einpassen* | Zoom in, out, reset, fit |
| *Fokus auf prod-vnet*, *zeig mir das VNet*, *focus on prod deployment* | Focus a container (matched loosely by name or id) |
| *Ganzes Diagramm*, *Ebene höher* | Leave the focus, go up one level |
| *Rückgängig* | Undo |
| *Wiederholen* | Redo |
| *Version sichern* | Manual snapshot labelled *Sprachbefehl: Version sichern* |
| *Nächstes Diagramm* / *Vorheriges Diagramm* | Switch diagrams |
| *Quelltext* | Toggle the Mermaid source panel |
| *Absenden* | Send the text in the input box |
| *Eingabe löschen* | Clear the input box |
| *Hilfe* | Open the command list |

Each executed command appears as a small grey line in the chat (not stored in the transcript).
Fillers such as *bitte* are ignored; anything that is not a command becomes text in the input
box, so a real instruction like *Füge ein VNet hinzu* is never swallowed.

## Problems

| Message in the status line | Cause |
|---|---|
| *Mikrofonzugriff verweigert* | The browser blocked the microphone. Allow it in the site permissions and try again. |
| *Kein Mikrofon gefunden* | No input device. |
| *Spracherkennung braucht eine Netzwerkverbindung* | Most browsers send audio to a recognition service; offline it does not work. |
| *Keine Sprache erkannt* | Push-to-talk released without speech. |

## Reading replies aloud

Tick **Vorlesen** (shown only when the browser offers speech synthesis) and the model's final
reply of each turn is read aloud in the selected recognition language, using the browser's own
voices. Speech stops as soon as you press the microphone, send the next instruction, switch the
diagram or untick the box. Tool lines, errors and interim text are never spoken. The setting is
remembered in the browser and off by default.

## Browser support

Chrome, Edge and Safari provide the Web Speech API for recognition; Firefox does not, and recognition
only works on a secure origin (`https://…` or `http://localhost`). Arcviz treats an insecure origin as
unsupported even where the browser still exposes the API. Without support the microphone,
the **Dauerhören** checkbox and the language selector are greyed out with the reason as tooltip,
the state label says *nicht verfügbar*, and a one-time grey notice appears above the transcript.
Typing and every other feature keep working; the local voice commands are then unavailable too.
Speech synthesis (**Vorlesen**) is independent of recognition and available in Firefox as well;
the checkbox is hidden where it is missing.

!!! note "Privacy"
    The Web Speech API in Chrome and Edge streams audio to the browser vendor's recognition
    service. If that is not acceptable, type instead.
