Die vier Workflows teilen sich denselben Aufbau:

1. `scripts/db_sync.sh restore` holt die Archiv-DB aus dem Branch `mi-data`.
2. Der eigentliche `mi`-Befehl laeuft.
3. `scripts/db_sync.sh save` schreibt sie zurueck — auch wenn der Schritt
   davor fehlgeschlagen ist, sonst gehen die gesammelten Items verloren.

`concurrency` verhindert, dass zwei Laeufe gleichzeitig auf denselben Branch
schreiben und einer den anderen ueberholt.

Benoetigte Secrets: ANTHROPIC_API_KEY, RESEND_API_KEY (oder die SMTP_*),
MI_MAIL_FROM, MI_MAIL_TO.
