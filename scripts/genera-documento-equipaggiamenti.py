#!/usr/bin/env python3
"""Builds the option-selection document handed to a market expert.

The reader is not technical and will not open a CSV. They need to read what
each option is, see how common it is, and hand back a choice -- so the page
carries a checkbox per row and copies the selection out as plain text. A
document they can only read would have to be answered in a separate email,
in whatever wording came to mind.
"""
from __future__ import annotations

import html
import json
import sys

DATA = json.load(open(sys.argv[1]))
N = DATA["auto"]


def bar(pct: float) -> str:
    return f'<span class="bar" style="--w:{min(pct, 100):.1f}%"></span>'


def euro(d) -> str:
    if d is None:
        return '<span class="nil">—</span>'
    s = f"{abs(d):,}".replace(",", ".")
    cls = "up" if d > 0 else "down"
    return f'<span class="{cls}">{"+" if d > 0 else "−"}{s} €</span>'


def opt_rows(cat: str) -> str:
    rows = [o for o in DATA["opzioni"] if o["cat_it"] == cat]
    rows.sort(key=lambda o: (-(o["delta_prezzo"] if o["delta_prezzo"] is not None else -10**9), -o["pct"]))
    out = []
    for o in rows:
        name = html.escape(o["opzione"])
        out.append(
            f'<tr><td class="pick"><label><input type="checkbox" value="{name}"><span></span></label></td>'
            f'<td class="name">{name}</td>'
            f'<td class="freq">{bar(o["pct"])}<b>{o["pct"]:.0f}%</b></td>'
            f'<td class="num">{euro(o["delta_prezzo"])}</td></tr>'
        )
    return "\n".join(out)


def attr_rows(key: str) -> str:
    out = []
    for v in DATA[key]:
        nd = v["valore"] == "non dichiarato"
        out.append(
            f'<tr class="{"muted" if nd else ""}"><td class="name">{html.escape(v["valore"])}</td>'
            f'<td class="freq">{bar(v["pct"])}<b>{v["pct"]:.0f}%</b></td>'
            f'<td class="num">{euro(v["delta"])}</td></tr>'
        )
    return "\n".join(out)


CATS = ["Comfort e praticità", "Sicurezza", "Extra", "Intrattenimento e multimedia"]

sections = "\n".join(
    f"""<section class="block">
  <h3>{html.escape(c)} <span class="count">{sum(1 for o in DATA["opzioni"] if o["cat_it"] == c)} voci</span></h3>
  <div class="scroll"><table class="opts">
    <thead><tr><th class="pick"><span class="sr">Scegli</span></th><th>Dotazione</th>
      <th>Su quante auto</th><th class="num">Scarto di prezzo</th></tr></thead>
    <tbody>{opt_rows(c)}</tbody>
  </table></div>
</section>"""
    for c in CATS
)

HTML = f"""<title>Dotazioni auto usate — quali vale la pena registrare</title>
<style>
:root {{
  --paper:#F4F6F5; --card:#FFFFFF; --ink:#171C1E; --soft:#5C6A6C; --rule:#DBE2E0;
  --accent:#12514C; --accent-soft:#E3EDEB; --up:#7A5310; --down:#4A5A5C;
  --bar:#C9D8D4;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --paper:#101416; --card:#171D1F; --ink:#E4EAE8; --soft:#94A3A3; --rule:#283234;
    --accent:#63B8AC; --accent-soft:#182B29; --up:#D6A94E; --down:#8FA0A1; --bar:#2C3E3B;
  }}
}}
:root[data-theme="dark"] {{
  --paper:#101416; --card:#171D1F; --ink:#E4EAE8; --soft:#94A3A3; --rule:#283234;
  --accent:#63B8AC; --accent-soft:#182B29; --up:#D6A94E; --down:#8FA0A1; --bar:#2C3E3B;
}}
:root[data-theme="light"] {{
  --paper:#F4F6F5; --card:#FFFFFF; --ink:#171C1E; --soft:#5C6A6C; --rule:#DBE2E0;
  --accent:#12514C; --accent-soft:#E3EDEB; --up:#7A5310; --down:#4A5A5C; --bar:#C9D8D4;
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--paper); color:var(--ink);
  font:400 17px/1.62 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased; padding-bottom:5.5rem;
}}
.wrap {{ max-width:62rem; margin:0 auto; padding:3.5rem 1.5rem 2rem; }}
.prose {{ max-width:36rem; }}
.eyebrow {{
  font-size:.72rem; letter-spacing:.16em; text-transform:uppercase;
  color:var(--accent); font-weight:600; margin:0 0 .9rem;
}}
h1 {{ font-size:clamp(1.9rem,4.2vw,2.6rem); line-height:1.12; letter-spacing:-.022em;
     font-weight:650; margin:0 0 1rem; text-wrap:balance; }}
h2 {{ font-size:1.42rem; letter-spacing:-.014em; font-weight:640; margin:0 0 .8rem; text-wrap:balance; }}
h3 {{ font-size:1.06rem; letter-spacing:-.008em; font-weight:640; margin:0 0 .7rem;
     display:flex; align-items:baseline; gap:.6rem; }}
p {{ margin:0 0 1rem; }}
.lead {{ font-size:1.1rem; color:var(--soft); }}
.count {{ font:500 .74rem/1 ui-monospace,SFMono-Regular,Menlo,monospace;
          color:var(--soft); letter-spacing:.04em; }}
hr {{ border:0; border-top:1px solid var(--rule); margin:3rem 0; }}
section {{ margin:0 0 2.6rem; }}
.facts {{ display:flex; flex-wrap:wrap; gap:0 2.4rem; margin:1.6rem 0 0;
          padding-top:1.2rem; border-top:1px solid var(--rule); }}
.facts div {{ display:flex; flex-direction:column; gap:.15rem; }}
.facts dt {{ font-size:.74rem; letter-spacing:.09em; text-transform:uppercase; color:var(--soft); }}
.facts dd {{ margin:0; font:600 1.35rem/1.2 ui-monospace,SFMono-Regular,Menlo,monospace;
             font-variant-numeric:tabular-nums; }}
.note {{ background:var(--card); border:1px solid var(--rule); border-left:3px solid var(--accent);
         padding:1.1rem 1.3rem; margin:0 0 1.1rem; }}
.note p:last-child {{ margin:0; }}
.note b {{ font-weight:640; }}
.scroll {{ overflow-x:auto; border:1px solid var(--rule); background:var(--card); }}
table {{ border-collapse:collapse; width:100%; font-size:.95rem; }}
th {{ text-align:left; font-size:.72rem; letter-spacing:.09em; text-transform:uppercase;
      color:var(--soft); font-weight:600; padding:.75rem .9rem; border-bottom:1px solid var(--rule);
      white-space:nowrap; }}
td {{ padding:.5rem .9rem; border-bottom:1px solid var(--rule); vertical-align:middle; }}
tbody tr:last-child td {{ border-bottom:0; }}
tbody tr:hover {{ background:var(--accent-soft); }}
.name {{ min-width:15rem; }}
.num, th.num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap;
                font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:.9rem; }}
.up {{ color:var(--up); font-weight:600; }}
.down {{ color:var(--down); }}
.nil {{ color:var(--soft); }}
.freq {{ width:11rem; white-space:nowrap; }}
.freq b {{ font:600 .84rem/1 ui-monospace,SFMono-Regular,Menlo,monospace;
           font-variant-numeric:tabular-nums; color:var(--soft); }}
.bar {{ display:inline-block; width:6.5rem; height:.42rem; margin-right:.6rem;
        background:linear-gradient(to right,var(--accent) var(--w),var(--bar) var(--w));
        vertical-align:middle; }}
tr.muted td {{ color:var(--soft); }}
.pick {{ width:2.6rem; }}
.pick label {{ display:inline-flex; cursor:pointer; }}
.pick input {{ position:absolute; opacity:0; width:1px; height:1px; }}
.pick span {{ display:block; width:1.05rem; height:1.05rem; border:1.5px solid var(--rule);
              background:var(--card); }}
.pick input:checked + span {{ background:var(--accent); border-color:var(--accent);
  background-image:linear-gradient(45deg,transparent 44%,var(--card) 44%,var(--card) 56%,transparent 56%),
                   linear-gradient(-45deg,transparent 44%,var(--card) 44%,var(--card) 56%,transparent 56%); }}
.pick input:focus-visible + span {{ outline:2px solid var(--accent); outline-offset:2px; }}
.sr {{ position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); }}
.tray {{ position:fixed; left:0; right:0; bottom:0; background:var(--card);
         border-top:1px solid var(--rule); padding:.85rem 1.5rem; display:flex;
         align-items:center; gap:1rem; justify-content:center; flex-wrap:wrap; }}
.tray output {{ font:600 .95rem/1 ui-monospace,SFMono-Regular,Menlo,monospace;
                font-variant-numeric:tabular-nums; }}
button {{ font:600 .9rem/1 inherit; padding:.62rem 1.1rem; border:1px solid var(--accent);
          background:var(--accent); color:var(--card); cursor:pointer; }}
button.ghost {{ background:transparent; color:var(--accent); }}
button:focus-visible {{ outline:2px solid var(--accent); outline-offset:2px; }}
footer {{ color:var(--soft); font-size:.9rem; }}
@media (max-width:640px) {{ .freq {{ width:auto; }} .bar {{ width:3.4rem; }} .name {{ min-width:11rem; }} }}
</style>

<div class="wrap">
<p class="eyebrow">AutoSmart24 · dati di mercato</p>
<h1>Dotazioni delle auto usate:<br>quali vale la pena registrare</h1>
<div class="prose">
<p class="lead">Raccogliamo gli annunci di auto usate da AutoScout24 per studiare
prezzi e tempi di vendita. Le pagine degli annunci elencano anche le dotazioni,
che oggi non salviamo. Prima di cominciare a farlo, vorremmo sapere quali contano
davvero.</p>
<p><b>Cosa ti chiediamo:</b> scorrere gli elenchi e spuntare le voci che, per
esperienza, incidono sul valore o sulla vendibilità di un'auto usata. In fondo
alla pagina un pulsante copia la tua selezione, da rimandarci così com'è.</p>
</div>

<dl class="facts">
  <div><dt>Auto esaminate</dt><dd>{N}</dd></div>
  <div><dt>Marche</dt><dd>25</dd></div>
  <div><dt>Dotazioni trovate</dt><dd>{len(DATA["opzioni"])}</dd></div>
</dl>

<hr>

<section class="prose">
<h2>Come leggere i numeri</h2>
<p><b>Su quante auto</b> indica quanto la dotazione è diffusa nel campione. Una
voce presente sull'80% delle auto distingue poco: quasi tutte ce l'hanno.</p>
<p><b>Scarto di prezzo</b> è la differenza fra il prezzo medio delle auto che
hanno quella dotazione e quello delle auto che non ce l'hanno.</p>
<div class="note">
<p><b>Attenzione: non è quanto vale l'accessorio.</b> Un'auto col tettuccio
costa in media 27.000 € più di una senza, ma non perché il tettuccio valga
ventisettemila euro: chi ordina il tettuccio ordina anche il motore grosso e la
pelle, su un'auto di categoria superiore.</p>
<p>Si vede dai casi assurdi: l'ESP risulta a <b>−4.400 €</b>. Non deprezza
l'auto — è che viene elencato soprattutto sulle utilitarie. Il numero serve a
ordinare i candidati, non a stimare un valore.</p>
</div>
<div class="note">
<p><b>Seconda avvertenza:</b> questi elenchi dicono cosa il venditore ha scelto
di dichiarare, non cosa l'auto ha davvero. Nessuna voce supera il 90% di
diffusione, nemmeno l'ABS, obbligatorio per legge dal 2004: compare sul 76%.
Un concessionario compila tutto, un privato mette quattro voci.</p>
</div>
</section>

<hr>

<section>
<h2>Caratteristiche dell'auto</h2>
<div class="prose">
<p>Queste non sono dotazioni dichiarate a piacere, ma attributi che AutoScout
registra in modo strutturato. Sono più affidabili delle voci dell'elenco.</p>
</div>

<h3>Finitura della vernice <span class="count">richiesta esplicitamente</span></h3>
<div class="prose"><div class="note">
<p><b>Disponibile, ma non nel dettaglio che serve.</b> AutoScout distingue solo
<i>metallizzato</i> da <i>altro</i>: perlato, opaco e pastello finiscono tutti
nella stessa casella e non sono separabili.</p>
<p>L'unica traccia più fine è il nome commerciale del colore («Rosso Alfa
Pastello», «Nero perla»), presente sul 32% delle auto e con un termine di
finitura riconoscibile solo sul 4%. Troppo poco per farne una variabile
affidabile.</p>
</div></div>
<div class="scroll"><table>
  <thead><tr><th>Valore</th><th>Su quante auto</th><th class="num">Scarto di prezzo</th></tr></thead>
  <tbody>{attr_rows("vernice")}</tbody>
</table></div>

<h3 style="margin-top:2rem">Rivestimento degli interni</h3>
<div class="scroll"><table>
  <thead><tr><th>Valore</th><th>Su quante auto</th><th class="num">Scarto di prezzo</th></tr></thead>
  <tbody>{attr_rows("interni")}</tbody>
</table></div>

<h3 style="margin-top:2rem">Trazione</h3>
<div class="scroll"><table>
  <thead><tr><th>Valore</th><th>Su quante auto</th><th class="num">Scarto di prezzo</th></tr></thead>
  <tbody>{attr_rows("trazione")}</tbody>
</table></div>
</section>

<hr>

<section>
<h2>Dotazioni ed equipaggiamenti</h2>
<div class="prose">
<p>{len(DATA["opzioni"])} voci distinte, divise nelle quattro categorie usate da
AutoScout. All'interno di ogni categoria sono ordinate dallo scarto di prezzo
più alto al più basso, così le più promettenti stanno in cima.</p>
</div>
{sections}
</section>

<hr>
<footer class="prose">
<p><b>Come è stato costruito il campione.</b> {N} auto, cinque per ciascuna
fascia di prezzo di ciascuna delle 25 marche seguite. La scelta di bilanciare
per marca e prezzo serve a non far sparire le dotazioni di fascia alta: un
campione casuale sarebbe dominato dalle marche con più annunci in vendita, e
farebbe sembrare il tetto panoramico più raro di quanto sia.</p>
</footer>
</div>

<div class="tray">
  <output id="n">0 dotazioni scelte</output>
  <button id="copy">Copia la selezione</button>
  <button class="ghost" id="clear">Azzera</button>
</div>

<script>
const boxes = () => [...document.querySelectorAll('.pick input')];
const chosen = () => boxes().filter(b => b.checked).map(b => b.value);
const out = document.getElementById('n');
function refresh() {{
  const n = chosen().length;
  out.textContent = n === 1 ? '1 dotazione scelta' : n + ' dotazioni scelte';
}}
document.addEventListener('change', e => {{ if (e.target.matches('.pick input')) refresh(); }});
document.getElementById('copy').addEventListener('click', async () => {{
  const list = chosen();
  const btn = document.getElementById('copy');
  if (!list.length) {{ btn.textContent = 'Nessuna voce spuntata'; setTimeout(() => btn.textContent = 'Copia la selezione', 1800); return; }}
  const text = 'Dotazioni scelte (' + list.length + '):\\n' + list.map(v => '- ' + v).join('\\n');
  try {{ await navigator.clipboard.writeText(text); btn.textContent = 'Copiato'; }}
  catch {{ btn.textContent = 'Copia non riuscita'; }}
  setTimeout(() => btn.textContent = 'Copia la selezione', 1800);
}});
document.getElementById('clear').addEventListener('click', () => {{
  boxes().forEach(b => b.checked = false); refresh();
}});
refresh();
</script>
"""

open(sys.argv[2], "w").write(HTML)
print(f"scritto {sys.argv[2]}")
