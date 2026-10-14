# Eval: validation_parse

A tervvalidálás értelmezője (`agents/worker_agent.py:_parse_validation`). Minden
worker `validate_plan` válasza ezen megy át, és ez dönti el, hogy APPROVE vagy
CONCERN lesz belőle, vagyis ez hajtja a konszenzust. Az eval nyers (szintetikus)
LLM-válaszokat ad a parsernek: modell nélkül, teljesen determinisztikusan fut,
a felhőben is.

| Fájl | Tartalom |
|---|---|
| `cases.jsonl` | Esetek: nyers válasz → `expected` (mit KELLENE a parsernek visszaadnia), `intent` (mit akart a modell mondani), `well_formed` (ember szerint követi-e a kért formátumot), `tag`, `source` |
| `run_eval.py` | Futtatás, lásd lent |
| `results/` | Futásonként egy JSON: pontosság, téves jóváhagyások, téves aggályok, hibás esetek |

```bash
python evals/validation_parse/run_eval.py                       # results/<dátum>_baseline.json
python evals/validation_parse/run_eval.py --label parser-v2     # parser-módosítás után
python evals/validation_parse/run_eval.py --no-write            # csak kiírja
```

Mérőszámok:
- **accuracy** — a parser döntése egyezik az `expected`-del (cél ≥ 0.90).
- **false_approvals** — aggályt jelző válaszból APPROVE lett. Ez a biztonsági
  szempontból kritikus hiba (cél: 0).
- **false_concerns** — formátumot követő jóváhagyásból CONCERN lett (pl. markdown
  `**STATUS:**`). Nem veszélyes, de fölösleges finomítási kört okoz.
- **run_log_rejected** — hány választ címkéz a futásnapló `rejected`-nek (nem
  talált STATUS-döntést, ezért az alapértelmezett CONCERN lépett életbe).

Szabályok:
- Ha a válaszban nincs STATUS-döntés, az elvárt eredmény CONCERN. Ez szándékos
  biztonsági alapértelmezés, nem hiba; ne írd át APPROVE-ra.
- A `_parse_validation` módosítása Gábor jóváhagyását igényli. A PR-ben szerepeljen
  az eval előtte/utána eredménye.
- Ha egy éles session-ben értelmezési hiba derül ki, előbb kerüljön be esetként
  (`source: "incident-<dátum>"`), csak utána jöjjön a javítás. Az esetbe csak a
  válasz *formája* kerüljön, kitalált tartalommal: valódi feladat-, terv- vagy
  persona-szöveg nem (a repó nyilvános).
- Élő modellel mért formátumkövetés (milyen gyakran tér el a modell a STATUS-formától)
  a futásnapló `malformed_validation_share` mérőszámából látszik, nem ebből az evalból.

Kiinduló eredmény (2026-10-14, 34 szintetikus eset): accuracy 0.765, 4 téves
jóváhagyás (`STATUS: NOT APPROVED`, `STATUS: I cannot approve this`,
`STATUS: DISAPPROVE`, `STATUS: APPROVE / CONCERN`: a parser bármely STATUS-sorban
talált „APPROVE” szóra jóváhagy), 4 téves aggály (markdown-os vagy `STATUS :`
szóközös formák).
