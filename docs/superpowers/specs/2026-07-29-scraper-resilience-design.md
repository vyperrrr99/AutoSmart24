# Resilienza dello scraper — Design

**29/07/2026**

## 1. Il problema, con le prove

In due giorni sei scansioni di marca sono state perse per intero. In nessun caso il guasto riguardava più di un elemento.

| data | marca | causa | lavoro perso |
|---|---|---|---|
| 28/07 | MINI | timeout su una pagina | 5.988 annunci visti, 5.499 già arricchiti |
| 28/07 | MG | timeout su una pagina | tutto: era caduta prima di inserire |
| 28/07 | Audi | chiave duplicata | 28.394 annunci visti — sulla macchina Windows, dove è caduta tre volte di fila |
| 29/07 | Audi | chiave duplicata | 28.396 annunci visti, due volte di fila |
| 29/07 | Fiat | timeout | 28.263 annunci visti, 1.183 nuovi |

Fiat è stata poi recuperata rilanciandola a mano: 68m52s, **1.267 vendite rilevate**, zero falsi positivi. Quelle 1.267 vendite sarebbero rimaste invisibili fino al giro successivo.

Audi invece è caduta **tre volte sullo stesso identico annuncio**, il che dimostra che non tutti i guasti sono transitori e che rilanciare non basta come strategia generale.

## 2. I due guasti, e cosa hanno in comune

**Guasto di rete.** In `scraping/concurrency.py:97`, qualunque eccezione sollevata da un worker fa chiamare `_drain_queue()`, che scarta tutti i job rimanenti; la riga 131 la rilancia. Il client HTTP ritenta già tre volte su errore di trasporto, quindi un job che fallisce ha subito tre timeout consecutivi — ma da lì in poi muore la marca, non la pagina.

**Chiave duplicata.** In `run_manager.py:251` gli id già esistenti sono cercati filtrando per marca:

```python
select(Listing.id).where(Listing.brand == brand.display_name)
```

AutoScout riassegna gli id degli annunci ritirati, **anche a marche diverse**: l'id `c56aac2b-479a-4761-a605-8c7f34404ed1` era una Mercedes-Benz nel nostro database ed è oggi un'Audi sul sito, con un redirect `308` dal vecchio URL al nuovo. Durante la scansione Audi quell'id non risulta esistente, il codice prende la strada dell'inserimento, e la chiave primaria esplode. Il riuso degli id attraversa le marche; la guardia no. Diagnosi completa in `2026-07-28-listing-id-reuse-known-issue.md`.

Cause diverse, **stessa forma**: un singolo elemento difettoso distrugge il lavoro su decine di migliaia di annunci sani.

## 3. Principio guida

**Un elemento non deve poter far cadere l'insieme.** Una pagina irraggiungibile costa quella pagina; un annuncio non scrivibile costa quell'annuncio.

Il corollario, che vale per la rilevazione vendite: **l'assenza di prova non è prova di assenza.** Un timeout dice che non siamo riusciti a chiedere, non che l'annuncio non c'è. Solo una risposta del server che dichiara la pagina inesistente è una prova.

Questa seconda parte è **già soddisfatta** dal fix del 29/07 e non va rifatta: una vendita si dichiara solo dentro il secondo passaggio, e solo se `fetch_detail` ha restituito un risultato — cosa che richiede o una pagina caricata, o un `404`/`410` esplicito. Un timeout solleva un'eccezione e non produce alcun risultato. Nemmeno un `500`: `fetch_detail` intercetta solo `404` e `410` e rilancia il resto.

Ne discende una conseguenza che semplifica tutto il resto del design: **una scansione incompleta non può produrre falsi venduti.** Gli annunci di una pagina saltata finiscono in `missing_ids`, vengono aperti, rispondono `Active`, e restano attivi. Le soglie che seguono governano quindi il **costo**, mai la correttezza.

## 4. Componente 1 — Isolamento del singolo job

`run_worker_pool` smette di trattare come fatale l'eccezione di un job. Il worker la cattura, registra quale job è fallito, e passa al successivo. I fallimenti tornano al chiamante insieme ai risultati.

`BlockedError` **resta fatale** e conserva il comportamento attuale — svuota la coda e rilancia. È l'unico caso in cui proseguire peggiora la situazione: il sito ci sta rifiutando e insistere allunga il blocco.

Gli invarianti documentati in testa al modulo restano validi e vanno preservati: coda dei risultati illimitata, il marcatore di fine sempre dietro a ogni risultato, nessun ordinamento garantito.

**Questo componente da solo ripara tre dei quattro chiamanti**, senza modificarli. `process_detail_backlog` parcheggia già in `failed_ids` le righe su cui il pool non ha riferito (`run_manager.py:196`). Il passaggio sui mancanti e quello di conferma non dichiarano nulla per un annuncio di cui non hanno ricevuto risultato. Erano già scritti per tollerare risultati mancanti: fallivano solo perché l'eccezione arrivava da sotto.

## 5. Componente 2 — La ricerca ritenta e riferisce

Il quarto chiamante, la ricerca, richiede lavoro proprio, perché i suoi job non hanno tutti lo stesso peso.

`crawl_brand` lavora in due fasi (`scraping/crawler.py:114` e `131`):

- **scoperta**: un job per modello, che impara quante pagine ha quel modello e ne restituisce la prima
- **paginazione**: un job per ogni pagina dalla seconda in poi

Un job di paginazione perso costa una pagina, circa venti annunci. Un job di **scoperta** perso costa il **modello intero**: l'unità non entra mai in `units`, quindi le sue pagine non vengono nemmeno messe in lista. Per un modello di grande volume sono migliaia di annunci.

**Terza fase: recupero.** In coda alla scansione i job falliti vengono ritentati una volta, prima le scoperte, poi le pagine — incluse quelle dei modelli appena recuperati, che prima non esistevano in lista. Fra il primo tentativo e il recupero passano minuti: è lo stesso ragionamento che regge la doppia conferma delle vendite, un guasto breve non colpisce due volte a distanza.

**Rapporto di copertura.** `crawl_brand` restituisce cosa non è stato recuperato, tenendo **separati modelli e pagine**. La distinzione non è cosmetica, come stabilisce il componente successivo.

## 6. Componente 3 — Quando la rilevazione vendite può girare

Il buco lasciato da una scansione incompleta è **stimabile solo per le pagine**. Una pagina persa non l'abbiamo mai scaricata, quindi non sappiamo quanti annunci contenesse: la si conta a **20 annunci**, la dimensione di pagina di AutoScout (`MAX_RESULTS_PER_QUERY = 4000` su 200 pagine). È una stima, ed è accettabile che lo sia perché la soglia governa il costo e non la correttezza — sbagliarla di qualche annuncio cambia solo di quanto lavoro inutile ci si fa carico.

Un modello perso in scoperta è invece caduto proprio mentre imparava quante pagine ha: non sappiamo se mancano cinquanta annunci o cinquemila, e non possiamo saperlo senza rifare la richiesta appena fallita. Qui non c'è nemmeno una stima da fare.

Una soglia percentuale su un denominatore ignoto non è una misura. Quindi:

| cosa è fallito dopo il recupero | rilevazione vendite | stato |
|---|---|---|
| niente | sì | `success` |
| solo pagine, buco ≤ 5% degli annunci visti | **sì** | `success` |
| solo pagine, buco > 5% | no | `partial` |
| **almeno un modello in scoperta** | no | `partial` |

La soglia del 5% non protegge la correttezza — quella è garantita a monte (§3). Governa il lavoro sprecato: su una marca da 30.000 annunci un buco del 5% sono 1.500 annunci da verificare due volte, circa 43 minuti per scoprire che sono tutti vivi. Oltre quella soglia il costo smette di valere il beneficio, e conviene rifare la scansione.

**`partial` ha un significato unico: questo giro non ha valutato le vendite.** Una run con un buco piccolo che ha comunque fatto il controllo chiude `success`, perché il suo lavoro l'ha fatto; il buco resta registrato. Questo mantiene lo stato leggibile a colpo d'occhio e rende banale la regola del componente 5.

## 7. Componente 4 — Isolamento del singolo annuncio

Il crash da chiave duplicata non avviene in un worker ma scrivendo a database, quindi il componente 1 non lo intercetta. Serve la stessa idea applicata alla scrittura.

**Rilevazione.** Gli id già esistenti vengono caricati **senza filtro di marca**, come coppie id → marca. Un id che risulta appartenere a un'altra marca è un riuso, e viene riconosciuto **prima** del tentativo di inserimento anziché scoperto dall'esplosione del vincolo.

**Trattamento.** L'annuncio viene saltato, con un evento di livello `warning` che riporta id, marca a database e marca incontrata, e un incremento di `errors_count`. La scansione prosegue.

**Rete di sicurezza generale.** Il riuso id è la causa che conosciamo, non l'unica possibile: un dato malformato o un futuro cambio di schema possono far fallire una scrittura in modi che non sappiamo prevedere. Il commit di un lotto viene quindi protetto: se fallisce, **il lotto viene scartato e registrato, e la scansione prosegue**.

La granularità è il lotto, non la singola riga, ed è una scelta di rischio deliberata. Recuperare riga per riga richiederebbe di riprocessare il lotto rifacendo il confronto e la scrittura per ogni annuncio, cioè di estrarre e rifattorizzare il blocco di scrittura più critico del progetto — proprio quello che ha appena superato un ciclo di revisione. Il salto di grandezza che conta è già tutto nel primo passo: si perdono qualche centinaio di annunci invece di ventottomila, e quelli persi non portano stato proprio, quindi il giro successivo li inserisce normalmente. Scendere dal lotto alla riga aggiungerebbe un rischio reale per un guadagno marginale.

C'è un dettaglio che questo passaggio non deve sbagliare: gli id del lotto scartato **restano** fra quelli visti. Erano davvero sul sito, e lasciarli cadere fra i mancanti li esporrebbe esattamente al percorso di falsa vendita che il lavoro del 29/07 ha chiuso.

**Quel che questo componente non fa.** La riga vecchia resta come sta e l'auto nuova non viene catturata. È il limite già dichiarato dal documento sul riuso id: chiudere correttamente la riga obsoleta e assegnare all'auto nuova una chiave che non collida è un lavoro semantico più grande, che resta suo. Qui ci limitiamo a non far cadere la marca — e a registrare ogni occorrenza, così che la frequenza reale del fenomeno diventi finalmente misurabile invece che stimata su un solo caso.

## 8. Componente 5 — Riaccodamento

Una marca la cui run finisce in `error` o `partial` torna **in fondo alla coda del giro corrente** e viene ritentata **una volta sola**. Non vengono ritentate le run `success` né quelle `blocked`, dove insistere peggiora le cose.

In fondo alla coda, non subito: fra il fallimento e il nuovo tentativo passano ore, quindi un guasto di rete transitorio è verosimilmente rientrato. Il recupero di Fiat del 29/07 è la prova diretta che funziona — fallita alle 15:12, ripassata alle 18:30 con 1.267 vendite rilevate.

Un solo tentativo, non di più: Audi ha dimostrato che un guasto deterministico fallisce identico a ogni ripetizione, e insistere consumerebbe solo tempo.

## 9. Gestione degli errori

| situazione | comportamento |
|---|---|
| pagina in timeout | job saltato, ritentato a fine scansione |
| pagina ancora in timeout dopo il recupero | conta nel buco; decide la soglia del §6 |
| modello perso in scoperta e non recuperato | run `partial`, niente rilevazione vendite |
| `BlockedError` in qualunque fase | comportamento attuale invariato: interrompe |
| timeout nell'arricchimento | riga parcheggiata in `failed_ids`, ripresa al giro dopo |
| timeout nella verifica dei mancanti | il candidato non entra in lista, resta `active` |
| timeout nella conferma | nessuna vendita dichiarata: non c'è prova |
| id già esistente sotto un'altra marca | annuncio saltato, evento `warning`, scansione prosegue |
| altro fallimento di scrittura | lotto riprovato riga per riga, scartate solo le righe rotte |
| run `error` o `partial` | riaccodata una volta in fondo al giro |

## 10. Effetti collaterali da governare

**Diluvio di eventi.** Un buco vicino alla soglia genera centinaia di annunci "non trovato nella scansione ma ancora attivo", oggi un evento e un incremento di `errors_count` ciascuno. Sulla dashboard sembrerebbe un guasto grave mentre è il sistema che funziona come previsto. Quando la scansione è nota incompleta, quel percorso produce **un evento riassuntivo** invece di uno per annuncio, e il conteggio tiene distinte le anomalie vere dal recupero di un buco già dichiarato.

**`partial` è uno stato nuovo.** Vanno aggiornati la dashboard e gli script di monitoraggio (`run-completo.sh`, `run-recupero2.sh`), che oggi attendono `success|error|blocked` e su `partial` resterebbero in attesa indefinita.

## 11. Testing

Il difetto nasce da uno scenario che nessun test copriva, quindi si parte da lì.

**Riproduzione.** Un pool in cui un job solleva un timeout deve consegnare **tutti gli altri risultati** e riferire il fallito. Scritto contro il codice attuale questo test deve **fallire**: è la prova che riproduce il difetto e non altro.

**Recupero.** Una scoperta che fallisce al primo tentativo e riesce al recupero porta a copertura completa, e il modello recuperato contribuisce anche le sue pagine. Una che non si recupera dà `partial`.

**La soglia.** Buco sotto il 5% di sole pagine → la rilevazione vendite gira e la run è `success`. Buco sopra → non gira. Un modello perso → non gira **qualunque** sia il numero di pagine perse, anche zero.

**Nessuna vendita senza prova.** Un candidato la cui conferma va in timeout resta `active`. È il cuore del principio del §3 e va asserito esplicitamente, non dato per scontato dal fatto che il codice del 29/07 lo faceva.

**Blocco.** `BlockedError` continua a interrompere tutto, in ognuna delle quattro fasi.

**Riuso id.** Un annuncio il cui id esiste già sotto un'altra marca viene saltato con un evento, e la scansione **completa** e dichiara le vendite normalmente. È il caso Audi: il test deve verificare che la marca arriva in fondo, non solo che non esplode.

**Scrittura rotta.** Un lotto il cui commit fallisce viene scartato e la scansione **arriva in fondo**, dichiarando le vendite normalmente. Il test verifica che la marca si completi, non solo che non esploda, e che gli annunci del lotto scartato non finiscano fra i mancanti.

## 12. Fuori perimetro

- **La semantica del riuso id** — chiudere la riga obsoleta, catturare l'auto nuova sotto una chiave non collidente. Resta in `2026-07-28-listing-id-reuse-known-issue.md`, che descrive già cosa servirebbe.
- **Ripresa fra processi.** La scansione si completa dentro la stessa run; non si conserva su disco un punto di ripresa per un riavvio del container. Il riaccodamento del §8 copre il caso pratico a costo molto minore.
- **`search_total` mai popolato** e **la resa dei tempi in fuso locale sulla dashboard**: follow-up noti e indipendenti da questo lavoro.
- **Estendere `BlockRateTracker` a contare `404`/`410`** nella fase di arricchimento: già rimandato dalla spec del 28/07, e questo lavoro non cambia le ragioni del rinvio.
