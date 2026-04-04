# Rollout Plan

## Zakres

Jeśli worker działa dla wielu skrzynek, rollout robimy per `mailbox_id`, nie dla wszystkich naraz. Każda skrzynka przechodzi etapy 1-4 osobno, nawet jeśli korzysta z tej samej instancji workera.

## Etap 1

Uruchomić kilka realnych wiadomości w `INBOX.Test-AI-Review`.

Cel:
- sprawdzić klasyfikację na prawdziwych danych
- nie dotykać jeszcze produkcyjnego `AI-Review`
- nie mieszać realnych obserwacji z syntetycznymi testami

Warunki:
- `.env.test` ma `DRY_RUN=true`
- foldery `INBOX.Test-*` istnieją
- lokalny stan testowy jest wyczyszczony przed startem serii

Do sprawdzenia:
- czy klasyfikacja ma sens biznesowy
- czy nie ma nadmiarowego `uncertain`
- czy oferty B2B trafiają do `Other`
- czy reklamacje trafiają do `Complaints`

## Etap 2

Zrobić 1-2 dni obserwacji w `DRY_RUN=true`.

Cel:
- zebrać rzeczywiste przypadki błędów
- poprawić reguły i prompt bez mutacji IMAP

Do sprawdzenia codziennie:
- `logs/test-audit.jsonl`
- `data/test-state.sqlite`
- `output/final-review.csv` lub nowy review CSV dla bieżącej serii

Kryteria przejścia dalej:
- brak zgubionych wiadomości
- brak niekontrolowanego podwójnego przetwarzania
- sensowna klasyfikacja większości realnych wiadomości

## Etap 3

Krótki test `DRY_RUN=false`, dalej tylko na folderach testowych.

Cel:
- potwierdzić, że realny `move` działa poprawnie na realnych mailach
- zweryfikować foldery docelowe, cleanup pass i ślad audytowy

Do sprawdzenia:
- wiadomości trafiają do właściwych `INBOX.Test-*`
- source folder zachowuje się zgodnie z audytem i cleanup pass
- `cleanup_pending` wraca do zera albo jest świadomie wyjaśnione

## Etap 4

Dopiero na końcu myśleć o `AI-Review`.

Warunki wejścia:
- pozytywny wynik etapu 1-3
- brak krytycznych pomyłek w reklamacjach i ofertach
- operator zna procedurę rollbacku i cleanupu
- jest zgoda na przejście z testowych folderów do produkcyjnego przepływu

## Zasada nadrzędna

Najpierw jakość i obserwacja, potem mutacje IMAP, a dopiero na końcu produkcyjny folder.
