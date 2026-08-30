# Lavoro rimandato

Elenco vivo dei follow-up noti: cose viste, capite, e rimandate di proposito
o per mancanza di tempo. Versionato apposta — non nella memoria privata della
sessione — così un incidente locale non può cancellarlo senza lasciare
traccia nella cronologia di git.

Quando si chiude un follow-up, spostarlo in fondo sotto "Chiusi", con la data
e il commit che l'ha risolto. Non cancellarlo: la cronologia di cosa è stato
rimandato e perché è la parte utile.

---

## Aperti

### `search_total` non è mai scritto

Da fine luglio 2026. Zero riferimenti nel codice di produzione, zero giri con
quel campo valorizzato a database (verificato l'ultima volta il 30/08/2026).
Durante la fase di ricerca la dashboard non ha una percentuale, solo una barra
indeterminata. Innocuo, mai chiuso.

### Nessuna sorveglianza sui giri che restano `running`

Il 21/08/2026 Jeep è rimasta `running` per dieci ore (la macchina era
sospesa) ed è stata chiusa a mano. Nessuno script controlla le righe orfane
in `scrape_runs` in modo continuo — l'unico che lo fa,
`attiva-due-macchine.sh`, è in disuso dopo l'abbandono della seconda macchina
(Surfshark non riusciva a dare al contenitore un IP di uscita separato senza
rompere Tailscale). Se un giro si pianta di notte, al mattino la coda risulta
ferma e nessuno se ne accorge finché non si guarda a mano.

### Fallimenti isolati del pool di lavoro non lasciano traccia aggregata

`run_worker_pool` isola un'eccezione per singolo job — registrata in
`failures`, non ferma il pool — ma non c'è un tetto complessivo su quanti job
possono fallire in un giro prima che valga la pena fermarsi e segnalarlo. Un
problema sistematico ma sotto la soglia di un 403/429 esplicito passerebbe
silenzioso.

### Nessun tetto aggregato sugli skip

Imparentato col punto sopra. Singoli skip (annuncio non processabile,
riprovato la notte dopo) sono normali; non c'è una soglia oltre la quale il
volume di skip in un giro segnali un problema strutturale invece che rumore
di fondo.

### Autoflush fuori dal punto in cui si scrive

Segnalato come rischio nel ciclo di scrittura del giro: un flush automatico
di SQLAlchemy fuori dalla sezione pensata per proteggere la scrittura
potrebbe esporre righe a metà scritte a una query concorrente. Da
riverificare puntualmente nel codice — non ricontrollato di recente.

### Migrazione del fuso orario

`datetime.utcnow()` è usato ovunque ed è deprecato (`DeprecationWarning` in
ogni esecuzione di test, centinaia di occorrenze). Non rotto, da migrare a
oggetti timezone-aware prima che Python lo rimuova in una versione futura.

### Sorveglianza mensile sui redirect non verificabili

Aperto il 30/08/2026. `scripts/controllo-redirect-mensile.py`, agganciato a
`riclassifica-notturna.sh`, traccia se ricompare un blocco di annunci "mai
arricchiti" marcati venduti via redirect alla pagina di lista invece che
404/410 — il difetto che ha prodotto 373 vendite Fiat non verificabili il
23/08, ripulite lo stesso giorno (`removal_reason = redirect_unverified`).

**Da rivedere entro fine settembre 2026.** Se lo storico in
`stato/redirect-mensile.jsonl` resta a zero, l'incidente era legato solo
all'arretrato Fiat pre-correzione e il follow-up si chiude. Se cresce, serve
un criterio strutturale invece della pulizia una tantum.

---

## Chiusi

*(vuoto per ora — i follow-up sopra sono tutti aperti al 30/08/2026)*
