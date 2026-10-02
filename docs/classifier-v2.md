# Classifier v2: action axis + protective signals

## Problem (golden set, 210 labelled mails, 2026-09-27)
Production (qwen3:8b, thinking) sent 19 of 69 human mails out of INBOX. 13 of them were lost by
every model tested (qwen3:8b ±thinking, qwen3:14b, gemma3:12b): the question asked was wrong, not the model.
- 8 replies in threads the owner started (tickets, quotes, correspondence)
- 6 booking notifications (Booksy, own booking form)
- 5 transactional mails that need action (insurer waits for photos, order awaits confirmation, ...)
Root causes: the taxonomy mixes *type* with *action*; strong non-LLM signals were ignored;
the cost of an error is asymmetric (lost mail >> noise left in INBOX) but thresholds were symmetric.

## Design (src/mail_ai_agent/classifier_v2.py)
1. Protective: `In-Reply-To`/`References` hits a Message-ID from the owner's Sent folders -> question;
   booking subject from own or trusted domain -> appointment.
2. Hard rules (header/content-independent): spam headers, bank documents, bounces, complaints.
3. Keyword rules (newsletter/offer/billing/payment) apply only to bulk mail (List-* headers).
4. LLM answers the action first: do_zrobienia | informacja | szum | spam, then the type tag.
   Signals (reply-to-me, known correspondent, Re: prefix, bulk) are passed as context.
5. Floors: a known correspondent (someone the owner wrote to) is never newsletter/offer/spam/system;
   a noise verdict below `CLASSIFIER_V2_NOISE_MIN_CONFIDENCE` (0.75) stays in INBOX as `other`.
Output is a v1 category, so hybrid routing is unchanged.

## Sent index
`python -m mail_ai_agent.sent_index` (launchd `com.mailai.sentindex`, every 15 min, read-only IMAP).
Stores sha256 hashes only, `data/sent-index.json` (0600).

## Rollout
1. Benchmark: `ops/bench_models.py --v2 --models ...`, then `--rescore`.
2. Shadow: `CLASSIFIER_V2_SHADOW=true` -> `logs/shadow-v2.jsonl`; review `ops/shadow_report.py`.
   Production routing is untouched; one extra model call per message.
3. Switch: separate change, per mailbox, after owner review of shadow disagreements.
Rollback of shadow: remove `CLASSIFIER_V2_SHADOW` from `.env.multi.prod` (worker reads env per run).

## 2026-10-02 additions (after 5 days of shadow)
- Production: `CLASSIFIER_V2_PROTECT=true` — protective signals (reply to owner, booking, ticket reply)
  decide before rules and the v1 LLM. Shadow: 66 protective decisions, none wrong; v1 had filed 11/62 bookings as noise.
- Mailbox phishing rule: subject about expiring password / helpdesk / full mailbox from a domain other than
  the mailbox's own or the provider (mydevil.net) -> spam, without the LLM.
- Payment notices: LLM "needs action" + type billing stays billing only when the subject names an invoice,
  payment, arrears or subscription (PAYMENT_SUBJECT_REGEX) and is not a reply. A broader version lost
  insurer/refund mails (golden 6 -> 11 lost) and was narrowed.
- Golden (dev set): accuracy 70% -> 72%, human mail lost 6 -> 6, noise left in INBOX 18 -> 14.
